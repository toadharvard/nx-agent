/* fnv-nx native renderer, render-veg families (grass; trees, particles and decals to follow): per-draw uniform
 * blocks (std140, UBO slot 1, both stages) and the CPU folding of the engine's shader constants into them.
 * Each struct mirrors the GLSL block of render/native/shaders/<family>.*.glsl field by field; matrices are
 * column-major (clip = M * vec4(p, 1)), clip space D3D9's (z in [0, 1]). Moves into nx/families.h when the render
 * machine takes the family (CLAIMS.md). */
#ifndef FNVNX_N1_VEG_FAMILIES_H
#define FNVNX_N1_VEG_FAMILIES_H
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- grass (TallGrassShader, GRASS passes: GRASS2000-2003.vso, GRASS2000[TMS].pso) ---- */
#define NX_GRASS_MAX_INSTANCES 236          /* VS c20..c255: the engine's InstanceData register range */

/* The engine's values for one grass draw, by register (TallGrassShader's constant maps; CTAB names). */
typedef struct NxGrassConstants {
    float mvpRows[16];      /* ModelViewProj, VS c9-c12, row-major (clip.x = dot(row 0, p)) */
    float diffuseDir[4];    /* DiffuseDir c0 */
    float diffuseColor[4];  /* DiffuseColor c1 */
    float scaleMask[4];     /* ScaleMask c2 */
    float windData[4];      /* WindData c4: xy direction, z amplitude, w phase */
    float alphaParam[4];    /* AlphaParam c5: z fade start, w fade range (clip-space distance) */
    float ambientColor[4];  /* AmbientColor c6 */
    float addlParams[4];    /* AddlParams c7: x diffuse scale */
    float fogColor[4];      /* FogColor c14 */
    float fogParam[4];      /* FogParam c15: x far, y range, z power */
    float alphaTestRef;     /* AlphaTestRef, PS c3.x */
} NxGrassConstants;

typedef struct NxGrassDraw {   /* shaders/grass.vert.glsl GrassDraw; grass.frag.glsl reads the head (through alpha) */
    float mvp[16];
    float lightDir[4];      /* DiffuseDir */
    float diffuse[4];       /* DiffuseColor x AddlParams.x */
    float ambient[4];       /* AmbientColor */
    float scaleMask[4];     /* ScaleMask x 0.01 */
    float wind[4];          /* xy = WindData.xy x WindData.z, z = WindData.w */
    float fadeFog[4];       /* AlphaParam.z, 1 / AlphaParam.w, FogParam.x, 1 / FogParam.y */
    float fogPow[4];        /* FogParam.z */
    float fogColor[4];      /* saturate(FogColor) */
    float alpha[4];         /* x = AlphaTestRef */
    float inst[NX_GRASS_MAX_INSTANCES][4];   /* InstanceData: xyz position, w = scale % + shade fraction */
} NxGrassDraw;

#define NX_GRASS_HEAD_BYTES offsetof(NxGrassDraw, inst)

/* Fills out from the engine's constants and its InstanceData; returns the bytes of out to upload (the head plus
 * count instances), 0 when count exceeds NX_GRASS_MAX_INSTANCES. */
size_t nx_grass_fold(const NxGrassConstants* c, const float (*instances)[4], uint32_t count, NxGrassDraw* out);

/* Package shader names for the engine's permutation macros (TallGrassPrograms: VERTLIT, FIT_TO_SLOPE;
 * TRANSPARENCYMS for the pixel shader). NULL for a permutation the family does not have natively yet
 * (PTLIGHT, PROJ_SHADOW, BILLBOARD, SHADOWMAP: those draws stay on the Translated path). */
enum { NxGrass_VertLit = 1u, NxGrass_FitToSlope = 2u, NxGrass_PtLight = 4u, NxGrass_ProjShadow = 8u,
       NxGrass_Billboard = 16u, NxGrass_ShadowMap = 32u, NxGrass_TransparencyMS = 64u };
const char* nx_grass_vs(uint32_t macros);
const char* nx_grass_ps(uint32_t macros);

#ifdef __cplusplus
}
#define NX_VEG_ASSERT_ static_assert
#else
#define NX_VEG_ASSERT_ _Static_assert
#endif
NX_VEG_ASSERT_(NX_GRASS_HEAD_BYTES == 208, "NxGrassDraw head = 13 vec4 + mat4 (std140)");
NX_VEG_ASSERT_(sizeof(NxGrassDraw) == 208 + 16 * NX_GRASS_MAX_INSTANCES, "NxGrassDraw");
#endif
