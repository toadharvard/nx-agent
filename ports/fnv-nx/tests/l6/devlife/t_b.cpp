// part b: reference counts and identity of the implicit objects (README R2, R3; Wine d3d9/tests/device.c test_refcount, test_swapchain: the findings written against Windows)
#include "t_common.h"
void t_b() {
    t_part('b'); g_profile = 0; IDirect3D9* d3d = t_d3d(false); NxObj* d3o = O(d3d); D3DPRESENT_PARAMETERS pp = t_pp(true, D3DSWAPEFFECT_DISCARD, 2); pp.EnableAutoDepthStencil = TRUE; pp.AutoDepthStencilFormat = D3DFMT_D24S8;
    t_clear(); IDirect3DDevice9* dev = t_dev(d3d, &pp); NxObj* dv = O(dev); CHECK(dev);
    CHECK_EQ(d3o->pub, 2); CHECK_EQ(dv->pub, 1);                                         // R2: IDirect3D9 count 2 after CreateDevice
    IDirect3DSwapChain9 *sc = nullptr, *sc2 = nullptr; CHECK_HR(dev->GetSwapChain(0, &sc), D3D_OK); CHECK_HR(dev->GetSwapChain(0, &sc2), D3D_OK); CHECK(sc && sc == sc2);
    CHECK_EQ(O(sc)->pub, 2); CHECK_EQ(dv->pub, 2); sc2->Release(); CHECK_EQ(O(sc)->pub, 1);   // R3: the implicit chain has public count 0, the device is held only while it is referenced
    CHECK_EQ(sc->Release(), 0); CHECK_EQ(dv->pub, 1); CHECK_EQ(sc->Release(), 0);          // R6: Release at 0 stays 0
    IDirect3DSwapChain9* none = (IDirect3DSwapChain9*)1; CHECK_HR(dev->GetSwapChain(1, &none), D3DERR_INVALIDCALL); CHECK(none == nullptr); CHECK_EQ(dev->GetNumberOfSwapChains(), 1);
    IDirect3DSurface9 *b0 = nullptr, *b1 = nullptr, *b0b = nullptr, *rt = nullptr, *ds = nullptr, *none2 = (IDirect3DSurface9*)5;
    CHECK_HR(dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &b0), D3D_OK); CHECK_HR(dev->GetBackBuffer(0, 1, D3DBACKBUFFER_TYPE_MONO, &b1), D3D_OK); CHECK_HR(dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &b0b), D3D_OK);
    CHECK(b0 && b1 && b0 != b1 && b0 == b0b); CHECK_EQ(O(b0)->pub, 2); CHECK_EQ(O(b1)->pub, 1); CHECK_EQ(dv->pub, 3);                  // a held back buffer holds the device once
    CHECK_HR(dev->GetBackBuffer(0, 2, D3DBACKBUFFER_TYPE_MONO, &none2), D3DERR_INVALIDCALL); CHECK(none2 == nullptr);               // device level nulls the out pointer
    IDirect3DSurface9* keep = (IDirect3DSurface9*)7; IDirect3DSwapChain9* sc0 = nullptr; dev->GetSwapChain(0, &sc0); CHECK_HR(sc0->GetBackBuffer(5, D3DBACKBUFFER_TYPE_MONO, &keep), D3DERR_INVALIDCALL); CHECK(keep == (IDirect3DSurface9*)7);   // swap chain level: untouched
    IDirect3DSwapChain9* cont = nullptr; CHECK_HR(b0->GetContainer(IID_IDirect3DSwapChain9, (void**)&cont), D3D_OK); CHECK(cont == sc0); if (cont) cont->Release(); sc0->Release();
    D3DSURFACE_DESC d; CHECK_HR(b0->GetDesc(&d), D3D_OK); CHECK(d.Usage == D3DUSAGE_RENDERTARGET && d.Pool == D3DPOOL_DEFAULT && d.Width == 640 && d.Height == 480 && d.Type == D3DRTYPE_SURFACE);
    CHECK_HR(dev->GetRenderTarget(0, &rt), D3D_OK); CHECK(rt == b0); CHECK_EQ(O(b0)->pub, 3); rt->Release();
    CHECK_HR(dev->GetDepthStencilSurface(&ds), D3D_OK); CHECK(ds); IDirect3DDevice9* cd = nullptr; CHECK_HR(ds->GetContainer(IID_IDirect3DDevice9, (void**)&cd), D3D_OK); CHECK(cd == dev); if (cd) cd->Release();
    CHECK_HR(ds->GetDesc(&d), D3D_OK); CHECK(d.Usage == D3DUSAGE_DEPTHSTENCIL && d.Format == D3DFMT_D24S8); ds->Release();
    IDirect3DDevice9* gd = nullptr; CHECK_HR(b1->GetDevice(&gd), D3D_OK); CHECK(gd == dev); gd->Release();
    b0b->Release(); b0->Release(); CHECK_EQ(b1->Release(), 0); CHECK_EQ(dv->pub, 1);
    // an additional swap chain is a normal object (public 1, one device reference); its back buffers keep it alive (owner = chain)
    D3DPRESENT_PARAMETERS ap = t_pp(true, D3DSWAPEFFECT_COPY, 1); ap.hDeviceWindow = (HWND)W_SMALL; ap.BackBufferWidth = 64; ap.BackBufferHeight = 32; IDirect3DSwapChain9* ac = nullptr; t_clear();
    CHECK_HR(dev->CreateAdditionalSwapChain(&ap, &ac), D3D_OK); CHECK(ac && ac != sc); CHECK_EQ(O(ac)->pub, 1); CHECK_EQ(dv->pub, 2); CHECK_EQ(dev->GetNumberOfSwapChains(), 1); CHECK_EQ(t_count(R_CREATE), 1);
    IDirect3DSurface9* ab = nullptr; ac->GetBackBuffer(0, D3DBACKBUFFER_TYPE_MONO, &ab); CHECK_EQ(O(ab)->pub, 1); CHECK_EQ(O(ac)->pub, 2); CHECK_EQ(ac->Release(), 1); CHECK_EQ(dv->pub, 2);   // chain survives while its back buffer is held
    t_clear(); CHECK_EQ(ab->Release(), 0); CHECK_EQ(t_count(R_DESTROY), 1); CHECK_EQ(dv->pub, 1);
    // R4-like: foreign pointers are rejected, the device dies with its last reference and takes the implicit objects (2 back buffers + auto depth stencil) with it
    t_clear(); CHECK_EQ(dev->Release(), 0); CHECK_EQ(t_count(R_DESTROY), 3); CHECK_EQ(d3o->pub, 1); CHECK_EQ(d3d->Release(), 0);
    // a back buffer in a state bind outlives the application's reference; Release at 0 stays 0
    d3d = t_d3d(false); dev = t_dev(d3d, &pp); IDirect3DSurface9* bb = nullptr; dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb); CHECK_EQ(bb->Release(), 0); CHECK_EQ(bb->Release(), 0);
    IDirect3DSurface9* rt0 = nullptr; CHECK_HR(dev->GetRenderTarget(0, &rt0), D3D_OK); CHECK(rt0 == bb); rt0->Release(); dev->Release(); d3d->Release();
}
