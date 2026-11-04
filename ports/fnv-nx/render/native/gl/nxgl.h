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

/* The GLSL shim: rewrites "#version 460..." to "#version 450 core", every "binding = n" to
 * "binding = stage * 16 + n", appends _nxvs / _nxfs to uniform block names (a block of the same name in both
 * stages would otherwise be one GL block with one binding), and in vertex shaders renames main to nx_main_ and
 * appends a main that calls it and negates gl_Position.y (the GL backend runs GL_LOWER_LEFT, see nxgl.c). Returns the output length (0 if cap is too small);
 * out is NUL-terminated. */
size_t nxgl_shim_glsl(const char* src, size_t n, uint32_t stage, char* out, size_t cap);

/* Tools (nx_render): hook called after every replayed draw with its index in the submit and the pass's colour image;
 * a nonzero return skips every later draw until the hook is set again. nxgl_read_rgba8 reads a colour image (0 = the
 * swapchain image) as RGBA8, top row first, without disturbing the replay state; safe inside the hook. */
void nxgl_set_draw_hook(int (*hook)(void* user, uint32_t draw, uint32_t colorImage), void* user);
int nxgl_read_rgba8(uint32_t image, uint32_t* w, uint32_t* h, uint8_t* dst, size_t cap);
/* the same as RGBA float (4 x float per pixel, same row order as nxgl_read_rgba8; float targets unclamped) */
int nxgl_read_rgba32f(uint32_t image, uint32_t* w, uint32_t* h, float* dst, size_t cap);

/* Last GL/compile error text (empty if none). */
const char* nxgl_last_error(void);

#ifdef __cplusplus
}
#endif
#endif
