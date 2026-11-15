/* The GLSL programs of the GL 4.5 backend: the one table (render/gl4shim/include/nx_gl4shim.h, NX_GL4SHIM_PROG_LIST). */
#include "nx_gl4shim.h"
#include "gl4shim_ffp.gen.h"

#define NX_GL4SHIM_ROW_VS(text) {(text), 0}
#define NX_GL4SHIM_ROW_FS(text) {0, (text)}
const struct nx_gl4shim_prog nx_gl4shim_progs[NX_GL4SHIM_PROG_COUNT] = {
#define NX_GL4SHIM_ROW_P(id, vs, fs) [NX_GL4SHIM_PROG_##id] = {vs, fs},
#define NX_GL4SHIM_ROW_S(id, st, text) [NX_GL4SHIM_PROG_##id] = NX_GL4SHIM_ROW_##st(text),
    NX_GL4SHIM_PROG_LIST(NX_GL4SHIM_ROW_P, NX_GL4SHIM_ROW_S)
#undef NX_GL4SHIM_ROW_P
#undef NX_GL4SHIM_ROW_S
};
