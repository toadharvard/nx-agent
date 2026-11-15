// nx_pair.h: the pair functions of render/pair (pair.c).
#pragma once
#include <stdint.h>

#include "nx_gl4shim.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

int nx_link_set(const uint8_t* vs_out, uint32_t nv, const uint8_t* ps_in, uint32_t np, uint8_t* l, uint32_t* nl);   // shcache_link_set (render/shaders/cache/shcache_key.c) over FNVNX_DK_PAIR codes
// The fixed-function stage that stands for a null VS or a null PS: the one row of gl4shim's stage list for the draw's D3DRS_SHADEMODE (FLAT: flat = true,
// GOURAUD / PHONG: false; the latch of nx_ffp_flat keeps the previous mode for any other value). One function per stage, so the stage is named by the caller and no value is outside the domain.
enum nx_gl4shim_stage_id nx_pair_ffp_vs_row(bool flat);
enum nx_gl4shim_stage_id nx_pair_ffp_ps_row(bool flat);
// The varyings the fixed-function programs declare, as FNVNX_DK_PAIR codes (ascending): the VS writes (ffp.vert.glsl `out`), the PS reads (ffp.frag.glsl `in`), generated from the GLSL, so they are exactly what the program
// links against. They enter nx_pair_variant like any translated stage's sets. Return the count (<= 31).
uint32_t nx_pair_ffp_vs_writes(uint8_t out[31]);
uint32_t nx_pair_ffp_ps_reads(uint8_t out[31]);
// One pure function of the pair: the VS variant (translator key flags of fnvnx_shaders.h: FNVNX_SHADER_VS_COMPLETE iff fnvnx_dk_vs_needs_complete(VS written + FOG0, PS read), FNVNX_SHADER_FLAT_COLOR iff flat) and the PS variant (FNVNX_SHADER_FLAT_COLOR iff flat). Returns nx_link_set's result (-1: a pair outside the link slots).
int nx_pair_variant(const uint8_t* vs_pairs, uint32_t nv, uint8_t ps_major, const uint8_t* ps_pairs, uint32_t np, uint32_t flat, uint32_t* vs_variant, uint32_t* ps_variant, uint8_t* l, uint32_t* nl);

#ifdef __cplusplus
}
#endif
