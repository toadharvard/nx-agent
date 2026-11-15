/* fnv-nx L6 front: sampler state translation. Rules and out-of-range results: d3d9_state.h. */
#include "front_int.h"

/* D3DTEXTUREADDRESS -> NxWrap for one axis. Out of range -> REPEAT, tested before the cube-map clamp, in this order, as
 * wined3d get_texture_address_mode does; an enumerator on a cube map -> CLAMP. */
static uint32_t wrap_mode(uint32_t a, int cube) {
#define NXF_WRAP(mode) (cube ? NxWrap_Clamp : (mode))
    uint32_t r = NxWrap_Repeat;
    if (!nxd3d_in_D3DTEXTUREADDRESS(a)) return r;
    switch ((NxD3DTEXTUREADDRESS)a) {
    case NX_D3DTADDRESS_WRAP: r = NXF_WRAP(NxWrap_Repeat); break;
    case NX_D3DTADDRESS_MIRROR: r = NXF_WRAP(NxWrap_Mirror); break;
    case NX_D3DTADDRESS_CLAMP: r = NXF_WRAP(NxWrap_Clamp); break;
    case NX_D3DTADDRESS_BORDER: r = NXF_WRAP(NxWrap_Border); break;
    case NX_D3DTADDRESS_MIRRORONCE: r = NXF_WRAP(NxWrap_MirrorOnce); break;
    case NX_D3DTADDRESS_FORCE_DWORD: break;
    }
#undef NXF_WRAP
    return r;
}

/* What the sampler translation needs from one D3DTEXTUREFILTERTYPE value. Out of range -> LINEAR, no anisotropy, linear mip
 * (wined3d sampler_desc_from_sampler_states: min(max(v, POINT), LINEAR) with v above LINEAR). */
typedef struct { uint8_t point, aniso; uint8_t mip; } FilterClass;
static FilterClass filter_class(uint32_t f) {
    FilterClass r = {0, 0, NxMip_Linear};
    if (!nxd3d_in_D3DTEXTUREFILTERTYPE(f)) return r;
    switch ((NxD3DTEXTUREFILTERTYPE)f) {
    case NX_D3DTEXF_NONE: r.mip = NxMip_None; break;
    case NX_D3DTEXF_POINT: r.point = 1; r.mip = NxMip_Nearest; break;
    case NX_D3DTEXF_LINEAR: break;
    case NX_D3DTEXF_ANISOTROPIC: r.aniso = 1; break;
    case NX_D3DTEXF_FLATCUBIC: break;
    case NX_D3DTEXF_GAUSSIANCUBIC: break;
    case NX_D3DTEXF_PYRAMIDALQUAD: break;
    case NX_D3DTEXF_GAUSSIANQUAD: break;
    case NX_D3DTEXF_CONVOLUTIONMONO: break;
    case NX_D3DTEXF_FORCE_DWORD: break;
    }
    return r;
}

void nx_d3d9_sampler(const uint32_t* s, const NxD3D9Texture* tex, NxSamplerState* o) {
    static const NxD3D9Texture none = {0, 0, 0, 0, 0};
    if (!tex) tex = &none;
    memset(o, 0, sizeof *o);
    uint32_t f = 0;
    FilterClass mip = filter_class(NXF_SAMP(s, MIPFILTER)), mag = filter_class(NXF_SAMP(s, MAGFILTER)),
                min = filter_class(NXF_SAMP(s, MINFILTER));
    if (mip.mip != NxMip_None) {
        o->lodBias = nxf_asfloat(NXF_SAMP(s, MIPMAPLODBIAS));
        uint32_t top = NXF_SAMP(s, MAXMIPLEVEL);   /* a DWORD: unsigned, as wined3d (min(max(top, lod), levels - 1)) */
        uint32_t lod = top > tex->managed_lod ? top - tex->managed_lod : 0;
        o->minLod = (uint8_t)(lod > 15 ? 15 : lod);
        f |= (uint32_t)mip.mip << NXSMP_MIP_SHIFT;
    }
    o->maxLod = 15;
    o->wrap = (uint16_t)(wrap_mode(NXF_SAMP(s, ADDRESSU), tex->cube) | wrap_mode(NXF_SAMP(s, ADDRESSV), tex->cube) << 3 |
                         wrap_mode(NXF_SAMP(s, ADDRESSW), tex->cube) << 6);
    if (!(min.point && !tex->shadow)) f |= NXSMP_MIN_LINEAR;
    if (!(mag.point && !tex->shadow)) f |= NXSMP_MAG_LINEAR;
    if (min.aniso || mag.aniso) {
        uint32_t a = NXF_SAMP(s, MAXANISOTROPY);
        o->maxAniso = (uint8_t)(a > 255 ? 255 : a);
    }
    if (tex->shadow) f |= NXSMP_SHADOW;
    if (NXF_SAMP(s, SRGBTEXTURE) && (nx_d3d9_format(tex->format).flags & NXFMT_SRGB)) f |= NXSMP_SRGB;
    o->filter = (uint8_t)f;
    uint32_t c = NXF_SAMP(s, BORDERCOLOR);   /* ARGB -> RGBA8 */
    o->border = ((c >> 16) & 0xFF) | (c & 0xFF00) | (c & 0xFF) << 16 | (c & 0xFF000000u);
}
