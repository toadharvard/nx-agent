// pair.c: the program pair of a draw, as pure functions of the two shaders' declared pairs (render/shaders/translator/include/fnvnx_dk_layout.h, fnvnx_shaders.h).
// nx_link_set: the varying link set L = (VS outputs without POSITION0 / PSIZE0) U (PS inputs), empty when every pair is canonical (fnvnx_dk_varying_location >= 0), else the set ascending (shcache_link_set).
// nx_pair_variant: the translator key flags of the pair (FNVNX_SHADER_VS_COMPLETE iff fnvnx_dk_vs_needs_complete(VS written + FOG0, PS read), FNVNX_SHADER_FLAT_COLOR iff flat) and the link set.
#include "nx_pair.h"
#include "fnvnx_dk_layout.h"
#include "fnvnx_shaders.h"
#include <stdbool.h>
#include <string.h>

int nx_link_set(const uint8_t* vs_out, uint32_t nv, const uint8_t* ps_in, uint32_t np, uint8_t* l, uint32_t* nl) {
    uint8_t u[2 * 224]; uint32_t n = 0, m = 0; bool canon = true;
    for (uint32_t i = 0; i < nv; i++) if (vs_out[i] != FNVNX_DK_PAIR(FNVNX_USAGE_POSITION, 0) && vs_out[i] != FNVNX_DK_PAIR(FNVNX_USAGE_PSIZE, 0)) u[n++] = vs_out[i];
    for (uint32_t i = 0; i < np; i++) u[n++] = ps_in[i];
    uint8_t d[2 * 224];
    for (uint32_t i = 0; i < n; i++) { uint32_t j = 0; while (j < m && d[j] != u[i]) j++; if (j == m) d[m++] = u[i]; }
    for (uint32_t i = 0; i < m; i++) { if (d[i] / 16 > 13) return -1; if (fnvnx_dk_varying_location(d[i] / 16, d[i] % 16) < 0) canon = false; }
    if (m > FNVNX_DK_VARYING_SLOTS) return -1;
    if (canon) { *nl = 0; return 0; }
    for (uint32_t i = 1; i < m; i++) { uint8_t v = d[i]; uint32_t q = i; while (q > 0 && d[q - 1] > v) { d[q] = d[q - 1]; q--; } d[q] = v; }
    memcpy(l, d, m); *nl = m; return 0;
}
enum nx_gl4shim_stage_id nx_pair_ffp_vs_row(bool flat) { return flat ? NX_GL4SHIM_STAGE_FFP_VS_FLAT : NX_GL4SHIM_STAGE_FFP_VS_SMOOTH; }
enum nx_gl4shim_stage_id nx_pair_ffp_ps_row(bool flat) { return flat ? NX_GL4SHIM_STAGE_FFP_PS_FLAT : NX_GL4SHIM_STAGE_FFP_PS_SMOOTH; }
int nx_pair_variant(const uint8_t* vs_pairs, uint32_t nv, uint8_t ps_major, const uint8_t* ps_pairs, uint32_t np, uint32_t flat, uint32_t* vs_variant, uint32_t* ps_variant, uint8_t* l, uint32_t* nl) {
    uint8_t vw[225], pr[225]; uint32_t nvw = 0, npr = 0;
    for (uint32_t i = 0; i < nv; i++) if (vs_pairs[i] != FNVNX_DK_PAIR(FNVNX_USAGE_POSITION, 0) && vs_pairs[i] != FNVNX_DK_PAIR(FNVNX_USAGE_PSIZE, 0)) vw[nvw++] = vs_pairs[i];
    bool fog = false; for (uint32_t i = 0; i < nvw; i++) fog |= vw[i] == FNVNX_DK_PAIR(FNVNX_USAGE_FOG, 0); if (!fog) vw[nvw++] = FNVNX_DK_PAIR(FNVNX_USAGE_FOG, 0);
    memcpy(pr, ps_pairs, np); npr = np;
    if (ps_major < 3) { fog = false; for (uint32_t i = 0; i < npr; i++) fog |= pr[i] == FNVNX_DK_PAIR(FNVNX_USAGE_FOG, 0); if (!fog) pr[npr++] = FNVNX_DK_PAIR(FNVNX_USAGE_FOG, 0); }
    *vs_variant = (fnvnx_dk_vs_needs_complete(vw, (int)nvw, pr, (int)npr) ? (uint32_t)FNVNX_SHADER_VS_COMPLETE : 0u) | (flat ? (uint32_t)FNVNX_SHADER_FLAT_COLOR : 0u);
    *ps_variant = flat ? (uint32_t)FNVNX_SHADER_FLAT_COLOR : 0u;
    return nx_link_set(vs_pairs, nv, ps_pairs, np, l, nl);
}
