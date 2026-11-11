/* Part i: NxOp_BeginPass4. MRT pass writing 4 distinct colours into 4 targets (2D, a cube face of a mip level, a mip
 * level, a 3D slice) + a depth target and a rect Clear of all colour targets; a depth-only pass on a mip level; a
 * multisampled pass; passes that violate the contract are BAD_CALL before anything runs. */
#include "t_ref.h"

static const char k_fs_mrt[] = "#version 460\nlayout (location = 0) out vec4 o0;\nlayout (location = 1) out vec4 o1;\n"
    "layout (location = 2) out vec4 o2;\nlayout (location = 3) out vec4 o3;\n"
    "void main() { o0 = vec4(1, 0, 0, 1); o1 = vec4(0, 1, 0, 1); o2 = vec4(0, 0, 1, 1); o3 = vec4(1, 0, 1, 0); }\n";
static const char k_fs_nothing[] = "#version 460\nvoid main() {}\n";

static void ops(Stream* s, uint32_t op, const uint32_t* w, uint32_t n) {
    s->w[s->n++] = op | n << 16;
    memcpy(s->w + s->n, w, n * 4);
    s->n += n;
}
/* BeginPass4 words: colour count, (image, sub) per colour, depth (image, sub) */
static void begin4(Stream* s, uint32_t nc, const uint32_t c[][2], uint32_t z, uint32_t zsub) {
    uint32_t w[16], n = 0;
    w[n++] = nc;
    for (uint32_t i = 0; i < nc; i++) { w[n++] = c[i][0]; w[n++] = c[i][1]; }
    w[n++] = z; w[n++] = zsub;
    ops(s, NxOp_BeginPass4, w, n);
}
static int sub_is(uint32_t img, uint32_t level, uint32_t layer, uint32_t w, uint32_t h, uint32_t d, const uint8_t* model, const char* what) {
    uint8_t* got = t_read_all(img, level, layer, w, h, d, t_fmt(NxFmt_RGBA8));
    int ok = got && !memcmp(got, model, (size_t)w * h * d * 4);
    if (!ok && got) printf("  %s: first texels %02x%02x%02x%02x %02x%02x%02x%02x ... last %02x%02x%02x%02x\n", what, got[0],got[1],got[2],got[3],got[4],got[5],got[6],got[7], got[w*h*d*4-4],got[w*h*d*4-3],got[w*h*d*4-2],got[w*h*d*4-1]);
    CHECKM(ok, "%s", what);
    free(got);
    return ok;
}
static void paint(uint8_t* m, int w, int h, const uint8_t c[4], int clearRect) {   /* the target after the draw and the 2x2 clear */
    static const uint8_t clr[4] = {0x11, 0x22, 0x33, 0x44};
    for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) memcpy(m + (y * w + x) * 4, clearRect && x < 2 && y < 2 ? clr : c, 4);
}

static void mrt(void) {
    uint32_t A = t_image(NxImgType_2D, NxFmt_RGBA8, 4, 4, 1, 1, 1), B = t_image(NxImgType_Cube, NxFmt_RGBA8, 8, 8, 1, 2, 1);
    uint32_t C = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 2, 1), D = t_image(NxImgType_3D, NxFmt_RGBA8, 4, 4, 4, 1, 1);
    uint32_t Z = t_image(NxImgType_2D, NxFmt_Z24S8, 4, 4, 1, 1, 1);
    CHECK(A && B && C && D && Z);
    t_zero(A, NxImgType_2D, NxFmt_RGBA8, 4, 4, 1, 1); t_zero(B, NxImgType_Cube, NxFmt_RGBA8, 8, 8, 1, 2);
    t_zero(C, NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 2); t_zero(D, NxImgType_3D, NxFmt_RGBA8, 4, 4, 4, 1);
    t_zero(Z, NxImgType_2D, NxFmt_Z24S8, 4, 4, 1, 1);
    uint32_t vs = t_shader(NxStage_Vertex, T_VS_FULL), fs = t_shader(NxStage_Fragment, k_fs_mrt);
    CHECK(vs && fs);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    const uint32_t tg[4][2] = {{A, 0}, {B, 1 | 2u << 16}, {C, 1}, {D, 2u << 16}};
    t_state(s, 1, vs, fs, 0, T_W0_ALL, T_W1_DEPTH_ALWAYS, 0, 0);
    begin4(s, 4, tg, Z, 0);
    t_op(s, NxOp_BindState, 1, 1);
    t_op(s, NxOp_Draw, 5, 0, 3, 1, 0, 0);
    t_op(s, NxOp_Clear, 8, NxClear_Color, 0x44332211u, 0, 0, 0, 0, 2, 2);   /* every colour target of the pass */
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    static const uint8_t col[4][4] = {{255, 0, 0, 255}, {0, 255, 0, 255}, {0, 0, 255, 255}, {255, 0, 255, 0}};
    uint8_t m[4 * 4 * 4], zero[4 * 4 * 4] = {0};
    paint(m, 4, 4, col[0], 1); sub_is(A, 0, 0, 4, 4, 1, m, "MRT target 0: 2D image");
    paint(m, 4, 4, col[1], 1); sub_is(B, 1, 2, 4, 4, 1, m, "MRT target 1: cube face 2 of level 1");
    paint(m, 4, 4, col[2], 1); sub_is(C, 1, 0, 4, 4, 1, m, "MRT target 2: level 1 of a 2D image");
    uint8_t vol[4 * 4 * 4 * 4];
    memset(vol, 0, sizeof vol);
    paint(m, 4, 4, col[3], 1);
    memcpy(vol + 2 * 64, m, 64);
    uint8_t* gv = t_read_all(D, 0, 0, 4, 4, 4, t_fmt(NxFmt_RGBA8));
    CHECKM(gv && !memcmp(gv, vol, sizeof vol), "MRT target 3: slice 2 of a 3D image, the other slices untouched");
    free(gv);
    static const uint8_t zero8[8 * 8 * 4] = {0};
    for (int f = 0; f < 6; f++) sub_is(B, 0, f, 8, 8, 1, zero8, "cube level 0 untouched");
    sub_is(B, 1, 0, 4, 4, 1, zero, "cube face 0 level 1 untouched");
    sub_is(B, 1, 5, 4, 4, 1, zero, "cube face 5 level 1 untouched");
    uint8_t* c0 = t_read_all(C, 0, 0, 8, 8, 1, t_fmt(NxFmt_RGBA8));
    uint8_t z8[256]; memset(z8, 0, sizeof z8);
    CHECKM(c0 && !memcmp(c0, z8, sizeof z8), "level 0 of the mip target untouched");
    free(c0);
    uint8_t* dz = t_read_all(Z, 0, 0, 4, 4, 1, t_fmt(NxFmt_Z24S8));
    int ok = dz != NULL;
    for (int i = 0; ok && i < 16; i++) { uint32_t v; memcpy(&v, dz + 4 * i, 4); ok = (v >> 8) == 0x7FFFFF || (v >> 8) == 0x800000; }
    CHECKM(ok, "MRT pass depth target written (0.5)");
    free(dz); free(s);
}

static void depth_only(void) {
    uint32_t Z = t_image(NxImgType_2D, NxFmt_Z32F, 16, 16, 1, 2, 1);
    float z75[256];
    for (int i = 0; i < 256; i++) z75[i] = 0.75f;
    uint32_t b0[6] = {0, 0, 0, 16, 16, 1}, b1[6] = {0, 0, 0, 8, 8, 1};
    CHECK(t_update(Z, 0, 0, b0, z75, sizeof z75, 64, 0) == 0 && t_update(Z, 1, 0, b1, z75, 256, 32, 0) == 0);
    uint32_t vs = t_shader(NxStage_Vertex, T_VS_FULL), fs = t_shader(NxStage_Fragment, k_fs_nothing);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_state(s, 1, vs, fs, 0, T_W0_ALL, T_W1_DEPTH_ALWAYS, 0, 0);
    begin4(s, 0, NULL, Z, 1);   /* colorCount 0: depth only, on mip level 1 */
    t_op(s, NxOp_BindState, 1, 1);
    t_op(s, NxOp_Draw, 5, 0, 3, 1, 0, 0);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    float got1[64], got0[256];
    CHECK(t_read(Z, 1, 0, b1, got1, sizeof got1, 32) == 0 && t_read(Z, 0, 0, b0, got0, sizeof got0, 64) == 0);
    int ok1 = 1, ok0 = 1;
    for (int i = 0; i < 64; i++) ok1 &= got1[i] == 0.5f;
    for (int i = 0; i < 256; i++) ok0 &= got0[i] == 0.75f;
    CHECKM(ok1, "depth-only pass wrote level 1");
    CHECKM(ok0, "depth-only pass left level 0");
    free(s);
}

static void msaa(void) {
    uint32_t col = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 1, 4), dep = t_image(NxImgType_2D, NxFmt_Z24S8, 8, 8, 1, 1, 4);
    uint32_t one = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 1, 1);
    CHECK(col && dep && one);
    uint32_t vs = t_shader(NxStage_Vertex, T_VS_FULL), fs = t_shader(NxStage_Fragment, T_FS_WHITE);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    const uint32_t tg[1][2] = {{col, 0}};
    float z = 0.25f; uint32_t zb; memcpy(&zb, &z, 4);
    begin4(s, 1, tg, dep, 0);
    t_op(s, NxOp_Clear, 8, 7, 0x80302010u, zb, 5, 0, 0, 8, 8);
    t_op(s, NxOp_Clear, 8, NxClear_Color, 0xFF0000FFu, 0, 0, 4, 0, 4, 8);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    t_op(s, NxOp_Blit, 13, col, 0, 0, 0, 8, 8, one, 0, 0, 0, 8, 8, 0);
    CHECK(t_submit(s) == 0);
    uint8_t m[256];
    for (int y = 0; y < 8; y++) for (int x = 0; x < 8; x++) { static const uint8_t a[4] = {0x10, 0x20, 0x30, 0x80}, b[4] = {255, 0, 0, 255}; memcpy(m + (y * 8 + x) * 4, x < 4 ? a : b, 4); }
    sub_is(one, 0, 0, 8, 8, 1, m, "BeginPass4 on a 4x multisampled target: Clear rects, resolved");
    t_state(s, 1, vs, fs, 0, T_W0_ALL, T_W1_DEPTH_ALWAYS, 0, T_W3_ALL | 1);   /* MSAA bit on, all samples */
    begin4(s, 1, tg, dep, 0);
    t_viewport(s, 0, 0, 8, 8);   /* the default viewport starts at 0.5: its clip rect would cut the samples left of it */
    t_op(s, NxOp_BindState, 1, 1);
    t_op(s, NxOp_Draw, 5, 0, 3, 1, 0, 0);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    t_op(s, NxOp_Blit, 13, col, 0, 0, 0, 8, 8, one, 0, 0, 0, 8, 8, 0);
    CHECK(t_submit(s) == 0);
    memset(m, 255, sizeof m);
    sub_is(one, 0, 0, 8, 8, 1, m, "multisampled pass: a full-screen draw is white in every sample");
    free(s);
}

static void violations(void) {
    uint32_t T = t_image(NxImgType_2D, NxFmt_RGBA8, 4, 4, 1, 1, 1), big = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 1, 1);
    uint32_t ms = t_image(NxImgType_2D, NxFmt_RGBA8, 4, 4, 1, 1, 4), cube = t_image(NxImgType_Cube, NxFmt_RGBA8, 4, 4, 1, 1, 1);
    uint32_t Z = t_image(NxImgType_2D, NxFmt_Z32F, 4, 4, 1, 1, 1);
    t_zero(T, NxImgType_2D, NxFmt_RGBA8, 4, 4, 1, 1);
    typedef struct { const char* name; uint32_t n, w[14]; } Bad;
    const Bad bad[] = {
        {"sizes differ", 7, {2, T, 0, big, 0, 0, 0}},
        {"sample counts differ", 7, {2, T, 0, ms, 0, 0, 0}},
        {"cube face 6", 5, {1, cube, 6u << 16, 0, 0}},
        {"level 5 of a 1-level image", 5, {1, T, 5, 0, 0}},
        {"5 colour targets", 13, {5, T, 0, T, 0, T, 0, T, 0, T, 0, 0, 0}},
        {"no target at all", 3, {0, 0, 0}},
        {"colour target is a depth image", 5, {1, Z, 0, 0, 0}},
        {"depth target is a colour image", 5, {0, T, 0}},
        {"too few words", 2, {1, T}},
        {"colour image does not exist", 5, {1, 999, 0, 0, 0}},
    };
    for (unsigned i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        Stream* s = (Stream*)calloc(1, sizeof *s);
        const uint32_t good[1][2] = {{T, 0}};
        begin4(s, 1, good, 0, 0);
        t_op(s, NxOp_Clear, 8, NxClear_Color, 0xFF0000FFu, 0, 0, 0, 0, 4, 4);
        ops(s, NxOp_BeginPass4, bad[i].w, bad[i].n);
        CHECKM(t_submit(s) == NXGPU_BAD_CALL, "bad BeginPass4 (%s) is BAD_CALL", bad[i].name);
        uint8_t zero[64] = {0};
        sub_is(T, 0, 0, 4, 4, 1, zero, "nothing before a bad BeginPass4 executed");
        free(s);
    }
}

void part_i(void) { mrt(); depth_only(); msaa(); violations(); }
