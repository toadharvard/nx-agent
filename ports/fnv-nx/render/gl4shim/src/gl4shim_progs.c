/* The GLSL programs of the GL 4.5 backend: the one table (render/gl4shim/include/nx_gl4shim.h, NX_GL4SHIM_PROG_LIST), one const data symbol per row. */
#include "nx_gl4shim.h"
#include "gl4shim_ffp.gen.h"

#define NX_GL4SHIM_ROW_P(id, vs, fs) const struct nx_gl4shim_pair nx_gl4shim_##id = {vs, fs};
#define NX_GL4SHIM_ROW_S(id, st, text) const struct nx_gl4shim_stage nx_gl4shim_##id = {NX_GL4SHIM_STG_##st, text};
NX_GL4SHIM_PROG_LIST(NX_GL4SHIM_ROW_P, NX_GL4SHIM_ROW_S)
