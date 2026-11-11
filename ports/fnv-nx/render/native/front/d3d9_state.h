/* fnv-nx native renderer: D3D9 state -> native state translator (stream S6n).
 *
 * Pure functions: C99, no globals, no allocation, no I/O. The semantics mirror Gallium Nine (Mesa 20.1,
 * src/gallium/state_trackers/nine: nine_pipe.c/.h nine_convert_*_state, nine_state.c update_viewport /
 * update_sampler_derived / commit, basetexture9.c view swizzles, nine_shader.c PS fog stage). Every deliberate
 * difference is listed under "Deviations" below. Encodings of the outputs: nx/state.h.
 *
 * Usage:
 *     uint32_t rs[NXD3D_RS_COUNT];            // indexed by D3DRENDERSTATETYPE, as the device holds them
 *     NxD3D9Input in = {rs, ...};
 *     NxRenderState st; NxDynamicState dyn; uint32_t sb;
 *     nx_d3d9_translate(&in, &st, &dyn, &sb);
 *     NxSamplerState ss; nx_d3d9_sampler(samp[i], &tex, &ss);   // samp: DWORD[14] by D3DSAMPLERSTATETYPE
 *     NxFormatInfo fi = nx_d3d9_format(fmt);
 * Floats held in DWORD render states (FOGSTART, DEPTHBIAS, POINTSIZE, ...) are IEEE bit patterns, as in D3D9.
 *
 * ---- Rules (each is a claim in render/native/CLAIMS.md and has a unit test in tests/s6n/) --------------------
 * Depth:   ZENABLE != 0 enables the test; func = ZFUNC (D3DCMP 1..8; 0 and values > 8 become NEVER).
 *          Write = ZWRITEENABLE != 0 and func not EQUAL/NEVER. ZENABLE == 0: all depth fields 0.
 * Stencil: STENCILENABLE != 0: front = STENCILFUNC/FAIL/ZFAIL/PASS; back = CCW_* when TWOSIDEDSTENCILMODE != 0,
 *          else a copy of front; masks = STENCILMASK, STENCILWRITEMASK, ref = STENCILREF (each & 0xFF), shared by
 *          both faces. Ops D3DSTENCILOP 1..8, other values become ZERO. Disabled: all stencil fields 0.
 * Blend:   ALPHABLENDENABLE != 0. Colour factors from SRCBLEND/DESTBLEND through the colour table, op BLENDOP.
 *          SEPARATEALPHABLENDENABLE != 0: alpha from SRCBLENDALPHA/DESTBLENDALPHA through the alpha table, op
 *          BLENDOPALPHA; else alpha factors = SRCBLEND/DESTBLEND through the alpha table, op = colour op.
 *          Fix-up: SRCBLEND = BOTHSRCALPHA (BOTHINVSRCALPHA) sets dst colour = INVSRCALPHA (SRCALPHA), and the
 *          same for dst alpha when not separate; otherwise, when separate and SRCBLENDALPHA is BOTH[INV]SRCALPHA,
 *          dst alpha gets the fix-up. Tables (D3DBLEND -> NxBlendFactor), colour / alpha where they differ:
 *          ZERO Zero, ONE One, SRCCOLOR SrcColor, INVSRCCOLOR InvSrcColor, SRCALPHA SrcAlpha, INVSRCALPHA
 *          InvSrcAlpha, DESTALPHA DstAlpha, INVDESTALPHA InvDstAlpha, DESTCOLOR DstColor, INVDESTCOLOR
 *          InvDstColor, SRCALPHASAT SrcAlphaSat, BOTHSRCALPHA SrcAlpha, BOTHINVSRCALPHA InvSrcAlpha,
 *          BLENDFACTOR ConstColor, INVBLENDFACTOR InvConstColor, SRCCOLOR2 Src1Color / One,
 *          INVSRCCOLOR2 InvSrc1Color / Zero, anything else Zero. Ops 1..5 -> Add..Max, else Add.
 *          Disabled: factors and ops 0. Write masks: COLORWRITEENABLE, COLORWRITEENABLE1..3 (& 0xF) for RT0..3.
 *          Blend constant = BLENDFACTOR (D3DCOLOR) as RGBA floats c/255.
 * Raster:  cull NONE(1) -> None, CW(2) -> CW, CCW(3) -> CCW, else None. Fill POINT(1), WIREFRAME(2),
 *          SOLID(3) and SOLID_ZERO(0)/other -> Solid. Bias flag = (DEPTHBIAS | SLOPESCALEDEPTHBIAS) != 0 as
 *          bit patterns. Scissor = SCISSORTESTENABLE != 0. sRGB = SRGBWRITEENABLE != 0. Flat = SHADEMODE == 1.
 *          Clip mask = CLIPPLANEENABLE & 0xFF. Point sprite = POINTSPRITEENABLE != 0.
 * Bias:    depthBiasSlope = SLOPESCALEDEPTHBIAS; depthBiasD3D = DEPTHBIAS; depthBiasUnits = DEPTHBIAS x 2^24 for
 *          D24S8, D24X8, D24X4S4, INTZ, DF24; 2^16 for D16, D16_LOCKABLE, DF16; 2^15 for D15S1; 2^32 for D32;
 *          2^23 for float depth (D32F_LOCKABLE, D24FS8), for no depth buffer (depth_format 0) and anything else.
 * Alpha:   test = ALPHATESTENABLE != 0, func = ALPHAFUNC (as ZFUNC), alphaRef = ALPHAREF / 255.0f.
 * Fog:     shader bits: FOGENABLE == 0 or ps_version >= 0x0300 -> None; else FOGTABLEMODE EXP(1) -> Exp, EXP2(2)
 *          -> Exp2, LINEAR(3) -> Linear, NONE(0)/other -> Vertex. fogColor = FOGCOLOR as RGBA (c/255),
 *          fogEnd = FOGEND, fogScale = 1/(FOGEND - FOGSTART), fogDensity = FOGDENSITY (always filled).
 *          Epilogue (render/native/shaders/d3d9_epilogue.glsl): z = gl_FragCoord.z / gl_FragCoord.w;
 *          linear f = sat((end - z) * scale); exp f = 2^(-(z*d)*log2 e); exp2 f = 2^(-(z*d)^2 * log2 e);
 *          vertex f = the vertex fog input; rgb = mix(fogColor.rgb, rgb, f), alpha unchanged. Fog runs before
 *          the alpha test.
 * Points:  pointSize = Mesa CLAMP(POINTSIZE, POINTSIZE_MIN, POINTSIZE_MAX) = x > min ? (x > max ? max : x) : min.
 * Viewport: D3DVIEWPORT9 (X, Y, Width, Height, MinZ, MaxZ) -> NxViewport x = X + 0.5, y = Y + 0.5, w = Width,
 *          h = Height, minZ, maxZ. The +0.5 makes a +0.5-centre rasterizer (GL, deko3d) cover exactly the pixels
 *          D3D9 / Nine (half_pixel_center = 0) cover; gl_FragCoord.xy is then D3D9 VPOS + 0.5.
 * Scissor: RECT (left, top, right, bottom) -> x = left, y = top, w = max(right - left, 0), h = max(bottom - top, 0).
 * Sampler: MIPFILTER NONE(0): mip None, lodBias 0, minLod 0. Otherwise mip = POINT(1) ? Nearest : Linear,
 *          lodBias = MIPMAPLODBIAS, minLod = max(MAXMIPLEVEL - managed_lod, 0) (clamped to 15). maxLod = 15.
 *          min/mag linear unless the filter is POINT(1) and the texture is not a shadow (depth) texture.
 *          maxAniso = MAXANISOTROPY when MINFILTER or MAGFILTER is ANISOTROPIC(3), else 0. Wrap per axis:
 *          WRAP(1) Repeat, MIRROR(2) Mirror, CLAMP(3) Clamp, BORDER(4) Border, MIRRORONCE(5) MirrorOnce, other
 *          Clamp; cube textures: Clamp on all axes. border = BORDERCOLOR ARGB -> RGBA8. SRGB flag =
 *          SRGBTEXTURE != 0 and the format has an sRGB view. SHADOW flag = shadow texture.
 * Format:  nx_d3d9_format(): see the table in d3d9_state.c (one line per D3DFORMAT, with the view swizzle Nine
 *          builds: unused channels read 1, except A8 (0,0,0,A), ATI1 (R,0,0,1), ATI2 (G,R,1,1)). Formats whose
 *          Nine format has channels our view swizzle would have to duplicate or invent (A8, L8, A8L8, L16, ATI1)
 *          or move (A2R10G10B10) are NXFMT_CONVERT: the texels are expanded by nx_d3d9_convert() so BORDER
 *          sampling returns Nine's border colour (V6n defect border_swizzle).
 *
 * ---- Deviations from Nine-on-softpipe (the verifier's oracle) -------------------------------------------------
 *  D1 depth bias: softpipe lacks POLYGON_OFFSET_UNITS_UNSCALED, so Nine there passes DEPTHBIAS x 2^23 and the draw
 *     module multiplies by 1/(2^24 - 1) for D24: an effective bias of ~DEPTHBIAS/2 depth units. We produce D3D9's
 *     DEPTHBIAS depth units (x 2^24 in GL/deko3d r units for D24).
 *  D2 (withdrawn): L8 and A8L8 are stored as RGBA8 since the border_swizzle fix and so have sRGB views like Nine.
 *  D3 D3DFMT_D32 (Z32 unorm) and other formats Nine maps but deko3d lacks are NxFmt_None.
 */
#ifndef FNVNX_NX_D3D9_STATE_H
#define FNVNX_NX_D3D9_STATE_H

#include <stdint.h>
#include "nx/state.h"

#ifdef __cplusplus
extern "C" {
#endif

#define NXD3D_RS_COUNT   210   /* D3DRS_BLENDOPALPHA + 1 */
#define NXD3D_SAMP_COUNT 14    /* D3DSAMP_DMAPOFFSET + 1 */
#define NXD3D_MAX_CLIP   6

/* D3DFORMAT FOURCCs used below (D3DFMT_* numeric values are the D3D9 ones). */
#define NXD3D_FOURCC(a, b, c, d) ((uint32_t)(uint8_t)(a) | (uint32_t)(uint8_t)(b) << 8 | \
                                  (uint32_t)(uint8_t)(c) << 16 | (uint32_t)(uint8_t)(d) << 24)

typedef struct NxD3D9Viewport { uint32_t X, Y, Width, Height; float MinZ, MaxZ; } NxD3D9Viewport;  /* D3DVIEWPORT9 */
typedef struct NxD3D9Rect { int32_t left, top, right, bottom; } NxD3D9Rect;                    /* RECT */

typedef struct NxD3D9Input {
    const uint32_t* rs;            /* [NXD3D_RS_COUNT], required */
    uint32_t ps_version;           /* pixel shader version, low 16 bits of the version token (0x0200 = ps_2_0);
                                      0 = fixed function. >= 0x0300 disables the fog epilogue. */
    uint32_t depth_format;         /* D3DFORMAT of the bound depth-stencil surface, 0 = none */
    NxD3D9Viewport viewport;
    NxD3D9Rect scissor;
    const float (*clip_planes)[4]; /* NXD3D_MAX_CLIP planes in clip space, or 0 */
} NxD3D9Input;

typedef struct NxD3D9Texture {     /* what the sampler translation needs to know about the bound texture */
    uint32_t format;               /* D3DFORMAT (decides whether an sRGB view exists), 0 = unknown */
    uint8_t cube;                  /* D3DRTYPE_CUBETEXTURE */
    uint8_t shadow;                /* depth format sampled with compare (Nine's NINED3DSAMP_SHADOW) */
    uint8_t managed_lod;           /* IDirect3DBaseTexture9::GetLOD() of a managed texture, else 0 */
    uint8_t pad;
} NxD3D9Texture;

/* Translates the render states. Any output pointer may be 0. */
void nx_d3d9_translate(const NxD3D9Input* in, NxRenderState* state, NxDynamicState* dyn, uint32_t* shaderBits);

/* Pieces of nx_d3d9_translate, for callers that track dirty groups. */
uint64_t nx_d3d9_blend_word(const uint32_t* rs);
uint64_t nx_d3d9_depth_stencil_word(const uint32_t* rs);
uint64_t nx_d3d9_raster_word(const uint32_t* rs);
uint32_t nx_d3d9_shader_bits(const uint32_t* rs, uint32_t ps_version);
float nx_d3d9_bias_scale(uint32_t depth_format);
void nx_d3d9_viewport(const NxD3D9Viewport* vp, NxViewport* out);
void nx_d3d9_scissor(const NxD3D9Rect* r, NxScissor* out);
void nx_d3d9_color(uint32_t d3dcolor, float rgba[4]);              /* ARGB -> RGBA floats, c/255 */

/* Sampler: samp[NXD3D_SAMP_COUNT] indexed by D3DSAMPLERSTATETYPE. tex may be 0 (2D, not shadow, lod 0). */
void nx_d3d9_sampler(const uint32_t* samp, const NxD3D9Texture* tex, NxSamplerState* out);

/* D3DFORMAT -> native format and view swizzle; format = NxFmt_None when unsupported. */
NxFormatInfo nx_d3d9_format(uint32_t d3dfmt);

/* Formats with NXFMT_CONVERT (A8, L8, A8L8, L16, A2R10G10B10, ATI1) are stored expanded so the view swizzle
 * never has to duplicate, invent or move a channel (it also applies to the BORDER colour, which Nine returns
 * unswizzled): A8 -> RGBA8 (0,0,0,A); L8 -> RGBA8 (L,L,L,255) read as RGB1; A8L8 -> RGBA8 (L,L,L,A); L16 -> RGBA16
 * (L,L,L,1) read as RGB1; A2R10G10B10 -> R/B swapped into RGB10A2; ATI1 (BC4) -> decoded to RGBA8 (R,0,0,255).
 * Source = all mips (and faces, face-major) tightly packed as D3D lays them out. Returns the bytes written to dst,
 * 0 if the format needs no conversion, the source is short or dstCap is too small. */
uint32_t nx_d3d9_converted_size(uint32_t d3dfmt, uint32_t width, uint32_t height, uint32_t mips, uint32_t faces);
uint32_t nx_d3d9_convert(uint32_t d3dfmt, uint32_t width, uint32_t height, uint32_t mips, uint32_t faces,
                         const void* src, uint32_t srcSize, void* dst, uint32_t dstCap);

/* D3D9 defaults (IDirect3DDevice9 after Reset), as Nine's nine_render_state_defaults / sampler defaults. */
void nx_d3d9_default_rs(uint32_t rs[NXD3D_RS_COUNT]);
void nx_d3d9_default_samp(uint32_t samp[NXD3D_SAMP_COUNT]);

#ifdef __cplusplus
}
#endif
#endif
