/* fnv-nx L6 deko3d backend: shaders (NXGPU_FN_SHADER_CREATE / SHADER_CREATE_D3D / SHADER_DESTROY). Ids 1..4096, lowest free, 0 = failed with a recorded error, destroying a dead id
 * is NXGPU_BAD_CALL: the numbers and codes of gl4_shader.c / gl4_d3dshader.c.
 *  K1 every byte a call reads comes from nxdk_guest_ptr over exactly the ranges the descriptor names, and is copied (malloc) before it is looked at; nothing beyond is read.
 *  K2 a DKSH image is accepted only if dkShaderInitialize's own requirements hold AND everything it would index is inside the image (check_dksh): magic, header_sz, control_sz / code_sz
 *     non-zero multiples of 256 that add up to the image size, exactly one program at programs_off inside the control section, its type is the requested stage, entrypoint and
 *     constant buffer 1 inside the code section. The code goes to a Code-flag block of code_sz + DK_SHADER_CODE_UNUSABLE_SIZE rounded up to 4 KiB (deko3d's last-0x400-bytes rule), offset 0.
 *  K3 D3D shaders are NOT translated here: the translation happened at install time (NXSP v2, dk.h). SHADER_CREATE_D3D takes its DKSH from the pack's D table: the key (sha256 of the
 *     bytecode) is an index, the row is accepted only if stage, variant, link set, bytecode size and bytecode bytes equal the descriptor's. A miss, a mismatch or an invalid pack is an
 *     error through nxdk_err (the cbError queue), code 0, nothing registered; there is no substitute shader.
 *  K4 variant and link set are part of the D row (compared, not ignored): the same bytecode with another variant or link set is another row.
 *  K5 the same D row returns the same id and counts one more owner (GL D3); each destroy takes one back, the last releases the code block.
 *  K6 SHADER_CREATE (NxShaderDesc = stage, glsl, glsl_size, sha256 key) takes its DKSH from the pack's G table (NXSP v2, dk.h): the key is an index, the row is accepted only if stage, GLSL size
 *     and GLSL bytes equal the descriptor's. A missing key, a mismatch or an invalid pack is an error through nxdk_err (the cbError queue), code 0, nothing registered; no default shader.
 * Assumption: the caller does not destroy a shader whose commands are in flight (the GL rule). */
#include "dk.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { NXDK_MAX_SHADERS = 4096 };   /* GL4_MAX_SHADERS */
enum { MAX_DKSH = 4u << 20, MAX_CODE = 1u << 20, MAX_L = 31, PROG_SZ = 64, HDR_SZ = 24, IDX_OFF = 32 };

typedef struct Shader { DkShader sh; DkMemBlock blk; uint32_t drow, refs, stage; uint8_t used; } Shader;
static Shader g_sh[NXDK_MAX_SHADERS + 1];

static uint32_t rd32(const uint8_t* p) { uint32_t v; memcpy(&v, p, 4); return v; }
static uint64_t refuse(const char* who, const char* fmt, ...) { /* records "<who>: <text>" through nxdk_err; returns 0 */
    char b[120]; va_list ap; va_start(ap, fmt); vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    nxdk_err("%s: %s", who, b);
    return 0;
}

/* K2: 0 = ok, else the reason. *ctl_sz, *code_sz are the section sizes. */
static const char* check_dksh(const uint8_t* d, uint64_t n, uint32_t stage, uint32_t* ctl, uint32_t* code) {
    if (n < HDR_SZ) return "image smaller than the 24-byte DKSH header";
    if (rd32(d) != 0x48534B44u) return "bad magic (not DKSH)";
    uint32_t hsz = rd32(d + 4), cs = rd32(d + 8), ks = rd32(d + 12), po = rd32(d + 16), np = rd32(d + 20);
    if (hsz != HDR_SZ) return "header_sz is not 24";
    if (!cs || (cs & (DK_SHADER_CODE_ALIGNMENT - 1))) return "control_sz is zero or not a multiple of 256";
    if (!ks || (ks & (DK_SHADER_CODE_ALIGNMENT - 1))) return "code_sz is zero or not a multiple of 256";
    if (ks > MAX_CODE) return "code_sz above 1 MiB";
    if ((uint64_t)cs + ks != n) return "control_sz + code_sz is not the image size";
    if (np != 1) return "the image does not hold exactly one program";
    if (po < HDR_SZ || (po & 3) || (uint64_t)po + PROG_SZ > cs) return "program header outside the control section";
    const uint8_t* p = d + po;
    if (rd32(p) != stage) return "program type is not the requested stage";   /* DkshProgramType_Vertex 0 / Fragment 1 = NxStage */
    uint32_t entry = rd32(p + 4), cb = rd32(p + 12), cbsz = rd32(p + 16);
    if (entry >= ks || (entry & 7)) return "entrypoint outside the code section or not instruction aligned";
    if ((uint64_t)cb + cbsz > ks) return "constant buffer 1 outside the code section";
    *ctl = cs; *code = ks;
    return NULL;
}

/* d: a private copy of the image (K1). Registers and returns the id, or 0 with the error (nothing registered). */
static uint64_t make(const char* who, const uint8_t* d, uint64_t n, uint32_t stage, uint32_t drow) {
    uint32_t cs, ks; const char* why = check_dksh(d, n, stage, &cs, &ks);
    if (why) return refuse(who, "bad DKSH: %s", why);
    uint32_t id = 1;
    while (id <= NXDK_MAX_SHADERS && g_sh[id].used) id++;
    if (id > NXDK_MAX_SHADERS) return refuse(who, "the shader table is full");
    DkMemBlockMaker mk; dkMemBlockMakerDefaults(&mk, nxdk.dev, (ks + DK_SHADER_CODE_UNUSABLE_SIZE + DK_MEMBLOCK_ALIGNMENT - 1) & ~(uint32_t)(DK_MEMBLOCK_ALIGNMENT - 1));
    mk.flags = DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached | DkMemBlockFlags_Code;
    DkMemBlock blk = dkMemBlockCreate(&mk);
    if (!blk) return refuse(who, "deko3d refused a code block for %u bytes of code", ks);
    memcpy(dkMemBlockGetCpuAddr(blk), d + cs, ks);
    Shader* s = &g_sh[id];
    DkShaderMaker sm; dkShaderMakerDefaults(&sm, blk, 0);
    sm.control = d; sm.programId = 0;
    dkShaderInitialize(&s->sh, &sm);
    if (!dkShaderIsValid(&s->sh) || dkShaderGetStage(&s->sh) != (stage ? DkStage_Fragment : DkStage_Vertex)) {
        dkMemBlockDestroy(blk); memset(s, 0, sizeof *s);
        return refuse(who, "dkShaderInitialize did not produce a valid shader of the stage");
    }
    s->blk = blk; s->drow = drow; s->stage = stage; s->refs = 1; s->used = 1;
    return id;
}

static uint8_t* copy_in(uint64_t a, uint64_t n) {
    const void* p = nxdk_guest_ptr(a, n);
    uint8_t* c = p ? (uint8_t*)malloc(n) : NULL;
    if (c) memcpy(c, p, n);
    return c;
}

/* K6: the pack of NXSP v2 (dk.h) is read and validated once, at the first use of a pack that is installed. state 0 = not read yet, 1 = valid, 2 = invalid (why = the reason, kept).
 * A pack that is not installed (NULL) is not cached: the embedder may install it later, and every call then reports the absence. */
static struct { int state; const uint8_t* pk; uint64_t size; uint32_t gcount, gidx, dcount, didx; const char* why; } g_pack;
enum { GROW = 52, DROW = 64, V2_HDR = 48 };

static const char* pack_validate(const uint8_t* pk, uint64_t psz, uint32_t* gcount, uint32_t* gidx, uint32_t* dcount, uint32_t* didx) {
    *gcount = *gidx = *dcount = *didx = 0;
    if (psz < IDX_OFF || memcmp(pk, "NXSP", 4)) return "the pack is not an NXSP pack";
    if (rd32(pk + 4) != 2) return "the NXSP version is not 2 (v1 has no G or D table)";
    if (psz < V2_HDR) return "an NXSP v2 pack is shorter than its 48-byte header";
    uint32_t gc = rd32(pk + 32), gi = rd32(pk + 36), dc = rd32(pk + 40), di = rd32(pk + 44);
    if (rd32(pk + 12) < V2_HDR) return "the v1 index of an NXSP v2 pack starts inside the 48-byte header";
    if (gi < V2_HDR || (uint64_t)gi + (uint64_t)GROW * gc > psz) return "the G table is inside the header or outside the pack";
    if (di < V2_HDR || (uint64_t)di + (uint64_t)DROW * dc > psz) return "the D table is inside the header or outside the pack";
    for (uint32_t i = 0; i < gc; i++) {
        const uint8_t* r = pk + gi + (uint64_t)i * GROW;   /* key[32] u32 stage, glsl_off, glsl_size, dksh_off, dksh_size */
        if (rd32(r + 32) > NxStage_Fragment) return "a G table row has a stage that is neither vertex nor fragment";
        if (rd32(r + 36) < V2_HDR || (uint64_t)rd32(r + 36) + rd32(r + 40) > psz) return "a G table row's GLSL is inside the header or outside the pack";
        if (rd32(r + 44) < V2_HDR || !rd32(r + 48) || rd32(r + 48) > MAX_DKSH || (uint64_t)rd32(r + 44) + rd32(r + 48) > psz) return "a G table row's DKSH is empty, above 4 MiB or outside the pack";
        if (i && memcmp(r - GROW, r, 32) >= 0) return "the G table keys are not strictly ascending";
    }
    for (uint32_t i = 0; i < dc; i++) {
        const uint8_t* r = pk + di + (uint64_t)i * DROW;   /* key[32] u32 stage, variant, n_link, link_off, bc_off, bc_size, dksh_off, dksh_size */
        uint32_t st = rd32(r + 32), var = rd32(r + 36), nl = rd32(r + 40), lo = rd32(r + 44), bo = rd32(r + 48), bs = rd32(r + 52), dof = rd32(r + 56), ds = rd32(r + 60);
        if (st > 1 || var > 1 || (st == 1 && var)) return "a D table row has a stage above 1, a variant above 1 or a variant on a pixel shader";
        if (nl > MAX_L) return "a D table row has more than 31 link pairs";
        if (nl && (lo < V2_HDR || (uint64_t)lo + nl > psz)) return "a D table row's link set is inside the header or outside the pack";
        for (uint32_t k = 0; k < nl; k++) {
            uint8_t b = pk[lo + k];
            if (b / 16 > 13 || (k && b <= pk[lo + k - 1])) return "a D table row's link set is not ascending, unique pairs of usage <= 13";
        }
        if (bo < V2_HDR || !bs || (bs & 3) || bs > MAX_CODE || (uint64_t)bo + bs > psz) return "a D table row's bytecode is inside the header, empty, not a multiple of 4, above 1 MiB or outside the pack";
        if (dof < V2_HDR || !ds || ds > MAX_DKSH || (uint64_t)dof + ds > psz) return "a D table row's DKSH is empty, above 4 MiB or outside the pack";
        if (i && memcmp(r - DROW, r, 32) >= 0) return "the D table keys are not strictly ascending";
    }
    *gcount = gc; *gidx = gi; *dcount = dc; *didx = di;
    return NULL;
}

/* 1 = a valid pack is in g_pack; 0 = an error was recorded through nxdk_err (the absence, or the invalid pack with its kept reason) */
static int pack_open(const char* who) {
    if (!g_pack.state) {
        uint64_t psz = 0;
        const uint8_t* pk = (const uint8_t*)nxdk_shader_pack(&psz);
        if (!pk) { nxdk_err("%s: no shader pack is installed", who); return 0; }
        g_pack.pk = pk; g_pack.size = psz;
        g_pack.why = pack_validate(pk, psz, &g_pack.gcount, &g_pack.gidx, &g_pack.dcount, &g_pack.didx);
        g_pack.state = g_pack.why ? 2 : 1;
    }
    if (g_pack.state == 2) { nxdk_err("%s: the shader pack is invalid: %s", who, g_pack.why); return 0; }
    return 1;
}

uint64_t nxdk_shader_create(uint64_t a) {
    static const char W[] = "shader_create";
    const NxShaderDesc* dp = (const NxShaderDesc*)nxdk_guest_ptr(a, sizeof *dp);
    if (!dp) return refuse(W, "descriptor %llx is not readable", (unsigned long long)a);
    const NxShaderDesc d = *dp;
    if (d.stage > NxStage_Fragment) return refuse(W, "stage %u is neither vertex (0) nor fragment (1)", d.stage);
    if (!pack_open(W)) return 0;
    /* The key is only an index into the G table: the row is accepted only if its GLSL bytes equal the descriptor's (below), and the row's DKSH was built from exactly those bytes.
     * A wrong or colliding key can therefore only miss (or hit a row whose GLSL differs, which is refused); it can never select a DKSH that does not belong to this program. */
    uint32_t lo = 0, hi = g_pack.gcount;
    const uint8_t* row = NULL;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        const uint8_t* r = g_pack.pk + g_pack.gidx + (uint64_t)mid * GROW;
        int c = memcmp(d.key, r, 32);
        if (!c) { row = r; break; }
        if (c < 0) hi = mid; else lo = mid + 1;
    }
    if (!row) return refuse(W, "the key is not in the shader pack (stage %u, GLSL size %u)", d.stage, d.glsl_size);
    if (rd32(row + 32) != d.stage) return refuse(W, "the pack row of the key has stage %u, the descriptor has %u", rd32(row + 32), d.stage);
    if (rd32(row + 40) != d.glsl_size) return refuse(W, "the pack row's GLSL size %u differs from the descriptor's %u", rd32(row + 40), d.glsl_size);
    uint8_t* glsl = d.glsl_size ? copy_in(d.glsl, d.glsl_size) : NULL;   /* K1: copied before it is looked at */
    if (d.glsl_size && !glsl) return refuse(W, "the GLSL %llx is not readable", (unsigned long long)d.glsl);
    int same = !d.glsl_size || !memcmp(g_pack.pk + rd32(row + 36), glsl, d.glsl_size);
    free(glsl);
    if (!same) return refuse(W, "the pack row's GLSL differs from the descriptor's");
    uint32_t dsz = rd32(row + 48);
    uint8_t* img = (uint8_t*)malloc(dsz);
    if (!img) return refuse(W, "out of memory");
    memcpy(img, g_pack.pk + rd32(row + 44), dsz);
    uint64_t id = make(W, img, dsz, d.stage, 0);
    free(img);
    return id;
}

uint64_t nxdk_shader_create_d3d(uint64_t a) {
    static const char W[] = "shader_create_d3d";
    const NxShaderD3D* dp = (const NxShaderD3D*)nxdk_guest_ptr(a, sizeof *dp);
    if (!dp) return refuse(W, "descriptor unreadable");
    const NxShaderD3D d = *dp;
    if (d.stage > 1) return refuse(W, "stage is neither vertex (0) nor pixel (1)");
    if (d.variant > 1 || (d.stage == 1 && d.variant)) return refuse(W, "variant: bit 0 is the vertex stage's, all other bits are 0");
    if (d.pad[0] || d.pad[1] || d.pad2) return refuse(W, "reserved fields are not 0");
    if (!d.size || (d.size & 3) || d.size > MAX_CODE) return refuse(W, "bytecode size is 0, not a multiple of 4 or above 1 MiB");
    if (d.n_link_pairs > MAX_L) return refuse(W, "more than 31 link pairs");
    if (!pack_open(W)) return 0;
    uint8_t* bc = copy_in(d.bytecode, d.size);
    uint8_t* lp = d.n_link_pairs ? copy_in(d.link_pairs, d.n_link_pairs) : NULL;
    uint64_t id = 0;
    const char* bad = NULL;
    if (!bc || (d.n_link_pairs && !lp)) bad = "bytecode or link set unreadable";
    for (uint32_t i = 0; !bad && i < d.n_link_pairs; i++)
        if (lp[i] / 16 > 13 || (i && lp[i] <= lp[i - 1])) bad = "link set is not ascending, unique pairs of usage <= 13";
    if (bad) { refuse(W, "%s", bad); goto out; }
    /* The key is only an index into the D table: the row is accepted only if stage, variant, link set, bytecode size and bytecode bytes equal the descriptor's, and the row's DKSH was
     * built from exactly that bytecode and link set. A wrong or colliding key can only miss or be refused. */
    uint32_t lo = 0, hi = g_pack.dcount;
    const uint8_t* row = NULL; uint32_t ri = 0;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        const uint8_t* r = g_pack.pk + g_pack.didx + (uint64_t)mid * DROW;
        int c = memcmp(d.key, r, 32);
        if (!c) { row = r; ri = mid; break; }
        if (c < 0) hi = mid; else lo = mid + 1;
    }
    if (!row) { refuse(W, "the key is not in the shader pack (stage %u, variant %u, %u link pairs)", d.stage, d.variant, d.n_link_pairs); goto out; }
    if (rd32(row + 32) != d.stage || rd32(row + 36) != d.variant || rd32(row + 40) != d.n_link_pairs) { refuse(W, "the pack row's stage, variant or link-pair count differs from the descriptor's"); goto out; }
    if (rd32(row + 52) != d.size) { refuse(W, "the pack row's bytecode size %u differs from the descriptor's %u", rd32(row + 52), d.size); goto out; }
    if ((d.n_link_pairs && memcmp(g_pack.pk + rd32(row + 44), lp, d.n_link_pairs)) || memcmp(g_pack.pk + rd32(row + 48), bc, d.size)) { refuse(W, "the pack row's link set or bytecode differs from the descriptor's"); goto out; }
    for (uint32_t i = 1; !id && i <= NXDK_MAX_SHADERS; i++)
        if (g_sh[i].used && g_sh[i].drow == ri + 1) { g_sh[i].refs++; id = i; }
    if (!id) {
        uint32_t dsz = rd32(row + 60);
        uint8_t* img = (uint8_t*)malloc(dsz);
        if (!img) refuse(W, "out of memory");
        else { memcpy(img, g_pack.pk + rd32(row + 56), dsz); id = make(W, img, dsz, d.stage, ri + 1); free(img); }
    }
out:
    free(bc); free(lp);
    return id;
}

uint64_t nxdk_shader_destroy(uint64_t id) {
    if (!id || id > NXDK_MAX_SHADERS || !g_sh[id].used) { nxdk_err("shader_destroy: %llu is not a live shader", (unsigned long long)id); return NXGPU_BAD_CALL; }
    if (--g_sh[id].refs) return 0;   /* made by SHADER_CREATE_D3D: one create taken back, others still hold the id */
    dkMemBlockDestroy(g_sh[id].blk);
    memset(&g_sh[id], 0, sizeof g_sh[id]);
    return 0;
}

const DkShader* nxdk_shader(uint32_t id) { return id && id <= NXDK_MAX_SHADERS && g_sh[id].used ? &g_sh[id].sh : NULL; }

void nxdk_shader_release_all(void) {
    for (uint32_t id = 1; id <= NXDK_MAX_SHADERS; id++)
        if (g_sh[id].used) dkMemBlockDestroy(g_sh[id].blk);
    memset(g_sh, 0, sizeof g_sh);
}
