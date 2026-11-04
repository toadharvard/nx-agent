// fnv-nx native renderer, render-veg: the trees family's CPU side (SpeedTree leaves: NxTreeLeafDraw).
// Folds DiffColor x SunDimmer.x and the reciprocals of the fog and point-light ranges out of the vertex work;
// tests/s6n/veg_parity.py checks the result against the translated STLEAF programs.
#include "veg_families.h"
#include <string.h>

static float rcp(float x) { return x != 0.0f ? 1.0f / x : 3.4028235e38f; }

extern "C" size_t nx_tree_leaf_fold(const NxTreeLeafConstants* c, NxTreeLeafDraw* o)
{
    for (int r = 0; r < 4; r++)
        for (int k = 0; k < 4; k++) o->mvp[k * 4 + r] = c->mvpRows[r * 4 + k];
    memcpy(o->ambient, c->ambientColor, 16);
    for (int k = 0; k < 4; k++) o->diffuse[k] = c->diffColor[k] * c->sunDimmer[0];
    memcpy(o->diffusePt, c->diffColorPt, 16);
    memcpy(o->lightVec, c->lightVector, 16);
    memcpy(o->lightPos, c->lightPos, 12); o->lightPos[3] = rcp(c->lightPos[3]);
    memcpy(o->bbRight, c->billboardRight, 16);
    memcpy(o->bbUp, c->billboardUp, 16);
    memcpy(o->rock, c->rockParams, 16);
    memcpy(o->rustle, c->rustleParams, 16);
    memcpy(o->leafLight, c->leafLighting, 16);
    o->fog[0] = c->fogParam[0]; o->fog[1] = rcp(c->fogParam[1]); o->fog[2] = c->fogParam[2]; o->fog[3] = 0.0f;
    memcpy(o->fogColor, c->fogColor, 16);
    memcpy(o->wind, c->windMatrices, sizeof o->wind);
    memcpy(o->leafBase, c->leafBase, sizeof o->leafBase);
    return sizeof *o;
}

extern "C" const char* nx_tree_leaf_vs(uint32_t m)
{
    static const char* const names[4] = {"tree_leaf", "tree_leaf_fog", "tree_leaf_pt", "tree_leaf_ptfog"};
    return names[m & 3u];
}

extern "C" const char* nx_tree_leaf_ps(uint32_t m)
{
    static const char* const names[4] = {"tree_leaf", "tree_leaf_fog", "tree_leaf_tms", "tree_leaf_fogtms"};
    return names[(m & NxTree_Fog) | ((m & NxTree_TransparencyMS) ? 2u : 0u)];
}
