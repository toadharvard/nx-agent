/*
 * ffp_vs_state.h - D3D9 fixed-function VERTEX pipeline state (rule RD5): the data that selects the behaviour of the one
 * program ffp.vert.glsl / nx_ffp_vs_eval(). Plain C99; NxFfpVsState IS the std140 UBO contents (binding
 * NX_FFP_VS_UBO_BINDING) and the matrix palette is a second UBO (binding NX_FFP_VS_PALETTE_BINDING). The driver UBO 2
 * of fnvnx_dk_layout.h (VS: vec4 posfix) is reused unchanged.
 *
 * Render states and TEXCOORDINDEX / TEXTURETRANSFORMFLAGS words are stored RAW; the program is total over every bit
 * pattern (domain functions: ffp_vs_README.md). Matrices keep D3D's row-major, row-vector layout (v' = v M); GLSL reads
 * the same bytes as the transposed mat4 and writes M * v (see ffp.vert.glsl).
 *
 * std140 layout of NxFfpVsState (bytes, sizeof == 2144, every member vec4-aligned):
 *   0    u32 inputs[4]   x flags NX_FFP_IN_*, y texCount nibbles (stage-set s: bits 4s..4s+3 = components 0..4 of
 *                        TEXCOORD s as the declaration provides them; > 4 reads as 4), z w 0
 *   16   u32 rsBlend[4]  VERTEXBLEND, INDEXEDVERTEXBLENDENABLE, NORMALIZENORMALS, LIGHTING
 *   32   u32 rsLight[4]  COLORVERTEX, SPECULARENABLE, LOCALVIEWER, FOGENABLE
 *   48   u32 rsFog[4]    FOGVERTEXMODE, FOGTABLEMODE, RANGEFOGENABLE, POINTSCALEENABLE
 *   64   u32 rsSrc[4]    DIFFUSEMATERIALSOURCE, SPECULARMATERIALSOURCE, AMBIENTMATERIALSOURCE, EMISSIVEMATERIALSOURCE
 *   80   u32 rsMisc[4]   CLIPPLANEENABLE, CLIPPING, number of lights (<= NX_FFP_MAX_LIGHTS), 0
 *   96   u32 tci[8]      D3DTSS_TEXCOORDINDEX per stage        (2 x uvec4)
 *   128  u32 ttff[8]     D3DTSS_TEXTURETRANSFORMFLAGS per stage (2 x uvec4)
 *   160  f32 fog[4]      FOGSTART, FOGEND, FOGDENSITY, TWEENFACTOR
 *   176  f32 point[4]    POINTSIZE, POINTSIZE_MIN, POINTSIZE_MAX, POINTSCALE_A
 *   192  f32 point2[4]   POINTSCALE_B, POINTSCALE_C, viewport height (pixels), 0
 *   208  f32 viewport[4] X, Y, Width, Height (pixels; used by POSITIONT)
 *   224  f32 ambient[4]  D3DRS_AMBIENT as r,g,b,a
 *   240  f32 mDiffuse[4], 256 mAmbient[4], 272 mSpecular[4], 288 mEmissive[4], 304 mPower[4] (x = power)
 *   320  f32 view[16]    D3DTS_VIEW
 *   384  f32 proj[16]    D3DTS_PROJECTION
 *   448  f32 normalMat[16] (inverse transpose of the upper 3x3 of WORLD0 * VIEW; adjugate transpose if singular)
 *   512  f32 tex[8][16]  D3DTS_TEXTURE0..7
 *   1024 f32 clipPlane[6][4]  user clip planes already in EYE space (V^-1 * plane; adjugate if V is singular)
 *   1120 NxFfpVsLight light[8]  (128 bytes each)
 * Palette block: f32 world[NX_FFP_MAX_BLEND_MATRICES][16] = D3DTS_WORLDMATRIX(0..255), 16384 bytes.
 */
#ifndef NX_FFP_VS_STATE_H
#define NX_FFP_VS_STATE_H
#include <stdint.h>
#include <stddef.h>

#define NX_FFP_VS_UBO_BINDING 4
#define NX_FFP_VS_PALETTE_BINDING 5
#define NX_FFP_MAX_LIGHTS 8                /* caps MaxActiveLights */
#define NX_FFP_MAX_BLEND_MATRICES 256      /* D3DTS_WORLDMATRIX(0..255); caps MaxVertexBlendMatrixIndex <= 255 */
#define NX_FFP_MAX_CLIP_PLANES 6

enum { /* NxFfpVsState.inputs[0] */
    NX_FFP_IN_POSITIONT = 1u << 0,         /* POSITIONT (xyzrhw) instead of POSITION */
    NX_FFP_IN_NORMAL = 1u << 1, NX_FFP_IN_PSIZE = 1u << 2, NX_FFP_IN_COLOR0 = 1u << 3, NX_FFP_IN_COLOR1 = 1u << 4,
    NX_FFP_IN_POSITION1 = 1u << 5, NX_FFP_IN_NORMAL1 = 1u << 6,   /* tweening inputs (usage index 1) */
    NX_FFP_IN_BLENDINDICES = 1u << 7,
    NX_FFP_IN_IDX_UNORM = 1u << 8,         /* indices are D3DCOLOR (delivered normalised, x255) rather than UBYTE4 */
    NX_FFP_IN_NBLEND_SHIFT = 12            /* bits 12..14: number of BLENDWEIGHT floats (0..4, > 4 reads as 4) */
};
enum { NX_FFP_LIGHT_NONE = 0, NX_FFP_LIGHT_POINT = 1, NX_FFP_LIGHT_SPOT = 2, NX_FFP_LIGHT_DIRECTIONAL = 3 };

typedef struct NxFfpVsLight {
    float diffuse[4], specular[4], ambient[4];   /* 0, 16, 32 */
    float position[4];     /* 48: eye-space position (point, spot); unit vector TO the light (directional); w 0 */
    float direction[4];    /* 64: unit eye-space spot axis (D3D direction); w = range */
    float att[4];          /* 80: attenuation0, 1, 2, falloff */
    float cone[4];         /* 96: cos(theta/2), cos(phi/2), 0, 0 */
    uint32_t kind[4];      /* 112: NX_FFP_LIGHT_* (any other D3DLIGHTTYPE value = NONE), 0, 0, 0 */
} NxFfpVsLight;

typedef struct NxFfpVsState {
    uint32_t inputs[4], rsBlend[4], rsLight[4], rsFog[4], rsSrc[4], rsMisc[4], tci[8], ttff[8];
    float fog[4], point[4], point2[4], viewport[4], ambient[4];
    float mDiffuse[4], mAmbient[4], mSpecular[4], mEmissive[4], mPower[4];
    float view[16], proj[16], normalMat[16], tex[8][16], clipPlane[6][4];
    NxFfpVsLight light[NX_FFP_MAX_LIGHTS];
} NxFfpVsState;

#define NX_FFP_VSA(n, c) typedef char nx_ffp_vs_sa_##n[(c) ? 1 : -1]
NX_FFP_VSA(light_size, sizeof(NxFfpVsLight) == 128 && offsetof(NxFfpVsLight, kind) == 112);
NX_FFP_VSA(tci, offsetof(NxFfpVsState, tci) == 96 && offsetof(NxFfpVsState, ttff) == 128);
NX_FFP_VSA(fog, offsetof(NxFfpVsState, fog) == 160 && offsetof(NxFfpVsState, viewport) == 208);
NX_FFP_VSA(mat, offsetof(NxFfpVsState, mDiffuse) == 240 && offsetof(NxFfpVsState, mPower) == 304);
NX_FFP_VSA(xf, offsetof(NxFfpVsState, view) == 320 && offsetof(NxFfpVsState, normalMat) == 448);
NX_FFP_VSA(tex, offsetof(NxFfpVsState, tex) == 512 && offsetof(NxFfpVsState, clipPlane) == 1024);
NX_FFP_VSA(light, offsetof(NxFfpVsState, light) == 1120 && sizeof(NxFfpVsState) == 2144);

/* ---- D3D9-side description (what the device holds); all of it is plain data ----------------------------------------- */
enum { NX_VS_RS_FOGENABLE = 28, NX_VS_RS_SPECULARENABLE = 29, NX_VS_RS_FOGTABLEMODE = 35, NX_VS_RS_FOGSTART = 36, NX_VS_RS_FOGEND = 37,
       NX_VS_RS_FOGDENSITY = 38, NX_VS_RS_RANGEFOGENABLE = 48, NX_VS_RS_CLIPPING = 136, NX_VS_RS_LIGHTING = 137, NX_VS_RS_AMBIENT = 139,
       NX_VS_RS_FOGVERTEXMODE = 140, NX_VS_RS_COLORVERTEX = 141, NX_VS_RS_LOCALVIEWER = 142, NX_VS_RS_NORMALIZENORMALS = 143,
       NX_VS_RS_DIFFUSEMATERIALSOURCE = 145, NX_VS_RS_SPECULARMATERIALSOURCE = 146, NX_VS_RS_AMBIENTMATERIALSOURCE = 147,
       NX_VS_RS_EMISSIVEMATERIALSOURCE = 148, NX_VS_RS_VERTEXBLEND = 151, NX_VS_RS_CLIPPLANEENABLE = 152, NX_VS_RS_POINTSIZE = 154,
       NX_VS_RS_POINTSIZE_MIN = 155, NX_VS_RS_POINTSCALEENABLE = 157, NX_VS_RS_POINTSCALE_A = 158, NX_VS_RS_POINTSCALE_B = 159,
       NX_VS_RS_POINTSCALE_C = 160, NX_VS_RS_POINTSIZE_MAX = 166, NX_VS_RS_INDEXEDVERTEXBLENDENABLE = 167, NX_VS_RS_TWEENFACTOR = 170 };
#define NX_VS_RS_COUNT 210

/* What the vertex declaration / FVF provides (all words; 0 = absent). POSITION always exists (or POSITIONT). */
typedef struct NxFfpVsDecl {
    uint32_t posT, nBlendW;     /* POSITIONT instead of POSITION; BLENDWEIGHT float count (D3DFVF_XYZB1..B5, declarations) */
    uint32_t blendIdx;          /* 0 none, 1 UBYTE4 (D3DFVF_LASTBETA_UBYTE4), 2 D3DCOLOR (D3DFVF_LASTBETA_D3DCOLOR) */
    uint32_t hasNormal, hasPsize, hasColor0, hasColor1, hasPos1, hasNormal1, pad;
    uint32_t nTex[8];           /* components (1..4) of TEXCOORD s as provided, 0 = absent; the set is selected by TEXCOORDINDEX */
    uint32_t pad2[2];
} NxFfpVsDecl;

typedef struct NxFfpD3dLight {  /* D3DLIGHT9 (world space) */
    uint32_t type;
    float diffuse[4], specular[4], ambient[4], position[3], direction[3];
    float range, falloff, attenuation0, attenuation1, attenuation2, theta, phi;
} NxFfpD3dLight;

typedef struct NxFfpVsD3d9 {
    const NxFfpVsDecl *decl;
    const uint32_t *rs;                 /* >= NX_VS_RS_COUNT D3DRS_* values (floats as bit patterns) */
    uint32_t texCoordIndex[8], texTransformFlags[8];   /* D3DTSS_TEXCOORDINDEX / TEXTURETRANSFORMFLAGS */
    const float *view, *proj, *world0;  /* D3DMATRIX, 16 floats row-major */
    const float (*texMatrix)[16];       /* 8 x D3DTS_TEXTURE0..7 */
    const float (*clipPlane)[4];        /* 6 x world-space plane (a,b,c,d), dot(plane, (x,y,z,1)) >= 0 visible */
    float mDiffuse[4], mAmbient[4], mSpecular[4], mEmissive[4], mPower;   /* D3DMATERIAL9 */
    uint32_t viewport[4];               /* D3DVIEWPORT9 X Y Width Height */
    const NxFfpD3dLight *lights;        /* enabled lights in ENABLE ORDER; only the first NX_FFP_MAX_LIGHTS are active */
    uint32_t nLights;                   /* any count */
} NxFfpVsD3d9;

/* Pure and total over every value of every input word; floats are copied/derived, never inspected (NaN in = NaN out). */
void nx_ffp_vs_state_from_d3d9(const NxFfpVsD3d9 *in, NxFfpVsState *out);
#endif
