// native_device.cpp - renderer init of the native-render guest (LINK_OVERRIDES=render, D3D9=null + libnxrender.a).
//
// The engine creates its device in NiDX9Renderer's init (InitRenderer 0x00e72e60: IDirect3D9::CreateDevice, or
// CreateDeviceEx) and keeps using the device object for the calls the native renderer does not replace. The null
// device answers those. Every replaced entry point (render/native/overrides.tsv -> n1/engine_stubs.cpp) forwards to
// nx::Renderer::Get(), which is null until someone runs Renderer::Init: the stubs then draw nothing and report
// failure. This file is that someone. The null device's CreateDevice/CreateDeviceEx (gen_null_d3d9.py) call the weak
// hook below once the device object exists; it creates the back buffer and depth images at the presentation size on
// the nxgpu backend (the host's deko3d module on the console, the GL reference backend under the emulator: ext_call
// module 10) and initialises the one nx::Renderer.
//
// The hook's result is what CreateDevice returns: S_OK, or D3DERR_NOTAVAILABLE when there is no GPU backend or the
// renderer cannot start, so the engine takes its own "no device" path (it tries the next device type, then reports
// "Failed to initialize renderer") instead of running on a renderer that draws nothing.
//
// Built only into a guest that links libnxrender.a (toolchain/game/build.sh adds it when EXTRA_OBJS names the
// archive). tests/s5/emu_guest.py --native-render injects the same code into older guests, whose null device has no
// hook call: the emulator then enters the hook at CreateDevice's return.
#include "renderer.h"   // render/native/n1

#include <new>
#include <stdint.h>
#include <stdio.h>

namespace {
const int32_t kS_OK = 0;
const int32_t kD3DERR_NOTAVAILABLE = (int32_t)0x8876086Au;
// D3DPRESENT_PARAMETERS starts with BackBufferWidth, BackBufferHeight (UINT); 0 means the window's client size,
// which on the Switch is the panel: the 720p handheld target (D18).
const uint32_t kDefaultW = 1280, kDefaultH = 720;
alignas(16) unsigned char g_storage[sizeof(nx::Renderer)];
uint32_t g_back, g_depth, g_w, g_h;
}

extern "C" int32_t fnvnx_d3d9_device_created(const void* present_params) {
    if (nx::Renderer::Get()) return kS_OK;   // a second CreateDevice (device reset, a retry): keep the renderer
    uint32_t w = kDefaultW, h = kDefaultH;
    if (present_params) {
        const uint32_t* p = static_cast<const uint32_t*>(present_params);
        if (p[0] && p[1] && p[0] <= 4096 && p[1] <= 4096) { w = p[0]; h = p[1]; }
    }
    nx::Backend* be = nx::GpuBackend(nullptr);
    if (!be) {
        printf("fnvnx native renderer: no nxgpu backend (host module 10 did not answer)\n");
        return kD3DERR_NOTAVAILABLE;
    }
    NxImageDesc rt = {};
    rt.width = (uint16_t)w; rt.height = (uint16_t)h; rt.format = NxFmt_RGBA8; rt.flags = NxImg_RenderTarget;
    NxImageDesc ds = rt;
    ds.format = NxFmt_Z24S8; ds.flags = NxImg_RenderTarget | NxImg_Depth;
    uint32_t back = be->ImageCreate(rt), depth = be->ImageCreate(ds);
    if (!back || !depth) {
        printf("fnvnx native renderer: %ux%u targets failed (back %u, depth %u)\n", w, h, back, depth);
        return kD3DERR_NOTAVAILABLE;
    }
    nx::Renderer* r = new (g_storage) nx::Renderer();
    if (!r->Init(be, w, h, back, depth)) {
        printf("fnvnx native renderer: Renderer::Init failed (%ux%u)\n", w, h);
        return kD3DERR_NOTAVAILABLE;
    }
    g_back = back; g_depth = depth; g_w = w; g_h = h;
    printf("fnvnx native renderer: up, %ux%u, back buffer image %u, depth image %u, backend %u\n", w, h, back, depth,
           be->Info().backend);
    return kS_OK;
}

// The back buffer for a frame readback (the emulator's --frames, a screenshot path later): image id and size.
extern "C" uint32_t fnvnx_native_back_buffer(uint32_t* w, uint32_t* h) {
    if (w) *w = g_w;
    if (h) *h = g_h;
    return g_back;
}
