/* fnv-nx L6 front: the D3DFORMAT table. One row per enumerator of NxD3DFORMAT (the spec's D3DFORMAT plus the vendor FOURCCs
 * of gen_enums.py), in one switch without a default, built with -Werror=switch-enum. A DWORD that is no enumerator is
 * WINED3DFMT_UNKNOWN in wined3d (wined3dformat_from_d3dformat, get_format_idx): no native format. An enumerator whose row
 * says NxFmt_None has no native layout in the backend contract (state.h); it is not a default of this function. */
#include "front_int.h"

#define C_ NXFMT_COMPRESSED
#define Z_ NXFMT_DEPTH
#define ZS_ (NXFMT_DEPTH | NXFMT_STENCIL)
#define SR_ NXFMT_SRGB
#define RT_ NXFMT_RENDER
#define B_DEFAULT 8388608.0f   /* 2^23: float depth and every format that is no fixed-point depth format (Nine) */

/* R: a row; fmt, view swizzle, flags, native bytes, source bytes, depth-bias scale, conversion */
#define ROW(f, s0, s1, s2, s3, fl, by, src, bs, cv) do { \
    r.info.format = (f); r.info.swizzle[0] = NxSwz_##s0; r.info.swizzle[1] = NxSwz_##s1; r.info.swizzle[2] = NxSwz_##s2; \
    r.info.swizzle[3] = NxSwz_##s3; r.info.flags = (uint8_t)(fl); r.info.bytes = (by); r.info.srcBytes = (src); \
    r.bias = (bs); r.conv = (cv); } while (0)
/* a native row (source bytes = native bytes) and a row with a texel conversion (the CONVERT flag) */
#define NAT(f, s0, s1, s2, s3, fl, by) ROW(f, s0, s1, s2, s3, fl, by, by, B_DEFAULT, NXCONV_NONE)
#define CNV(f, s0, s1, s2, s3, fl, by, src, cv) ROW(f, s0, s1, s2, s3, (fl) | NXFMT_CONVERT, by, src, B_DEFAULT, cv)
#define DEP(f, s0, s1, s2, s3, fl, by, bs) ROW(f, s0, s1, s2, s3, fl, by, by, bs, NXCONV_NONE)
#define NONE() ROW(NxFmt_None, R, G, B, A, 0, 0, 0, B_DEFAULT, NXCONV_NONE)

NxFmtRow nxfront_fmt_row(uint32_t d) {
    NxFmtRow r;
    NONE();                                  /* out of range (WINED3DFMT_UNKNOWN) */
    if (!nxd3d_in_D3DFORMAT(d)) return r;
    switch ((NxD3DFORMAT)d) {
    case NX_D3DFMT_A8R8G8B8: NAT(NxFmt_BGRA8, R, G, B, A, SR_ | RT_, 4); break;
    case NX_D3DFMT_X8R8G8B8: NAT(NxFmt_BGRX8, R, G, B, 1, SR_ | RT_, 4); break;
    case NX_D3DFMT_A8B8G8R8: NAT(NxFmt_RGBA8, R, G, B, A, SR_ | RT_, 4); break;
    case NX_D3DFMT_X8B8G8R8: NAT(NxFmt_RGBX8, R, G, B, 1, SR_ | RT_, 4); break;
    case NX_D3DFMT_R5G6B5: NAT(NxFmt_R5G6B5, R, G, B, 1, RT_, 2); break;
    case NX_D3DFMT_X1R5G5B5: NAT(NxFmt_X1R5G5B5, R, G, B, 1, RT_, 2); break;
    case NX_D3DFMT_A1R5G5B5: NAT(NxFmt_A1R5G5B5, R, G, B, A, RT_, 2); break;
    case NX_D3DFMT_A4R4G4B4: NAT(NxFmt_A4R4G4B4, R, G, B, A, 0, 2); break;
    case NX_D3DFMT_X4R4G4B4: NAT(NxFmt_A4R4G4B4, R, G, B, 1, 0, 2); break;
    case NX_D3DFMT_A2B10G10R10: NAT(NxFmt_RGB10A2, R, G, B, A, RT_, 4); break;
    case NX_D3DFMT_A2R10G10B10: NAT(NxFmt_BGR10A2, R, G, B, A, RT_, 4); break;               /* GL_RGB10_A2 read as GL_BGRA / 2_10_10_10_REV: the D3D bits exactly */
    case NX_D3DFMT_G16R16: NAT(NxFmt_RG16, R, G, 1, 1, RT_, 4); break;
    case NX_D3DFMT_A16B16G16R16: NAT(NxFmt_RGBA16, R, G, B, A, RT_, 8); break;
    case NX_D3DFMT_A8: CNV(NxFmt_RGBA8, R, G, B, A, RT_, 4, 1, NXCONV_A8); break;              /* -> (0, 0, 0, A); target: write mask A (nx_d3d9_rt_rule) */
    case NX_D3DFMT_L8: CNV(NxFmt_RGBA8, R, R, R, 1, SR_ | RT_, 4, 1, NXCONV_L8); break;       /* sampled (L, L, L, 1) by the view swizzle; target: write mask R (nx_d3d9_rt_rule) */
    case NX_D3DFMT_A8L8: CNV(NxFmt_RGBA8, R, G, B, A, SR_, 4, 2, NXCONV_A8L8); break;         /* -> (L, L, L, A) */
    case NX_D3DFMT_L16: CNV(NxFmt_RGBA16, R, G, B, 1, 0, 8, 2, NXCONV_L16); break;            /* -> (L, L, L, 1) */
    case NX_D3DFMT_V8U8: NAT(NxFmt_RG8S, R, G, 1, 1, RT_, 2); break;
    case NX_D3DFMT_Q8W8V8U8: NAT(NxFmt_RGBA8S, R, G, B, A, RT_, 4); break;
    case NX_D3DFMT_V16U16: NAT(NxFmt_RG16S, R, G, 1, 1, RT_, 4); break;
    case NX_D3DFMT_Q16W16V16U16: NAT(NxFmt_RGBA16S, R, G, B, A, RT_, 8); break;
    case NX_D3DFMT_R16F: NAT(NxFmt_R16F, R, 1, 1, 1, RT_, 2); break;
    case NX_D3DFMT_G16R16F: NAT(NxFmt_RG16F, R, G, 1, 1, RT_, 4); break;
    case NX_D3DFMT_A16B16G16R16F: NAT(NxFmt_RGBA16F, R, G, B, A, RT_, 8); break;
    case NX_D3DFMT_R32F: NAT(NxFmt_R32F, R, 1, 1, 1, RT_, 4); break;
    case NX_D3DFMT_G32R32F: NAT(NxFmt_RG32F, R, G, 1, 1, RT_, 8); break;
    case NX_D3DFMT_A32B32G32R32F: NAT(NxFmt_RGBA32F, R, G, B, A, RT_, 16); break;
    case NX_D3DFMT_DXT1: NAT(NxFmt_BC1, R, G, B, A, C_ | SR_, 8); break;
    case NX_D3DFMT_DXT2: NAT(NxFmt_BC2, R, G, B, A, C_ | SR_, 16); break;
    case NX_D3DFMT_DXT3: NAT(NxFmt_BC2, R, G, B, A, C_ | SR_, 16); break;
    case NX_D3DFMT_DXT4: NAT(NxFmt_BC3, R, G, B, A, C_ | SR_, 16); break;
    case NX_D3DFMT_DXT5: NAT(NxFmt_BC3, R, G, B, A, C_ | SR_, 16); break;
    case NX_D3DFMT_ATI1: CNV(NxFmt_RGBA8, R, G, B, A, 0, 4, 8, NXCONV_ATI1); break;           /* BC4 decoded -> (R, 0, 0, 1) */
    case NX_D3DFMT_ATI2: NAT(NxFmt_BC5, G, R, 1, 1, C_, 16); break;                           /* basetexture9.c: Y,X,1,1 */
    case NX_D3DFMT_D16_LOCKABLE: DEP(NxFmt_Z16, R, R, R, R, Z_, 2, 65536.0f); break;
    case NX_D3DFMT_D16: DEP(NxFmt_Z16, R, R, R, R, Z_, 2, 65536.0f); break;
    case NX_D3DFMT_D24S8: DEP(NxFmt_Z24S8, R, R, R, R, ZS_, 4, 16777216.0f); break;
    case NX_D3DFMT_D24X8: DEP(NxFmt_Z24X8, R, R, R, R, Z_, 4, 16777216.0f); break;
    case NX_D3DFMT_D32F_LOCKABLE: NAT(NxFmt_Z32F, R, R, R, R, Z_, 4); break;
    case NX_D3DFMT_D24FS8: NAT(NxFmt_Z32FS8, R, R, R, R, ZS_, 8); break;
    case NX_D3DFMT_INTZ: DEP(NxFmt_Z24S8, R, R, R, R, ZS_, 4, 16777216.0f); break;
    case NX_D3DFMT_DF24: DEP(NxFmt_Z24X8, R, 0, 0, 1, Z_, 4, 16777216.0f); break;
    case NX_D3DFMT_DF16: DEP(NxFmt_Z16, R, 0, 0, 1, Z_, 2, 65536.0f); break;
    /* formats with a depth-bias scale but no native layout (deko3d has no Z32 unorm, D15S1, D24X4S4) */
    case NX_D3DFMT_D32: DEP(NxFmt_None, R, G, B, A, 0, 0, 4294967296.0f); break;
    case NX_D3DFMT_D15S1: DEP(NxFmt_None, R, G, B, A, 0, 0, 32768.0f); break;
    case NX_D3DFMT_D24X4S4: DEP(NxFmt_None, R, G, B, A, 0, 0, 16777216.0f); break;
    /* enumerators without a native layout: no row to fill (NONE above) */
    case NX_D3DFMT_UNKNOWN: break;
    case NX_D3DFMT_R8G8B8: CNV(NxFmt_BGRX8, R, G, B, 1, SR_ | RT_, 4, 3, NXCONV_R8G8B8); break;   /* bytes B,G,R -> B,G,R,x */
    case NX_D3DFMT_R3G3B2: break;
    case NX_D3DFMT_A8R3G3B2: break;
    case NX_D3DFMT_A8P8: break;
    case NX_D3DFMT_P8: break;
    case NX_D3DFMT_A4L4: break;
    case NX_D3DFMT_L6V5U5: CNV(NxFmt_RGBA32F, R, G, B, 1, 0, 16, 2, NXCONV_L6V5U5); break;      /* exact (U, V, L) floats, alpha = the side word of front_int.h */
    case NX_D3DFMT_X8L8V8U8: CNV(NxFmt_RGBA32F, R, G, B, 1, 0, 16, 4, NXCONV_X8L8V8U8); break;
    case NX_D3DFMT_A2W10V10U10: break;
    case NX_D3DFMT_UYVY: break;
    case NX_D3DFMT_YUY2: break;
    case NX_D3DFMT_MULTI2_ARGB8: break;
    case NX_D3DFMT_G8R8_G8B8: break;
    case NX_D3DFMT_R8G8_B8G8: break;
    case NX_D3DFMT_D32_LOCKABLE: break;
    case NX_D3DFMT_S8_LOCKABLE: break;
    case NX_D3DFMT_VERTEXDATA: break;
    case NX_D3DFMT_INDEX16: NAT(NxFmt_R16, R, 1, 1, 1, RT_, 2); break;                         /* R16_UINT: the bits are the texel (storage; a draw reads v / 65535) */
    case NX_D3DFMT_INDEX32: NAT(NxFmt_R32F, R, 1, 1, 1, RT_, 4); break;                        /* R32_UINT: the bits are the texel (storage; a draw reads them as a float) */
    case NX_D3DFMT_CxV8U8: break;
    case NX_D3DFMT_A1: break;
    case NX_D3DFMT_A2B10G10R10_XR_BIAS: break;
    case NX_D3DFMT_BINARYBUFFER: break;
    case NX_D3DFMT_FORCE_DWORD: break;
    case NX_D3DFMT_YV12: break;
    case NX_D3DFMT_INST: break;
    case NX_D3DFMT_NVDB: break;
    case NX_D3DFMT_NVHU: break;
    case NX_D3DFMT_NVHS: break;
    case NX_D3DFMT_RESZ: break;
    case NX_D3DFMT_NULL: ROW(NxFmt_None, R, G, B, A, NXFMT_NULL, 0, 0, B_DEFAULT, NXCONV_NONE); break;   /* a target without storage (wined3d WINED3DFMT_NULL) */
    case NX_D3DFMT_R16: break;
    case NX_D3DFMT_AL16: break;
    case NX_D3DFMT_NV12: break;
    case NX_D3DFMT_ATOC: break;
    }
    return r;
}

NxFormatInfo nx_d3d9_format(uint32_t d) { return nxfront_fmt_row(d).info; }
