// D7: hooks of the device lifecycle / presentation module. Declared here, DEFINED BY THE PLATFORM (Win32 facts) / THE BACKEND (nxgpu4.h concepts) / THE TESTS; undefined in the product objects.
// Image functions of the back buffers are NOT here: back buffers are NxImg objects and use nxd3d_image_create / _destroy / _update / _read of res_tex.h.
#pragma once
#include "obj.h"
#include "nxgpu4.h"
#include <fnvnx_render_hooks.h>

// ---- backend, 1:1 onto nxgpu4.h; product definitions in glue_present.cpp. Result of the int hooks: 0 = done, NXD3D_HOOK_NOMEM = nxd3d_gpu_alloc returned null (the device reports D3DERR_OUTOFVIDEOMEMORY),
// anything else = the backend refused the call (a defect: the device validated everything first; D3DERR_DRIVERINTERNALERROR).
enum { NXD3D_HOOK_NOMEM = 2 };
// NxGpuApi4.present: `p` is the NxPresent of the call (image = the GPU image id of the chain's current back buffer 0, rects, interval 0-4, gamma = 0 or the HOST address of the chain's D3DGAMMARAMP, frame = the
// device's present counter, cursor = 0 or the HOST address of the software cursor's A8R8G8B8 pixels with (cx, cy, cw, ch) = its place in the presented image: nxgpu4.h rule P2, the cursor is blended onto the
// presented image by the backend as part of the present and never touches the back buffer). The hook copies the host memory into nxd3d_gpu_alloc memory; `p` itself is the caller's.
int nxd3d_present(NxObj* chain, const NxPresent* p);
// Waits until all submitted work has completed (the backend's image_read semantics without a read; used by Reset before images are destroyed).
void nxd3d_wait_idle(NxObj* device);
// GetFrontBufferData: the presented image of `chain` (the swapchain image, RGBA8, top row first: nxgpu4.h rule P4) read into dst (rowPitch bytes per row), w x h = the chain's back buffer size. The part of the
// w x h rectangle outside the swapchain image reads as zero bytes.
int nxd3d_frontbuffer_read(NxObj* chain, uint32_t w, uint32_t h, void* dst, uint32_t rowPitch);
// IDirect3DDevice9Ex::WaitForVBlank: blocks until the next vertical blank of the output the chain presents to (nxgpu4.h rule P5).
int nxd3d_wait_vblank(NxObj* chain);

// ---- platform (Win32 facts, each stated)
// Win32: GetClientRect(w) mapped to screen coordinates with MapWindowPoints; false iff w is not a window (IsWindow(w) == FALSE, including NULL); r may be null. *r = {left, top, right, bottom} of the client area in
// SCREEN coordinates (left / top = the client origin on the screen). Used for: 0-sized back buffers (width = right - left, 8 when 0), the default Present destination, GetFrontBufferData windowed
// offset, and the validity of the focus window of a full-screen device.
// Win32: QueryPerformanceCounter / QueryPerformanceFrequency; returns the counter, *freq = ticks per second (> 0). Used by GetRasterStatus (Wine wined3d_output_get_raster_status).
// Win32: GetCursorPos (the OS cursor position in screen coordinates). Used by ShowCursor when the cursor becomes visible (wined3d_device_show_cursor).
