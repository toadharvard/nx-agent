// fnv-nx S6c-nine hardware test (ILP32 guest): Direct3DCreate9 -> CreateDevice -> Clear (colour cycling) -> Present,
// all through the generated proxies and Gallium Nine on nouveau. Exits on + or after 20 s; logs to the host log.
#include <d3d9.h>
#include <stdio.h>
#include <time.h>
#include "nine_proxy.h"
#include "../include_nine_api.h"

extern "C" IDirect3D9* WINAPI Direct3DCreate9(UINT sdk);

static uint64_t ticks() { return fnvnx_hcall0(fnvnx_host->system_tick); }

int main() {
    const FnvnxNineHostApi* api = (const FnvnxNineHostApi*)fnvnx_host;
    if (api->base.size < sizeof *api || api->nine_magic != FNVNX_NINE_MAGIC) { printf("nine: no host slot\n"); return 2; }
    g_nine_call = api->nine_call;

    IDirect3D9* d3d = Direct3DCreate9(D3D_SDK_VERSION);
    printf("nine: Direct3DCreate9 -> %p\n", (void*)d3d);
    if (!d3d) return 3;
    D3DADAPTER_IDENTIFIER9 id = {};
    d3d->GetAdapterIdentifier(0, 0, &id);
    printf("nine: adapter '%s' (%s), modes %u\n", id.Description, id.Driver, d3d->GetAdapterModeCount(0, D3DFMT_X8R8G8B8));
    D3DCAPS9 caps = {};
    HRESULT hr = d3d->GetDeviceCaps(0, D3DDEVTYPE_HAL, &caps);
    printf("nine: caps hr=%08x VS %08x PS %08x\n", (unsigned)hr, (unsigned)caps.VertexShaderVersion, (unsigned)caps.PixelShaderVersion);

    D3DPRESENT_PARAMETERS pp = {};
    pp.BackBufferWidth = 1280; pp.BackBufferHeight = 720; pp.BackBufferFormat = D3DFMT_X8R8G8B8;
    pp.BackBufferCount = 1; pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.Windowed = TRUE;
    pp.EnableAutoDepthStencil = TRUE; pp.AutoDepthStencilFormat = D3DFMT_D24S8;
    pp.PresentationInterval = D3DPRESENT_INTERVAL_ONE;
    IDirect3DDevice9* dev = nullptr;
    hr = d3d->CreateDevice(0, D3DDEVTYPE_HAL, nullptr, D3DCREATE_HARDWARE_VERTEXPROCESSING, &pp, &dev);
    printf("nine: CreateDevice hr=%08x dev=%p\n", (unsigned)hr, (void*)dev);
    if (FAILED(hr) || !dev) return 4;

    uint64_t freq = fnvnx_hcall0(fnvnx_host->system_tick_freq), t0 = ticks(), last = t0;
    unsigned frames = 0;
    while (fnvnx_hcall0(fnvnx_host->main_loop)) {
        if (fnvnx_hcall0(fnvnx_host->buttons_down) & (1u << 10) /* HidNpadButton_Plus */) break;
        uint64_t now = ticks();
        if (now - t0 > 20 * freq) break;
        float t = (float)(now - t0) / (float)freq;
        unsigned r = (unsigned)(127.5f + 127.5f * __builtin_sinf(t * 1.3f));
        unsigned g = (unsigned)(127.5f + 127.5f * __builtin_sinf(t * 1.7f + 2.f));
        unsigned b = (unsigned)(127.5f + 127.5f * __builtin_sinf(t * 2.3f + 4.f));
        dev->BeginScene();
        dev->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER, D3DCOLOR_XRGB(r, g, b), 1.0f, 0);
        D3DRECT bar = {0, 330, (LONG)(frames % 1280), 390};
        dev->Clear(1, &bar, D3DCLEAR_TARGET, D3DCOLOR_XRGB(255, 255, 255), 1.0f, 0);
        dev->EndScene();
        hr = dev->Present(nullptr, nullptr, nullptr, nullptr);
        if (FAILED(hr)) { printf("nine: Present hr=%08x at frame %u\n", (unsigned)hr, frames); break; }
        if (++frames % 120 == 0) {
            printf("nine: %u frames, %.2f ms/frame\n", frames, 1000.0 * (double)(now - last) / (double)freq / 120.0);
            last = now;
        }
    }
    printf("nine: %u frames presented\n", frames);
    dev->Release();
    d3d->Release();
    printf("fnvnx nine test: OK\n");
    return 0;
}
