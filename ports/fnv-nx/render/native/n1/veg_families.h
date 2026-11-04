/* fnv-nx native renderer, render-veg families (grass, tree leaves, particles, decals, blood): per-draw uniform
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

/* ---- trees: leaves (SpeedTreeLeafShader: STLEAF000-003.vso, STLEAF2000/2001[TMS].pso) ---- */
typedef struct NxTreeLeafConstants {   /* the engine's values by register (CTAB names) */
    float mvpRows[16];      /* ModelViewProj c0-c3, rows */
    float ambientColor[4];  /* AmbientColor c5 */
    float diffColor[4];     /* DiffColor c6 */
    float diffColorPt[4];   /* DiffColorPt c7 (PT) */
    float fogParam[4];      /* FogParam c8 (FOG): x far, y range, z power */
    float fogColor[4];      /* FogColor c9 (FOG) */
    float sunDimmer[4];     /* SunDimmer c10: x */
    float lightVector[4];   /* LightVector c11 */
    float lightPos[4];      /* LightPos c12 (PT): xyz, w range */
    float billboardRight[4], billboardUp[4];   /* c13, c14 */
    float rockParams[4], rustleParams[4];      /* c15, c16 */
    float leafLighting[4];  /* LeafLighting c17 */
    float windMatrices[16][4];   /* c18-c33 */
    float leafBase[48][4];       /* c34-c81 */
} NxTreeLeafConstants;

typedef struct NxTreeLeafDraw {   /* shaders/tree_leaf.vert.glsl TreeLeafDraw; the PS reads the head through fogColor */
    float mvp[16];
    float ambient[4], diffuse[4], diffusePt[4], lightVec[4], lightPos[4], bbRight[4], bbUp[4], rock[4], rustle[4],
          leafLight[4], fog[4], fogColor[4];
    float wind[16][4];
    float leafBase[48][4];
} NxTreeLeafDraw;

/* Fills out; returns the bytes to upload (the whole block). */
size_t nx_tree_leaf_fold(const NxTreeLeafConstants* c, NxTreeLeafDraw* out);
/* Package names: macros = NxTree_Fog | NxTree_Pt (vertex), NxTree_Fog | NxTree_TransparencyMS (pixel). */
enum { NxTree_Fog = 1u, NxTree_Pt = 2u, NxTree_TransparencyMS = 4u };
const char* nx_tree_leaf_vs(uint32_t macros);
const char* nx_tree_leaf_ps(uint32_t macros);

/* ---- particles (BSParticleShader: PARTICLE.vso / PARTICLE.pso) ---- */
#define NX_PARTICLE_MAX 79                 /* InstanceData c18 + 3i, i < 79: VS c18..c254 */
typedef struct NxParticleConstants {      /* the engine's values by register (CTAB names) */
    float wvpRows[16];                    /* WorldViewProj c0-c3, rows */
    float vars0[4], vars1[4], vars2[4], vars3[4], vars4[4];   /* fVars0-4 c4-c8 */
    float color1[4], color2[4], color3[4];                    /* c9-c11 */
    float velocity[4], acceleration[4], scaleAdjust[4], wind[4];   /* c12-c15 */
} NxParticleConstants;

typedef struct NxParticleDraw {           /* shaders/particle.vert.glsl ParticleDraw */
    float wvp[16];
    float vars0[4];                       /* fVars0 with y = 1 / fVars0.y */
    float vars1[4], vars2[4], vars3[4], vars4[4], color1[4], color2[4], color3[4], velocity[4], accel[4],
          scaleAdjust[4], wind[4];
    float inst[3 * NX_PARTICLE_MAX][4];
} NxParticleDraw;

/* Returns the bytes to upload (the head plus 3 x count vec4), 0 when count > NX_PARTICLE_MAX. */
size_t nx_particle_fold(const NxParticleConstants* c, const float (*instances)[4], uint32_t count, NxParticleDraw* out);

/* ---- decals (GeometryDecalShader: GDECAL.vso, GDECALS.vso skinned, GDECAL.pso) ---- */
typedef struct NxDecalDraw {        /* shaders/decal.*.glsl DecalDraw; filled straight from the constants */
    float mvp[16];                  /* ModelViewProj c0-c3 (GDECALS: SkinModelViewProj c1-c4), column-major */
    float fade[4];                  /* x = DecalFade.x x DecalFade.y (c31) */
    float offset[4];                /* PSDecalOffset (PS c15) */
} NxDecalDraw;
typedef struct NxDecalSkinDraw { NxDecalDraw d; float bones[54][4]; } NxDecalSkinDraw;   /* Bones c44-c97 */

/* ---- blood splatter (BSShaderBloodSplatter: BS.vso/pso, BSF.vso/pso = FLARE) ---- */
typedef struct NxBloodDraw {        /* shaders/blood.*.glsl BloodDraw */
    float mvp[16];                  /* ModelViewProj c0-c3, column-major */
    float lightLoc[4];              /* LightLoc c4 */
    float ctrl[4];                  /* Ctrl c5 (BSF) */
    float alpha[4];                 /* Alpha (PS c0) */
} NxBloodDraw;

#ifdef __cplusplus
}
#define NX_VEG_ASSERT_ static_assert
#else
#define NX_VEG_ASSERT_ _Static_assert
#endif
NX_VEG_ASSERT_(NX_GRASS_HEAD_BYTES == 208, "NxGrassDraw head = 13 vec4 + mat4 (std140)");
NX_VEG_ASSERT_(sizeof(NxGrassDraw) == 208 + 16 * NX_GRASS_MAX_INSTANCES, "NxGrassDraw");
NX_VEG_ASSERT_(sizeof(NxParticleDraw) == 256 + 3 * 16 * NX_PARTICLE_MAX, "NxParticleDraw");
NX_VEG_ASSERT_(sizeof(NxDecalDraw) == 96 && sizeof(NxDecalSkinDraw) == 96 + 54 * 16, "NxDecalDraw");
NX_VEG_ASSERT_(sizeof(NxBloodDraw) == 112, "NxBloodDraw");
NX_VEG_ASSERT_(sizeof(NxTreeLeafDraw) == 64 + 12 * 16 + 64 * 16, "NxTreeLeafDraw");
#endif
