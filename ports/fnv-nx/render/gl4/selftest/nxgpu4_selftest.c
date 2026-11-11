/* fnv-nx L6: nxgpu v4.1 first-frame self test. See nxgpu4_selftest.h. Scene: one textured triangle (4x4 point sampled
 * texture, 2 x S = H/24 px grid) on a cleared backbuffer. Rule that makes the checksum backend independent: a pixel
 * whose centre is >= 1 px inside all three edge lines is exactly the texel the model names, a pixel whose centre is
 * >= 1 px outside one edge line is exactly the clear colour, every other (edge) pixel is excluded from the checksum.
 * The expected checksum is computed analytically (tests/l6/selftest), never from a render. */
#include "nxgpu4_selftest.h"
#include "../nxgpu4.h"
#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

typedef struct { NxGpuInfo info; NxMemBlockDesc mem; NxImageDesc4 img; NxSamplerState smp; NxShaderDesc sh; NxSubmit sub;
                 NxImageRead rd; NxPresent pr; NxVtxLayout4 lay; } Ctl;
enum { A_CTL = 0x10000, A_STREAM = 0x12000, A_SH0 = 0x20000, A_SHMAX = 0x20000, A_TOTAL = 0x100000, VB_OFF = 4096, MEM_SIZE = 0x10000 };
enum { CLEAR_S2 = 0xFF804020u, CLEAR_SCENE = 0xFF804020u, CLEAR_OTHER = 0xFF10F0C0u };   /* RGBA8, R in the low byte */
typedef struct { const NxSelftestHost* h; uint8_t* a; uint64_t A, rbA, fnv; uint8_t* rb; uint32_t* w; Ctl* c;
                 uint32_t n, frame, W, H, mem, vs, ps, tex, smp, rt; int64_t rc; char why[200]; } St;

static uint64_t call(St* s, uint32_t fn, uint64_t a, uint32_t n) { return s->h->ext(fn, &a, n); }
static int bad(uint64_t r) { return r == 0 || r == NXGPU_BAD_CALL; }
static int fail(St* s, int64_t rc, const char* fmt, ...) {
    va_list ap; va_start(ap, fmt);
    if (!s->why[0]) { vsnprintf(s->why, sizeof s->why, fmt, ap); s->rc = rc; }
    va_end(ap);
    return 1;
}
#define CA(m) (s->A + A_CTL + offsetof(Ctl, m))
static uint32_t fb(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
static void op(St* s, uint32_t opc, int argc, ...) {
    va_list ap; va_start(ap, argc);
    s->w[s->n++] = opc | (uint32_t)argc << 16;
    for (int i = 0; i < argc; i++) s->w[s->n++] = va_arg(ap, uint32_t);
    va_end(ap);
}
static int submit(St* s) {
    s->w[s->n++] = NxOp_End;
    s->c->sub = (NxSubmit){s->A + A_STREAM, s->n, s->frame++, 0, 0, 0};
    s->n = 0;
    uint64_t r = call(s, NXGPU_FN_SUBMIT, CA(sub), 1);
    return r ? fail(s, (int64_t)r, "SUBMIT rc=%llx", (unsigned long long)r) : 0;
}

/* ---- the scene model (S = H / 24, triangle (x0,y0) (x0+8S,y0) (x0,y0+8S), uv (0,0) (2,0) (0,2), 4x4 texture) ---- */
static void texel(uint32_t x, uint32_t y, uint8_t o[4]) { o[0] = (uint8_t)(40 + 50 * x); o[1] = (uint8_t)(40 + 50 * y); o[2] = (uint8_t)(255 - 16 * (4 * x + y)); o[3] = 255; }
static void clearpx(uint32_t c, uint8_t o[4]) { o[0] = (uint8_t)c; o[1] = (uint8_t)(c >> 8); o[2] = (uint8_t)(c >> 16); o[3] = (uint8_t)(c >> 24); }
/* 1 inside (o = texel), 0 outside (o = clear colour), -1 edge pixel (excluded) */
static int model(const St* s, uint32_t px, uint32_t py, uint8_t o[4]) {
    double S = s->H / 24, x0 = 2 * S, y0 = S, cx = px + 0.5, cy = py + 0.5;
    double m = cy - y0, d2 = cx - x0, d3 = (x0 + y0 + 8 * S - cx - cy) / 1.4142135623730951;
    if (d2 < m) m = d2;
    if (d3 < m) m = d3;
    if (m <= -1) { clearpx(CLEAR_SCENE, o); return 0; }
    if (m < 1) return -1;
    int tx = (int)((cx - x0) / S), ty = (int)((cy - y0) / S);
    texel((uint32_t)(tx > 3 ? 3 : tx), (uint32_t)(ty > 3 ? 3 : ty), o);
    return 1;
}
static int check_scene(St* s, const char* tag) {
    const uint8_t* p = s->rb; uint8_t e[4]; uint64_t f = 0xcbf29ce484222325ull; uint32_t ex = 0, S = s->H / 24;
    for (uint32_t j = 0; j < 16; j++) {   /* exact probes written without the model: 16 texel centres, 5 outside pixels */
        uint32_t px = 2 * S + (j & 3) * S + S / 2, py = S + (j >> 2) * S + S / 2; texel(j & 3, j >> 2, e);
        if (memcmp(p + ((size_t)py * s->W + px) * 4, e, 4)) return fail(s, 0, "%s texel probe %u at (%u,%u) differs", tag, j, px, py);
    }
    uint32_t q[5][2] = {{0, 0}, {s->W - 1, 0}, {0, s->H - 1}, {s->W - 1, s->H - 1}, {2 * S + 6 * S, S + 4 * S}};
    for (int j = 0; j < 5; j++) {
        clearpx(CLEAR_SCENE, e);
        if (memcmp(p + ((size_t)q[j][1] * s->W + q[j][0]) * 4, e, 4)) return fail(s, 0, "%s outside probe %d at (%u,%u) differs", tag, j, q[j][0], q[j][1]);
    }
    for (uint32_t y = 0; y < s->H; y++)
        for (uint32_t x = 0; x < s->W; x++) {
            const uint8_t* g = p + ((size_t)y * s->W + x) * 4;
            if (model(s, x, y, e) < 0) { ex++; continue; }
            if (memcmp(g, e, 4)) return fail(s, 0, "%s pixel (%u,%u) is %02x%02x%02x%02x, model %02x%02x%02x%02x", tag, x, y, g[0], g[1], g[2], g[3], e[0], e[1], e[2], e[3]);
            for (int k = 0; k < 4; k++) f = (f ^ g[k]) * 0x100000001b3ull;
        }
    s->h->log(s->h->ctx, "%s fnv=%016llx excluded=%u", tag, (unsigned long long)f, ex);
    if (f != s->fnv) return fail(s, 0, "%s checksum %016llx, expected %016llx", tag, (unsigned long long)f, (unsigned long long)s->fnv);
    return 0;
}
static int read0(St* s) {
    s->c->rd = (NxImageRead){{0, 0, 0}, 0, 0, 0, s->W, s->H, 1, s->rbA, s->W * s->H * 4, s->W * 4};
    uint64_t r = call(s, NXGPU_FN_IMAGE_READ, CA(rd), 1);
    return r ? fail(s, (int64_t)r, "IMAGE_READ image 0 rc=%llx", (unsigned long long)r) : 0;
}
/* one pass on `target` cleared to `clear`; draw = the textured triangle */
static int pass(St* s, uint32_t target, uint32_t clear, int draw) {
    float S = (float)(s->H / 24), W = (float)s->W, H = (float)s->H, x0 = 2 * S, y0 = S;
    if (draw) {
        float v[12] = {2 * x0 / W - 1, 1 - 2 * y0 / H, 0, 0, 2 * (x0 + 8 * S) / W - 1, 1 - 2 * y0 / H, 2, 0, 2 * x0 / W - 1, 1 - 2 * (y0 + 8 * S) / H, 0, 2};
        memcpy(s->a + VB_OFF, v, sizeof v);
        s->c->lay = (NxVtxLayout4){2, {0}, 0, {0}, {{0, 0, NxAttr4_Float2, 0, {0}}, {8, 0, NxAttr4_Float2, 1, {0}}}};
        s->w[s->n++] = NxOp_DefineLayout4 | (uint32_t)(1 + sizeof(NxVtxLayout4) / 4) << 16;
        s->w[s->n++] = 1;
        memcpy(s->w + s->n, &s->c->lay, sizeof(NxVtxLayout4)); s->n += sizeof(NxVtxLayout4) / 4;
        op(s, NxOp_DefineState, 12, 1u, s->vs, s->ps, 1u, 0u, 0xFFFFu, 0u, 0u, 0u, 0u, 0u, 0xFFFFFFFFu);   /* w0 all write masks, w3 all samples */
    }
    op(s, NxOp_BeginPass, 8, target, 0u, 1u, clear, fb(1.0f), 0u, s->W, s->H);
    if (draw) {
        op(s, NxOp_Viewport, 6, fb(0), fb(0), fb(W), fb(H), fb(0), fb(1));   /* pixel grid (0,0)-(W,H): BeginPass leaves (0.5,0.5) */
        op(s, NxOp_BindState, 1, 1u);
        op(s, NxOp_BindVtxN, 5, 0u, s->mem, (uint32_t)VB_OFF, 48u, 16u);
        op(s, NxOp_BindTextures, 4, (uint32_t)NxStage_Fragment, 0u, 1u, NX_TEX_HANDLE(s->tex, s->smp));
        op(s, NxOp_Draw, 5, (uint32_t)NxPrim_Triangles, 3u, 1u, 0u, 0u);
    }
    op(s, NxOp_EndPass, 0);
    return submit(s);
}

/* ---- stages ---- */
static int s1(St* s) {
    const NxSelftestHost* h = s->h; Ctl* c = s->c; uint64_t r;
    r = call(s, NXGPU_FN_INFO, CA(info), 1);
    if (r || c->info.magic != NXGPU_MAGIC || c->info.version != NXGPU_VERSION4) return fail(s, (int64_t)r, "INFO rc=%llx magic=%08x version=%u", (unsigned long long)r, c->info.magic, c->info.version);
    s->W = c->info.width; s->H = c->info.height;
    h->log(h->ctx, "INFO backend=%u frames_in_flight=%u backbuffer=%ux%u", c->info.backend, c->info.frames_in_flight, s->W, s->H);
    if (!s->W || !s->H || s->H % 24 || s->W > 4096 || s->H > 4096) return fail(s, 0, "unsupported backbuffer %ux%u (H must be a multiple of 24)", s->W, s->H);
    for (uint32_t i = 0; i < h->nexpect; i++) if (h->expect[i].w == s->W && h->expect[i].h == s->H) s->fnv = h->expect[i].fnv;
    if (!s->fnv) return fail(s, 0, "no expected checksum for %ux%u", s->W, s->H);
    if (!(s->rb = h->alloc(h->ctx, s->W * s->H * 4, &s->rbA))) return fail(s, 0, "alloc of the read-back buffer failed");
    c->mem = (NxMemBlockDesc){s->A, MEM_SIZE, NxMem_Generic};
    if (bad(r = call(s, NXGPU_FN_MEM_CREATE, CA(mem), 1))) return fail(s, (int64_t)r, "MEM_CREATE rc=%llx", (unsigned long long)r);
    s->mem = (uint32_t)r;
    for (uint32_t i = 0; i < 2; i++) {
        if (!h->shader[i].data || !h->shader[i].size || h->shader[i].size > A_SHMAX) return fail(s, 0, "shader %u: no blob or larger than %u", i, (unsigned)A_SHMAX);
        memcpy(s->a + A_SH0 + i * A_SHMAX, h->shader[i].data, h->shader[i].size);
        uint64_t at = s->A + A_SH0 + i * A_SHMAX, sz = h->shader[i].size;
        c->sh = h->shader_form == NXST_SHADER_DKSH ? (NxShaderDesc){i, (uint32_t)sz, at, 0, 0, 0} : (NxShaderDesc){i, 0, 0, at, (uint32_t)sz, 0};
        if (bad(r = call(s, NXGPU_FN_SHADER_CREATE, CA(sh), 1))) return fail(s, (int64_t)r, "SHADER_CREATE stage %u rc=%llx", i, (unsigned long long)r);
        if (i) s->ps = (uint32_t)r; else s->vs = (uint32_t)r;
    }
    c->img = (NxImageDesc4){4, 4, 1, NxImgType_2D, NxFmt_RGBA8, 1, 1, 0, 0, 0, 0};
    if (bad(r = call(s, NXGPU_FN_IMAGE_CREATE4, CA(img), 1))) return fail(s, (int64_t)r, "IMAGE_CREATE4 texture rc=%llx", (unsigned long long)r);
    s->tex = (uint32_t)r;
    c->img = (NxImageDesc4){(uint16_t)s->W, (uint16_t)s->H, 1, NxImgType_2D, NxFmt_RGBA8, 1, 1, NxImg_RenderTarget, 0, 0, 0};
    if (bad(r = call(s, NXGPU_FN_IMAGE_CREATE4, CA(img), 1))) return fail(s, (int64_t)r, "IMAGE_CREATE4 target rc=%llx", (unsigned long long)r);
    s->rt = (uint32_t)r;
    c->smp = (NxSamplerState){0, 0, NxWrap_Clamp | NxWrap_Clamp << 3 | NxWrap_Clamp << 6, 0, 0, {0, 0}, 0.0f, 0};   /* point, no mips, clamp */
    if (bad(r = call(s, NXGPU_FN_SAMPLER_CREATE, CA(smp), 1))) return fail(s, (int64_t)r, "SAMPLER_CREATE rc=%llx", (unsigned long long)r);
    s->smp = (uint32_t)r;
    return 0;
}
static int s2(St* s) {
    if (pass(s, 0, CLEAR_S2, 0) || read0(s)) return 1;
    uint8_t e[4]; clearpx(CLEAR_S2, e);
    for (size_t i = 0; i < (size_t)s->W * s->H; i++)
        if (memcmp(s->rb + i * 4, e, 4)) return fail(s, 0, "clear: pixel (%u,%u) is %02x%02x%02x%02x", (unsigned)(i % s->W), (unsigned)(i / s->W), s->rb[i * 4], s->rb[i * 4 + 1], s->rb[i * 4 + 2], s->rb[i * 4 + 3]);
    return 0;
}
static int s3(St* s) {
    uint8_t* t = s->a;
    for (uint32_t y = 0; y < 4; y++) for (uint32_t x = 0; x < 4; x++) texel(x, y, t + (y * 4 + x) * 4);
    op(s, NxOp_ImageUpdate, 12, s->tex, 0u, 0u, 0u, 0u, 4u, 4u, 1u, s->mem, 0u, 64u, 16u);
    if (submit(s) || pass(s, 0, CLEAR_SCENE, 1) || read0(s)) return 1;
    return check_scene(s, "S3");
}
static int s4(St* s) {
    const NxSelftestHost* h = s->h; uint64_t r;
    if (!h->present_frames) return fail(s, 0, "present_frames is 0");
    if (pass(s, 0, CLEAR_OTHER, 0) || pass(s, s->rt, CLEAR_SCENE, 1)) return 1;   /* the swapchain differs from the scene until PRESENT writes it */
    for (uint32_t i = 0; i < h->present_frames; i++) {
        s->c->pr = (NxPresent){s->rt, 0, 0, (int32_t)s->W, (int32_t)s->H, 0, 0, (int32_t)s->W, (int32_t)s->H, 0, 0, i + 1, 0, 0, 0, 0, 0, 0};
        if ((r = call(s, NXGPU_FN_PRESENT, CA(pr), 1))) return fail(s, (int64_t)r, "PRESENT frame %u rc=%llx", i, (unsigned long long)r);
        if ((r = call(s, NXGPU_FN_WAIT_VBLANK, 0, 1))) return fail(s, (int64_t)r, "WAIT_VBLANK frame %u rc=%llx", i, (unsigned long long)r);
    }
    if (read0(s) || check_scene(s, "S4")) return 1;
    h->frame(h->ctx, s->W, s->H, s->rb);
    return 0;
}
static int s5(St* s) {
    uint64_t r = call(s, NXGPU_FN_SHUTDOWN, 0, 0);
    return r ? fail(s, (int64_t)r, "SHUTDOWN rc=%llx", (unsigned long long)r) : 0;
}

int nxgpu4_selftest(const NxSelftestHost* h) {
    static const struct { const char* name; int (*fn)(St*); } T[5] = {{"INIT", s1}, {"CLEAR", s2}, {"DRAW", s3}, {"PRESENT", s4}, {"SHUTDOWN", s5}};
    St s; int bad_stage = 0;
    memset(&s, 0, sizeof s);
    s.h = h;
    if (h->reason_cap) h->reason[0] = 0;
    s.a = h->alloc(h->ctx, A_TOTAL, &s.A);
    if (s.a) { s.c = (Ctl*)(s.a + A_CTL); s.w = (uint32_t*)(s.a + A_STREAM); }
    for (int i = 0; i < 5; i++) {
        if (i == 4 || !bad_stage) {
            int f = s.a ? T[i].fn(&s) : fail(&s, 0, "alloc of the 1 MiB arena failed");
            h->log(h->ctx, "STAGE %d %s %s rc=%lld", i + 1, T[i].name, f ? "FAIL" : "OK", f ? (long long)s.rc : 0ll);
            if (f && !bad_stage) bad_stage = i + 1;
        } else h->log(h->ctx, "STAGE %d %s NOT RUN (stage %d failed)", i + 1, T[i].name, bad_stage);
        if (!s.a) break;
    }
    if (bad_stage && h->reason_cap) snprintf(h->reason, h->reason_cap, "stage %d: %s", bad_stage, s.why);
    return bad_stage;
}
