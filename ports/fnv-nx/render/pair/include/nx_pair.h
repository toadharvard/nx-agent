// nx_pair.h: the pair functions of render/pair (pair.c).
#pragma once
#include <stdint.h>

#include "nx_gl4shim.h"

#ifdef __cplusplus
extern "C" {
#endif

int nx_link_set(const uint8_t* vs_out, uint32_t nv, const uint8_t* ps_in, uint32_t np, uint8_t* l, uint32_t* nl);   // shcache_link_set (render/shaders/cache/shcache_key.c) over FNVNX_DK_PAIR codes
// The fixed-function stage that stands for a null VS or PS: the one row of gl4shim's list (NX_GL4SHIM_PROG_FFP_{VS,PS}_{SMOOTH,FLAT}) for STAGE (NxStage_Vertex = 0 / NxStage_Fragment = 1, gpu_abi.h) and the draw's D3DRS_SHADEMODE
// (FLAT -> flat, GOURAUD / PHONG -> smooth; the latch of nx_ffp_flat keeps the previous mode for any other value). Returns the row id, -1 for a stage outside 0..1. nx_pair_variant's flat is the same shade-mode flag.
int nx_pair_ffp_row(uint32_t stage, uint32_t flat);
// One pure function of the pair: the VS variant (translator key flags of fnvnx_shaders.h: FNVNX_SHADER_VS_COMPLETE iff fnvnx_dk_vs_needs_complete(VS written + FOG0, PS read), FNVNX_SHADER_FLAT_COLOR iff flat) and the PS variant (FNVNX_SHADER_FLAT_COLOR iff flat). Returns nx_link_set's result (-1: a pair outside the link slots).
int nx_pair_variant(const uint8_t* vs_pairs, uint32_t nv, uint8_t ps_major, const uint8_t* ps_pairs, uint32_t np, uint32_t flat, uint32_t* vs_variant, uint32_t* ps_variant, uint8_t* l, uint32_t* nl);

#ifdef __cplusplus
}
#endif
