/* fnv-nx native renderer: desktop OpenGL 4.5 reference backend (stream S6n). Host only (Linux, EGL surfaceless,
 * Mesa llvmpipe). Same command stream and NxGpuApi as the deko3d backend (nx/gpu_abi.h). */
#ifndef FNVNX_NXGL_H
#define FNVNX_NXGL_H
#include <stddef.h>
#include <stdint.h>
#include "nx/gpu_abi.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Creates a surfaceless GL 4.5 core context (EGL_PLATFORM_SURFACELESS_MESA, EGL_NO_CONFIG_KHR) current on the
 * calling thread, and a "swapchain" render target of width x height (image 0; none if 0). All later calls must
 * come from that thread. Returns 0 on failure (reason in err). */
const NxGpuApi* nxgl_create(uint32_t width, uint32_t height, char* err, size_t errlen);
void nxgl_destroy(void);

/* The GLSL shim: rewrites "#version 460..." to "#version 450 core" and every "binding = n" to
 * "binding = stage * 16 + n". Returns the output length (0 if cap is too small); out is NUL-terminated. */
size_t nxgl_shim_glsl(const char* src, size_t n, uint32_t stage, char* out, size_t cap);

/* Last GL/compile error text (empty if none). */
const char* nxgl_last_error(void);

#ifdef __cplusplus
}
#endif
#endif
