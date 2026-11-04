/* fnv-nx native renderer: per-draw uniform blocks of the shader families (std140, both stages, UBO slot 1).
 * Each struct mirrors the GLSL block of render/native/shaders/<family>.*.glsl field by field. Matrices are
 * column-major as GLSL reads them (clip = M * vec4(position, 1)); the clip space is D3D9's (y up, z in [0,1]).
 * The D3D9 epilogue block (fog, alpha test) is NxEpilogue in nx/state.h, fragment slot 2. */
#ifndef FNVNX_NX_FAMILIES_H
#define FNVNX_NX_FAMILIES_H
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* tile (TileShader, TILE passes; menus and HUD). Variants: VS tile / tile_scroll (TEXSCROLL) / tile_vc (VC);
 * PS tile / tile_alphamap (ALPHAMAP) / tile_vc (VC) - the engine's three VS and three PS (TILE1000-1002). */
typedef struct NxTileDraw {
    float wvp[16];          /* "WorldViewProjTranspose" (VS c0-c3) */
    float texScroll[4];     /* "TexScroll" (VS c4): uv offset in xy */
    float tint[4];          /* "tintcolor" (PS c0): material diffuse rgb, alpha = material alpha x alpha property */
} NxTileDraw;

/* nolight (BSShaderNoLighting, NOLIGHTING passes). Variants by define: TEX, VC, FALLOFF (SKIN not in N1). */
typedef struct NxNoLightDraw {
    float wvp[16];
    float eyeModel[4];      /* eye position in model space (FALLOFF) */
    float falloff[4];       /* cos(start angle), cos(stop angle), start opacity, stop opacity (FALLOFF) */
    float color[4];         /* material colour x alpha */
    float uvOffsetScale[4]; /* uv' = uv * zw + xy */
} NxNoLightDraw;

/* ---- N2 families (provisional semantics: parity against the translated originals pending) ---- */

/* lit (SM3 path: Lighting30 / 3XLIGHTING): per-pixel diffuse + optional specular from up to 8 lights in one pass.
 * Variants: VS lit / lit_vc (vertex colour); PS lit / lit_spec (SPECULAR: specular masked by the texture alpha).
 * Light i: posRange = (position or direction, range); w of color = 1 for a directional light (pos = direction the
 * light travels), 0 for a point light. Point attenuation = clamp(1 - (d / range)^2, 0, 1). */
typedef struct NxLight { float posRange[4]; float color[4]; } NxLight;
typedef struct NxLitDraw {
    float wvp[16];
    float world[16];
    float ambient[4];       /* rgb */
    float diffuse[4];       /* material diffuse rgb, alpha */
    float specular[4];      /* rgb, power in w */
    float eye[4];           /* eye position, world space */
    uint32_t lightCount, pad[3];
    NxLight lights[8];
} NxLitDraw;

/* depth (ZONLY, DEPTHMAP, shadow casters): VS depth; PS depth (no colour: use COLORWRITEENABLE 0) / depth_out
 * (DEPTHMAP: writes the post-projection depth z/w to all four channels). */
typedef struct NxDepthDraw { float wvp[16]; } NxDepthDraw;

/* land (LAND passes): a base texture plus up to 3 layers blended by per-vertex weights (attribute 3, xyz); the
 * vertex colour (attribute 2) modulates the result. Textures: slot 0 base, 1-3 layers. */
typedef struct NxLandDraw { float wvp[16]; float uvScale[4]; } NxLandDraw;

/* sky (SKY passes): colour from the view direction's elevation e = normalize(dir).z (Gamebryo z up):
 * c = mix(horizon, zenith, smoothstep(0, 1, max(e, 0) ^ (1/blend)) ) below the zenith, plus a cloud layer (sky_clouds:
 * texture 0, alpha-blended over the gradient with alpha x cloudAlpha). */
typedef struct NxSkyDraw {
    float wvp[16];
    float zenith[4], horizon[4];
    float params[4];        /* blend exponent, cloud alpha, uv scroll x, uv scroll y */
} NxSkyDraw;

#ifdef __cplusplus
}
#define NX_FAM_ASSERT_ static_assert
#else
#define NX_FAM_ASSERT_ _Static_assert
#endif
NX_FAM_ASSERT_(sizeof(NxTileDraw) == 96, "NxTileDraw");
NX_FAM_ASSERT_(sizeof(NxNoLightDraw) == 128, "NxNoLightDraw");
NX_FAM_ASSERT_(sizeof(NxLitDraw) == 208 + 8 * 32, "NxLitDraw");
NX_FAM_ASSERT_(sizeof(NxSkyDraw) == 112, "NxSkyDraw");
#endif
