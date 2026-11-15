// test_attach.cpp: Rule D and the incomplete pass of the d3d9 front end (render/d3d9/dev/draw_state.cpp nx_attach), as a GUEST test of the real product files.
// It defines no symbol of any product root (no double, CLAUDE.md "no doubles in tests"): the device, the surfaces, Clear and the draw path are the real render/d3d9 + gl4 objects, reached by the public D3D9
// interface; the decision under test is read from the device state by the product's own nx_attach. UNADMITTED (not in verify/admit.json): it waits for L4's guest-test harness (the shared boot machine
// runs it); its existence is the deliverable of this branch, its run is L4's.
// Cases (Wine 9.0 context_gl.c find_fbo_entry 656-659 size, 664-666 multisample type / quality; check_fbo_status 420-449 + GL 4.5 9.4.4 incomplete): each row is (RT0, DS) -> depth bound?; then the incomplete rows.
#include "../../render/d3d9/dev/draw_core.h"
#include <stdio.h>

static int g_fail;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); g_fail++; } } while (0)

struct Surf { UINT w, h; D3DMULTISAMPLE_TYPE ms; DWORD q; };
static IDirect3DSurface9* rt(IDirect3DDevice9* d, Surf s) { IDirect3DSurface9* p = nullptr; return d->CreateRenderTarget(s.w, s.h, D3DFMT_A8R8G8B8, s.ms, s.q, FALSE, &p, nullptr) == D3D_OK ? p : nullptr; }
static IDirect3DSurface9* ds(IDirect3DDevice9* d, Surf s) { IDirect3DSurface9* p = nullptr; return d->CreateDepthStencilSurface(s.w, s.h, D3DFMT_D24S8, s.ms, s.q, FALSE, &p, nullptr) == D3D_OK ? p : nullptr; }
static NxDeviceCore* core(IDirect3DDevice9* d) { return static_cast<NxDeviceCore*>(nx_obj_of((IUnknown*)d)); }

// (colour 0, depth) -> bound?   every pair differs from an equal pair in exactly one of: width, height, type, quality
struct DepthCase { Surf c, z; bool bound; const char* why; };
static const DepthCase kDepth[] = {
    {{64, 64, D3DMULTISAMPLE_NONE, 0}, {64, 64, D3DMULTISAMPLE_NONE, 0}, true,  "equal"},
    {{64, 64, D3DMULTISAMPLE_NONE, 0}, {128, 128, D3DMULTISAMPLE_NONE, 0}, true, "depth larger"},
    {{64, 64, D3DMULTISAMPLE_NONE, 0}, {32, 64, D3DMULTISAMPLE_NONE, 0}, false, "depth narrower"},
    {{64, 64, D3DMULTISAMPLE_NONE, 0}, {64, 32, D3DMULTISAMPLE_NONE, 0}, false, "depth shorter"},
    {{64, 64, D3DMULTISAMPLE_4_SAMPLES, 0}, {64, 64, D3DMULTISAMPLE_4_SAMPLES, 0}, true, "equal type, equal quality"},
    {{64, 64, D3DMULTISAMPLE_4_SAMPLES, 0}, {64, 64, D3DMULTISAMPLE_2_SAMPLES, 0}, false, "type differs (both multisampled)"},
    {{64, 64, D3DMULTISAMPLE_4_SAMPLES, 0}, {64, 64, D3DMULTISAMPLE_NONE, 0}, false, "type differs (depth single)"},
    {{64, 64, D3DMULTISAMPLE_NONE, 0}, {64, 64, D3DMULTISAMPLE_4_SAMPLES, 0}, false, "type differs (colour single)"},
    {{64, 64, D3DMULTISAMPLE_4_SAMPLES, 0}, {64, 64, D3DMULTISAMPLE_4_SAMPLES, 1}, false, "type equal, quality differs"},
    {{64, 64, D3DMULTISAMPLE_NONE, 0}, {64, 64, D3DMULTISAMPLE_NONE, 1}, true, "quality is not compared for type NONE (Wine 665-666)"},
};

int main() {
    IDirect3D9* d3d = Direct3DCreate9(D3D_SDK_VERSION); CHECK(d3d);
    D3DPRESENT_PARAMETERS pp = {}; pp.Windowed = TRUE; pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.BackBufferFormat = D3DFMT_UNKNOWN; pp.BackBufferWidth = 64; pp.BackBufferHeight = 64;
    IDirect3DDevice9* dev = nullptr; CHECK(d3d->CreateDevice(0, D3DDEVTYPE_HAL, nullptr, D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &dev) == D3D_OK && dev);
    if (!dev) return 1;
    NxDeviceCore* d = core(dev);
    for (const DepthCase& k : kDepth) {                                                                          // depth size, type, quality: each equal and different
        IDirect3DSurface9* c = rt(dev, k.c); IDirect3DSurface9* z = ds(dev, k.z);
        if (!c || !z) { printf("SKIP-NOT %s: surface creation failed\n", k.why); g_fail++; continue; }
        CHECK(dev->SetRenderTarget(0, c) == D3D_OK); CHECK(dev->SetDepthStencilSurface(z) == D3D_OK);
        NxAttach a = nx_attach(d->cur);
        if ((a.ds != nullptr) != k.bound) printf("case '%s': bound %d expected %d\n", k.why, a.ds != nullptr, k.bound);
        CHECK((a.ds != nullptr) == k.bound); CHECK(!a.incomplete);
        NxSurfCore* c0 = nx_surf_core(c); NxSurfCore* z0 = nx_surf_core(z); CHECK(nx_depth_bound(c0, z0) == k.bound);   // the decision itself, both ways round the descriptors
        CHECK(dev->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER, 0, 1.0f, 0) == D3D_OK);                  // a complete pass: the clear is accepted
        c->Release(); z->Release();
    }
    {   // mixed colour sample counts: RT0 4x, RT1 single: the pass is incomplete; the draw and the clear are skipped with D3D_OK (check_fbo_status 420-449 only logs, device.c 2104-2144, GL 4.5 9.4.4)
        IDirect3DSurface9* c0 = rt(dev, {64, 64, D3DMULTISAMPLE_4_SAMPLES, 0}); IDirect3DSurface9* c1 = rt(dev, {64, 64, D3DMULTISAMPLE_NONE, 0});
        CHECK(c0 && c1 && dev->SetRenderTarget(0, c0) == D3D_OK && dev->SetRenderTarget(1, c1) == D3D_OK && dev->SetDepthStencilSurface(nullptr) == D3D_OK);
        CHECK(nx_attach(d->cur).incomplete);
        CHECK(dev->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFFFFFFFF, 1.0f, 0) == D3D_OK);
        float v[3] = {0, 0, 0}; CHECK(dev->SetFVF(D3DFVF_XYZ) == D3D_OK); CHECK(dev->DrawPrimitiveUP(D3DPT_POINTLIST, 1, v, sizeof v) == D3D_OK);
        // a following complete pass in the same submit still draws: RT1 unset, the pass is RT0 alone
        CHECK(dev->SetRenderTarget(1, nullptr) == D3D_OK); CHECK(!nx_attach(d->cur).incomplete);
        CHECK(dev->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFFFFFFFF, 1.0f, 0) == D3D_OK); CHECK(dev->DrawPrimitiveUP(D3DPT_POINTLIST, 1, v, sizeof v) == D3D_OK);
        // a NULL-format RT0 has no attachment: a multisampled depth with a single-sample RT1 is incomplete too (RT0 is not compared, the depth stays bound: Wine 647-649)
        IDirect3DSurface9* z4 = ds(dev, {64, 64, D3DMULTISAMPLE_4_SAMPLES, 0}); IDirect3DSurface9* nul = nullptr;
        CHECK(dev->CreateRenderTarget(64, 64, (D3DFORMAT)MAKEFOURCC('N', 'U', 'L', 'L'), D3DMULTISAMPLE_NONE, 0, FALSE, &nul, nullptr) == D3D_OK);
        CHECK(z4 && nul && dev->SetRenderTarget(0, nul) == D3D_OK && dev->SetRenderTarget(1, c1) == D3D_OK && dev->SetDepthStencilSurface(z4) == D3D_OK);
        NxAttach a = nx_attach(d->cur); CHECK(a.ds == nx_surf_core(z4)); CHECK(a.incomplete);
        if (c0) c0->Release(); if (c1) c1->Release(); if (z4) z4->Release(); if (nul) nul->Release();
    }
    dev->Release(); d3d->Release();
    printf(g_fail ? "FAIL %d\n" : "PASS\n", g_fail); return g_fail ? 1 : 0;
}
