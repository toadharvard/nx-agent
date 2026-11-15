/* The GLSL programs of the GL 4.5 backend: the one table (render/gl4shim/include/nx_gl4shim.h, NX_GL4SHIM_PROG_LIST), as pair rows and stage rows. */
#include "nx_gl4shim.h"
#include "gl4shim_ffp.gen.h"

const struct nx_gl4shim_pair nx_gl4shim_pairs[NX_GL4SHIM_PAIR_COUNT] = {
#define NX_GL4SHIM_ROW_P(id, vs, fs) [NX_GL4SHIM_PAIR_##id] = {vs, fs},
#define NX_GL4SHIM_ROW_S(id, st, text)
    NX_GL4SHIM_PROG_LIST(NX_GL4SHIM_ROW_P, NX_GL4SHIM_ROW_S)
#undef NX_GL4SHIM_ROW_P
#undef NX_GL4SHIM_ROW_S
};
const struct nx_gl4shim_stage nx_gl4shim_stages[NX_GL4SHIM_STAGE_COUNT] = {
#define NX_GL4SHIM_ROW_P(id, vs, fs)
#define NX_GL4SHIM_ROW_S(id, st, text) [NX_GL4SHIM_STAGE_##id] = {NX_GL4SHIM_STG_##st, text},
    NX_GL4SHIM_PROG_LIST(NX_GL4SHIM_ROW_P, NX_GL4SHIM_ROW_S)
#undef NX_GL4SHIM_ROW_P
#undef NX_GL4SHIM_ROW_S
};
