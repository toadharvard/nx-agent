/* Part q: NXGPU_FN_SHADER_CREATE_D3D (gl4_d3dshader.c). Synthetic hand-assembled shaders (t_d3d.h from d3dsyn.py).
 *  ids of every model class, refusals (hash, size, stage, variant, reserved fields, L, descriptor, garbage) leave the backend usable and register nothing, memo + owner counts, variants / link sets are distinct ids,
 *  every (VS, PS) pair of the classes links and draws c0 through the v3 path (UBO 0 = c, UBO 2 = driver block). */
#include "t_common.h"
#include "fnvnx_shaders.h"
#include "fnvnx_sha256.h"
#include "t_d3d.h"

static const TD3d* find(const char* n) { for (uint32_t i = 0; i < sizeof k_d3d / sizeof k_d3d[0]; i++) if (!strcmp(k_d3d[i].name, n)) return &k_d3d[i]; return NULL; }
static uint64_t create(const uint32_t* w, uint32_t bytes, uint32_t stage, uint32_t variant, const uint8_t* l, uint32_t nl, uint64_t hash_xor) {
    NxShaderD3D d; memset(&d, 0, sizeof d);
    d.bytecode = (uint64_t)(uintptr_t)w; d.size = bytes; d.stage = (uint8_t)stage; d.variant = (uint8_t)variant; fnvnx_sha256(w, bytes, d.key); d.key[0] ^= (uint8_t)hash_xor;
    d.link_pairs = (uint64_t)(uintptr_t)l; d.n_link_pairs = nl;
    return t_ext(NXGPU_FN_SHADER_CREATE_D3D, 1, (uint64_t)(uintptr_t)&d);
}
static uint64_t raw(uint64_t code, uint32_t bytes) {   /* a descriptor that never hashed the bytes (they may not exist) */
    NxShaderD3D d; memset(&d, 0, sizeof d); d.bytecode = code; d.size = bytes;
    return t_ext(NXGPU_FN_SHADER_CREATE_D3D, 1, (uint64_t)(uintptr_t)&d);
}
static uint64_t make(const char* name, uint32_t variant) { const TD3d* s = find(name); return create(s->w, s->n * 4, s->stage, variant, NULL, 0, 0); }
static uint32_t live_shaders(void) { uint32_t n = 0; for (uint32_t i = 1; i <= GL4_MAX_SHADERS; i++) n += g4.sh[i].used; return n; }

static uint8_t g_px[16 * 16 * 4];
/* one draw of the big triangle with (vs, ps) into the swapchain image, c0 = k; returns 1 when the read-back worked */
static int draw_vc(uint32_t vs, uint32_t ps, const float k[4], const float* vc) {
    Stream* s = (Stream*)calloc(1, sizeof *s);
    float tri[12] = {-1, -1, 0, 1, 3, -1, 0, 1, -1, 3, 0, 1}, drv_vs[16] = {0, 0, 1, 0, 1, 0, 0, 0};   /* posfix z = 1; the diffuse default 1.0 (ps < 3.0) */
    memset(g_scratch, 0, 32768);
    memcpy(g_scratch + 8192, k, 16); memcpy(g_scratch + 12288, tri, sizeof tri); if (vc) memcpy(g_scratch, vc, 48);   /* VS c0..c2 */
    memcpy(g_scratch + 1024, drv_vs, 64);
    NxVtxLayout lay; memset(&lay, 0, sizeof lay);
    lay.count = 1; lay.stride = 16; lay.attr[0].type = NxAttr_Float32; lay.attr[0].components = 4;
    uint32_t lw[9]; memcpy(lw, &lay, sizeof lay);
    t_op(s, NxOp_DefineLayout, 10, 1, lw[0], lw[1], lw[2], lw[3], lw[4], lw[5], lw[6], lw[7], lw[8]);
    t_op(s, NxOp_DefineState, 12, 1, vs, ps, 1, 0, 0xF, 0, 0, 0, 0, 0, 0);
    t_op(s, NxOp_BeginPass, 8, 0, 0, 1, 0xFF000000u, 0, 0, 16, 16);
    t_op(s, NxOp_BindUbo, 5, 1, 0, g_mem, 0, 4096);
    t_op(s, NxOp_BindUbo, 5, 1, 1, g_mem, 0, 512);
    t_op(s, NxOp_BindUbo, 5, 1, 2, g_mem, 1024, 64);
    t_op(s, NxOp_BindUbo, 5, 2, 0, g_mem, 8192, 4096);
    t_op(s, NxOp_BindUbo, 5, 2, 1, g_mem, 0, 512);
    t_op(s, NxOp_BindUbo, 5, 2, 2, g_mem, 20480, 352);     /* PS driver block: zeros = no alpha test, no fog */
    t_op(s, NxOp_BindVtx, 3, g_mem, 12288, sizeof tri);
    t_op(s, NxOp_BindState, 1, 1);
    t_op(s, NxOp_Draw, 5, 0, 3, 1, 0, 0);
    t_op(s, NxOp_EndPass, 0);
    uint64_t r = t_submit(s); free(s);
    return r == 0 && t_readback(0, NxRb_RGBA8, 16, 16, g_px, sizeof g_px);
}
static int draw(uint32_t vs, uint32_t ps, const float k[4]) { return draw_vc(vs, ps, k, NULL); }
static int is_px(const uint8_t* p, const float k[4]) { for (int c = 0; c < 4; c++) if (abs((int)p[c] - (int)lroundf(k[c] * 255.0f)) > 1) return 0; return 1; }
static int whole(const float k[4]) { for (int i = 0; i < 256; i++) if (!is_px(g_px + 4 * i, k)) return 0; return 1; }

void part_q(void) {
    static const char* vsn[] = {"vs_1_1", "vs_2_0", "vs_2_x", "vs_3_0"}, *psn[] = {"ps_1_1", "ps_1_2", "ps_1_3", "ps_1_4", "ps_2_0", "ps_2_x", "ps_3_0"};
    uint32_t vs[4], ps[7]; int ok = 1;
    CHECK(live_shaders() == 0);
    for (int i = 0; i < 4; i++) { vs[i] = (uint32_t)make(vsn[i], 0); ok &= vs[i] >= 1 && vs[i] <= GL4_MAX_SHADERS; }
    for (int i = 0; i < 7; i++) { ps[i] = (uint32_t)make(psn[i], 0); ok &= ps[i] >= 1 && ps[i] <= GL4_MAX_SHADERS; }
    CHECK(ok); CHECK(live_shaders() == 11);
    for (int i = 0; i < 11; i++) for (int j = i + 1; j < 11; j++) { uint32_t a = i < 4 ? vs[i] : ps[i - 4], b = j < 4 ? vs[j] : ps[j - 4]; CHECK(a != b); }
    for (int i = 0; i < 4; i++) CHECK(g4.sh[vs[i]].stage == 0);
    for (int i = 0; i < 7; i++) CHECK(g4.sh[ps[i]].stage == 1);
    /* every pair of the classes draws c0 */
    const float k[4] = {0.25f, 0.5f, 0.75f, 1.0f};
    for (int i = 0; i < 4; i++) for (int j = 0; j < 7; j++) { CHECKM(draw(vs[i], ps[j], k) && whole(k), "pair %s + %s", vsn[i], psn[j]); }
    /* the memo: the same key is the same id, one owner more; DESTROY takes one back, the id lives until the last */
    CHECK(make("vs_2_0", 0) == vs[1] && make("ps_2_0", 0) == ps[4] && live_shaders() == 11);
    CHECK(t_ext(NXGPU_FN_SHADER_DESTROY, 1, (uint64_t)vs[1]) == 0 && g4.sh[vs[1]].used);
    const float k2[4] = {0.5f, 0.25f, 0.125f, 0.75f};
    CHECK(draw(vs[1], ps[4], k2) && whole(k2));
    CHECK(t_ext(NXGPU_FN_SHADER_DESTROY, 1, (uint64_t)vs[1]) == 0 && !g4.sh[vs[1]].used && live_shaders() == 10);
    CHECK(t_ext(NXGPU_FN_SHADER_DESTROY, 1, (uint64_t)vs[1]) == NXGPU_BAD_CALL);
    CHECK(make("vs_2_0", 0) == vs[1]);                                  /* the freed id is the first free one */
    /* variant bit and link set: other keys, other ids; each repeated is itself */
    uint64_t v0 = make("vs_t0", 0), v1 = make("vs_t0", 1);
    CHECK(v0 && v1 && v0 != v1 && make("vs_t0", 1) == v1 && make("vs_t0", 0) == v0);
    const uint8_t l1[2] = {0x20, 0x70}, l2[2] = {0x20, 0x71}, l3[1] = {0x20};   /* TEXCOORD0, TEXCOORD0 + POSITION1.. ascending pairs */
    const TD3d* t0 = find("vs_t0");
    uint64_t a1 = create(t0->w, t0->n * 4, 0, 0, l1, 2, 0), a2 = create(t0->w, t0->n * 4, 0, 0, l2, 2, 0), a3 = create(t0->w, t0->n * 4, 0, 0, l3, 1, 0);
    CHECKM(a1 && a2 && a3, "link sets %llu %llu %llu: %s", (unsigned long long)a1, (unsigned long long)a2, (unsigned long long)a3, nxgl4_last_error());
    CHECK(a1 != a2 && a1 != a3 && a2 != a3 && a1 != v0 && a3 != v0 && create(t0->w, t0->n * 4, 0, 0, l1, 2, 0) == a1);
    /* refusals: 0, a reason, nothing registered, the backend still works */
    uint32_t before = live_shaders(); const TD3d* p20 = find("ps_2_0"); const TD3d* v20 = find("vs_2_0");
    uint32_t garbage[16]; for (int i = 0; i < 16; i++) garbage[i] = 0x9e3779b9u * (uint32_t)(i + 1);
    uint32_t trunc[16]; memcpy(trunc, v20->w, v20->n * 4); uint32_t tn = v20->n - 1;
    NxShaderD3D d; memset(&d, 0, sizeof d); d.bytecode = (uint64_t)(uintptr_t)v20->w; d.size = v20->n * 4; fnvnx_sha256(v20->w, d.size, d.key);
    uint8_t unsorted[2] = {0x71, 0x20}, dup[2] = {0x20, 0x20}, hi[1] = {0xE0}, many[32]; for (int i = 0; i < 32; i++) many[i] = (uint8_t)i;
    struct { uint64_t r; const char* what; } bad[] = {
        {create(p20->w, p20->n * 4, 1, 0, NULL, 0, 1), "wrong hash"},
        {create(trunc, tn * 4, 0, 0, NULL, 0, 0), "truncated (no END token)"},
        {create(v20->w, v20->n * 4 - 2, 0, 0, NULL, 0, 0), "size not a multiple of 4"},
        {create(v20->w, 0, 0, 0, NULL, 0, 0), "size 0"},
        {create(v20->w, v20->n * 4, 2, 0, NULL, 0, 0), "stage 2"},
        {create(v20->w, v20->n * 4, 1, 0, NULL, 0, 0), "a vertex shader declared as pixel"},
        {create(p20->w, p20->n * 4, 0, 0, NULL, 0, 0), "a pixel shader declared as vertex"},
        {create(garbage, sizeof garbage, 0, 0, NULL, 0, 0), "garbage as vertex"},
        {create(garbage, sizeof garbage, 1, 0, NULL, 0, 0), "garbage as pixel"},
        {create(v20->w, v20->n * 4, 0, 4, NULL, 0, 0), "variant 4 (bit 2)"},
        {create(p20->w, p20->n * 4, 1, 3, NULL, 0, 0), "pixel with variant 3 (VS-complete bit)"},
        {create(v20->w, v20->n * 4, 0, 0, unsorted, 2, 0), "L descending"},
        {create(v20->w, v20->n * 4, 0, 0, dup, 2, 0), "L with a duplicate"},
        {create(v20->w, v20->n * 4, 0, 0, hi, 1, 0), "L usage 14"},
        {create(v20->w, v20->n * 4, 0, 0, many, 32, 0), "L of 32 pairs"},
        {raw((uint64_t)(uintptr_t)v20->w, 2u << 20), "size above 1 MiB"},
        {t_ext(NXGPU_FN_SHADER_CREATE_D3D, 1, (uint64_t)0), "descriptor address 0"},
        {raw(0, 8), "bytecode address 0"},
    };
    for (uint32_t i = 0; i < sizeof bad / sizeof bad[0]; i++) CHECKM(bad[i].r == 0, "%s returned %llu", bad[i].what, (unsigned long long)bad[i].r);
    d.pad[1] = 1; CHECK(t_ext(NXGPU_FN_SHADER_CREATE_D3D, 1, (uint64_t)(uintptr_t)&d) == 0); d.pad[1] = 0; d.pad2 = 1; CHECK(t_ext(NXGPU_FN_SHADER_CREATE_D3D, 1, (uint64_t)(uintptr_t)&d) == 0);
    d.pad2 = 0; d.link_pairs = 0; d.n_link_pairs = 1; CHECK(t_ext(NXGPU_FN_SHADER_CREATE_D3D, 1, (uint64_t)(uintptr_t)&d) == 0);   /* L unreadable */
    CHECK(live_shaders() == before);
    CHECK(t_ext(NXGPU_FN_SHADER_CREATE_D3D, 0, NULL) == NXGPU_BAD_CALL && t_ext(NXGPU_FN_SHADER_CREATE_D3D + 1, 1, (uint64_t)0) == NXGPU_BAD_CALL);   /* argument count 1 required; no function above */
    CHECK(create(garbage, sizeof garbage, 0, 0, NULL, 0, 0) == 0 && strstr(nxgl4_last_error(), "shader_create_d3d: translation failed") != NULL);   /* the translator's text is the reason */
    CHECK(create(v20->w, v20->n * 4, 0, 0, NULL, 0, 1) == 0 && strstr(nxgl4_last_error(), "hash") != NULL);
    /* the backend is as before: a draw with the pair made before the refusals works; ids made after them are the first free ones */
    CHECK(draw(vs[1], ps[4], k) && whole(k));
    uint64_t nx = make("vs_2_x", 1); CHECK(nx && live_shaders() == before + 1 && nx != vs[2]);
    /* the pixel stage that reads a canonical pair the vertex stage does not write, with the complete vertex variant: links, draws (value: the rule's default 0, written by the complete VS) */
    uint64_t pt = make("ps_t0", 0), vc = make("vs_2_0", 1);
    CHECK(pt && vc && vc != vs[1]);
    const float zero[4] = {0, 0, 0, 0};
    CHECK(draw((uint32_t)vc, (uint32_t)pt, k) && whole(zero));
    /* the pixel shader that reads COLOR0 (ps_2_0: the rule's default 1.0 for an unwritten diffuse) */
    uint64_t pc = make("ps_c0", 0); const float one[4] = {1, 1, 1, 1};
    CHECK(pc && draw((uint32_t)vc, (uint32_t)pc, k) && whole(one));
    /* a link set with non-canonical pairs (COLOR2 = 0xA2, COLOR3 = 0xA3): both stages translated with it agree on the varying locations; the PS reads COLOR3 = the VS's c2 */
    const TD3d *vl = find("vs_col23"), *pl = find("ps_col3"); const uint8_t lc[2] = {0xA2, 0xA3};
    uint64_t vlid = create(vl->w, vl->n * 4, 0, 0, lc, 2, 0), plid = create(pl->w, pl->n * 4, 1, 0, lc, 2, 0);
    const float vcs[12] = {0, 0, 0, 0, 0.1f, 0.2f, 0.3f, 0.4f, 0.6f, 0.5f, 0.4f, 0.3f}, want[4] = {0.6f, 0.5f, 0.4f, 0.3f};
    CHECKM(vlid && plid, "L pair: %s", nxgl4_last_error());
    CHECK(vlid && plid && draw_vc((uint32_t)vlid, (uint32_t)plid, k, vcs) && whole(want));
}
