/* fnv-nx L6 front: shader bits (alpha test, fog), dynamic values, the combined translation, latch and defaults.
 * Rules and out-of-range results: d3d9_state.h. */
#include "front_int.h"

/* D3DFOGMODE -> NxFog for the fog table mode; out of range -> no fog (shader.c find_ps_compile_args: args->fog stays OFF).
 * FOG_NONE in the table mode means the vertex fog value (Nine). */
static uint32_t fog_mode(uint32_t d) {
    uint32_t r = NxFog_None;
    if (!nxd3d_in_D3DFOGMODE(d)) return r;
    switch ((NxD3DFOGMODE)d) {
    case NX_D3DFOG_NONE: r = NxFog_Vertex; break;
    case NX_D3DFOG_EXP: r = NxFog_Exp; break;
    case NX_D3DFOG_EXP2: r = NxFog_Exp2; break;
    case NX_D3DFOG_LINEAR: r = NxFog_Linear; break;
    case NX_D3DFOG_FORCE_DWORD: break;
    }
    return r;
}

uint32_t nx_d3d9_shader_bits(const uint32_t* rs, uint32_t ps_version) {
    uint32_t b = 0;
    if (NXF_RS(rs, ALPHATESTENABLE))   /* out of range alpha func: ALWAYS (wined3d_sanitize_cmp_func) */
        b |= 1u << NXSB_ALPHATEST | nxfront_cmp(NXF_RS(rs, ALPHAFUNC), NxCmp_Always) << NXSB_ALPHAFUNC;
    if (NXF_RS(rs, FOGENABLE) && (ps_version & 0xFFFF) < 0x0300) b |= fog_mode(NXF_RS(rs, FOGTABLEMODE)) << NXSB_FOG;
    return b;
}

void nx_d3d9_color(uint32_t c, float rgba[4]) {
    rgba[0] = (float)((c >> 16) & 0xFF) / 0xFF;
    rgba[1] = (float)((c >> 8) & 0xFF) / 0xFF;
    rgba[2] = (float)(c & 0xFF) / 0xFF;
    rgba[3] = (float)((c >> 24) & 0xFF) / 0xFF;
}

float nx_d3d9_bias_scale(uint32_t f) { return nxfront_fmt_row(f).bias; }

void nx_d3d9_viewport(const NxD3D9Viewport* vp, NxViewport* o) {
    o->x = (float)vp->X + 0.5f;
    o->y = (float)vp->Y + 0.5f;
    o->w = (float)vp->Width;
    o->h = (float)vp->Height;
    o->minZ = vp->MinZ;
    o->maxZ = vp->MaxZ;
}

void nx_d3d9_scissor(const NxD3D9Rect* r, NxScissor* o) {
    o->x = r->left;
    o->y = r->top;
    o->w = r->right > r->left ? r->right - r->left : 0;
    o->h = r->bottom > r->top ? r->bottom - r->top : 0;
}

#define NX_CLAMP(X, MIN, MAX) ((X) > (MIN) ? ((X) > (MAX) ? (MAX) : (X)) : (MIN))   /* Mesa's CLAMP */

void nx_d3d9_translate(const NxD3D9Input* in, NxRenderState* st, NxDynamicState* dyn, uint32_t* sb) {
    const uint32_t* rs = in->rs;
    if (st) {
        st->w[0] = nx_d3d9_blend_word(rs);
        st->w[1] = nx_d3d9_depth_stencil_word(rs, in->latch);
        st->w[2] = nx_d3d9_raster_word(rs, in->latch);
        st->w[3] = 0;
    }
    if (sb) *sb = nx_d3d9_shader_bits(rs, in->ps_version);
    if (!dyn) return;
    memset(dyn, 0, sizeof *dyn);
    nx_d3d9_color(NXF_RS(rs, BLENDFACTOR), dyn->blendConstant);
    dyn->depthBiasD3D = nxf_asfloat(NXF_RS(rs, DEPTHBIAS));
    dyn->depthBiasUnits = dyn->depthBiasD3D * nx_d3d9_bias_scale(in->depth_format);
    dyn->depthBiasSlope = nxf_asfloat(NXF_RS(rs, SLOPESCALEDEPTHBIAS));
    dyn->alphaRef = (float)NXF_RS(rs, ALPHAREF) / 255.0f;
    nx_d3d9_color(NXF_RS(rs, FOGCOLOR), dyn->fogColor);
    dyn->fogEnd = nxf_asfloat(NXF_RS(rs, FOGEND));
    dyn->fogScale = 1.0f / (nxf_asfloat(NXF_RS(rs, FOGEND)) - nxf_asfloat(NXF_RS(rs, FOGSTART)));
    dyn->fogDensity = nxf_asfloat(NXF_RS(rs, FOGDENSITY));
    dyn->pointSize = NX_CLAMP(nxf_asfloat(NXF_RS(rs, POINTSIZE)), nxf_asfloat(NXF_RS(rs, POINTSIZE_MIN)),
                              nxf_asfloat(NXF_RS(rs, POINTSIZE_MAX)));
    dyn->stencilRef = NXF_RS(rs, STENCILREF) & 0xFF;
    dyn->stencilReadMask = NXF_RS(rs, STENCILMASK) & 0xFF;
    dyn->stencilWriteMask = NXF_RS(rs, STENCILWRITEMASK) & 0xFF;
    nx_d3d9_viewport(&in->viewport, &dyn->viewport);
    nx_d3d9_scissor(&in->scissor, &dyn->scissor);
    if (in->clip_planes) memcpy(dyn->clipPlane, in->clip_planes, sizeof dyn->clipPlane);
}

/* ---- defaults (IDirect3DDevice9 after Reset) -------------------------------------------------------------------- */
void nx_d3d9_default_rs(uint32_t rs[NXD3D_RS_COUNT]) {
    memset(rs, 0, NXD3D_RS_COUNT * sizeof *rs);
    NXF_RS(rs, ZENABLE) = NX_D3DZB_FALSE;   /* Nine: no auto depth stencil */
    NXF_RS(rs, FILLMODE) = NX_D3DFILL_SOLID;      NXF_RS(rs, SHADEMODE) = NX_D3DSHADE_GOURAUD;
    NXF_RS(rs, ZWRITEENABLE) = 1;                 NXF_RS(rs, LASTPIXEL) = 1;
    NXF_RS(rs, SRCBLEND) = NX_D3DBLEND_ONE;       NXF_RS(rs, DESTBLEND) = NX_D3DBLEND_ZERO;
    NXF_RS(rs, CULLMODE) = NX_D3DCULL_CCW;        NXF_RS(rs, ZFUNC) = NX_D3DCMP_LESSEQUAL;
    NXF_RS(rs, ALPHAFUNC) = NX_D3DCMP_ALWAYS;
    NXF_RS(rs, FOGEND) = 0x3F800000;              NXF_RS(rs, FOGDENSITY) = 0x3F800000;
    NXF_RS(rs, STENCILFAIL) = NX_D3DSTENCILOP_KEEP;  NXF_RS(rs, STENCILZFAIL) = NX_D3DSTENCILOP_KEEP;
    NXF_RS(rs, STENCILPASS) = NX_D3DSTENCILOP_KEEP;
    NXF_RS(rs, STENCILMASK) = 0xFFFFFFFF;         NXF_RS(rs, STENCILFUNC) = NX_D3DCMP_ALWAYS;
    NXF_RS(rs, STENCILWRITEMASK) = 0xFFFFFFFF;    NXF_RS(rs, TEXTUREFACTOR) = 0xFFFFFFFF;
    NXF_RS(rs, CLIPPING) = 1;                     NXF_RS(rs, LIGHTING) = 1;
    NXF_RS(rs, COLORVERTEX) = 1;                  NXF_RS(rs, LOCALVIEWER) = 1;
    NXF_RS(rs, DIFFUSEMATERIALSOURCE) = 1;        NXF_RS(rs, SPECULARMATERIALSOURCE) = 2;
    NXF_RS(rs, POINTSIZE) = 0x3F800000;           NXF_RS(rs, POINTSIZE_MIN) = 0x3F800000;
    NXF_RS(rs, POINTSCALE_A) = 0x3F800000;        NXF_RS(rs, MULTISAMPLEANTIALIAS) = 1;
    NXF_RS(rs, MULTISAMPLEMASK) = 0xFFFFFFFF;     NXF_RS(rs, DEBUGMONITORTOKEN) = 0xDEADCAFE;
    NXF_RS(rs, POINTSIZE_MAX) = 0x3F800000;       NXF_RS(rs, COLORWRITEENABLE) = 0xF;
    NXF_RS(rs, BLENDOP) = NX_D3DBLENDOP_ADD;      NXF_RS(rs, POSITIONDEGREE) = 3;
    NXF_RS(rs, NORMALDEGREE) = 1;                 NXF_RS(rs, MINTESSELLATIONLEVEL) = 0x3F800000;
    NXF_RS(rs, MAXTESSELLATIONLEVEL) = 0x3F800000;   NXF_RS(rs, ADAPTIVETESS_Z) = 0x3F800000;
    NXF_RS(rs, CCW_STENCILFAIL) = NX_D3DSTENCILOP_KEEP;  NXF_RS(rs, CCW_STENCILZFAIL) = NX_D3DSTENCILOP_KEEP;
    NXF_RS(rs, CCW_STENCILPASS) = NX_D3DSTENCILOP_KEEP;  NXF_RS(rs, CCW_STENCILFUNC) = NX_D3DCMP_ALWAYS;
    NXF_RS(rs, COLORWRITEENABLE1) = 0xF;          NXF_RS(rs, COLORWRITEENABLE2) = 0xF;
    NXF_RS(rs, COLORWRITEENABLE3) = 0xF;          NXF_RS(rs, BLENDFACTOR) = 0xFFFFFFFF;
    NXF_RS(rs, SRCBLENDALPHA) = NX_D3DBLEND_ONE;  NXF_RS(rs, DESTBLENDALPHA) = NX_D3DBLEND_ZERO;
    NXF_RS(rs, BLENDOPALPHA) = NX_D3DBLENDOP_ADD;
}

void nx_d3d9_default_samp(uint32_t s[NXD3D_SAMP_COUNT]) {
    memset(s, 0, NXD3D_SAMP_COUNT * sizeof *s);
    NXF_SAMP(s, ADDRESSU) = NX_D3DTADDRESS_WRAP;  NXF_SAMP(s, ADDRESSV) = NX_D3DTADDRESS_WRAP;
    NXF_SAMP(s, ADDRESSW) = NX_D3DTADDRESS_WRAP;
    NXF_SAMP(s, MAGFILTER) = NX_D3DTEXF_POINT;    NXF_SAMP(s, MINFILTER) = NX_D3DTEXF_POINT;
    NXF_SAMP(s, MIPFILTER) = NX_D3DTEXF_NONE;     NXF_SAMP(s, MAXANISOTROPY) = 1;
}

/* The latch starts as the GL state the D3D9 defaults give (a valid value of each of the four states). */
void nx_d3d9_latch_init(NxD3D9Latch* latch) {
    uint32_t rs[NXD3D_RS_COUNT];
    nx_d3d9_default_rs(rs);
    memset(latch, 0, sizeof *latch);
    nx_d3d9_depth_stencil_word(rs, latch);
    nx_d3d9_raster_word(rs, latch);
}
