/* Shared by vs_driver.c and vs_gl.c: the record (field order = vsrec.py FIELDS), the conversion to the D3D9-side
 * description + inputs, the palette expansion (world[i] = P[i % 16]). */
#ifndef VS_COMMON_H
#define VS_COMMON_H
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../../../render/d3d9/ffp/ffp_vs_ref.h"

typedef struct VsRec {
    uint32_t decl[20], rs[210], tcIndex[8], ttff[8];
    float view[16], proj[16], world0[16], texm[8][16], clip[6][4], mat[20], vp[4];
    uint32_t nLights, lights[16 * 26];
    float posfix[4], pos[4], pos1[4], n1[4], bw[4], bi[4], n[4], c0[4], c1[4], psize[4], tex[8][4], palette[16][16];
} VsRec;
#define OUT_WORDS 52

static float g_palette[NX_FFP_MAX_BLEND_MATRICES][16];

static void rec_expand(const VsRec *r, NxFfpVsState *st, NxFfpVsIn *in)
{
    NxFfpVsDecl decl; NxFfpVsD3d9 d;
    memcpy(&decl, r->decl, sizeof decl);
    memset(&d, 0, sizeof d);
    d.decl = &decl; d.rs = r->rs;
    memcpy(d.texCoordIndex, r->tcIndex, 32); memcpy(d.texTransformFlags, r->ttff, 32);
    d.view = r->view; d.proj = r->proj; d.world0 = r->world0; d.texMatrix = r->texm; d.clipPlane = r->clip;
    memcpy(d.mDiffuse, r->mat, 16); memcpy(d.mAmbient, r->mat + 4, 16); memcpy(d.mSpecular, r->mat + 8, 16);
    memcpy(d.mEmissive, r->mat + 12, 16); d.mPower = r->mat[16];
    for (int i = 0; i < 4; i++) d.viewport[i] = (uint32_t)r->vp[i];
    d.lights = (const NxFfpD3dLight *)r->lights; d.nLights = r->nLights;
    nx_ffp_vs_state_from_d3d9(&d, st);
    for (int i = 0; i < NX_FFP_MAX_BLEND_MATRICES; i++) memcpy(g_palette[i], r->palette[i % 16], 64);
    memcpy(in->pos, r->pos, 16); memcpy(in->pos1, r->pos1, 16); memcpy(in->normal1, r->n1, 16);
    memcpy(in->blendweight, r->bw, 16); memcpy(in->blendindex, r->bi, 16); memcpy(in->normal, r->n, 16);
    memcpy(in->color0, r->c0, 16); memcpy(in->color1, r->c1, 16); in->psize = r->psize[0]; memcpy(in->tex, r->tex, 128);
}

static void out_flat(const NxFfpVsOut *o, float f[OUT_WORDS])
{
    memcpy(f, o->position, 16); memcpy(f + 4, o->color0, 16); memcpy(f + 8, o->color1, 16); memcpy(f + 12, o->tex, 128);
    f[44] = o->fog; f[45] = o->psize; memcpy(f + 46, o->clip, 24);
}
#endif
