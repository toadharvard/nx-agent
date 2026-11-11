/* fnvnx_render_hooks.h - the eight functions the Direct3D 9 device layer (render/d3d9/dev) calls and the platform
 * defines. C++ linkage, EXACTLY the names and parameter types the render objects reference (checked mechanically
 * against the render declarations by comparing the mangled names; the contract is the render lead's
 * platform_hooks.tsv).
 *
 *   nxd3d_gpu_alloc / nxd3d_gpu_free   platform/win32/render_hooks.cpp  guest memory of the page map (VirtualAlloc)
 *   nxd3d_dc_create / nxd3d_dc_release                                  a GDI memory DC over the caller's pixels
 *   nxd3d_window_client / nxd3d_perf_counter / nxd3d_os_cursor_pos      Win32 facts (GetClientRect, QPC, GetCursorPos)
 *   nxd3d_gpu_call                     link-time pair, like fnvnx_boundary.h: the product is
 *                                      toolchain/guest/platform_glue.cpp (ext_call of host module 10), the host
 *                                      test process is tests/s4/host_gpu.cpp (records the call, returns what the
 *                                      test installed). No weak symbols, no modes. */
#ifndef FNVNX_RENDER_HOOKS_H
#define FNVNX_RENDER_HOOKS_H

#include <stddef.h>
#include <stdint.h>

#include "fnvnx_win32.h"

/* the host module of the GPU backend (hostapi.h: "render/native 10") */
#define FNVNX_EXT_GPU 10u

uint64_t nxd3d_gpu_call(uint32_t fn, const uint64_t *args, uint32_t nargs);
void *nxd3d_gpu_alloc(size_t bytes);
void nxd3d_gpu_free(void *p);
void *nxd3d_dc_create(void *mem, uint32_t d3dfmt, uint32_t width, uint32_t height, uint32_t pitch);
void nxd3d_dc_release(void *dc);
bool nxd3d_window_client(void *hwnd, RECT *r);
uint64_t nxd3d_perf_counter(uint64_t *freq);
void nxd3d_os_cursor_pos(int32_t *x, int32_t *y);

#endif
