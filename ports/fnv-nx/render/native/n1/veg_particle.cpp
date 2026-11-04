// fnv-nx native renderer, render-veg: the particles family's CPU side (BSParticleShader: NxParticleDraw).
// Folds the reciprocal of the lifetime scale (fVars0.y) out of the vertex work; tests/s6n/veg_parity.py checks the
// result against the translated PARTICLE programs.
#include "veg_families.h"
#include <string.h>

extern "C" size_t nx_particle_fold(const NxParticleConstants* c, const float (*instances)[4], uint32_t count,
                                   NxParticleDraw* o)
{
    if (count > NX_PARTICLE_MAX) return 0;
    for (int r = 0; r < 4; r++)
        for (int k = 0; k < 4; k++) o->wvp[k * 4 + r] = c->wvpRows[r * 4 + k];
    memcpy(o->vars0, c->vars0, 16);
    o->vars0[1] = c->vars0[1] != 0.0f ? 1.0f / c->vars0[1] : 3.4028235e38f;   // dk_rcp
    memcpy(o->vars1, c->vars1, 16 * 11);                                       // vars1 .. wind, same order
    if (count) memcpy(o->inst, instances, 3 * count * sizeof o->inst[0]);
    return offsetof(NxParticleDraw, inst) + 3 * count * sizeof o->inst[0];
}
