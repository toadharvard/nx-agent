/*
 * ffp_vs_ref.h - CPU reference of the D3D9 fixed-function VERTEX pipeline (mirrors ffp.vert.glsl statement by statement;
 * plain float). Semantic source for open points: WineD3D (wined3d_ffp_get_vs_settings,
 * shader_glsl_generate_ffp_vertex_shader, shader_glsl_ffp_vertex_lighting, compute_texture_matrix), listed with every
 * decision in ffp_vs_README.md.
 */
#ifndef NX_FFP_VS_REF_H
#define NX_FFP_VS_REF_H
#include "ffp_vs_state.h"

typedef struct NxFfpVsIn {          /* the vertex attributes as the host binds them (GLSL locations in ffp.vert.glsl) */
    float pos[4];                   /* POSITION xyz (w ignored) or POSITIONT x y z rhw */
    float pos1[4], normal1[4];      /* POSITION1 / NORMAL1 (tweening); xyz */
    float blendweight[4], blendindex[4];
    float normal[4];
    float color0[4], color1[4];     /* COLOR0 / COLOR1 as r,g,b,a floats */
    float psize;
    float tex[8][4];                /* TEXCOORD0..7; only the components the declaration provides are meaningful */
} NxFfpVsIn;

typedef struct NxFfpVsOut {
    float position[4];              /* clip space incl. the posfix convention */
    float color0[4], color1[4];     /* saturated */
    float tex[8][4];
    float fog;                      /* varying 18 .x: vertex fog factor (1 = unfogged); table fog: eye depth */
    float psize;
    float clip[6];                  /* gl_ClipDistance */
} NxFfpVsOut;

/* palette[i] = D3DTS_WORLDMATRIX(i), i < NX_FFP_MAX_BLEND_MATRICES. posfix = driver UBO 2 vec4 (xy, y scale z, w unused). */
void nx_ffp_vs_eval(const NxFfpVsState *st, const float (*palette)[16], const float posfix[4], const NxFfpVsIn *in,
                    NxFfpVsOut *out);
#endif
