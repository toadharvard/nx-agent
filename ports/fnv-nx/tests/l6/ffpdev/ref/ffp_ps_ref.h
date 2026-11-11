/*
 * ffp_ps_ref.h - CPU reference of the D3D9 fixed-function pixel pipeline (mirrors ffp.frag.glsl statement by
 * statement; plain float). Semantic source for every open point: WineD3D's fixed-function GLSL generator
 * (dlls/wined3d wined3d_ffp_get_fs_settings / is_invalid_op / shader_glsl_generate_ffp_fragment_shader /
 * shader_glsl_ffp_fragment_op / compute_texture_matrix) except where README.md lists a deviation.
 */
#ifndef NX_FFP_PS_REF_H
#define NX_FFP_PS_REF_H
#include "ffp_state.h"

/* sample stage `stage` (texType 2D: coord[0..1]; CUBE and VOLUME: coord[0..2]) -> out rgba. Called once for every
 * stage in the cascade whose (normalised) texture type is not NONE; a NONE stage reads (0,0,0,0). */
typedef void (*NxFfpSampleFn)(void *user, uint32_t stage, uint32_t texType, const float coord[3], float out[4]);

typedef struct NxFfpPsInputs {
    float diffuse[4], specular[4];
    float texcoord[8][4];           /* interpolated, as the vertex stage wrote them (TEXCOORDINDEX / texgen is vertex-side) */
    NxFfpSampleFn sample;
    void *user;
} NxFfpPsInputs;

/* Domain functions: ONE total meaning for each out-of-range value (wined3d has none for these, see README).
 *   op:  1..26 -> itself; every other value (0, 27.. incl. 2^32-1) -> NX_FFP_TOP_NOP (reads no argument, writes nothing;
 *        wined3d's args[]/switch default does exactly this for 0, and for >26 we extend it).
 *   arg: (a & 15) in 0..6 -> itself, 7..15 -> NX_FFP_TA_ZERO (reads vec4(0)); bits 4 (COMPLEMENT), 5 (ALPHAREPLICATE)
 *        are modifiers, bits 6.. ignored (as wined3d).
 *   tex: 1,2,3 -> itself, anything else -> NX_FFP_TEX_NONE. */
uint32_t nx_ffp_norm_op(uint32_t v);
uint32_t nx_ffp_norm_arg_sel(uint32_t a);
uint32_t nx_ffp_norm_textype(uint32_t t);

/* rgba before the shared fog / alpha-test epilogue (d9_epilogue.glsl), saturated to [0,1]. */
void nx_ffp_ps_eval(const NxFfpPsState *st, const NxFfpPsInputs *in, float out_rgba[4]);
#endif
