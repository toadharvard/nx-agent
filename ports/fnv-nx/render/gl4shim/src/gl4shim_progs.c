/* The internal GLSL programs of the GL 4.5 backend: the one table (render/gl4shim/include/nx_gl4shim.h, NX_GL4SHIM_PROG_LIST). */
#include "nx_gl4shim.h"

const struct nx_gl4shim_prog nx_gl4shim_progs[NX_GL4SHIM_PROG_COUNT] = {
#define NX_GL4SHIM_ROW(id, vs, fs) [NX_GL4SHIM_PROG_##id] = {vs, fs},
    NX_GL4SHIM_PROG_LIST(NX_GL4SHIM_ROW)
#undef NX_GL4SHIM_ROW
};
