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
// Then, before the engine creates any shader (RENDER_NATIVE.md 4.5): the engine's pass globals are bound
// (BindImagePassGlobals), the new device's method table is hooked (HookD3DDevice: it records the bytecode hash of
// every shader the engine creates and the constants it writes, for the translated path), and the player's pack of
// translated shaders, translated.nxsp, is loaded into a TranslatedStore that the renderer draws from. Without the
// pack only the native families draw (tile, menu, menu background); the boot goes on.
//
// The hook's result is what CreateDevice returns: S_OK, or D3DERR_NOTAVAILABLE when there is no GPU backend or the
// renderer cannot start, so the engine takes its own "no device" path (it tries the next device type, then reports
// "Failed to initialize renderer") instead of running on a renderer that draws nothing.
//
// Built only into a guest that links libnxrender.a (toolchain/game/build.sh adds it when EXTRA_OBJS names the
// archive). tests/s5/native_render.py --native-render injects the same code, with the current null device, into a
// guest linked without it.
#include "renderer.h"       // render/native/n1
#include "engine_stubs.h"
#include "translated.h"

#include <new>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

namespace {
const int32_t kS_OK = 0;
const int32_t kD3DERR_NOTAVAILABLE = (int32_t)0x8876086Au;
// D3DPRESENT_PARAMETERS starts with BackBufferWidth, BackBufferHeight (UINT); 0 means the window's client size,
// which on the Switch is the panel: the 720p handheld target (D18).
const uint32_t kDefaultW = 1280, kDefaultH = 720;
alignas(16) unsigned char g_storage[sizeof(nx::Renderer)];
uint32_t g_back, g_depth, g_w, g_h;
// placement storage: no static constructor or exit-time destructor (the store lives as long as the process)
alignas(16) unsigned char g_store_storage[sizeof(nx::TranslatedStore)];
nx::TranslatedStore* g_store;
void* g_pack;   // the pack's bytes: the store points into them for the process's lifetime

// translated.nxsp: fnvshd:/ is NX1's shader mount (next to shaders.nxsp); the SD folder's copy is the fallback, and
// the emulator's path (sdmc:/switch/fnv is its game folder)
const char* const kPackPaths[] = {"fnvshd:/translated.nxsp", "sdmc:/switch/fnv/translated.nxsp"};

void LoadTranslatedPack(nx::Renderer* r) {
    for (const char* path : kPackPaths) {
        FILE* f = fopen(path, "rb");
        if (!f) continue;
        size_t size = 0, cap = 0;   // read to the end in chunks (no ftell: one stdio call fewer for the guest)
        unsigned char* data = nullptr;
        bool ok = true;
        for (;;) {
            if (size == cap) {
                unsigned char* n = (unsigned char*)realloc(data, cap = cap ? cap * 2 : 1u << 20);
                if (!n) { ok = false; break; }
                data = n;
            }
            size_t got = fread(data + size, 1, cap - size, f);
            size += got;
            if (got == 0) break;
        }
        fclose(f);
        ok = ok && size > 32;
        if (!g_store) g_store = new (g_store_storage) nx::TranslatedStore();
        if (ok && g_store->Load(data, (uint32_t)size)) {
            g_pack = data;
            r->SetTranslatedStore(g_store);
            printf("fnvnx native renderer: %s, %u translated programs\n", path, (unsigned)g_store->count());
            return;
        }
        free(data);
        printf("fnvnx native renderer: %s unreadable or not a translated pack\n", path);
    }
    printf("fnvnx native renderer: no translated.nxsp; only the native families draw\n");
}
}

// Stub boots only: the emulator (tests/s5/emu_guest.py --stub-nulldev, farm_boot.sh with FNVNX_STUB_NULLDEV=1) sets
// this to 1 before the constructors run. Then a CreateDevice without a GPU backend succeeds on the null device alone,
// so the engine goes on past its renderer init headless (the replaced entry points draw nothing) and the boot frontier
// beyond it can be measured. Nothing in the product sets it: there the hook fails as described above.
extern "C" volatile uint32_t fnvnx_stub_nulldev;
volatile uint32_t fnvnx_stub_nulldev = 0;

extern "C" int32_t fnvnx_d3d9_device_created(const void* present_params, void* device) {
    if (nx::Renderer::Get()) {   // a second CreateDevice (device reset, a retry): keep the renderer, hook the device
        if (device) nx::engine::HookD3DDevice(device);
        return kS_OK;
    }
    uint32_t w = kDefaultW, h = kDefaultH;
    if (present_params) {
        const uint32_t* p = static_cast<const uint32_t*>(present_params);
        if (p[0] && p[1] && p[0] <= 4096 && p[1] <= 4096) { w = p[0]; h = p[1]; }
    }
    nx::Backend* be = nx::GpuBackend(nullptr);
    if (!be) {
        printf("fnvnx native renderer: no nxgpu backend (host module 10 did not answer)\n");
        if (fnvnx_stub_nulldev) {
            printf("fnvnx native renderer: STUB BOOT (fnvnx_stub_nulldev): CreateDevice succeeds on the null device "
                   "without a renderer - nothing is drawn, a measurement mode, never the product\n");
            return kS_OK;   // the device is not hooked: its methods must not reach a renderer that does not exist
        }
        return kD3DERR_NOTAVAILABLE;
    }
    NxImageDesc rt = {};
    rt.width = (uint16_t)w; rt.height = (uint16_t)h; rt.format = NxFmt_RGBA8; rt.flags = NxImg_RenderTarget;
    NxImageDesc ds = rt;
    ds.format = NxFmt_Z24S8; ds.flags = NxImg_RenderTarget | NxImg_Depth;
    uint32_t back = be->ImageCreate(rt), depth = be->ImageCreate(ds);
    if (!back || !depth) {
        printf("fnvnx native renderer: %ux%u targets failed (back %u, depth %u)\n", (unsigned)w, (unsigned)h, (unsigned)back,
               (unsigned)depth);
        return kD3DERR_NOTAVAILABLE;
    }
    nx::Renderer* r = new (g_storage) nx::Renderer();
    if (!r->Init(be, w, h, back, depth)) {
        printf("fnvnx native renderer: Renderer::Init failed (%ux%u)\n", (unsigned)w, (unsigned)h);
        return kD3DERR_NOTAVAILABLE;
    }
    g_back = back; g_depth = depth; g_w = w; g_h = h;
    printf("fnvnx native renderer: up, %ux%u, back buffer image %u, depth image %u, backend %u\n", (unsigned)w,
           (unsigned)h, (unsigned)back, (unsigned)depth, (unsigned)be->Info().backend);
    nx::engine::BindImagePassGlobals();
    bool hooked = device && nx::engine::HookD3DDevice(device);
    printf("fnvnx native renderer: pass globals bound, device %s\n", hooked ? "hooked" : "NOT hooked");
    LoadTranslatedPack(r);
    return kS_OK;
}

// The back buffer for a frame readback (the emulator's --frames, a screenshot path later): image id and size.
extern "C" uint32_t fnvnx_native_back_buffer(uint32_t* w, uint32_t* h) {
    if (w) *w = g_w;
    if (h) *h = g_h;
    return g_back;
}
