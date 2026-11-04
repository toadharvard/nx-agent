/* V6n state-level differential (D14): S6n's D3D9 state translator (render/native/front/d3d9_state.c) against
 * Gallium Nine's own state conversion (nine_pipe.c compiled from the pinned Mesa 20.1 tree, the oracle).
 *
 *   statediff [-n CASES] [-s SEED] [-v] [-r REPRO.txt]
 *
 * Each case is a random D3D9 render-state vector (biased to the values where D3D9 -> modern mappings go wrong:
 * enum edges, 0 / 0xFF / 0x100 / 0xFFFFFFFF, float bit patterns incl. -0.0 and NaN) and a random sampler-state
 * vector. Both sides convert it; every field is compared in Nine's terms (pipe_* enums). A mismatch is printed as
 * one line `MISMATCH <field> nine=<v> s6n=<v> | rs ...` holding only the states that field depends on, which
 * is a minimal reproducer (one per field kind; -r writes them to a text file).
 *
 * Fields compared (Nine function -> S6n output):
 *   nine_convert_dsa_state        -> w[1] depth/stencil + shader bits alpha test + dyn.alphaRef
 *   nine_convert_rasterizer_state -> w[2] + dyn.pointSize + dyn.depthBiasSlope (offset_units: deviation D1, both
 *                                    values printed in the summary, not counted)
 *   nine_convert_blend_state      -> w[0] + dyn.blendConstant (Nine: d3dcolor_to_rgba in nine_state.c)
 *   nine_convert_sampler_state    -> NxSamplerState (+ Nine's update_sampler_derived MINLOD/CUBETEX/SHADOW rules,
 *                                    replicated below from nine_state.c because they are static there)
 *   pixel-shader fog key          -> shader bits fog (pixelshader9.c: fog_enable/fog_mode, nine_shader.c:
 *                                    shader_add_ps_fog_stage for ps < 3) */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>

#include "pipe/p_state.h"
#include "pipe/p_defines.h"
#include "d3d9types.h"
#include "nine_state.h"
/* Nine's conversion functions (nine_pipe.c); declared here to avoid nine_pipe.h's inline helpers' includes */
struct NineDevice9;
struct cso_context;
void nine_convert_dsa_state(struct pipe_depth_stencil_alpha_state*, const DWORD*);
void nine_convert_rasterizer_state(struct NineDevice9*, struct pipe_rasterizer_state*, const DWORD*);
void nine_convert_blend_state(struct pipe_blend_state*, const DWORD*);
void nine_convert_sampler_state(struct cso_context*, int, const DWORD*);

#include "d3d9_state.h"   /* S6n */

/* captured by our cso_single_sampler (nine_convert_sampler_state's sink) */
static struct pipe_sampler_state g_samp;
void cso_single_sampler(struct cso_context* c, unsigned shader, unsigned idx, const struct pipe_sampler_state* s) {
    (void)c; (void)shader; (void)idx; g_samp = *s;
}
/* nine_convert_rasterizer_state reads device->driver_caps.offset_units_unscaled only: device9_caps_stub.c */
struct NineDevice9* v6n_fake_device(int offset_units_unscaled);

/* ---- random ---------------------------------------------------------------------------------------------- */
static uint64_t g_rng = 1;
static uint32_t rnd(void) { g_rng ^= g_rng << 13; g_rng ^= g_rng >> 7; g_rng ^= g_rng << 17; return (uint32_t)(g_rng >> 11); }
static uint32_t pick(const uint32_t* v, int n) { return v[rnd() % n]; }
static uint32_t fbits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
#define N(a) (int)(sizeof(a) / sizeof *(a))

static const uint32_t EDGE[] = {0, 1, 2, 3, 0xFF, 0x100, 0x7FFFFFFF, 0x80000000u, 0xFFFFFFFFu};
static uint32_t r_enum(int lo, int hi) {           /* valid range, plus edges 1 in 6 */
    if (rnd() % 6 == 0) return pick(EDGE, N(EDGE));
    return (uint32_t)(lo + (int)(rnd() % (unsigned)(hi - lo + 1)));
}
static uint32_t r_bool(void) { static const uint32_t v[] = {0, 1, 0, 1, 2, 0xFFFFFFFFu, 0x100}; return pick(v, N(v)); }
static uint32_t r_float(void) {
    float f[] = {0.0f, -0.0f, 1.0f, -1.0f, 0.5f, 1e-6f, -1e-6f, 2.0f, 16.0f, 255.0f, 1e30f, 0.0001f, -0.002f};
    uint32_t extra[] = {0x7FC00000u, 0xFF800000u, 0x7F800000u, 1u};
    if (rnd() % 8 == 0) return pick(extra, N(extra));
    return fbits(f[rnd() % N(f)]);
}
static uint32_t r_color(void) { return rnd() % 4 == 0 ? pick(EDGE, N(EDGE)) : rnd() ^ (rnd() << 16); }

static void random_rs(DWORD* rs) {
    nx_d3d9_default_rs(rs);
    rs[D3DRS_ZENABLE] = r_enum(0, 2); rs[D3DRS_ZWRITEENABLE] = r_bool(); rs[D3DRS_ZFUNC] = r_enum(0, 9);
    rs[D3DRS_FILLMODE] = r_enum(0, 4); rs[D3DRS_SHADEMODE] = r_enum(0, 4); rs[D3DRS_CULLMODE] = r_enum(0, 4);
    rs[D3DRS_ALPHATESTENABLE] = r_bool(); rs[D3DRS_ALPHAFUNC] = r_enum(0, 9);
    rs[D3DRS_ALPHAREF] = rnd() % 3 ? rnd() % 256 : pick(EDGE, N(EDGE));
    rs[D3DRS_ALPHABLENDENABLE] = r_bool(); rs[D3DRS_SRCBLEND] = r_enum(0, 18); rs[D3DRS_DESTBLEND] = r_enum(0, 18);
    rs[D3DRS_BLENDOP] = r_enum(0, 6); rs[D3DRS_SEPARATEALPHABLENDENABLE] = r_bool();
    rs[D3DRS_SRCBLENDALPHA] = r_enum(0, 18); rs[D3DRS_DESTBLENDALPHA] = r_enum(0, 18); rs[D3DRS_BLENDOPALPHA] = r_enum(0, 6);
    rs[D3DRS_BLENDFACTOR] = r_color();
    rs[D3DRS_COLORWRITEENABLE] = rnd() % 4 ? rnd() % 16 : pick(EDGE, N(EDGE));
    rs[D3DRS_COLORWRITEENABLE1] = rnd() % 2 ? rs[D3DRS_COLORWRITEENABLE] : rnd() % 16;
    rs[D3DRS_COLORWRITEENABLE2] = rnd() % 2 ? rs[D3DRS_COLORWRITEENABLE] : rnd() % 16;
    rs[D3DRS_COLORWRITEENABLE3] = rnd() % 2 ? rs[D3DRS_COLORWRITEENABLE] : rnd() % 16;
    rs[D3DRS_STENCILENABLE] = r_bool(); rs[D3DRS_TWOSIDEDSTENCILMODE] = r_bool();
    rs[D3DRS_STENCILFUNC] = r_enum(0, 9); rs[D3DRS_STENCILFAIL] = r_enum(0, 9);
    rs[D3DRS_STENCILZFAIL] = r_enum(0, 9); rs[D3DRS_STENCILPASS] = r_enum(0, 9);
    rs[D3DRS_CCW_STENCILFUNC] = r_enum(0, 9); rs[D3DRS_CCW_STENCILFAIL] = r_enum(0, 9);
    rs[D3DRS_CCW_STENCILZFAIL] = r_enum(0, 9); rs[D3DRS_CCW_STENCILPASS] = r_enum(0, 9);
    rs[D3DRS_STENCILREF] = r_color(); rs[D3DRS_STENCILMASK] = r_color(); rs[D3DRS_STENCILWRITEMASK] = r_color();
    rs[D3DRS_DEPTHBIAS] = r_float(); rs[D3DRS_SLOPESCALEDEPTHBIAS] = r_float();
    rs[D3DRS_SCISSORTESTENABLE] = r_bool(); rs[D3DRS_SRGBWRITEENABLE] = r_bool();
    rs[D3DRS_CLIPPLANEENABLE] = rnd() % 2 ? rnd() % 64 : r_color();
    rs[D3DRS_POINTSPRITEENABLE] = r_bool();
    rs[D3DRS_POINTSIZE] = r_float(); rs[D3DRS_POINTSIZE_MIN] = r_float(); rs[D3DRS_POINTSIZE_MAX] = r_float();
    rs[D3DRS_FOGENABLE] = r_bool(); rs[D3DRS_FOGTABLEMODE] = r_enum(0, 4);
    rs[D3DRS_FOGCOLOR] = r_color(); rs[D3DRS_FOGSTART] = r_float(); rs[D3DRS_FOGEND] = r_float();
    rs[D3DRS_FOGDENSITY] = r_float();
}
static void random_samp(DWORD* s) {
    nx_d3d9_default_samp(s);
    s[D3DSAMP_ADDRESSU] = r_enum(0, 6); s[D3DSAMP_ADDRESSV] = r_enum(0, 6); s[D3DSAMP_ADDRESSW] = r_enum(0, 6);
    s[D3DSAMP_BORDERCOLOR] = r_color();
    s[D3DSAMP_MAGFILTER] = r_enum(0, 7); s[D3DSAMP_MINFILTER] = r_enum(0, 7); s[D3DSAMP_MIPFILTER] = r_enum(0, 4);
    s[D3DSAMP_MIPMAPLODBIAS] = r_float(); s[D3DSAMP_MAXMIPLEVEL] = rnd() % 2 ? rnd() % 20 : pick(EDGE, N(EDGE));
    s[D3DSAMP_MAXANISOTROPY] = rnd() % 2 ? rnd() % 17 : pick(EDGE, N(EDGE));
    s[D3DSAMP_SRGBTEXTURE] = r_bool();
}

/* ---- S6n enums -> pipe ------------------------------------------------------------------------------------- */
static const unsigned BF[NxBf_Count] = {
    [NxBf_Zero] = PIPE_BLENDFACTOR_ZERO, [NxBf_One] = PIPE_BLENDFACTOR_ONE, [NxBf_SrcColor] = PIPE_BLENDFACTOR_SRC_COLOR,
    [NxBf_InvSrcColor] = PIPE_BLENDFACTOR_INV_SRC_COLOR, [NxBf_SrcAlpha] = PIPE_BLENDFACTOR_SRC_ALPHA,
    [NxBf_InvSrcAlpha] = PIPE_BLENDFACTOR_INV_SRC_ALPHA, [NxBf_DstAlpha] = PIPE_BLENDFACTOR_DST_ALPHA,
    [NxBf_InvDstAlpha] = PIPE_BLENDFACTOR_INV_DST_ALPHA, [NxBf_DstColor] = PIPE_BLENDFACTOR_DST_COLOR,
    [NxBf_InvDstColor] = PIPE_BLENDFACTOR_INV_DST_COLOR, [NxBf_SrcAlphaSat] = PIPE_BLENDFACTOR_SRC_ALPHA_SATURATE,
    [NxBf_ConstColor] = PIPE_BLENDFACTOR_CONST_COLOR, [NxBf_InvConstColor] = PIPE_BLENDFACTOR_INV_CONST_COLOR,
    [NxBf_Src1Color] = PIPE_BLENDFACTOR_SRC1_COLOR, [NxBf_InvSrc1Color] = PIPE_BLENDFACTOR_INV_SRC1_COLOR};
static const unsigned SOP[8] = {PIPE_STENCIL_OP_KEEP, PIPE_STENCIL_OP_ZERO, PIPE_STENCIL_OP_REPLACE,
    PIPE_STENCIL_OP_INCR, PIPE_STENCIL_OP_DECR, PIPE_STENCIL_OP_INVERT, PIPE_STENCIL_OP_INCR_WRAP,
    PIPE_STENCIL_OP_DECR_WRAP};
static const unsigned WRAP[5] = {PIPE_TEX_WRAP_REPEAT, PIPE_TEX_WRAP_MIRROR_REPEAT, PIPE_TEX_WRAP_CLAMP_TO_EDGE,
    PIPE_TEX_WRAP_CLAMP_TO_BORDER, PIPE_TEX_WRAP_MIRROR_CLAMP_TO_EDGE};
static const unsigned CULL[3] = {PIPE_FACE_NONE, PIPE_FACE_FRONT, PIPE_FACE_BACK};   /* Nine: CW = front */
static const unsigned FILL[3] = {PIPE_POLYGON_MODE_FILL, PIPE_POLYGON_MODE_LINE, PIPE_POLYGON_MODE_POINT};

/* ---- mismatch bookkeeping ---------------------------------------------------------------------------------- */
#define MAXF 96
static struct { const char* name; long n; char repro[512]; } g_f[MAXF];
static int g_nf, g_verbose;
static long g_checks;
static const DWORD* g_rs; static const DWORD* g_ss;

static void note(const char* field, double nine, double s6n, const int* deps, int ndeps, int samp) {
    int i;
    for (i = 0; i < g_nf; i++) if (!strcmp(g_f[i].name, field)) break;
    if (i == g_nf) { if (g_nf == MAXF) return; g_f[g_nf].name = field; g_nf++; }
    if (g_f[i].n++ == 0 || g_verbose) {
        char* p = g_f[i].repro; int left = sizeof g_f[i].repro;
        int k = snprintf(p, left, "%s nine=%g s6n=%g |", field, nine, s6n); p += k; left -= k;
        for (int d = 0; d < ndeps && left > 24; d++) {
            k = snprintf(p, left, " %s%d=0x%x", samp ? "samp" : "rs", deps[d], (unsigned)(samp ? g_ss : g_rs)[deps[d]]);
            p += k; left -= k;
        }
        if (g_verbose) printf("MISMATCH %s\n", g_f[i].repro);
    }
}
#define CHK(field, nine, s6n, ...) do { int deps_[] = {__VA_ARGS__}; g_checks++; \
    if ((double)(nine) != (double)(s6n)) note(field, (double)(nine), (double)(s6n), deps_, N(deps_), 0); } while (0)
static int feq(float a, float b) { return a == b || (isnan(a) && isnan(b)); }
#define CHKS(field, nine, s6n, ...) do { int deps_[] = {__VA_ARGS__}; g_checks++; \
    if (!feq((float)(nine), (float)(s6n))) note(field, (double)(nine), (double)(s6n), deps_, N(deps_), 1); } while (0)
#define CHKF(field, nine, s6n, ...) do { int deps_[] = {__VA_ARGS__}; g_checks++; \
    if (!feq((nine), (s6n))) note(field, (double)(nine), (double)(s6n), deps_, N(deps_), 0); } while (0)

static double g_bias_ratio_sum; static long g_bias_n;

static void one_case(struct NineDevice9* dev) {
    DWORD rs[NINED3DRS_LAST + 1]; DWORD ss[NINED3DSAMP_LAST + 1];
    memset(rs, 0, sizeof rs); memset(ss, 0, sizeof ss);
    random_rs(rs); random_samp(ss);
    g_rs = rs; g_ss = ss;
    uint32_t ps_version = rnd() % 3 == 0 ? 0 : (rnd() % 2 ? 0x0200 : 0x0300);
    NxD3D9Input in; memset(&in, 0, sizeof in);
    in.rs = (const uint32_t*)rs; in.ps_version = ps_version; in.depth_format = D3DFMT_D24S8;
    NxRenderState st; NxDynamicState dyn; uint32_t sb;
    nx_d3d9_translate(&in, &st, &dyn, &sb);

    /* depth / stencil / alpha */
    struct pipe_depth_stencil_alpha_state dsa;
    nine_convert_dsa_state(&dsa, rs);
    uint64_t w1 = st.w[1];
    CHK("depth.enabled", dsa.depth.enabled, NXRS_GET(w1, NXRS_D_TEST, 1), D3DRS_ZENABLE);
    CHK("depth.writemask", dsa.depth.writemask, NXRS_GET(w1, NXRS_D_WRITE, 1), D3DRS_ZENABLE, D3DRS_ZWRITEENABLE, D3DRS_ZFUNC);
    if (dsa.depth.enabled) CHK("depth.func", dsa.depth.func, NXRS_GET(w1, NXRS_D_FUNC, 3), D3DRS_ZFUNC);
    CHK("stencil.enabled", dsa.stencil[0].enabled, NXRS_GET(w1, NXRS_S_ENABLE, 1), D3DRS_STENCILENABLE);
    if (dsa.stencil[0].enabled) {
        const struct pipe_stencil_state* f = &dsa.stencil[0];
        const struct pipe_stencil_state* b = dsa.stencil[1].enabled ? &dsa.stencil[1] : &dsa.stencil[0];
        CHK("stencil.front.func", f->func, NXRS_GET(w1, NXRS_S_FUNC, 3), D3DRS_STENCILFUNC);
        CHK("stencil.front.fail", f->fail_op, SOP[NXRS_GET(w1, NXRS_S_FAIL, 3)], D3DRS_STENCILFAIL);
        CHK("stencil.front.zfail", f->zfail_op, SOP[NXRS_GET(w1, NXRS_S_ZFAIL, 3)], D3DRS_STENCILZFAIL);
        CHK("stencil.front.pass", f->zpass_op, SOP[NXRS_GET(w1, NXRS_S_PASS, 3)], D3DRS_STENCILPASS);
        CHK("stencil.back.func", b->func, NXRS_GET(w1, NXRS_S_BFUNC, 3), D3DRS_TWOSIDEDSTENCILMODE, D3DRS_STENCILFUNC, D3DRS_CCW_STENCILFUNC);
        CHK("stencil.back.fail", b->fail_op, SOP[NXRS_GET(w1, NXRS_S_BFAIL, 3)], D3DRS_TWOSIDEDSTENCILMODE, D3DRS_STENCILFAIL, D3DRS_CCW_STENCILFAIL);
        CHK("stencil.back.zfail", b->zfail_op, SOP[NXRS_GET(w1, NXRS_S_BZFAIL, 3)], D3DRS_TWOSIDEDSTENCILMODE, D3DRS_STENCILZFAIL, D3DRS_CCW_STENCILZFAIL);
        CHK("stencil.back.pass", b->zpass_op, SOP[NXRS_GET(w1, NXRS_S_BPASS, 3)], D3DRS_TWOSIDEDSTENCILMODE, D3DRS_STENCILPASS, D3DRS_CCW_STENCILPASS);
        CHK("stencil.valuemask", f->valuemask, NXRS_GET(w1, NXRS_S_READMASK, 8), D3DRS_STENCILMASK);
        CHK("stencil.writemask", f->writemask, NXRS_GET(w1, NXRS_S_WRITEMASK, 8), D3DRS_STENCILWRITEMASK);
        CHK("stencil.back.valuemask", b->valuemask, NXRS_GET(w1, NXRS_S_READMASK, 8), D3DRS_STENCILMASK);
        CHK("stencil.ref", (uint8_t)rs[D3DRS_STENCILREF], NXRS_GET(w1, NXRS_S_REF, 8), D3DRS_STENCILREF);  /* nine_state.c: pipe_stencil_ref ubyte */
    }
    CHK("alpha.enabled", dsa.alpha.enabled, (sb >> NXSB_ALPHATEST) & 1, D3DRS_ALPHATESTENABLE);
    if (dsa.alpha.enabled) {
        CHK("alpha.func", dsa.alpha.func, (sb >> NXSB_ALPHAFUNC) & 7, D3DRS_ALPHAFUNC);
        CHKF("alpha.ref", dsa.alpha.ref_value, dyn.alphaRef, D3DRS_ALPHAREF);
    }

    /* rasterizer */
    struct pipe_rasterizer_state rast;
    nine_convert_rasterizer_state(dev, &rast, rs);
    uint64_t w2 = st.w[2];
    CHK("rast.flatshade", rast.flatshade, NXRS_GET(w2, NXRS_R_FLAT, 1), D3DRS_SHADEMODE);
    CHK("rast.cull_face", rast.cull_face, CULL[NXRS_GET(w2, NXRS_R_CULL, 2)], D3DRS_CULLMODE);
    CHK("rast.fill", rast.fill_front, FILL[NXRS_GET(w2, NXRS_R_FILL, 2)], D3DRS_FILLMODE);
    CHK("rast.offset_tri", rast.offset_tri, NXRS_GET(w2, NXRS_R_BIAS, 1), D3DRS_DEPTHBIAS, D3DRS_SLOPESCALEDEPTHBIAS);
    CHK("rast.scissor", rast.scissor, NXRS_GET(w2, NXRS_R_SCISSOR, 1), D3DRS_SCISSORTESTENABLE);
    CHK("rast.clip_plane_enable", rast.clip_plane_enable, NXRS_GET(w2, NXRS_R_CLIP, 8), D3DRS_CLIPPLANEENABLE);
    CHK("rast.sprite_coord_enable", rast.sprite_coord_enable != 0, NXRS_GET(w2, NXRS_R_POINTSPRITE, 1), D3DRS_POINTSPRITEENABLE);
    CHKF("rast.point_size", rast.point_size, dyn.pointSize, D3DRS_POINTSIZE, D3DRS_POINTSIZE_MIN, D3DRS_POINTSIZE_MAX);
    if (rast.offset_tri) CHKF("rast.offset_scale", rast.offset_scale, dyn.depthBiasSlope, D3DRS_SLOPESCALEDEPTHBIAS);
    if (rast.offset_tri && isfinite(rast.offset_units) && rast.offset_units != 0) {
        g_bias_ratio_sum += dyn.depthBiasUnits / rast.offset_units; g_bias_n++;
    }
    CHK("rast.flatshade_first", rast.flatshade_first, 1, D3DRS_SHADEMODE);   /* contract: provoking = first */
    CHK("rast.clip_halfz", rast.clip_halfz, 1, D3DRS_ZENABLE);               /* contract: depth range [0,1] */

    /* blend */
    struct pipe_blend_state bl;
    nine_convert_blend_state(&bl, rs);
    uint64_t w0 = st.w[0];
    CHK("blend.enable", bl.rt[0].blend_enable, NXRS_GET(w0, NXRS_B_ENABLE, 1), D3DRS_ALPHABLENDENABLE);
    if (bl.rt[0].blend_enable) {
        CHK("blend.rgb_func", bl.rt[0].rgb_func, NXRS_GET(w0, NXRS_B_OP, 3), D3DRS_BLENDOP);
        CHK("blend.rgb_src", bl.rt[0].rgb_src_factor, BF[NXRS_GET(w0, NXRS_B_SRC, 5)], D3DRS_SRCBLEND);
        CHK("blend.rgb_dst", bl.rt[0].rgb_dst_factor, BF[NXRS_GET(w0, NXRS_B_DST, 5)], D3DRS_SRCBLEND, D3DRS_DESTBLEND);
        CHK("blend.alpha_func", bl.rt[0].alpha_func, NXRS_GET(w0, NXRS_B_OPA, 3), D3DRS_SEPARATEALPHABLENDENABLE, D3DRS_BLENDOP, D3DRS_BLENDOPALPHA);
        CHK("blend.alpha_src", bl.rt[0].alpha_src_factor, BF[NXRS_GET(w0, NXRS_B_SRCA, 5)], D3DRS_SEPARATEALPHABLENDENABLE, D3DRS_SRCBLEND, D3DRS_SRCBLENDALPHA);
        CHK("blend.alpha_dst", bl.rt[0].alpha_dst_factor, BF[NXRS_GET(w0, NXRS_B_DSTA, 5)], D3DRS_SEPARATEALPHABLENDENABLE, D3DRS_SRCBLEND, D3DRS_DESTBLEND, D3DRS_SRCBLENDALPHA, D3DRS_DESTBLENDALPHA);
    }
    for (int rt = 0; rt < 4; rt++) {
        unsigned nine_mask = bl.independent_blend_enable ? bl.rt[rt].colormask : bl.rt[0].colormask;
        static const char* nm[4] = {"blend.colormask0", "blend.colormask1", "blend.colormask2", "blend.colormask3"};
        CHK(nm[rt], nine_mask & 15, NXRS_GET(w0, NXRS_B_MASK(rt), 4), D3DRS_COLORWRITEENABLE, D3DRS_COLORWRITEENABLE1 + rt - 1 + (rt == 0));
    }
    {   /* nine_state.c: d3dcolor_to_rgba(blend_color.color, rs[D3DRS_BLENDFACTOR]) */
        DWORD c = rs[D3DRS_BLENDFACTOR];
        float n[4] = {((c >> 16) & 0xFF) / 255.0f, ((c >> 8) & 0xFF) / 255.0f, (c & 0xFF) / 255.0f, ((c >> 24) & 0xFF) / 255.0f};
        CHKF("blend.color.r", n[0], dyn.blendConstant[0], D3DRS_BLENDFACTOR);
        CHKF("blend.color.a", n[3], dyn.blendConstant[3], D3DRS_BLENDFACTOR);
    }

    /* pixel-shader fog key (ps 1.x/2.x; ps 3 and FF have their own paths) */
    if (ps_version && ps_version < 0x0300) {
        unsigned nine_fog;  /* shader_add_ps_fog_stage: mode via FOGTABLEMODE, 'else' branch = vertex fog */
        if (!rs[D3DRS_FOGENABLE]) nine_fog = NxFog_None;
        else switch (rs[D3DRS_FOGTABLEMODE]) {
            case D3DFOG_LINEAR: nine_fog = NxFog_Linear; break;
            case D3DFOG_EXP: nine_fog = NxFog_Exp; break;
            case D3DFOG_EXP2: nine_fog = NxFog_Exp2; break;
            default: nine_fog = NxFog_Vertex; break;
        }
        CHK("ps.fog_mode", nine_fog, (sb >> NXSB_FOG) & 7, D3DRS_FOGENABLE, D3DRS_FOGTABLEMODE);
        float nend = *(float*)&rs[D3DRS_FOGEND], nscale = 1.0f / (*(float*)&rs[D3DRS_FOGEND] - *(float*)&rs[D3DRS_FOGSTART]);
        if (nine_fog == NxFog_Linear) { CHKF("fog.end", nend, dyn.fogEnd, D3DRS_FOGEND); CHKF("fog.scale", nscale, dyn.fogScale, D3DRS_FOGSTART, D3DRS_FOGEND); }
        if (nine_fog == NxFog_Exp || nine_fog == NxFog_Exp2) CHKF("fog.density", *(float*)&rs[D3DRS_FOGDENSITY], dyn.fogDensity, D3DRS_FOGDENSITY);
    } else if (ps_version >= 0x0300) {
        CHK("ps3.fog_mode", NxFog_None, (sb >> NXSB_FOG) & 7, D3DRS_FOGENABLE);   /* nine_shader.c: ps < 3 only */
    }

    /* sampler (+ update_sampler_derived) */
    NxD3D9Texture tex; memset(&tex, 0, sizeof tex);
    tex.format = D3DFMT_A8R8G8B8; tex.cube = rnd() % 4 == 0; tex.shadow = rnd() % 6 == 0; tex.managed_lod = rnd() % 4 ? 0 : rnd() % 12;
    ss[NINED3DSAMP_SHADOW] = tex.shadow; ss[NINED3DSAMP_CUBETEX] = tex.cube;
    if (ss[D3DSAMP_MIPFILTER] != D3DTEXF_NONE) {
        int lod = (int)ss[D3DSAMP_MAXMIPLEVEL] - (int)tex.managed_lod;
        if (lod < 0) lod = 0;
        ss[NINED3DSAMP_MINLOD] = lod;
    }
    nine_convert_sampler_state(NULL, 0, ss);
    NxSamplerState so; nx_d3d9_sampler((const uint32_t*)ss, &tex, &so);
    const struct pipe_sampler_state* p = &g_samp;
    CHKS("samp.wrap_s", p->wrap_s, WRAP[so.wrap & 7], D3DSAMP_ADDRESSU);
    CHKS("samp.wrap_t", p->wrap_t, WRAP[(so.wrap >> 3) & 7], D3DSAMP_ADDRESSV);
    CHKS("samp.wrap_r", p->wrap_r, WRAP[(so.wrap >> 6) & 7], D3DSAMP_ADDRESSW);
    CHKS("samp.min_img_filter", p->min_img_filter, (so.filter & NXSMP_MIN_LINEAR) ? PIPE_TEX_FILTER_LINEAR : PIPE_TEX_FILTER_NEAREST, D3DSAMP_MINFILTER);
    CHKS("samp.mag_img_filter", p->mag_img_filter, (so.filter & NXSMP_MAG_LINEAR) ? PIPE_TEX_FILTER_LINEAR : PIPE_TEX_FILTER_NEAREST, D3DSAMP_MAGFILTER);
    {
        unsigned m = (so.filter >> NXSMP_MIP_SHIFT) & 3;
        unsigned s6n = m == NxMip_None ? PIPE_TEX_MIPFILTER_NONE : m == NxMip_Nearest ? PIPE_TEX_MIPFILTER_NEAREST : PIPE_TEX_MIPFILTER_LINEAR;
        CHKS("samp.min_mip_filter", p->min_mip_filter, s6n, D3DSAMP_MIPFILTER);
    }
    CHKS("samp.lod_bias", p->lod_bias, so.lodBias, D3DSAMP_MIPFILTER, D3DSAMP_MIPMAPLODBIAS);
    /* Nine min_lod is unclamped above; any min_lod >= the last level samples the last level: compare clamped */
    CHKS("samp.min_lod", p->min_lod > 15 ? 15 : p->min_lod, so.minLod, D3DSAMP_MIPFILTER, D3DSAMP_MAXMIPLEVEL);
    CHKS("samp.max_lod", p->max_lod, so.maxLod, D3DSAMP_MIPFILTER);
    /* Nine's pipe field is 5 bits (values >= 32 wrap there); D3D9 caps MaxAnisotropy at 16: compare below 32 */
    if (ss[D3DSAMP_MAXANISOTROPY] < 32) CHKS("samp.max_anisotropy", p->max_anisotropy, so.maxAniso, D3DSAMP_MINFILTER, D3DSAMP_MAGFILTER, D3DSAMP_MAXANISOTROPY);
    CHKS("samp.compare_mode", p->compare_mode, (so.filter & NXSMP_SHADOW) ? PIPE_TEX_COMPARE_R_TO_TEXTURE : PIPE_TEX_COMPARE_NONE, D3DSAMP_MINFILTER);
    {
        uint32_t b = so.border;
        CHKS("samp.border.r", p->border_color.f[0], (b & 0xFF) / 255.0f, D3DSAMP_BORDERCOLOR);
        CHKS("samp.border.g", p->border_color.f[1], ((b >> 8) & 0xFF) / 255.0f, D3DSAMP_BORDERCOLOR);
        CHKS("samp.border.b", p->border_color.f[2], ((b >> 16) & 0xFF) / 255.0f, D3DSAMP_BORDERCOLOR);
        CHKS("samp.border.a", p->border_color.f[3], ((b >> 24) & 0xFF) / 255.0f, D3DSAMP_BORDERCOLOR);
    }
}

int main(int argc, char** argv) {
    long n = 200000; uint64_t seed = 1; const char* repro = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-n") && i + 1 < argc) n = atol(argv[++i]);
        else if (!strcmp(argv[i], "-s") && i + 1 < argc) seed = strtoull(argv[++i], 0, 0);
        else if (!strcmp(argv[i], "-v")) g_verbose = 1;
        else if (!strcmp(argv[i], "-r") && i + 1 < argc) repro = argv[++i];
    }
    g_rng = seed * 0x9E3779B97F4A7C15ull | 1;
    struct NineDevice9* dev = v6n_fake_device(0);   /* softpipe: no PIPE_CAP_POLYGON_OFFSET_UNITS_UNSCALED */
    for (long i = 0; i < n; i++) one_case(dev);
    long bad = 0;
    for (int i = 0; i < g_nf; i++) bad += g_f[i].n;
    printf("statediff: %ld cases, %ld field checks, %d mismatching fields (%ld mismatches)\n", n, g_checks, g_nf, bad);
    FILE* rf = repro ? fopen(repro, "w") : NULL;
    for (int i = 0; i < g_nf; i++) {
        printf("  %-26s %8ld  e.g. %s\n", g_f[i].name, g_f[i].n, g_f[i].repro);
        if (rf) fprintf(rf, "%s\n", g_f[i].repro);
    }
    if (rf) fclose(rf);
    if (g_bias_n) printf("  (deviation D1) mean s6n depthBiasUnits / nine-softpipe offset_units = %.3f over %ld cases\n",
                         g_bias_ratio_sum / g_bias_n, g_bias_n);
    return g_nf ? 1 : 0;
}
