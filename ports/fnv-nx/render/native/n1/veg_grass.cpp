// fnv-nx native renderer, render-veg: the grass family's CPU side (NxGrassDraw from the engine's constants).
// The folding moves the per-draw arithmetic of the original vertex program (GRASS2000-2003.vso: reciprocals of the
// fade and fog ranges, wind amplitude x direction, diffuse x AddlParams.x, 0.01 x ScaleMask) out of the per-vertex
// work; tests/s6n/veg_parity.py checks the result against the translated originals.
#include "veg_families.h"
#include <string.h>

static float sat(float x) { return x < 0.0f ? 0.0f : x > 1.0f ? 1.0f : x; }
static float rcp(float x) { return x != 0.0f ? 1.0f / x : 3.4028235e38f; }   // dk_rcp: huge, not inf, for 0

extern "C" size_t nx_grass_fold(const NxGrassConstants* c, const float (*instances)[4], uint32_t count, NxGrassDraw* o)
{
    if (count > NX_GRASS_MAX_INSTANCES) return 0;
    for (int r = 0; r < 4; r++)
        for (int k = 0; k < 4; k++) o->mvp[k * 4 + r] = c->mvpRows[r * 4 + k];     // rows -> column-major
    memcpy(o->lightDir, c->diffuseDir, sizeof o->lightDir);
    for (int k = 0; k < 4; k++) o->diffuse[k] = c->diffuseColor[k] * c->addlParams[0];
    memcpy(o->ambient, c->ambientColor, sizeof o->ambient);
    for (int k = 0; k < 4; k++) o->scaleMask[k] = c->scaleMask[k] * 0.01f;
    o->wind[0] = c->windData[0] * c->windData[2];
    o->wind[1] = c->windData[1] * c->windData[2];
    o->wind[2] = c->windData[3];
    o->wind[3] = 0.0f;
    o->fadeFog[0] = c->alphaParam[2];
    o->fadeFog[1] = rcp(c->alphaParam[3]);
    o->fadeFog[2] = c->fogParam[0];
    o->fadeFog[3] = rcp(c->fogParam[1]);
    o->fogPow[0] = c->fogParam[2]; o->fogPow[1] = o->fogPow[2] = o->fogPow[3] = 0.0f;
    for (int k = 0; k < 4; k++) o->fogColor[k] = sat(c->fogColor[k]);
    o->alpha[0] = c->alphaTestRef; o->alpha[1] = o->alpha[2] = o->alpha[3] = 0.0f;
    if (count) memcpy(o->inst, instances, count * sizeof o->inst[0]);
    return NX_GRASS_HEAD_BYTES + count * sizeof o->inst[0];
}

extern "C" const char* nx_grass_vs(uint32_t m)
{
    if (m & (NxGrass_PtLight | NxGrass_ProjShadow | NxGrass_Billboard | NxGrass_ShadowMap)) return nullptr;
    static const char* const names[4] = {"grass", "grass_vl", "grass_slope", "grass_vlslope"};
    return names[m & 3u];
}

extern "C" const char* nx_grass_ps(uint32_t m)
{
    if (m & (NxGrass_PtLight | NxGrass_ProjShadow | NxGrass_ShadowMap)) return nullptr;
    return (m & NxGrass_TransparencyMS) ? "grass_tms" : "grass";
}
