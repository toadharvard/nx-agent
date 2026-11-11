// part e: the three rechecked lifecycle decisions. (a) Reset with another BackBufferCount, (b) GetLastPresentCount, (c) GetFrontBufferData destination formats. Sources are named at each check.
#include "t_common.h"
void t_e() {
    t_part('e'); g_profile = 0; IDirect3D9* dx = t_d3d(true); D3DPRESENT_PARAMETERS pp = t_pp(true, D3DSWAPEFFECT_DISCARD, 1); IDirect3DDevice9Ex* e = static_cast<IDirect3DDevice9Ex*>(t_dev(dx, &pp)); if (!CHECK(e)) return; IDirect3DDevice9* d = e;
    // (a) MS D3DPRESENT_PARAMETERS BackBufferCount (the parameter of the new swap chain; "The method fails if one back buffer cannot be created"; COPY needs exactly 1) + Wine d3d9 tests/device.c test_swapchain_parameters rows (Windows:
    //     Reset to counts 0 / 1 / 2 / 3 = D3D_OK, 4 and COPY with 2 = D3DERR_INVALIDCALL): the chain has the requested number of back buffers afterwards
    for (UINT c : {2u, 3u, 0u, 1u, 3u}) {
        D3DPRESENT_PARAMETERS rp = t_pp(true, D3DSWAPEFFECT_DISCARD, c); CHECK_HR(d->Reset(&rp), D3D_OK); IDirect3DSwapChain9* sc; d->GetSwapChain(0, &sc); D3DPRESENT_PARAMETERS q; sc->GetPresentParameters(&q); UINT want = c ? c : 1; CHECK_EQ(q.BackBufferCount, want); CHECK_EQ(rp.BackBufferCount, want);
        IDirect3DSurface9* b = nullptr; for (UINT i = 0; i < want; i++) { CHECK_HR(sc->GetBackBuffer(i, D3DBACKBUFFER_TYPE_MONO, &b), D3D_OK); if (b) b->Release(); } CHECK_HR(sc->GetBackBuffer(want, D3DBACKBUFFER_TYPE_MONO, &b), D3DERR_INVALIDCALL); sc->Release();
    }
    { D3DPRESENT_PARAMETERS rp = t_pp(true, D3DSWAPEFFECT_DISCARD, 4); CHECK_HR(d->Reset(&rp), D3DERR_INVALIDCALL); rp = t_pp(true, D3DSWAPEFFECT_COPY, 2); CHECK_HR(d->Reset(&rp), D3DERR_INVALIDCALL); rp = t_pp(true, D3DSWAPEFFECT_COPY, 1); CHECK_HR(d->Reset(&rp), D3D_OK); }
    // (b) MS GetLastPresentCount ("the number of times PresentEx has been called") and D3DPRESENTSTATS.PresentCount ("successful Present calls"): successful presents count, failed ones do not, one number for both methods
    IDirect3DSwapChain9* sc; d->GetSwapChain(0, &sc); IDirect3DSwapChain9Ex* sx; CHECK_HR(sc->QueryInterface(IID_IDirect3DSwapChain9Ex, (void**)&sx), D3D_OK); UINT c0 = 99; CHECK_HR(sx->GetLastPresentCount(&c0), D3D_OK);
    CHECK_HR(d->Present(nullptr, nullptr, nullptr, nullptr), D3D_OK); CHECK_HR(e->PresentEx(nullptr, nullptr, nullptr, nullptr, 0), D3D_OK); RECT bad = {5, 5, 5, 5}; CHECK_HR(d->Present(&bad, nullptr, nullptr, nullptr), D3DERR_INVALIDCALL);
    UINT c1 = 0; sx->GetLastPresentCount(&c1); CHECK_EQ(c1, c0 + 2); D3DPRESENTSTATS st; sx->GetPresentStats(&st); CHECK_EQ(st.PresentCount, c1); nx_life(O(d))->state = NX_DEV_LOST; CHECK_HR(d->Present(nullptr, nullptr, nullptr, nullptr), S_PRESENT_OCCLUDED); nx_life(O(d))->state = NX_DEV_OK;
    sx->GetLastPresentCount(&c1); CHECK_EQ(c1, c0 + 2); sx->Release(); sc->Release();
    // (c) MS GetFrontBufferData ("converted to the standard 32 bits per pixel format D3DFMT_A8R8G8B8"); Windows rows (visual.c): an A8R8G8B8 SYSTEMMEM surface and a SYSTEMMEM texture level work. Other formats: INVALIDCALL
    { D3DPRESENT_PARAMETERS rp = t_pp(true, D3DSWAPEFFECT_DISCARD, 1); rp.hDeviceWindow = (HWND)W_SCREEN; CHECK_HR(d->Reset(&rp), D3D_OK); }
    IDirect3DSurface9* s = nullptr; CHECK_HR(d->CreateOffscreenPlainSurface(640, 480, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &s, nullptr), D3D_OK); g_nfront = 0;
    CHECK_HR(d->GetFrontBufferData(0, s), D3D_OK); CHECK_EQ(g_nfront, 1); D3DLOCKED_RECT lr; CHECK_HR(s->LockRect(&lr, nullptr, D3DLOCK_READONLY), D3D_OK); const uint8_t* p = (const uint8_t*)lr.pBits + 3 * lr.Pitch + 2 * 4; CHECK(p[0] == 7 && p[1] == 3 && p[2] == 2 && p[3] == 200); s->UnlockRect(); s->Release();   // the recording hook writes (x, y, 7, 200) as R, G, B, A
    IDirect3DTexture9* t = nullptr; CHECK_HR(d->CreateTexture(640, 480, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &t, nullptr), D3D_OK); IDirect3DSurface9* l0 = nullptr; t->GetSurfaceLevel(0, &l0); CHECK_HR(d->GetFrontBufferData(0, l0), D3D_OK); l0->Release(); t->Release();
    for (uint32_t f : {(uint32_t)D3DFMT_X8R8G8B8, (uint32_t)D3DFMT_R5G6B5, (uint32_t)D3DFMT_X1R5G5B5, (uint32_t)D3DFMT_A1R5G5B5, (uint32_t)D3DFMT_A16B16G16R16F, (uint32_t)D3DFMT_L8}) {
        IDirect3DSurface9* o = nullptr; if (SUCCEEDED(d->CreateOffscreenPlainSurface(640, 480, (D3DFORMAT)f, D3DPOOL_SYSTEMMEM, &o, nullptr))) { CHECK_HR(d->GetFrontBufferData(0, o), D3DERR_INVALIDCALL); o->Release(); }
    }
    CHECK_HR(d->GetFrontBufferData(0, nullptr), D3DERR_INVALIDCALL); CHECK_HR(d->GetFrontBufferData(1, nullptr), D3DERR_INVALIDCALL);
    e->Release(); dx->Release();
}
