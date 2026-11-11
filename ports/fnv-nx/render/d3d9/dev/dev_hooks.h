// D7: hooks of the device lifecycle / presentation module. Declared here, DEFINED BY THE PLATFORM (Win32 facts) / THE BACKEND (nxgpu4.h concepts) / THE TESTS; undefined in the product objects.
// Image functions of the back buffers are NOT here: back buffers are NxImg objects and use nxd3d_image_create / _destroy / _update / _read of res_tex.h.
#pragma once
#include "obj.h"
#include "nxgpu4.h"

// ---- backend, 1:1 onto nxgpu4.h
// NxGpuApi4.present: `p` is the NxPresent of the call (image = the GPU image id of the chain's current back buffer 0, rects, interval 0-4, gamma = 0 or the address of the chain's D3DGAMMARAMP,
// frame = the device's present counter). Returns the backend result: 0 = done, anything else = BAD_CALL (the device validates the rects first, so this cannot happen on a valid image).
int nxd3d_present(NxObj* chain, const NxPresent* p);
// The software cursor of the device, composited over the presented image after nxd3d_present of the same Present call: `bgra` = w x h pixels of D3DFMT_A8R8G8B8 (row pitch w * 4),
// (x, y) = top-left corner in the presented image's coordinates (cursor position minus hot spot), alpha blended (SRCALPHA, INVSRCALPHA) onto the presented image.
void nxd3d_present_cursor(NxObj* chain, const uint8_t* bgra, uint32_t w, uint32_t h, int32_t x, int32_t y);
// Waits until all submitted work has completed (the backend's image_read semantics without a read; used by Reset before images are destroyed and by Present with a frame latency).
void nxd3d_wait_idle(NxObj* device);
// GetFrontBufferData: the presented image of `chain` (the swapchain image, RGBA8, top row first, w x h = the chain's back buffer size) read into dst (rowPitch bytes per row).
// ASSUMPTION on nxgpu4.h: NxImageRead with src.image 0 reads the swapchain image, as NxPresent / NxOp_Blit use image 0 as the destination; if the ABI lacks it the hook needs a backend addition.
void nxd3d_frontbuffer_read(NxObj* chain, uint32_t w, uint32_t h, void* dst, uint32_t rowPitch);
// IDirect3DDevice9Ex::WaitForVBlank: blocks until the next vertical blank of the output the chain presents to.
void nxd3d_wait_vblank(NxObj* chain);

// ---- platform (Win32 facts, each stated)
// Win32: GetClientRect(w) mapped to screen coordinates with MapWindowPoints; false iff w is not a window (IsWindow(w) == FALSE, including NULL); r may be null. *r = {left, top, right, bottom} of the client area in
// SCREEN coordinates (left / top = the client origin on the screen). Used for: 0-sized back buffers (width = right - left, 8 when 0), the default Present destination, GetFrontBufferData windowed
// offset, and the validity of the focus window of a full-screen device.
bool nxd3d_window_client(void* hwnd, RECT* r);
// Win32: QueryPerformanceCounter / QueryPerformanceFrequency; returns the counter, *freq = ticks per second (> 0). Used by GetRasterStatus (Wine wined3d_output_get_raster_status).
uint64_t nxd3d_perf_counter(uint64_t* freq);
// Win32: GetCursorPos (the OS cursor position in screen coordinates). Used by ShowCursor when the cursor becomes visible (wined3d_device_show_cursor).
void nxd3d_os_cursor_pos(int32_t* x, int32_t* y);
