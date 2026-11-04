/* Unit tests for render/native/front/d3d9_state.c: one test per D3D9 state (S6n). Host C99. */
#include "d3d9_state.h"
#include <stdio.h>
#include <string.h>
#include <math.h>

static int g_fail, g_checks;
#define CHECK(cond) do { g_checks++; if (!(cond)) { g_fail++; printf("FAIL %s:%d: %s\n", __func__, __LINE__, #cond); } } while (0)
#define CHECKEQ(a, b) do { long long a_ = (long long)(a), b_ = (long long)(b); g_checks++; \
    if (a_ != b_) { g_fail++; printf("FAIL %s:%d: %s = %lld, want %lld\n", __func__, __LINE__, #a, a_, b_); } } while (0)

static uint32_t fbits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
static uint32_t rs[NXD3D_RS_COUNT];
static void reset(void) { nx_d3d9_default_rs(rs); }
#define B(sh, n) NXRS_GET(nx_d3d9_blend_word(rs), sh, n)
#define D(sh, n) NXRS_GET(nx_d3d9_depth_stencil_word(rs), sh, n)
#define R(sh, n) NXRS_GET(nx_d3d9_raster_word(rs), sh, n)

/* D3DRS_* */
enum { ZENABLE = 7, FILLMODE = 8, SHADEMODE = 9, ZWRITEENABLE = 14, ALPHATESTENABLE = 15, SRCBLEND = 19,
       DESTBLEND = 20, CULLMODE = 22, ZFUNC = 23, ALPHAREF = 24, ALPHAFUNC = 25, ALPHABLENDENABLE = 27,
       FOGENABLE = 28, FOGCOLOR = 34, FOGTABLEMODE = 35, FOGSTART = 36, FOGEND = 37, FOGDENSITY = 38,
       STENCILENABLE = 52, STENCILFAIL = 53, STENCILZFAIL = 54, STENCILPASS = 55, STENCILFUNC = 56,
       STENCILREF = 57, STENCILMASK = 58, STENCILWRITEMASK = 59, CLIPPLANEENABLE = 152, POINTSIZE = 154,
       POINTSIZE_MIN = 155, POINTSPRITEENABLE = 156, POINTSIZE_MAX = 166, COLORWRITEENABLE = 168, BLENDOP = 171,
       SCISSORTESTENABLE = 174, SLOPESCALEDEPTHBIAS = 175, TWOSIDEDSTENCILMODE = 185, CCW_STENCILFAIL = 186,
       CCW_STENCILZFAIL = 187, CCW_STENCILPASS = 188, CCW_STENCILFUNC = 189, COLORWRITEENABLE1 = 190,
       COLORWRITEENABLE2 = 191, COLORWRITEENABLE3 = 192, BLENDFACTOR = 193, SRGBWRITEENABLE = 194,
       DEPTHBIAS = 195, SEPARATEALPHABLENDENABLE = 206, SRCBLENDALPHA = 207, DESTBLENDALPHA = 208,
       BLENDOPALPHA = 209 };

static void translate(NxRenderState* st, NxDynamicState* dyn, uint32_t* sb, uint32_t ps, uint32_t zfmt) {
    NxD3D9Input in; memset(&in, 0, sizeof in);
    in.rs = rs; in.ps_version = ps; in.depth_format = zfmt;
    in.viewport.Width = 640; in.viewport.Height = 480; in.viewport.MaxZ = 1.0f;
    nx_d3d9_translate(&in, st, dyn, sb);
}

static void test_defaults(void) {
    reset();
    NxRenderState st; NxDynamicState dyn; uint32_t sb;
    translate(&st, &dyn, &sb, 0x0200, 75);
    CHECKEQ(st.w[0], 0xFFFFull << 32);             /* no blend, all masks RGBA */
    CHECKEQ(st.w[1], 0);                           /* ZENABLE default FALSE (Nine), stencil off */
    CHECKEQ(st.w[2], NxCull_CCW);                  /* CULLMODE default CCW */
    CHECKEQ(st.w[3], 0);
    CHECKEQ(sb, 0);
    CHECK(dyn.blendConstant[0] == 1.0f && dyn.blendConstant[3] == 1.0f);
    CHECK(dyn.pointSize == 1.0f);
}

static void test_zenable(void) {
    reset();
    CHECKEQ(D(NXRS_D_TEST, 1), 0);
    rs[ZENABLE] = 1; CHECKEQ(D(NXRS_D_TEST, 1), 1); CHECKEQ(D(NXRS_D_FUNC, 3), NxCmp_LEqual);
    rs[ZENABLE] = 2; CHECKEQ(D(NXRS_D_TEST, 1), 1);           /* D3DZB_USEW counts as enabled */
    rs[ZENABLE] = 0; rs[ZFUNC] = 2; CHECKEQ(D(0, 5), 0);       /* disabled: no func, no write */
}

static void test_zfunc(void) {
    reset(); rs[ZENABLE] = 1;
    for (uint32_t f = 1; f <= 8; f++) { rs[ZFUNC] = f; CHECKEQ(D(NXRS_D_FUNC, 3), f - 1); }
    rs[ZFUNC] = 0; CHECKEQ(D(NXRS_D_FUNC, 3), NxCmp_Never);   /* D3DCMP_NEVER_ZERO */
    rs[ZFUNC] = 9; CHECKEQ(D(NXRS_D_FUNC, 3), NxCmp_Never);
}

static void test_zwriteenable(void) {
    reset(); rs[ZENABLE] = 1;
    for (uint32_t f = 0; f <= 9; f++) {
        rs[ZFUNC] = f; rs[ZWRITEENABLE] = 1;
        int want = !(f == 0 || f == 1 || f == 3 || f == 9);   /* NEVER(_ZERO), EQUAL, invalid -> NEVER */
        CHECKEQ(D(NXRS_D_WRITE, 1), want);
        rs[ZWRITEENABLE] = 0; CHECKEQ(D(NXRS_D_WRITE, 1), 0);
    }
    rs[ZFUNC] = 4; rs[ZWRITEENABLE] = 7; CHECKEQ(D(NXRS_D_WRITE, 1), 1);
}

static void test_stencilenable(void) {
    reset(); rs[STENCILREF] = 5;
    CHECKEQ(D(NXRS_S_ENABLE, 51), 0);                          /* disabled: every stencil field 0 */
    rs[STENCILENABLE] = 1;
    CHECKEQ(D(NXRS_S_ENABLE, 1), 1);
    CHECKEQ(D(NXRS_S_FUNC, 3), NxCmp_Always);
    CHECKEQ(D(NXRS_S_READMASK, 8), 0xFF); CHECKEQ(D(NXRS_S_WRITEMASK, 8), 0xFF); CHECKEQ(D(NXRS_S_REF, 8), 5);
}

static void test_stencilops(void) {
    reset(); rs[STENCILENABLE] = 1;
    for (uint32_t o = 0; o <= 9; o++) {
        uint32_t want = (o >= 1 && o <= 8) ? o - 1 : NxSop_Zero;
        rs[STENCILFAIL] = o; CHECKEQ(D(NXRS_S_FAIL, 3), want);
        rs[STENCILZFAIL] = o; CHECKEQ(D(NXRS_S_ZFAIL, 3), want);
        rs[STENCILPASS] = o; CHECKEQ(D(NXRS_S_PASS, 3), want);
    }
    rs[STENCILFUNC] = 3; CHECKEQ(D(NXRS_S_FUNC, 3), NxCmp_Equal);
}

static void test_twosidedstencil(void) {
    reset(); rs[STENCILENABLE] = 1;
    rs[STENCILFUNC] = 2; rs[STENCILFAIL] = 2; rs[STENCILZFAIL] = 3; rs[STENCILPASS] = 4;
    rs[CCW_STENCILFUNC] = 7; rs[CCW_STENCILFAIL] = 5; rs[CCW_STENCILZFAIL] = 6; rs[CCW_STENCILPASS] = 8;
    /* off: back is a copy of front */
    CHECKEQ(D(NXRS_S_TWOSIDED, 1), 0);
    CHECKEQ(D(NXRS_S_BFUNC, 12), D(NXRS_S_FUNC, 12));
    rs[TWOSIDEDSTENCILMODE] = 1;
    CHECKEQ(D(NXRS_S_TWOSIDED, 1), 1);
    CHECKEQ(D(NXRS_S_FUNC, 3), NxCmp_Less); CHECKEQ(D(NXRS_S_FAIL, 3), NxSop_Zero);
    CHECKEQ(D(NXRS_S_ZFAIL, 3), NxSop_Replace); CHECKEQ(D(NXRS_S_PASS, 3), NxSop_IncrSat);
    CHECKEQ(D(NXRS_S_BFUNC, 3), NxCmp_GEqual); CHECKEQ(D(NXRS_S_BFAIL, 3), NxSop_DecrSat);
    CHECKEQ(D(NXRS_S_BZFAIL, 3), NxSop_Invert); CHECKEQ(D(NXRS_S_BPASS, 3), NxSop_DecrWrap);
}

static void test_stencilmasks_ref(void) {
    reset(); rs[STENCILENABLE] = 1;
    rs[STENCILMASK] = 0x1234F0; rs[STENCILWRITEMASK] = 0xABCD0F; rs[STENCILREF] = 0x1FF;
    CHECKEQ(D(NXRS_S_READMASK, 8), 0xF0); CHECKEQ(D(NXRS_S_WRITEMASK, 8), 0x0F); CHECKEQ(D(NXRS_S_REF, 8), 0xFF);
    NxDynamicState dyn; translate(0, &dyn, 0, 0, 0);
    CHECKEQ(dyn.stencilRef, 0xFF); CHECKEQ(dyn.stencilReadMask, 0xF0); CHECKEQ(dyn.stencilWriteMask, 0x0F);
}

static void test_alphablendenable(void) {
    reset();
    rs[SRCBLEND] = 5; rs[DESTBLEND] = 6;
    CHECKEQ(B(0, 27), 0);                                      /* disabled: no factors */
    rs[ALPHABLENDENABLE] = 1;
    CHECKEQ(B(NXRS_B_ENABLE, 1), 1);
    CHECKEQ(B(NXRS_B_SRC, 5), NxBf_SrcAlpha); CHECKEQ(B(NXRS_B_DST, 5), NxBf_InvSrcAlpha);
    CHECKEQ(B(NXRS_B_OP, 3), NxBop_Add);
}

static void test_srcblend_destblend_tables(void) {
    static const uint8_t col[18] = {0, NxBf_Zero, NxBf_One, NxBf_SrcColor, NxBf_InvSrcColor, NxBf_SrcAlpha,
        NxBf_InvSrcAlpha, NxBf_DstAlpha, NxBf_InvDstAlpha, NxBf_DstColor, NxBf_InvDstColor, NxBf_SrcAlphaSat,
        NxBf_SrcAlpha, NxBf_InvSrcAlpha, NxBf_ConstColor, NxBf_InvConstColor, NxBf_Src1Color, NxBf_InvSrc1Color};
    reset(); rs[ALPHABLENDENABLE] = 1;
    for (uint32_t b = 0; b < 18; b++) {
        if (b == 12 || b == 13) continue;                       /* BOTH*: fix-ups, tested below */
        uint32_t wantA = b == 16 ? NxBf_One : b == 17 ? NxBf_Zero : col[b];
        rs[SRCBLEND] = b; rs[DESTBLEND] = b;
        CHECKEQ(B(NXRS_B_SRC, 5), col[b]); CHECKEQ(B(NXRS_B_DST, 5), col[b]);
        CHECKEQ(B(NXRS_B_SRCA, 5), wantA); CHECKEQ(B(NXRS_B_DSTA, 5), wantA);   /* non-separate: alpha table */
    }
    rs[SRCBLEND] = 99; CHECKEQ(B(NXRS_B_SRC, 5), NxBf_Zero);
}

static void test_blendop(void) {
    reset(); rs[ALPHABLENDENABLE] = 1;
    for (uint32_t o = 0; o <= 6; o++) {
        rs[BLENDOP] = o;
        uint32_t want = o >= 1 && o <= 5 ? o - 1 : NxBop_Add;
        CHECKEQ(B(NXRS_B_OP, 3), want); CHECKEQ(B(NXRS_B_OPA, 3), want);   /* non-separate: alpha op = colour */
    }
}

static void test_separatealphablend(void) {
    reset(); rs[ALPHABLENDENABLE] = 1; rs[SEPARATEALPHABLENDENABLE] = 1;
    rs[SRCBLEND] = 5; rs[DESTBLEND] = 6; rs[BLENDOP] = 1;
    rs[SRCBLENDALPHA] = 2; rs[DESTBLENDALPHA] = 16; rs[BLENDOPALPHA] = 5;
    CHECKEQ(B(NXRS_B_SRCA, 5), NxBf_One); CHECKEQ(B(NXRS_B_DSTA, 5), NxBf_One);   /* SRCCOLOR2 alpha -> ONE */
    CHECKEQ(B(NXRS_B_OPA, 3), NxBop_Max);
    CHECKEQ(B(NXRS_B_SRC, 5), NxBf_SrcAlpha); CHECKEQ(B(NXRS_B_OP, 3), NxBop_Add);
    rs[SEPARATEALPHABLENDENABLE] = 0;                       /* alpha follows the colour states */
    CHECKEQ(B(NXRS_B_SRCA, 5), NxBf_SrcAlpha); CHECKEQ(B(NXRS_B_DSTA, 5), NxBf_InvSrcAlpha);
    CHECKEQ(B(NXRS_B_OPA, 3), NxBop_Add);
}

static void test_bothsrcalpha(void) {
    reset(); rs[ALPHABLENDENABLE] = 1; rs[DESTBLEND] = 2;
    rs[SRCBLEND] = 12;   /* BOTHSRCALPHA: src = SRCALPHA, dst = INVSRCALPHA (colour and, not separate, alpha) */
    CHECKEQ(B(NXRS_B_SRC, 5), NxBf_SrcAlpha); CHECKEQ(B(NXRS_B_DST, 5), NxBf_InvSrcAlpha);
    CHECKEQ(B(NXRS_B_SRCA, 5), NxBf_SrcAlpha); CHECKEQ(B(NXRS_B_DSTA, 5), NxBf_InvSrcAlpha);
    rs[SRCBLEND] = 13;   /* BOTHINVSRCALPHA */
    CHECKEQ(B(NXRS_B_SRC, 5), NxBf_InvSrcAlpha); CHECKEQ(B(NXRS_B_DST, 5), NxBf_SrcAlpha);
    CHECKEQ(B(NXRS_B_DSTA, 5), NxBf_SrcAlpha);
    /* separate: colour fixed up, alpha keeps DESTBLENDALPHA */
    rs[SEPARATEALPHABLENDENABLE] = 1; rs[SRCBLENDALPHA] = 2; rs[DESTBLENDALPHA] = 1;
    CHECKEQ(B(NXRS_B_DST, 5), NxBf_SrcAlpha); CHECKEQ(B(NXRS_B_DSTA, 5), NxBf_Zero);
    /* separate, SRCBLENDALPHA = BOTHSRCALPHA, SRCBLEND normal: alpha dst fixed up only */
    rs[SRCBLEND] = 2; rs[SRCBLENDALPHA] = 12;
    CHECKEQ(B(NXRS_B_DST, 5), NxBf_One); CHECKEQ(B(NXRS_B_SRCA, 5), NxBf_SrcAlpha);
    CHECKEQ(B(NXRS_B_DSTA, 5), NxBf_InvSrcAlpha);
    rs[SRCBLENDALPHA] = 13; CHECKEQ(B(NXRS_B_DSTA, 5), NxBf_SrcAlpha);
    /* SRCBLEND BOTH* wins over SRCBLENDALPHA BOTH* (else-branch in Nine) */
    rs[SRCBLEND] = 12; rs[SRCBLENDALPHA] = 13; rs[DESTBLENDALPHA] = 1;
    CHECKEQ(B(NXRS_B_DSTA, 5), NxBf_Zero);
}

static void test_colorwriteenable(void) {
    reset();
    rs[COLORWRITEENABLE] = 0x1; rs[COLORWRITEENABLE1] = 0x2; rs[COLORWRITEENABLE2] = 0x4;
    rs[COLORWRITEENABLE3] = 0x18;
    CHECKEQ(B(NXRS_B_MASK(0), 4), 1); CHECKEQ(B(NXRS_B_MASK(1), 4), 2); CHECKEQ(B(NXRS_B_MASK(2), 4), 4);
    CHECKEQ(B(NXRS_B_MASK(3), 4), 8);
}

static void test_blendfactor(void) {
    reset(); rs[BLENDFACTOR] = 0x80FF4000;   /* A=0x80 R=0xFF G=0x40 B=0x00 */
    NxDynamicState dyn; translate(0, &dyn, 0, 0, 0);
    CHECK(dyn.blendConstant[0] == 1.0f);
    CHECK(dyn.blendConstant[1] == (float)0x40 / 255);
    CHECK(dyn.blendConstant[2] == 0.0f);
    CHECK(dyn.blendConstant[3] == (float)0x80 / 255);
}

static void test_cullmode(void) {
    reset();
    rs[CULLMODE] = 1; CHECKEQ(R(NXRS_R_CULL, 2), NxCull_None);
    rs[CULLMODE] = 2; CHECKEQ(R(NXRS_R_CULL, 2), NxCull_CW);
    rs[CULLMODE] = 3; CHECKEQ(R(NXRS_R_CULL, 2), NxCull_CCW);
    rs[CULLMODE] = 0; CHECKEQ(R(NXRS_R_CULL, 2), NxCull_None);
    rs[CULLMODE] = 4; CHECKEQ(R(NXRS_R_CULL, 2), NxCull_None);
}

static void test_fillmode(void) {
    reset();
    rs[FILLMODE] = 1; CHECKEQ(R(NXRS_R_FILL, 2), NxFill_Point);
    rs[FILLMODE] = 2; CHECKEQ(R(NXRS_R_FILL, 2), NxFill_Wireframe);
    rs[FILLMODE] = 3; CHECKEQ(R(NXRS_R_FILL, 2), NxFill_Solid);
    rs[FILLMODE] = 0; CHECKEQ(R(NXRS_R_FILL, 2), NxFill_Solid);   /* SOLID_ZERO */
    rs[FILLMODE] = 7; CHECKEQ(R(NXRS_R_FILL, 2), NxFill_Solid);
}

static void test_shademode(void) {
    reset();
    CHECKEQ(R(NXRS_R_FLAT, 1), 0);
    rs[SHADEMODE] = 1; CHECKEQ(R(NXRS_R_FLAT, 1), 1);
    rs[SHADEMODE] = 3; CHECKEQ(R(NXRS_R_FLAT, 1), 0);
}

static void test_depthbias(void) {
    reset();
    CHECKEQ(R(NXRS_R_BIAS, 1), 0);
    rs[DEPTHBIAS] = fbits(-0.0001f); CHECKEQ(R(NXRS_R_BIAS, 1), 1);
    rs[DEPTHBIAS] = 0x80000000u; CHECKEQ(R(NXRS_R_BIAS, 1), 1);  /* -0.0f: nonzero bit pattern counts (Nine) */
    rs[DEPTHBIAS] = 0; rs[SLOPESCALEDEPTHBIAS] = fbits(2.0f); CHECKEQ(R(NXRS_R_BIAS, 1), 1);
    rs[DEPTHBIAS] = fbits(-0.0001f);
    NxDynamicState dyn;
    translate(0, &dyn, 0, 0, 75);   /* D24S8 */
    CHECK(dyn.depthBiasD3D == -0.0001f);
    CHECK(dyn.depthBiasUnits == -0.0001f * 16777216.0f);
    CHECK(dyn.depthBiasSlope == 2.0f);
    translate(0, &dyn, 0, 0, 80);   /* D16 */
    CHECK(dyn.depthBiasUnits == -0.0001f * 65536.0f);
    translate(0, &dyn, 0, 0, 0);    /* none */
    CHECK(dyn.depthBiasUnits == -0.0001f * 8388608.0f);
    CHECK(nx_d3d9_bias_scale(77) == 16777216.0f && nx_d3d9_bias_scale(82) == 8388608.0f);
}

static void test_scissortestenable(void) {
    reset();
    CHECKEQ(R(NXRS_R_SCISSOR, 1), 0);
    rs[SCISSORTESTENABLE] = 1; CHECKEQ(R(NXRS_R_SCISSOR, 1), 1);
    NxD3D9Rect r = {10, 20, 110, 15}; NxScissor s; nx_d3d9_scissor(&r, &s);
    CHECKEQ(s.x, 10); CHECKEQ(s.y, 20); CHECKEQ(s.w, 100); CHECKEQ(s.h, 0);
}

static void test_srgbwriteenable(void) {
    reset();
    CHECKEQ(R(NXRS_R_SRGB, 1), 0);
    rs[SRGBWRITEENABLE] = 1; CHECKEQ(R(NXRS_R_SRGB, 1), 1);
}

static void test_clipplaneenable(void) {
    reset();
    rs[CLIPPLANEENABLE] = 0x125; CHECKEQ(R(NXRS_R_CLIP, 8), 0x25);
    float planes[6][4] = {{1, 2, 3, 4}, {0}, {0}, {0}, {0}, {0, 0, 0, 9}};
    NxD3D9Input in; memset(&in, 0, sizeof in); in.rs = rs; in.clip_planes = (const float (*)[4])planes;
    NxDynamicState dyn; nx_d3d9_translate(&in, 0, &dyn, 0);
    CHECK(dyn.clipPlane[0][2] == 3 && dyn.clipPlane[5][3] == 9);
}

static void test_pointsprite_pointsize(void) {
    reset();
    rs[POINTSPRITEENABLE] = 1; CHECKEQ(R(NXRS_R_POINTSPRITE, 1), 1);
    NxDynamicState dyn;
    rs[POINTSIZE] = fbits(8); rs[POINTSIZE_MIN] = fbits(2); rs[POINTSIZE_MAX] = fbits(4);
    translate(0, &dyn, 0, 0, 0); CHECK(dyn.pointSize == 4);
    rs[POINTSIZE] = fbits(1); translate(0, &dyn, 0, 0, 0); CHECK(dyn.pointSize == 2);
    rs[POINTSIZE] = fbits(3); translate(0, &dyn, 0, 0, 0); CHECK(dyn.pointSize == 3);
    rs[POINTSIZE_MIN] = fbits(5); translate(0, &dyn, 0, 0, 0); CHECK(dyn.pointSize == 5);   /* x < min > max: min (Mesa CLAMP) */
}

static void test_alphatest(void) {
    reset();
    uint32_t sb;
    translate(0, 0, &sb, 0, 0); CHECKEQ(sb, 0);
    rs[ALPHATESTENABLE] = 1;
    for (uint32_t f = 0; f <= 9; f++) {
        rs[ALPHAFUNC] = f; translate(0, 0, &sb, 0, 0);
        CHECKEQ(sb & 15, 8 | ((f >= 1 && f <= 8) ? f - 1 : 0));
    }
    NxDynamicState dyn;
    rs[ALPHAREF] = 128; translate(0, &dyn, 0, 0, 0); CHECK(dyn.alphaRef == 128.0f / 255.0f);
    rs[ALPHAREF] = 300; translate(0, &dyn, 0, 0, 0); CHECK(dyn.alphaRef == 300.0f / 255.0f);   /* not clamped */
}

static void test_fog(void) {
    reset();
    uint32_t sb;
    rs[FOGTABLEMODE] = 3; translate(0, 0, &sb, 0x0200, 0); CHECKEQ(sb >> NXSB_FOG & 7, NxFog_None);  /* off */
    rs[FOGENABLE] = 1;
    rs[FOGTABLEMODE] = 3; translate(0, 0, &sb, 0x0200, 0); CHECKEQ(sb >> NXSB_FOG & 7, NxFog_Linear);
    rs[FOGTABLEMODE] = 1; translate(0, 0, &sb, 0x0104, 0); CHECKEQ(sb >> NXSB_FOG & 7, NxFog_Exp);
    rs[FOGTABLEMODE] = 2; translate(0, 0, &sb, 0, 0); CHECKEQ(sb >> NXSB_FOG & 7, NxFog_Exp2);
    rs[FOGTABLEMODE] = 0; translate(0, 0, &sb, 0x0200, 0); CHECKEQ(sb >> NXSB_FOG & 7, NxFog_Vertex);
    rs[FOGTABLEMODE] = 3; translate(0, 0, &sb, 0x0300, 0); CHECKEQ(sb >> NXSB_FOG & 7, NxFog_None);  /* ps_3_0 */
    translate(0, 0, &sb, 0xFFFF0300, 0); CHECKEQ(sb >> NXSB_FOG & 7, NxFog_None);   /* full version token */
    rs[FOGCOLOR] = 0xFF102030; rs[FOGSTART] = fbits(10); rs[FOGEND] = fbits(110); rs[FOGDENSITY] = fbits(0.5f);
    NxDynamicState dyn; translate(0, &dyn, 0, 0, 0);
    CHECK(dyn.fogColor[0] == (float)0x10 / 255 && dyn.fogColor[2] == (float)0x30 / 255 && dyn.fogColor[3] == 1);
    CHECK(dyn.fogEnd == 110 && dyn.fogScale == 1.0f / 100.0f && dyn.fogDensity == 0.5f);
}

static void test_viewport(void) {
    NxD3D9Viewport vp = {16, 8, 320, 200, 0.25f, 0.75f}; NxViewport o;
    nx_d3d9_viewport(&vp, &o);
    CHECK(o.x == 16.5f && o.y == 8.5f && o.w == 320 && o.h == 200 && o.minZ == 0.25f && o.maxZ == 0.75f);
}

/* ---- samplers ---- */
enum { ADDRESSU = 1, ADDRESSV = 2, ADDRESSW = 3, BORDERCOLOR = 4, MAGFILTER = 5, MINFILTER = 6, MIPFILTER = 7,
       MIPMAPLODBIAS = 8, MAXMIPLEVEL = 9, MAXANISOTROPY = 10, SRGBTEXTURE = 11 };
static uint32_t ss[NXD3D_SAMP_COUNT];

static void test_samp_defaults(void) {
    nx_d3d9_default_samp(ss);
    NxSamplerState o; nx_d3d9_sampler(ss, 0, &o);
    CHECKEQ(o.filter, 0); CHECKEQ(o.wrap, 0); CHECKEQ(o.maxAniso, 0); CHECKEQ(o.minLod, 0); CHECKEQ(o.maxLod, 15);
    CHECK(o.lodBias == 0); CHECKEQ(o.border, 0);
}

static void test_samp_address(void) {
    nx_d3d9_default_samp(ss);
    static const uint32_t want[7] = {NxWrap_Clamp, NxWrap_Repeat, NxWrap_Mirror, NxWrap_Clamp, NxWrap_Border,
                                     NxWrap_MirrorOnce, NxWrap_Clamp};
    for (uint32_t a = 0; a < 7; a++) {
        ss[ADDRESSU] = a; ss[ADDRESSV] = (a + 1) % 7; ss[ADDRESSW] = (a + 2) % 7;
        NxSamplerState o; nx_d3d9_sampler(ss, 0, &o);
        CHECKEQ(o.wrap & 7, want[a]); CHECKEQ(o.wrap >> 3 & 7, want[(a + 1) % 7]); CHECKEQ(o.wrap >> 6 & 7, want[(a + 2) % 7]);
        NxD3D9Texture cube = {21, 1, 0, 0, 0};
        nx_d3d9_sampler(ss, &cube, &o);
        CHECKEQ(o.wrap, NxWrap_Clamp | NxWrap_Clamp << 3 | NxWrap_Clamp << 6);
    }
}

static void test_samp_minmag(void) {
    nx_d3d9_default_samp(ss);
    for (uint32_t f = 0; f <= 8; f++) {
        ss[MINFILTER] = f; ss[MAGFILTER] = f;
        NxSamplerState o; nx_d3d9_sampler(ss, 0, &o);
        uint32_t lin = f == 1 ? 0 : 3;   /* only POINT is nearest */
        CHECKEQ(o.filter & 3, lin);
        NxD3D9Texture sh = {75, 0, 1, 0, 0};
        nx_d3d9_sampler(ss, &sh, &o);
        CHECKEQ(o.filter & 3, 3);        /* shadow: always linear */
        CHECK(o.filter & NXSMP_SHADOW);
    }
    ss[MINFILTER] = 1; ss[MAGFILTER] = 2;
    NxSamplerState o; nx_d3d9_sampler(ss, 0, &o); CHECKEQ(o.filter & 3, NXSMP_MAG_LINEAR);
}

static void test_samp_mipfilter(void) {
    nx_d3d9_default_samp(ss);
    ss[MIPMAPLODBIAS] = fbits(-1.5f); ss[MAXMIPLEVEL] = 3;
    NxSamplerState o;
    nx_d3d9_sampler(ss, 0, &o);          /* NONE: bias and MAXMIPLEVEL ignored */
    CHECKEQ(o.filter >> NXSMP_MIP_SHIFT & 3, NxMip_None); CHECK(o.lodBias == 0); CHECKEQ(o.minLod, 0);
    ss[MIPFILTER] = 1; nx_d3d9_sampler(ss, 0, &o);
    CHECKEQ(o.filter >> NXSMP_MIP_SHIFT & 3, NxMip_Nearest); CHECK(o.lodBias == -1.5f); CHECKEQ(o.minLod, 3);
    ss[MIPFILTER] = 2; nx_d3d9_sampler(ss, 0, &o); CHECKEQ(o.filter >> NXSMP_MIP_SHIFT & 3, NxMip_Linear);
    ss[MIPFILTER] = 3; nx_d3d9_sampler(ss, 0, &o); CHECKEQ(o.filter >> NXSMP_MIP_SHIFT & 3, NxMip_Linear);
    ss[MIPFILTER] = 7; nx_d3d9_sampler(ss, 0, &o); CHECKEQ(o.filter >> NXSMP_MIP_SHIFT & 3, NxMip_Linear);
}

static void test_samp_maxmiplevel_managed(void) {
    nx_d3d9_default_samp(ss); ss[MIPFILTER] = 2;
    NxSamplerState o; NxD3D9Texture t = {21, 0, 0, 2, 0};
    ss[MAXMIPLEVEL] = 5; nx_d3d9_sampler(ss, &t, &o); CHECKEQ(o.minLod, 3);
    ss[MAXMIPLEVEL] = 1; nx_d3d9_sampler(ss, &t, &o); CHECKEQ(o.minLod, 0);
    ss[MAXMIPLEVEL] = 40; nx_d3d9_sampler(ss, 0, &o); CHECKEQ(o.minLod, 15);
    CHECKEQ(o.maxLod, 15);
}

static void test_samp_anisotropy(void) {
    nx_d3d9_default_samp(ss); ss[MAXANISOTROPY] = 8;
    NxSamplerState o;
    ss[MINFILTER] = 2; nx_d3d9_sampler(ss, 0, &o); CHECKEQ(o.maxAniso, 0);
    ss[MINFILTER] = 3; nx_d3d9_sampler(ss, 0, &o); CHECKEQ(o.maxAniso, 8);
    ss[MINFILTER] = 2; ss[MAGFILTER] = 3; nx_d3d9_sampler(ss, 0, &o); CHECKEQ(o.maxAniso, 8);
}

static void test_samp_border(void) {
    nx_d3d9_default_samp(ss); ss[BORDERCOLOR] = 0x11223344;   /* A=11 R=22 G=33 B=44 */
    NxSamplerState o; nx_d3d9_sampler(ss, 0, &o);
    CHECKEQ(o.border, 0x11443322u);
}

static void test_samp_srgb(void) {
    nx_d3d9_default_samp(ss); ss[SRGBTEXTURE] = 1;
    NxSamplerState o;
    NxD3D9Texture argb = {21, 0, 0, 0, 0}, l8 = {50, 0, 0, 0, 0}, dxt5 = {NXD3D_FOURCC('D', 'X', 'T', '5'), 0, 0, 0, 0};
    nx_d3d9_sampler(ss, &argb, &o); CHECK(o.filter & NXSMP_SRGB);
    nx_d3d9_sampler(ss, &dxt5, &o); CHECK(o.filter & NXSMP_SRGB);
    nx_d3d9_sampler(ss, &l8, &o); CHECK(o.filter & NXSMP_SRGB);      /* L8 is stored as RGBA8 (border_swizzle fix) */
    NxD3D9Texture v8 = {60, 0, 0, 0, 0};
    nx_d3d9_sampler(ss, &v8, &o); CHECK(!(o.filter & NXSMP_SRGB));
    ss[SRGBTEXTURE] = 0; nx_d3d9_sampler(ss, &argb, &o); CHECK(!(o.filter & NXSMP_SRGB));
}

/* ---- formats ---- */
static void fmt(uint32_t d, uint32_t want, const char* sw) {
    NxFormatInfo f = nx_d3d9_format(d);
    static const char names[] = "RGBA01";
    char got[5] = {names[f.swizzle[0]], names[f.swizzle[1]], names[f.swizzle[2]], names[f.swizzle[3]], 0};
    g_checks++;
    if (f.format != want || (sw && strcmp(got, sw))) {
        g_fail++; printf("FAIL format %u: got %u %s, want %u %s\n", d, f.format, got, want, sw ? sw : "");
    }
}
static void test_formats(void) {
    fmt(21, NxFmt_BGRA8, "RGBA"); fmt(22, NxFmt_BGRX8, "RGB1"); fmt(32, NxFmt_RGBA8, "RGBA");
    fmt(23, NxFmt_R5G6B5, "RGB1"); fmt(25, NxFmt_A1R5G5B5, "RGBA"); fmt(24, NxFmt_X1R5G5B5, "RGB1");
    fmt(26, NxFmt_A4R4G4B4, "RGBA"); fmt(30, NxFmt_A4R4G4B4, "RGB1");
    fmt(50, NxFmt_RGBA8, "RGB1"); fmt(28, NxFmt_RGBA8, "RGBA"); fmt(51, NxFmt_RGBA8, "RGBA"); fmt(60, NxFmt_RG8S, "RG11");
    fmt(81, NxFmt_RGBA16, "RGB1"); fmt(35, NxFmt_RGB10A2, "RGBA"); fmt(31, NxFmt_RGB10A2, "RGBA");
    fmt(111, NxFmt_R16F, "R111"); fmt(113, NxFmt_RGBA16F, "RGBA"); fmt(114, NxFmt_R32F, "R111");
    fmt(NXD3D_FOURCC('D', 'X', 'T', '1'), NxFmt_BC1, "RGBA");
    fmt(NXD3D_FOURCC('D', 'X', 'T', '2'), NxFmt_BC2, 0); fmt(NXD3D_FOURCC('D', 'X', 'T', '3'), NxFmt_BC2, 0);
    fmt(NXD3D_FOURCC('D', 'X', 'T', '4'), NxFmt_BC3, 0); fmt(NXD3D_FOURCC('D', 'X', 'T', '5'), NxFmt_BC3, 0);
    fmt(NXD3D_FOURCC('A', 'T', 'I', '1'), NxFmt_RGBA8, "RGBA"); fmt(NXD3D_FOURCC('A', 'T', 'I', '2'), NxFmt_BC5, "GR11");
    fmt(75, NxFmt_Z24S8, 0); fmt(77, NxFmt_Z24X8, 0); fmt(80, NxFmt_Z16, 0); fmt(82, NxFmt_Z32F, 0);
    fmt(71, NxFmt_None, 0); fmt(41, NxFmt_None, 0); fmt(20, NxFmt_None, 0);
    CHECK(nx_d3d9_format(NXD3D_FOURCC('D', 'X', 'T', '1')).flags & NXFMT_COMPRESSED);
    CHECKEQ(nx_d3d9_format(NXD3D_FOURCC('D', 'X', 'T', '1')).bytes, 8);
    CHECK(nx_d3d9_format(75).flags & NXFMT_STENCIL);
    /* swizzles never duplicate or invent a channel except the 'missing channel reads 1' of Nine (border_swizzle) */
    static const uint32_t all[] = {21, 22, 32, 33, 23, 24, 25, 26, 30, 31, 35, 34, 36, 28, 50, 51, 81, 60, 63, 64, 110,
        111, 112, 113, 114, 115, 116, NXD3D_FOURCC('D', 'X', 'T', '1'), NXD3D_FOURCC('A', 'T', 'I', '1')};
    for (unsigned i = 0; i < sizeof all / sizeof all[0]; i++) {
        NxFormatInfo f = nx_d3d9_format(all[i]);
        for (int c = 0; c < 4; c++) { g_checks++; if (f.swizzle[c] != c && f.swizzle[c] != NxSwz_1) { g_fail++; printf("FAIL format %u swizzle[%d] = %d\n", all[i], c, f.swizzle[c]); } }
    }
}

static void test_convert(void) {
    uint8_t out[64];
    const uint8_t l8[4] = {0, 85, 170, 255}, a8[2] = {7, 200}, al[4] = {10, 20, 30, 40};
    CHECKEQ(nx_d3d9_converted_size(50, 2, 2, 1, 1), 16);
    CHECKEQ(nx_d3d9_convert(50, 2, 2, 1, 1, l8, 4, out, sizeof out), 16);
    CHECK(out[4] == 85 && out[5] == 85 && out[6] == 85 && out[7] == 255);
    CHECKEQ(nx_d3d9_convert(28, 2, 1, 1, 1, a8, 2, out, sizeof out), 8);
    CHECK(out[0] == 0 && out[1] == 0 && out[2] == 0 && out[3] == 7 && out[7] == 200);
    CHECKEQ(nx_d3d9_convert(51, 2, 1, 1, 1, al, 4, out, sizeof out), 8);
    CHECK(out[0] == 10 && out[2] == 10 && out[3] == 20 && out[4] == 30 && out[7] == 40);
    const uint8_t l16[2] = {0x34, 0x12};
    CHECKEQ(nx_d3d9_convert(81, 1, 1, 1, 1, l16, 2, out, sizeof out), 8);
    CHECK(out[0] == 0x34 && out[1] == 0x12 && out[4] == 0x34 && out[5] == 0x12 && out[6] == 255 && out[7] == 255);
    uint32_t a2 = 3u << 30 | 1023u << 20 | 5u << 10 | 1u, a2o;   /* A=3 R=1023 G=5 B=1 */
    CHECKEQ(nx_d3d9_convert(35, 1, 1, 1, 1, &a2, 4, &a2o, 4), 4);
    CHECKEQ(a2o, 3u << 30 | 1u << 20 | 5u << 10 | 1023u);         /* RGB10A2: R low */
    /* ATI1: one block e0 = 200 > e1 = 100, texel 0 code 0, texel 1 code 1, texel 2 code 2, texel 3 code 7 */
    uint8_t bc4[8] = {200, 100, 0, 0, 0, 0, 0, 0};
    uint64_t bits = 0ull | 1ull << 3 | 2ull << 6 | 7ull << 9;
    for (int i = 0; i < 6; i++) bc4[2 + i] = (uint8_t)(bits >> (8 * i));
    CHECKEQ(nx_d3d9_converted_size(NXD3D_FOURCC('A', 'T', 'I', '1'), 4, 4, 1, 1), 64);
    CHECKEQ(nx_d3d9_convert(NXD3D_FOURCC('A', 'T', 'I', '1'), 4, 4, 1, 1, bc4, 8, out, sizeof out), 64);
    CHECK(out[0] == 200 && out[4] == 100 && out[8] == (6 * 200 + 1 * 100) / 7 && out[12] == (1 * 200 + 6 * 100) / 7);
    CHECK(out[1] == 0 && out[2] == 0 && out[3] == 255);
    CHECKEQ(nx_d3d9_convert(21, 1, 1, 1, 1, l8, 4, out, sizeof out), 0);      /* no conversion needed */
    CHECKEQ(nx_d3d9_convert(50, 2, 2, 1, 1, l8, 3, out, sizeof out), 0);      /* short source */
    CHECKEQ(nx_d3d9_convert(50, 2, 2, 1, 1, l8, 4, out, 15), 0);              /* small destination */
    CHECKEQ(nx_d3d9_converted_size(50, 4, 4, 3, 6), (16 + 4 + 1) * 4 * 6);    /* mips and faces */
}

int main(void) {
    test_defaults(); test_zenable(); test_zfunc(); test_zwriteenable(); test_stencilenable(); test_stencilops();
    test_twosidedstencil(); test_stencilmasks_ref(); test_alphablendenable(); test_srcblend_destblend_tables();
    test_blendop(); test_separatealphablend(); test_bothsrcalpha(); test_colorwriteenable(); test_blendfactor();
    test_cullmode(); test_fillmode(); test_shademode(); test_depthbias(); test_scissortestenable();
    test_srgbwriteenable(); test_clipplaneenable(); test_pointsprite_pointsize(); test_alphatest(); test_fog();
    test_viewport();
    test_samp_defaults(); test_samp_address(); test_samp_minmag(); test_samp_mipfilter();
    test_samp_maxmiplevel_managed(); test_samp_anisotropy(); test_samp_border(); test_samp_srgb();
    test_formats(); test_convert();
    printf("d3d9_state: %d checks, %d failed\n", g_checks, g_fail);
    return g_fail != 0;
}
