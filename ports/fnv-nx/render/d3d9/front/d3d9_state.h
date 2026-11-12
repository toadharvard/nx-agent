/* fnv-nx L6: D3D9 state -> backend state words (render/d3d9/backend/nx/state.h). Pure functions: C99, no globals, no
 * allocation, no I/O. In-range semantics mirror Gallium Nine (nine_pipe.c nine_convert_*_state); the result for a value
 * OUTSIDE a D3D9 enumeration is Wine 9.0's (wined3d state.c / stateblock.c / utils.c), see "Totality" below.
 *
 *     uint32_t rs[NXD3D_RS_COUNT];            // indexed by D3DRENDERSTATETYPE, as the device holds them (any DWORD)
 *     NxD3D9Latch latch; nx_d3d9_latch_init(&latch);          // once per device (and after every Reset)
 *     NxD3D9Input in = {rs, ..., &latch};
 *     NxRenderState st; NxDynamicState dyn; uint32_t sb;
 *     nx_d3d9_translate(&in, &st, &dyn, &sb);                 // every application of the state
 *     NxSamplerState ss; nx_d3d9_sampler(samp[i], &tex, &ss); // samp: DWORD[14] by D3DSAMPLERSTATETYPE
 *     NxFormatInfo fi = nx_d3d9_format(fmt);
 * Floats held in DWORD render states (FOGSTART, DEPTHBIAS, POINTSIZE, ...) are IEEE bit patterns, as in D3D9.
 *
 * ---- Totality (the rule that closes the class) -------------------------------------------------------------------
 * Every D3D9 enumeration the translation reads (front/d3d9_enums.gen.h, generated from render/d3d9/spec/d3d9_spec.json:
 * D3DCMPFUNC, D3DSTENCILOP, D3DBLEND, D3DBLENDOP, D3DCULL, D3DFILLMODE, D3DSHADEMODE, D3DFOGMODE, D3DZBUFFERTYPE,
 * D3DTEXTUREADDRESS, D3DTEXTUREFILTERTYPE, D3DFORMAT) is a C enum, and its translation is a switch over that enum type with no
 * default branch, compiled with -Werror=switch -Werror=switch-enum: an enumerator without a case is a build error. A DWORD
 * that is not an enumerator (D3D9 stores any DWORD in a render state, dev/README R-ignore) is tested BEFORE the switch
 * (nxd3d_in_<enum>) and gets the result of the reference implementation, one rule per enumeration, below. FORCE_DWORD
 * (0x7fffffff) is an enumerator of the header enums and is no D3D9 value: it gets the out-of-range result.
 *
 *  out-of-range result (wined3d function)                                  state
 *  alpha func -> ALWAYS (wined3d_sanitize_cmp_func; shader alpha test)       D3DRS_ALPHAFUNC
 *  stencil func -> ALWAYS (state_stencil: `if (!func) func = GL_ALWAYS`)    D3DRS_STENCILFUNC, CCW_STENCILFUNC
 *  stencil op -> KEEP (gl_stencil_op default)                                D3DRS_STENCIL{FAIL,ZFAIL,PASS}, CCW_*
 *  blend factor -> ZERO (gl_blend_factor default returns GL_NONE == 0)       D3DRS_SRCBLEND, DESTBLEND, *BLENDALPHA
 *  blend op -> ADD (gl_blend_op default)                                     D3DRS_BLENDOP, BLENDOPALPHA
 *  z buffer type -> depth test off (stateblock.c depth_stencil state: desc.depth stays FALSE)   D3DRS_ZENABLE
 *  fog table mode -> no fog (shader.c find_ps_compile_args: args->fog stays OFF)   D3DRS_FOGTABLEMODE (ps < 3.0)
 *  texture address -> REPEAT (stateblock.c get_texture_address_mode, before the cube-map clamp)   D3DSAMP_ADDRESSU/V/W
 *  texture filter -> LINEAR (sampler_desc_from_sampler_states: min(max(v, POINT), LINEAR) with v above LINEAR)
 *  format -> NxFmt_None (wined3dformat_from_d3dformat / get_format_idx: WINED3DFMT_UNKNOWN)
 *  z func, cull mode, fill mode, shade mode -> the GL state is left as it was (state_depth: `if (depth_func)`, cullmode,
 *    fillmode, state_shademode: default branch without a GL call). That is a function of the history, not of the DWORD:
 *    NxD3D9Latch holds the value those four GL states have, updated by every application with a valid value. "No rule":
 *    wined3d's own GL state before the first valid application (and after its internal blits) is not a function of the
 *    render states; nx_d3d9_latch_init() starts it at the translation of the D3D9 defaults.
 * In-range values keep Nine's results where Wine differs (listed in the test, tests/l6/front/test_front.py: ORACLE_DIFF).
 *
 * ---- Rules (in range) ----------------------------------------------------------------------------------------------
 * Depth:   ZENABLE TRUE or USEW enables the test; func = ZFUNC (D3DCMP 1..8). Write = ZWRITEENABLE != 0 and func not
 *          EQUAL/NEVER. Test off: all depth fields 0 (the latch still takes the func).
 * Stencil: STENCILENABLE != 0: front = STENCILFUNC/FAIL/ZFAIL/PASS; back = CCW_* when TWOSIDEDSTENCILMODE != 0, else a
 *          copy of front; masks = STENCILMASK, STENCILWRITEMASK, ref = STENCILREF (each & 0xFF). Disabled: all 0.
 * Blend:   ALPHABLENDENABLE != 0. Colour factors from SRCBLEND/DESTBLEND (SRCCOLOR2/INVSRCCOLOR2 = Src1Color/InvSrc1Color),
 *          op BLENDOP. SEPARATEALPHABLENDENABLE != 0: alpha from SRCBLENDALPHA/DESTBLENDALPHA, op BLENDOPALPHA; else alpha
 *          factors = SRCBLEND/DESTBLEND (SRCCOLOR2 -> One, INVSRCCOLOR2 -> Zero, as Nine) and op = colour op. Fix-up: SRCBLEND
 *          BOTHSRCALPHA (BOTHINVSRCALPHA) sets dst colour = InvSrcAlpha (SrcAlpha), and dst alpha when not separate;
 *          when separate and SRCBLENDALPHA is BOTH[INV]SRCALPHA, dst alpha gets the fix-up. BOTH* as a dst factor =
 *          SrcAlpha / InvSrcAlpha. Disabled: factors and ops 0. Write masks: COLORWRITEENABLE, COLORWRITEENABLE1..3 & 0xF.
 * Raster:  cull NONE/CW/CCW; fill POINT/WIREFRAME/SOLID; flat = SHADEMODE FLAT (GOURAUD, PHONG smooth); bias flag =
 *          (DEPTHBIAS | SLOPESCALEDEPTHBIAS) != 0 as bit patterns; scissor, sRGB write, point sprite = state != 0;
 *          clip mask = CLIPPLANEENABLE & 0xFF.
 * Bias:    depthBiasUnits = DEPTHBIAS x 2^24 (D24S8, D24X8, D24X4S4, INTZ, DF24), 2^16 (D16, D16_LOCKABLE, DF16), 2^15
 *          (D15S1), 2^32 (D32), 2^23 (every other format, no depth buffer).
 * Alpha:   test = ALPHATESTENABLE != 0, func = ALPHAFUNC, alphaRef = ALPHAREF / 255.0f.
 * Fog:     FOGENABLE == 0 or ps_version >= 0x0300 -> None; else FOGTABLEMODE EXP/EXP2/LINEAR -> Exp/Exp2/Linear, NONE -> Vertex.
 *          fogColor = FOGCOLOR as RGBA (c/255), fogEnd = FOGEND, fogScale = 1/(FOGEND - FOGSTART), fogDensity = FOGDENSITY.
 * Points:  pointSize = CLAMP(POINTSIZE, POINTSIZE_MIN, POINTSIZE_MAX) = x > min ? (x > max ? max : x) : min.
 * Viewport/Scissor: x = X + 0.5, y = Y + 0.5, w = Width, h = Height; scissor w/h = max(right - left, 0), max(bottom - top, 0).
 * Sampler: MIPFILTER NONE: mip None, lodBias 0, minLod 0; else mip = POINT ? Nearest : Linear, lodBias = MIPMAPLODBIAS,
 *          minLod = max(MAXMIPLEVEL - managed_lod, 0) (clamped to 15); maxLod = 15. min/mag linear unless the filter is POINT
 *          and the texture is not a shadow texture. maxAniso = MAXANISOTROPY when MINFILTER or MAGFILTER is ANISOTROPIC else 0.
 *          Wrap per axis WRAP Repeat, MIRROR Mirror, CLAMP Clamp, BORDER Border, MIRRORONCE MirrorOnce; cube textures: Clamp.
 *          border = BORDERCOLOR ARGB -> RGBA8. SRGB flag = SRGBTEXTURE != 0 and the format has an sRGB view.
 * Format:  front_fmt.c, one case per D3DFORMAT enumerator (view swizzle as Nine's basetexture9.c). Formats whose channels a
 *          view swizzle would have to duplicate, invent or move (A8, L8, A8L8, L16, ATI1, R8G8B8, L6V5U5, X8L8V8U8) are NXFMT_CONVERT.
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

/* D3DFORMAT FOURCCs (D3DFMT_* numeric values are the D3D9 ones). */
#define NXD3D_FOURCC(a, b, c, d) ((uint32_t)(uint8_t)(a) | (uint32_t)(uint8_t)(b) << 8 | \
                                  (uint32_t)(uint8_t)(c) << 16 | (uint32_t)(uint8_t)(d) << 24)

typedef struct NxD3D9Viewport { uint32_t X, Y, Width, Height; float MinZ, MaxZ; } NxD3D9Viewport;  /* D3DVIEWPORT9 */
typedef struct NxD3D9Rect { int32_t left, top, right, bottom; } NxD3D9Rect;                    /* RECT */

/* The values of the four GL states wined3d leaves untouched for an out-of-range DWORD (see Totality). */
typedef struct NxD3D9Latch { uint8_t depth_func, cull, fill, flat; } NxD3D9Latch;   /* NxCompare, NxCull, NxFill, 0/1 */

typedef struct NxD3D9Input {
    const uint32_t* rs;            /* [NXD3D_RS_COUNT], required */
    uint32_t ps_version;           /* pixel shader version, low 16 bits of the version token; 0 = fixed function */
    uint32_t depth_format;         /* D3DFORMAT of the bound depth-stencil surface, 0 = none */
    NxD3D9Viewport viewport;
    NxD3D9Rect scissor;
    const float (*clip_planes)[4]; /* NXD3D_MAX_CLIP planes in clip space, or 0 */
    NxD3D9Latch* latch;            /* required when a render state word is produced */
} NxD3D9Input;

typedef struct NxD3D9Texture {     /* what the sampler translation needs to know about the bound texture */
    uint32_t format;               /* D3DFORMAT (decides whether an sRGB view exists), 0 = unknown */
    uint8_t cube;                  /* D3DRTYPE_CUBETEXTURE */
    uint8_t shadow;                /* depth format sampled with compare (Nine's NINED3DSAMP_SHADOW) */
    uint8_t managed_lod;           /* IDirect3DBaseTexture9::GetLOD() of a managed texture, else 0 */
    uint8_t pad;
} NxD3D9Texture;

void nx_d3d9_latch_init(NxD3D9Latch* latch);   /* the translation of the D3D9 defaults */

/* Translates the render states. Any output pointer may be 0; the latch is updated iff state is not 0. */
void nx_d3d9_translate(const NxD3D9Input* in, NxRenderState* state, NxDynamicState* dyn, uint32_t* shaderBits);

/* Pieces of nx_d3d9_translate, for callers that track dirty groups. */
uint64_t nx_d3d9_blend_word(const uint32_t* rs);
uint64_t nx_d3d9_depth_stencil_word(const uint32_t* rs, NxD3D9Latch* latch);
uint64_t nx_d3d9_raster_word(const uint32_t* rs, NxD3D9Latch* latch);
uint32_t nx_d3d9_shader_bits(const uint32_t* rs, uint32_t ps_version);
float nx_d3d9_bias_scale(uint32_t depth_format);
void nx_d3d9_viewport(const NxD3D9Viewport* vp, NxViewport* out);
void nx_d3d9_scissor(const NxD3D9Rect* r, NxScissor* out);
void nx_d3d9_color(uint32_t d3dcolor, float rgba[4]);              /* ARGB -> RGBA floats, c/255 */

/* Sampler: samp[NXD3D_SAMP_COUNT] indexed by D3DSAMPLERSTATETYPE. tex may be 0 (2D, not shadow, lod 0). */
void nx_d3d9_sampler(const uint32_t* samp, const NxD3D9Texture* tex, NxSamplerState* out);

/* D3DFORMAT -> native format and view swizzle; format = NxFmt_None for a format without a native layout. */
NxFormatInfo nx_d3d9_format(uint32_t d3dfmt);

/* Formats with NXFMT_CONVERT are stored in a backend format plus view swizzle so that PLAIN sampling returns exactly the D3D9 (r,g,b,a) and plain rendering with the write mask writes exactly the
 * D3D9 channels (no shader-side fixup exists): A8 -> RGBA8 (0,0,0,A); L8 -> RGBA8 (L,.,.,.) sampled by the swizzle (R,R,R,1); A8L8 ->
 * RGBA8 (L,L,L,A); L16 -> RGBA16 (L,L,L,1) read as RGB1; R8G8B8 -> BGRX8 (B,G,R,255); L6V5U5 / X8L8V8U8 -> RGBA32F (U, V as max(c, -m) / m, L as c / (2^n - 1), alpha = the integer side word
 * of front_int.h that makes the readback exact, never sampled: the swizzle gives 1); ATI1 (BC4) -> decoded to RGBA8 (R,0,0,255). Source = all mips (and faces, face-major) tightly packed as D3D lays them out. Returns the bytes
 * written to dst, 0 if the format needs no conversion, the source is short or dstCap is too small. */
uint32_t nx_d3d9_converted_size(uint32_t d3dfmt, uint32_t width, uint32_t height, uint32_t mips, uint32_t faces);
uint32_t nx_d3d9_convert(uint32_t d3dfmt, uint32_t width, uint32_t height, uint32_t mips, uint32_t faces,
                         const void* src, uint32_t srcSize, void* dst, uint32_t dstCap);

/* The rules of the CONVERT formats that are not only sampled (front_rt.c). All take a D3DFORMAT; a format with no such rule has the identity answer.
 *  - nx_d3d9_rt_rule: the draw path ANDs the colour write mask of a target of this format with *mask (bit 0 R .. bit 3 A); it is the only thing the device applies (A8: A; L8: R; R8G8B8: RGB).
 *  - nx_d3d9_clear_rgba8: the RGBA8 clear value (R in the low byte) for a D3DCOLOR on a target of this format, the same rule applied.
 *  - nx_d3d9_unconvert: native rows -> D3D rows of one level (tight rows both): A8 takes alpha, L8 red, A8L8 / L16 their luminance + alpha, R8G8B8 drops the x byte, L6V5U5 / X8L8V8U8 are exactly
 *    the codes that nx_d3d9_convert read (all 2^16 / 2^32 codes round-trip; the most negative code of a signed channel is restored from the side word, although it SAMPLES as -1 like the code -m);
 *    returns the bytes written, 0 for ATI1 (BC4 is decoded on upload: not invertible), for a format without conversion, or a short buffer. */
void nx_d3d9_rt_rule(uint32_t d3dfmt, uint8_t* mask);
uint32_t nx_d3d9_clear_rgba8(uint32_t d3dfmt, uint32_t d3dcolor);
uint32_t nx_d3d9_unconvert(uint32_t d3dfmt, uint32_t width, uint32_t height, const void* src, uint32_t srcSize, void* dst, uint32_t dstCap);

/* D3D9 defaults (IDirect3DDevice9 after Reset), as Nine's nine_render_state_defaults / sampler defaults. */
void nx_d3d9_default_rs(uint32_t rs[NXD3D_RS_COUNT]);
void nx_d3d9_default_samp(uint32_t samp[NXD3D_SAMP_COUNT]);

#ifdef __cplusplus
}
#endif
#endif
