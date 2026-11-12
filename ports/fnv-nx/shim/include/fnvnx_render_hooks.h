/* fnvnx_render_hooks.h - the nine functions the Direct3D 9 device layer (render/d3d9/dev) calls and the platform
 * defines. This is the single declaration of each; all have C linkage (the names the render objects reference, the
 * contract is the render lead's platform_hooks.tsv).
 *
 *   nxd3d_gpu_call                     platform/guest/platform_glue.cpp  ext_call of host module FNVNX_EXT_GPU
 *   nxd3d_gpu_alloc / nxd3d_gpu_free   platform/win32/render_hooks.cpp   guest memory of the page map (VirtualAlloc)
 *   nxd3d_dc_create / nxd3d_dc_release                                   a GDI memory DC over the caller's pixels
 *   nxd3d_window_client / nxd3d_perf_counter / nxd3d_os_cursor_pos       Win32 facts (GetClientRect, QPC, GetCursorPos)
 *   nx_env_profile                     platform/win32/display.cpp        the render profile (docked 1080p / handheld 720p)
 * Every function has exactly one definition, in the product; a test links those files. */
#ifndef FNVNX_RENDER_HOOKS_H
#define FNVNX_RENDER_HOOKS_H

#include <stddef.h>
#include <stdint.h>

#include <stdbool.h>

#include "fnvnx_win32.h"

#ifdef __cplusplus
extern "C" {
#endif

/* the render profile; int-sized in both languages (C: plain enum, int on AArch64 GCC; C++: fixed type) */
#ifdef __cplusplus
enum NxEnvProfile : int { NX_ENV_HANDHELD = 0, NX_ENV_DOCKED = 1 };
#else
typedef enum NxEnvProfile { NX_ENV_HANDHELD = 0, NX_ENV_DOCKED = 1 } NxEnvProfile;
#endif
#ifdef __cplusplus
static_assert(sizeof(NxEnvProfile) == 4, "NxEnvProfile is 4 bytes");
#else
_Static_assert(sizeof(NxEnvProfile) == 4, "NxEnvProfile is 4 bytes");
#endif

uint64_t nxd3d_gpu_call(uint32_t fn, const uint64_t *args, uint32_t nargs);
void *nxd3d_gpu_alloc(size_t bytes);
void nxd3d_gpu_free(void *p);
void *nxd3d_dc_create(void *mem, uint32_t d3dfmt, uint32_t width, uint32_t height, uint32_t pitch);
void nxd3d_dc_release(void *dc);
bool nxd3d_window_client(void *hwnd, RECT *r);
uint64_t nxd3d_perf_counter(uint64_t *freq);
void nxd3d_os_cursor_pos(int32_t *x, int32_t *y);
enum NxEnvProfile nx_env_profile(void);

#ifdef __cplusplus
}
#endif

#endif
