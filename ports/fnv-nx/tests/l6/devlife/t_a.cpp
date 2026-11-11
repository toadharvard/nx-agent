// part a: CreateDevice / CreateDeviceEx over the parameter domain against the python oracle of test_devlife.py (env tsv + the stated Wine rules), then the non-domain rules (null pointers)
#include "t_common.h"
static IDirect3D9* D[2][2];   // [profile][ex]
static void one(char* line) {
    unsigned obj, ad, dt, beh, focus, hdw, win, swap, cnt, iv, w, h, fmt, ms, msq, dsen, dsfmt, flags, refresh, prof, hr, bw, bh, bf, bc, fl2, dsf2, nimg, smp, dssmp;
    if (sscanf(line, "C %u %u %u %x %x %x %u %u %u %x %u %u %u %u %u %u %u %x %u %u | %x %u %u %u %u %x %u %u %u %u", &obj, &ad, &dt, &beh, &focus, &hdw, &win, &swap, &cnt, &iv, &w, &h, &fmt, &ms, &msq, &dsen, &dsfmt, &flags, &refresh, &prof,
               &hr, &bw, &bh, &bf, &bc, &fl2, &dsf2, &nimg, &smp, &dssmp) < 30) { CHECK(false); printf("  bad line %s", line); return; }
    g_profile = prof; IDirect3D9* d3d = D[prof][obj != 0]; D3DPRESENT_PARAMETERS pp = t_pp(win, swap, cnt); pp.hDeviceWindow = (HWND)(uintptr_t)hdw; pp.BackBufferWidth = w; pp.BackBufferHeight = h; pp.BackBufferFormat = (D3DFORMAT)fmt;
    pp.MultiSampleType = (D3DMULTISAMPLE_TYPE)ms; pp.MultiSampleQuality = msq; pp.EnableAutoDepthStencil = dsen; pp.AutoDepthStencilFormat = (D3DFORMAT)dsfmt; pp.Flags = flags; pp.FullScreen_RefreshRateInHz = refresh; pp.PresentationInterval = iv;
    D3DPRESENT_PARAMETERS before = pp; IDirect3DDevice9* dev = (IDirect3DDevice9*)1; HRESULT r; t_clear(); D3DDISPLAYMODEEX mx = {sizeof mx};
    if (obj == 2 || obj == 3) { IDirect3DDevice9Ex* e = nullptr; r = static_cast<IDirect3D9Ex*>(d3d)->CreateDeviceEx(ad, (D3DDEVTYPE)dt, (HWND)(uintptr_t)focus, beh, &pp, obj == 3 ? &mx : nullptr, &e); dev = e; }
    else r = d3d->CreateDevice(ad, (D3DDEVTYPE)dt, (HWND)(uintptr_t)focus, beh, &pp, &dev);
    if (!CHECK_HR(r, hr)) { printf("  case: %s  got %x\n", line, (unsigned)r); if (r == D3D_OK) dev->Release(); return; }
    NxObj* d3o = O(d3d);
    if (r != D3D_OK) { CHECK(dev == nullptr); CHECK(!memcmp(&pp, &before, sizeof pp)); CHECK_EQ(d3o->pub, 1); CHECK_EQ(t_count(R_CREATE), t_count(R_DESTROY)); return; }
    CHECK_EQ(d3o->pub, 2); CHECK(pp.BackBufferWidth == bw && pp.BackBufferHeight == bh && pp.BackBufferFormat == bf && pp.BackBufferCount == bc && pp.Flags == fl2 && pp.AutoDepthStencilFormat == dsf2);
    pp.BackBufferWidth = before.BackBufferWidth; pp.BackBufferHeight = before.BackBufferHeight; pp.BackBufferFormat = before.BackBufferFormat; pp.BackBufferCount = before.BackBufferCount; pp.Flags = before.Flags; pp.AutoDepthStencilFormat = before.AutoDepthStencilFormat;
    CHECK(!memcmp(&pp, &before, sizeof pp));                                                    // nothing else is rewritten
    CHECK_EQ(t_count(R_CREATE), nimg); { int first = -1, last = -1; for (int i = 0; i < g_nrec; i++) if (g_rec[i].op == R_CREATE) { if (first < 0) first = i; last = i; }
      if (nimg > (dsen ? 1u : 0u)) CHECK_EQ(g_rec[first].desc.samples, smp); if (dsen && last >= 0) CHECK_EQ(g_rec[last].desc.samples, dssmp); }
    D3DDEVICE_CREATION_PARAMETERS cp; CHECK_HR(dev->GetCreationParameters(&cp), D3D_OK); CHECK(cp.AdapterOrdinal == ad && cp.DeviceType == (D3DDEVTYPE)dt && cp.hFocusWindow == (HWND)(uintptr_t)focus && cp.BehaviorFlags == beh);
    IDirect3DSwapChain9* sc = nullptr; CHECK_HR(dev->GetSwapChain(0, &sc), D3D_OK); D3DPRESENT_PARAMETERS gp; if (sc) { CHECK_HR(sc->GetPresentParameters(&gp), D3D_OK); CHECK(gp.BackBufferWidth == bw && gp.BackBufferHeight == bh && gp.BackBufferFormat == bf && gp.BackBufferCount == bc);
        CHECK(gp.hDeviceWindow == (HWND)(uintptr_t)(hdw ? hdw : focus) && gp.SwapEffect == swap && gp.Windowed == win && gp.PresentationInterval == iv && gp.Flags == fl2); sc->Release(); }
    DWORD z = 9; dev->GetRenderState(D3DRS_ZENABLE, &z); CHECK_EQ(z, dsen ? D3DZB_TRUE : D3DZB_FALSE);
    D3DVIEWPORT9 vp; dev->GetViewport(&vp); CHECK(vp.Width == bw && vp.Height == bh);
    IDirect3DSurface9 *bb = nullptr, *rt = nullptr; dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb); dev->GetRenderTarget(0, &rt); CHECK(bb && bb == rt); if (bb) bb->Release(); if (rt) rt->Release();
    IDirect3DSurface9* ds = nullptr; HRESULT hd = dev->GetDepthStencilSurface(&ds); CHECK_HR(hd, dsen ? D3D_OK : D3DERR_NOTFOUND); if (ds) ds->Release();
    int cr = t_count(R_CREATE); t_clear(); CHECK_EQ(dev->Release(), 0); CHECK_EQ(t_count(R_DESTROY), cr); CHECK_EQ(d3o->pub, 1);
}
void t_a() {
    t_part('a'); for (int p = 0; p < 2; p++) { g_profile = p; D[p][0] = t_d3d(false); D[p][1] = t_d3d(true); }
    FILE* f = t_open("create.txt"); char line[1024]; int n = 0;
    while (f && fgets(line, sizeof line, f)) { if (line[0] == 'C') { one(line); n++; } }
    if (f) fclose(f); CHECK(n > 1000);
    g_profile = 0; IDirect3DDevice9* dev = nullptr; D3DPRESENT_PARAMETERS pp = t_pp();
    CHECK_HR(D[0][0]->CreateDevice(0, D3DDEVTYPE_HAL, (HWND)W_A, 0, nullptr, &dev), D3DERR_INVALIDCALL); CHECK_HR(D[0][0]->CreateDevice(0, D3DDEVTYPE_HAL, (HWND)W_A, 0, &pp, nullptr), D3DERR_INVALIDCALL); CHECK_EQ(O(D[0][0])->pub, 1);
    // an IDirect3D9Ex creates the Ex class; IDirect3D9 the base class (Wine: extended iff the d3d9 object is)
    IDirect3DDevice9* a = t_dev(D[0][1], &pp); IDirect3DDevice9* b = t_dev(D[0][0], &pp); void* x; CHECK(a && b);
    CHECK_HR(a->QueryInterface(IID_IDirect3DDevice9Ex, &x), D3D_OK); if (x) static_cast<IUnknown*>(x)->Release(); CHECK_HR(b->QueryInterface(IID_IDirect3DDevice9Ex, &x), E_NOINTERFACE);
    IDirect3D9* g = nullptr; CHECK_HR(a->GetDirect3D(&g), D3D_OK); CHECK(g == D[0][1]); if (g) g->Release(); a->Release(); b->Release();
}
