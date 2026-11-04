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

#ifdef __cplusplus
}
#define NX_FAM_ASSERT_ static_assert
#else
#define NX_FAM_ASSERT_ _Static_assert
#endif
NX_FAM_ASSERT_(sizeof(NxTileDraw) == 96, "NxTileDraw");
NX_FAM_ASSERT_(sizeof(NxNoLightDraw) == 128, "NxNoLightDraw");
#endif
