/* The GLSL shim of the GL 4.5 backend (render/gl4shim): rewrites the translator's GLSL for the driver. Returns the length written to OUT (NUL-terminated),
 * 0 when CAP is too small. STAGE: 0 vertex, 1 fragment. */
#ifndef NX_GL4SHIM_H
#define NX_GL4SHIM_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
size_t nxgl4_shim_glsl(const char* src, size_t n, uint32_t stage, char* out, size_t cap);
#ifdef __cplusplus
}
#endif
#endif
