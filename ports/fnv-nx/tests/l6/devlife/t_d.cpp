// part d: swap chains: additional chains, the back buffer rotation rule per swap effect (dev_chain.cpp header: wined3d_swapchain_gl_rotate), recorded present calls (rects, gamma, interval, frame), GetFrontBufferData
#include "t_common.h"
static const uint32_t IVS[6][2] = {{0, 1}, {D3DPRESENT_INTERVAL_ONE, 1}, {D3DPRESENT_INTERVAL_TWO, 2}, {D3DPRESENT_INTERVAL_THREE, 3}, {D3DPRESENT_INTERVAL_FOUR, 4}, {D3DPRESENT_INTERVAL_IMMEDIATE, 0}};   // d3d9 value -> NxPresent.interval
void t_d() {
    t_part('d'); g_profile = 0; IDirect3D9* d3d = t_d3d(false);
    // ---- rotation: images rotate, objects stay (count >= 2, every swap effect); count 1: nothing moves
    static const uint32_t SE[4] = {D3DSWAPEFFECT_DISCARD, D3DSWAPEFFECT_FLIP, D3DSWAPEFFECT_COPY, D3DSWAPEFFECT_DISCARD};
    for (uint32_t cnt = 1; cnt <= 3; cnt++) for (int se = 0; se < 3; se++) {
        if (SE[se] == D3DSWAPEFFECT_COPY && cnt > 1) continue;
        D3DPRESENT_PARAMETERS pp = t_pp(true, SE[se], cnt); t_clear(); t_clear_hooks(); IDirect3DDevice9* dev = t_dev(d3d, &pp); uint32_t ids[3]; for (uint32_t i = 0; i < cnt; i++) ids[i] = t_created(i);
        IDirect3DSurface9* b[3]; for (uint32_t i = 0; i < cnt; i++) dev->GetBackBuffer(0, i, D3DBACKBUFFER_TYPE_MONO, &b[i]);
        for (uint32_t k = 0; k < 7; k++) { CHECK_HR(dev->Present(nullptr, nullptr, nullptr, nullptr), D3D_OK); CHECK_EQ(g_pres[k].p.image, ids[k % cnt]); CHECK_EQ(g_pres[k].p.frame, k + 1); }
        IDirect3DSurface9* c; for (uint32_t i = 0; i < cnt; i++) { dev->GetBackBuffer(0, i, D3DBACKBUFFER_TYPE_MONO, &c); CHECK(c == b[i]); c->Release(); b[i]->Release(); }   // identity never moves
        dev->Release();
    }
    // ---- recorded present: default / explicit rects, interval, gamma pointer + content, validation without a hook call
    D3DPRESENT_PARAMETERS pp = t_pp(); pp.BackBufferWidth = 320; pp.BackBufferHeight = 200; t_clear_hooks(); IDirect3DDevice9* dev = t_dev(d3d, &pp);
    CHECK_HR(dev->Present(nullptr, nullptr, nullptr, nullptr), D3D_OK); NxPresent p = g_pres[0].p;
    CHECK(p.sx == 0 && p.sy == 0 && p.sw == 320 && p.sh == 200 && p.dx == 0 && p.dy == 0 && p.dw == 800 && p.dh == 600 && p.gamma == 0 && !g_pres[0].has_gamma);
    RECT s = {10, 20, 110, 70}, dr = {5, 6, 305, 206}; CHECK_HR(dev->Present(&s, &dr, nullptr, nullptr), D3D_OK); p = g_pres[1].p; CHECK(p.sx == 10 && p.sy == 20 && p.sw == 100 && p.sh == 50 && p.dx == 5 && p.dy == 6 && p.dw == 300 && p.dh == 200);
    D3DGAMMARAMP r; for (int i = 0; i < 256; i++) { r.red[i] = i * 3; r.green[i] = 65535 - i; r.blue[i] = i; } dev->SetGammaRamp(0, D3DSGR_NO_CALIBRATION, &r); dev->Present(nullptr, nullptr, nullptr, nullptr);
    CHECK(g_pres[2].has_gamma && !memcmp(g_pres[2].gamma, &r, sizeof r));
    int n0 = g_npres; RECT bads[6] = {{-1, 0, 10, 10}, {0, 0, 321, 10}, {5, 5, 5, 20}, {10, 10, 5, 5}, {0, 0, 320, 201}, {0, 0, 0, 0}};
    for (int i = 0; i < 6; i++) CHECK_HR(dev->Present(&bads[i], nullptr, nullptr, nullptr), D3DERR_INVALIDCALL);
    RECT bd[3] = {{0, 0, 801, 10}, {-1, 0, 10, 10}, {0, 5, 0, 20}}; for (int i = 0; i < 3; i++) CHECK_HR(dev->Present(nullptr, &bd[i], nullptr, nullptr), D3DERR_INVALIDCALL); CHECK_EQ(g_npres, n0);
    CHECK_HR(dev->Present(nullptr, nullptr, (HWND)W_SMALL, nullptr), D3D_OK);                                    // the window override is ignored (one output)
    g_present_result = 1; CHECK_HR(dev->Present(nullptr, nullptr, nullptr, nullptr), D3DERR_DRIVERINTERNALERROR); g_present_result = 0;
    NxDevLife* L = nx_life(O(dev)); L->state = NX_DEV_LOST; CHECK_HR(dev->Present(nullptr, nullptr, nullptr, nullptr), D3DERR_DEVICELOST); L->state = NX_DEV_OK; dev->Release();
    for (int i = 0; i < 6; i++) { D3DPRESENT_PARAMETERS q = t_pp(); q.PresentationInterval = IVS[i][0]; IDirect3DDevice9* v = t_dev(d3d, &q); int k = g_npres; v->Present(nullptr, nullptr, nullptr, nullptr); CHECK_EQ(g_pres[k & 255].p.interval, IVS[i][1]); v->Release(); }
    { D3DPRESENT_PARAMETERS q = t_pp(); q.hDeviceWindow = (HWND)W_BAD; IDirect3DDevice9* v = t_dev(d3d, &q, nullptr, (void*)W_BAD); CHECK(v); if (v) { CHECK_HR(v->Present(nullptr, nullptr, nullptr, nullptr), D3DERR_INVALIDCALL); RECT dd = {0, 0, 10, 10}; CHECK_HR(v->Present(nullptr, &dd, nullptr, nullptr), D3DERR_INVALIDCALL); v->Release(); } }
    // ---- lockable back buffers: the CPU copy is stale after the rotation (a read lock reads the GPU image once)
    { D3DPRESENT_PARAMETERS q = t_pp(true, D3DSWAPEFFECT_FLIP, 2); q.Flags = D3DPRESENTFLAG_LOCKABLE_BACKBUFFER; IDirect3DDevice9* v = t_dev(d3d, &q); IDirect3DSurface9* b; v->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &b); D3DLOCKED_RECT lr; t_clear();
      CHECK_HR(b->LockRect(&lr, nullptr, D3DLOCK_READONLY), D3D_OK); b->UnlockRect(); CHECK_EQ(t_count(R_READ), 0); v->Present(nullptr, nullptr, nullptr, nullptr); CHECK_HR(b->LockRect(&lr, nullptr, D3DLOCK_READONLY), D3D_OK); b->UnlockRect(); CHECK_EQ(t_count(R_READ), 1); b->Release(); v->Release(); }
    // ---- additional chains: windowed only, none next to a full-screen device, own parameters write-back, independent rotation + frame counter
    D3DPRESENT_PARAMETERS ap = t_pp(true, D3DSWAPEFFECT_FLIP, 2); ap.hDeviceWindow = (HWND)W_SMALL; ap.BackBufferWidth = ap.BackBufferHeight = 0; ap.BackBufferFormat = D3DFMT_UNKNOWN; IDirect3DDevice9* a = t_dev(d3d, &pp); IDirect3DSwapChain9* ac = nullptr; t_clear(); t_clear_hooks();
    CHECK_HR(a->CreateAdditionalSwapChain(&ap, &ac), D3D_OK); CHECK(ap.BackBufferWidth == 100 && ap.BackBufferHeight == 50 && ap.BackBufferFormat == D3DFMT_X8R8G8B8 && ap.BackBufferCount == 2); uint32_t i0 = t_created(0), i1 = t_created(1);
    ac->Present(nullptr, nullptr, nullptr, nullptr, 0); ac->Present(nullptr, nullptr, nullptr, nullptr, 0); a->Present(nullptr, nullptr, nullptr, nullptr); CHECK_EQ(g_pres[0].p.image, i0); CHECK_EQ(g_pres[1].p.image, i1); CHECK(g_pres[2].p.image != i0 && g_pres[2].p.image != i1);
    CHECK(g_pres[0].p.dw == 100 && g_pres[0].p.dh == 50 && g_pres[0].p.sw == 100); D3DPRESENT_PARAMETERS gp; CHECK_HR(ac->GetPresentParameters(&gp), D3D_OK); CHECK(gp.hDeviceWindow == (HWND)W_SMALL && gp.BackBufferWidth == 100);
    IDirect3DDevice9* gd = nullptr; CHECK_HR(ac->GetDevice(&gd), D3D_OK); CHECK(gd == a); gd->Release(); IDirect3DSurface9* ab = nullptr; CHECK_HR(ac->GetBackBuffer(1, D3DBACKBUFFER_TYPE_MONO, &ab), D3D_OK); IDirect3DSwapChain9* cc = nullptr; ab->GetContainer(IID_IDirect3DSwapChain9, (void**)&cc); CHECK(cc == ac); cc->Release(); ab->Release();
    ac->Release(); D3DPRESENT_PARAMETERS fsp = t_pp(false); uint32_t sw, sh; t_screen(&sw, &sh); fsp.BackBufferWidth = sw; fsp.BackBufferHeight = sh; fsp.hDeviceWindow = (HWND)W_SCREEN;
    CHECK_HR(a->CreateAdditionalSwapChain(&fsp, &ac), D3DERR_INVALIDCALL); D3DPRESENT_PARAMETERS bs = ap; bs.SwapEffect = (D3DSWAPEFFECT)0; CHECK_HR(a->CreateAdditionalSwapChain(&bs, &ac), D3DERR_INVALIDCALL); CHECK_HR(a->CreateAdditionalSwapChain(nullptr, &ac), D3DERR_INVALIDCALL); a->Release();
    D3DPRESENT_PARAMETERS full = t_pp(false); full.BackBufferWidth = sw; full.BackBufferHeight = sh; full.hDeviceWindow = (HWND)W_SCREEN; IDirect3DDevice9* fdev = t_dev(d3d, &full, nullptr, (void*)W_SCREEN); CHECK(fdev);
    if (fdev) { D3DPRESENT_PARAMETERS w2 = t_pp(); CHECK_HR(fdev->CreateAdditionalSwapChain(&w2, &ac), D3DERR_INVALIDCALL); fdev->Release(); }
    // ---- GetFrontBufferData: A8R8G8B8 destination (X8R8G8B8 = INVALIDCALL), windowed offset = client origin (10, 20), size checks
    D3DPRESENT_PARAMETERS fp = t_pp(); IDirect3DDevice9* v = t_dev(d3d, &fp); IDirect3DSurface9 *dst = nullptr, *tiny = nullptr, *x8 = nullptr, *r5 = nullptr;
    v->CreateOffscreenPlainSurface(700, 520, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &dst, nullptr); v->CreateOffscreenPlainSurface(100, 100, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &tiny, nullptr); v->CreateOffscreenPlainSurface(700, 520, D3DFMT_X8R8G8B8, D3DPOOL_SYSTEMMEM, &x8, nullptr); v->CreateOffscreenPlainSurface(700, 520, D3DFMT_R5G6B5, D3DPOOL_SYSTEMMEM, &r5, nullptr);
    CHECK_HR(v->GetFrontBufferData(0, dst), D3D_OK); D3DLOCKED_RECT lr; dst->LockRect(&lr, nullptr, D3DLOCK_READONLY); const uint8_t* px = (const uint8_t*)lr.pBits + 30 * lr.Pitch + 40 * 4; CHECK(px[0] == 7 && px[1] == 10 && px[2] == 30 && px[3] == 200);   // dest (40, 30) = front (30, 10)
    dst->UnlockRect(); CHECK_HR(v->GetFrontBufferData(0, x8), D3DERR_INVALIDCALL);   // M1 recheck (c): MS GetFrontBufferData converts to D3DFMT_A8R8G8B8 only, no source accepts X8R8G8B8
    CHECK_HR(v->GetFrontBufferData(0, tiny), D3DERR_INVALIDCALL); CHECK_HR(v->GetFrontBufferData(0, r5), D3DERR_INVALIDCALL); CHECK_HR(v->GetFrontBufferData(0, nullptr), D3DERR_INVALIDCALL); CHECK_HR(v->GetFrontBufferData(1, dst), D3DERR_INVALIDCALL);
    IDirect3DSwapChain9* sc; v->GetSwapChain(0, &sc); CHECK_HR(sc->GetFrontBufferData(dst), D3D_OK); D3DDISPLAYMODE dm, dm2; CHECK_HR(sc->GetDisplayMode(&dm), D3D_OK); CHECK_HR(v->GetDisplayMode(0, &dm2), D3D_OK); CHECK(!memcmp(&dm, &dm2, sizeof dm) && dm.Width == sw && dm.Height == sh && dm.Format == D3DFMT_X8R8G8B8);
    CHECK_HR(v->GetDisplayMode(1, &dm2), D3DERR_INVALIDCALL); sc->Release(); dst->Release(); tiny->Release(); x8->Release(); r5->Release(); v->Release(); d3d->Release();
}
