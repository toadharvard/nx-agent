/* Part m: render state w[3] (NXRS_M_*): each bit shown to change the output where GL 4.5 expresses it (MSAA, AALINE,
 * NOCLIP, SAMPLEMASK), the one value it cannot express (LASTPIXEL with a line) refused before anything runs,
 * reserved bits refused. */
#include "t_ref.h"

#define W3_MSAA 1ull
#define W3_AALINE 2ull
#define W3_LASTPIXEL 4ull
#define W3_NOCLIP 8ull
#define W3_MASK(m) ((uint64_t)(uint32_t)(m) << 32)
/* blend on: colour SrcAlpha / InvSrcAlpha, alpha One / Zero; all write masks */
#define W0_BLEND (T_W0_ALL | 1ull | 4ull << 1 | 5ull << 6 | 1ull << 14)

static const char k_vs_tri[] = "#version 460\nvoid main() { vec2 p[3] = vec2[3](vec2(-1, -1), vec2(1, -1), vec2(-1, 1)); gl_Position = vec4(p[gl_VertexID], 0.5, 1.0); }\n";
static const char k_vs_far[] = "#version 460\nvoid main() { vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); gl_Position = vec4(p * 2.0 - 1.0, 1.5, 1.0); }\n";

static void pass1(Stream* s, uint32_t c, uint32_t z) { t_op(s, NxOp_BeginPass4, 5, 1, c, 0, z, 0); }

static uint32_t line_vs(float x0, float y0, float x1, float y1) {
    char src[400];
    snprintf(src, sizeof src, "#version 460\nvoid main() { vec2 c[2] = vec2[2](vec2(%f, %f), vec2(%f, %f)); vec2 p = c[gl_VertexID];\n"
             "gl_Position = vec4(2.0 * p.x / 16.0 - 1.0, 1.0 - 2.0 * p.y / 16.0, 0.5, 1.0); }\n", x0, y0, x1, y1);
    return t_shader(NxStage_Vertex, src);
}

static uint8_t* resolve(uint32_t ms, uint32_t one) {
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_op(s, NxOp_Blit, 13, ms, 0, 0, 0, 8, 8, one, 0, 0, 0, 8, 8, 0);
    CHECK(t_submit(s) == 0);
    free(s);
    return t_read_all(one, 0, 0, 8, 8, 1, t_fmt(NxFmt_RGBA8));
}

static void msaa_bit(void) {
    uint32_t ms = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 1, 4), one = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 1, 1);
    uint32_t vs = t_shader(NxStage_Vertex, k_vs_tri), fs = t_shader(NxStage_Fragment, T_FS_WHITE);
    int partial[2], solid[2];
    for (int on = 0; on < 2; on++) {
        Stream* s = (Stream*)calloc(1, sizeof *s);
        t_state(s, 1, vs, fs, 0, T_W0_ALL, 0, 0, T_W3_ALL | (on ? W3_MSAA : 0));
        t_op(s, NxOp_BeginPass, 8, ms, 0, NxClear_Color, 0xFF000000u, 0, 0, 8, 8);
        t_viewport(s, 0, 0, 8, 8);
        t_op(s, NxOp_BindState, 1, 1);
        t_op(s, NxOp_Draw, 5, 0, 3, 1, 0, 0);
        t_op(s, NxOp_EndPass, 0);
        CHECK(t_submit(s) == 0);
        free(s);
        uint8_t* g = resolve(ms, one);
        partial[on] = solid[on] = 0;
        for (int i = 0; g && i < 64; i++) { partial[on] += g[i * 4] > 0 && g[i * 4] < 255; solid[on] += g[i * 4] == 255; }
        free(g);
    }
    CHECKM(partial[1] >= 7, "MSAA bit set: the diagonal edge has partially covered pixels (%d)", partial[1]);
    CHECKM(partial[0] == 0 && solid[0] >= 20, "MSAA bit clear: every pixel is all or nothing (%d partial, %d solid)", partial[0], solid[0]);
    CHECKM(solid[1] >= 20, "MSAA bit set: interior pixels are solid (%d)", solid[1]);
}

static void aaline_bit(void) {
    uint32_t T = t_image(NxImgType_2D, NxFmt_RGBA8, 16, 16, 1, 1, 1), vs = line_vs(1, 1, 14, 9), fs = t_shader(NxStage_Fragment, T_FS_WHITE);
    int partial[2];
    for (int on = 0; on < 2; on++) {
        Stream* s = (Stream*)calloc(1, sizeof *s);
        t_state(s, 1, vs, fs, 0, W0_BLEND, 0, 0, on ? W3_AALINE : 0);
        t_op(s, NxOp_BeginPass, 8, T, 0, NxClear_Color, 0xFF000000u, 0, 0, 16, 16);
        t_op(s, NxOp_BindState, 1, 1);
        t_op(s, NxOp_Draw, 5, NxPrim_Lines, 2, 1, 0, 0);
        t_op(s, NxOp_EndPass, 0);
        CHECK(t_submit(s) == 0);
        free(s);
        uint8_t* g = t_read_all(T, 0, 0, 16, 16, 1, t_fmt(NxFmt_RGBA8));
        partial[on] = 0;
        int lit = 0;
        for (int i = 0; g && i < 256; i++) { partial[on] += g[i * 4] > 0 && g[i * 4] < 255; lit += g[i * 4] > 0; }
        CHECKM(lit >= 12, "the line draws (%d pixels)", lit);
        free(g);
    }
    CHECKM(partial[1] > 0 && partial[0] == 0, "AALINE bit: fractional coverage only when set (%d / %d partial pixels)", partial[1], partial[0]);
}

static void lastpixel_bit(void) {
    uint32_t T = t_image(NxImgType_2D, NxFmt_RGBA8, 16, 16, 1, 1, 1), vs = line_vs(2.0f, 4.0f, 9.0f, 4.0f), fs = t_shader(NxStage_Fragment, T_FS_WHITE);
    t_zero(T, NxImgType_2D, NxFmt_RGBA8, 16, 16, 1, 1);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_state(s, 1, vs, fs, 0, T_W0_ALL, 0, 0, W3_LASTPIXEL);
    t_state(s, 2, vs, fs, 0, T_W0_ALL, 0, 0, 0);
    CHECK(t_submit(s) == 0);   /* defining the state is fine: the bit matters when a line is drawn with it */
    uint8_t zero[1024] = {0};
    for (int kind = 0; kind < 2; kind++) {   /* kind 0: Lines, kind 1: LineStrip */
        t_op(s, NxOp_BeginPass, 8, T, 0, NxClear_Color, 0xFF0000FFu, 0, 0, 16, 16);
        t_op(s, NxOp_BindState, 1, 1);
        t_op(s, NxOp_Draw, 5, kind ? NxPrim_LineStrip : NxPrim_Lines, 2, 1, 0, 0);
        CHECKM(t_submit(s) == NXGPU_BAD_CALL, "a line draw under D3DRS_LASTPIXEL = TRUE is refused (kind %d)", kind);
        uint8_t* g = t_read_all(T, 0, 0, 16, 16, 1, t_fmt(NxFmt_RGBA8));
        CHECKM(g && !memcmp(g, zero, sizeof zero) , "nothing ran (the clear before the bad draw did not happen)");
        free(g);
    }
    t_op(s, NxOp_BeginPass, 8, T, 0, NxClear_Color, 0xFF000000u, 0, 0, 16, 16);   /* the same state, a triangle: no line, no problem */
    t_op(s, NxOp_BindState, 1, 1);
    t_op(s, NxOp_Draw, 5, NxPrim_Triangles, 3, 1, 0, 0);
    CHECK(t_submit(s) == 0);
    t_op(s, NxOp_BeginPass, 8, T, 0, NxClear_Color, 0xFF000000u, 0, 0, 16, 16);   /* bit clear: GL's diamond-exit line, last pixel not drawn */
    t_op(s, NxOp_BindState, 1, 2);
    t_op(s, NxOp_Draw, 5, NxPrim_Lines, 2, 1, 0, 0);
    CHECK(t_submit(s) == 0);
    uint8_t* g = t_read_all(T, 0, 0, 16, 16, 1, t_fmt(NxFmt_RGBA8));
    int ok = g != NULL;
    for (int y = 0; ok && y < 16; y++) for (int x = 0; x < 16; x++) {
        int want = y == 4 && x >= 2 && x <= 8;   /* the segment exits the diamonds of pixels 2..8; its end lies inside pixel 9's */
        ok &= (g[(y * 16 + x) * 4] == 255) == want;
    }
    if (!ok) { for (int y = 3; y < 6; y++) { printf("  row %d:", y); for (int x = 0; x < 16; x++) printf(" %02x", g[(y * 16 + x) * 4]); printf("\n"); } }
    CHECKM(ok, "LASTPIXEL clear: horizontal line from window (2.5, 4.5) to (9.5, 4.5) = pixels 2..8 of row 4 (diamond exit)");
    free(g); free(s);
}

static void noclip_bit(void) {
    uint32_t T = t_image(NxImgType_2D, NxFmt_RGBA8, 16, 16, 1, 1, 1), Z = t_image(NxImgType_2D, NxFmt_Z32F, 16, 16, 1, 1, 1);
    uint32_t vs = t_shader(NxStage_Vertex, k_vs_far), fs = t_shader(NxStage_Fragment, T_FS_WHITE);
    float z = 0.25f; uint32_t zb; memcpy(&zb, &z, 4);
    for (int noclip = 0; noclip < 2; noclip++) {
        Stream* s = (Stream*)calloc(1, sizeof *s);
        t_state(s, 1, vs, fs, 0, T_W0_ALL, T_W1_DEPTH_ALWAYS, 0, noclip ? W3_NOCLIP : 0);
        pass1(s, T, Z);
        t_op(s, NxOp_Clear, 8, NxClear_Color | NxClear_Depth, 0xFF000000u, zb, 0, 0, 0, 16, 16);
        t_op(s, NxOp_BindState, 1, 1);
        t_op(s, NxOp_Draw, 5, 0, 3, 1, 0, 0);
        t_op(s, NxOp_EndPass, 0);
        CHECK(t_submit(s) == 0);
        free(s);
        uint8_t* c = t_read_all(T, 0, 0, 16, 16, 1, t_fmt(NxFmt_RGBA8));
        float d[256];
        uint32_t b[6] = {0, 0, 0, 16, 16, 1};
        CHECK(t_read(Z, 0, 0, b, d, sizeof d, 64) == 0);
        int white = 1, black = 1, far = 1, kept = 1;
        for (int i = 0; c && i < 256; i++) { white &= c[i * 4] == 255; black &= c[i * 4] == 0; far &= d[i] == 1.0f; kept &= d[i] == 0.25f; }
        if (noclip) CHECKM(white && far, "NOCLIP set: a primitive beyond the far plane is drawn, depth clamped to 1.0");
        else CHECKM(black && kept, "NOCLIP clear: a primitive beyond the far plane is clipped away");
        free(c);
    }
}

static void samplemask_bit(void) {
    uint32_t ms = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 1, 4), one = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 1, 1);
    uint32_t T = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 1, 1);
    uint32_t vs = t_shader(NxStage_Vertex, T_VS_FULL), fs = t_shader(NxStage_Fragment, T_FS_WHITE);
    static const struct { uint32_t mask, lo, hi; } cs[] = {{0xF, 255, 255}, {0x3, 127, 128}, {0x0, 0, 0}, {0xFFFFFFFFu, 255, 255}, {0x5, 127, 128}, {0x8, 63, 64}};
    for (unsigned k = 0; k < sizeof cs / sizeof cs[0]; k++) {
        Stream* s = (Stream*)calloc(1, sizeof *s);
        t_state(s, 1, vs, fs, 0, T_W0_ALL, 0, 0, W3_MSAA | W3_MASK(cs[k].mask));
        t_op(s, NxOp_BeginPass, 8, ms, 0, NxClear_Color, 0xFF000000u, 0, 0, 8, 8);
        t_viewport(s, 0, 0, 8, 8);
        t_op(s, NxOp_BindState, 1, 1);
        t_op(s, NxOp_Draw, 5, 0, 3, 1, 0, 0);
        t_op(s, NxOp_EndPass, 0);
        CHECK(t_submit(s) == 0);
        free(s);
        uint8_t* g = resolve(ms, one);
        int ok = g != NULL;
        for (int i = 0; ok && i < 64; i++) ok = g[i * 4] >= cs[k].lo && g[i * 4] <= cs[k].hi;
        CHECKM(ok, "sample mask 0x%x of 4 samples: resolved %d..%d, got %d", cs[k].mask, cs[k].lo, cs[k].hi, g ? g[0] : -1);
        free(g);
    }
    for (int m = 0; m < 2; m++) {   /* single-sample target: D3D9 defines the mask for multisample targets only: no effect here */
        Stream* s = (Stream*)calloc(1, sizeof *s);
        t_state(s, 1, vs, fs, 0, T_W0_ALL, 0, 0, W3_MASK(m ? 0xFFFFFFFFu : 0xFFFFFFFEu));
        t_op(s, NxOp_BeginPass, 8, T, 0, NxClear_Color, 0xFF000000u, 0, 0, 8, 8);
        t_op(s, NxOp_BindState, 1, 1);
        t_op(s, NxOp_Draw, 5, 0, 3, 1, 0, 0);
        CHECK(t_submit(s) == 0);
        free(s);
        uint8_t* g = t_read_all(T, 0, 0, 8, 8, 1, t_fmt(NxFmt_RGBA8));
        CHECKM(g && g[0] == 255 && g[63 * 4] == 255, "single-sample target: the sample mask has no effect (mask variant %d)", m);
        free(g);
    }
}

static void reserved_bits(void) {
    uint32_t vs = t_shader(NxStage_Vertex, k_vs_tri), fs = t_shader(NxStage_Fragment, T_FS_WHITE);
    static const uint64_t bad[] = {1ull << 4, 1ull << 17, 1ull << 31, 0xFFFFFFF0ull};
    for (unsigned k = 0; k < sizeof bad / sizeof bad[0]; k++) {
        Stream* s = (Stream*)calloc(1, sizeof *s);
        t_state(s, 1, vs, fs, 0, T_W0_ALL, 0, 0, bad[k]);
        CHECKM(t_submit(s) == NXGPU_BAD_CALL, "reserved w[3] bits 0x%llx are refused", (unsigned long long)bad[k]);
        free(s);
    }
}

void part_m(void) { msaa_bit(); aaline_bit(); lastpixel_bit(); noclip_bit(); samplemask_bit(); reserved_bits(); }
