// part c: Reset / ResetEx (Wine d3d9 tests test_reset, test_swapchain_parameters, test_reset_resources; d3d9_device_reset): preconditions, state after Reset = defaults (compared with a fresh device through the Get* methods), implicit objects recreated
#include "t_common.h"
static void snapshot(IDirect3DDevice9* d, uint32_t* o, int* n) {   // every state the model has, through the public getters
    int k = 0; DWORD v;
    for (DWORD i = 0; i < 256; i++) { v = 0xDEAD; d->GetRenderState((D3DRENDERSTATETYPE)i, &v); o[k++] = v; }
    for (DWORD s = 0; s < 16; s++) for (DWORD t = 1; t < 14; t++) { v = 0xDEAD; d->GetSamplerState(s, (D3DSAMPLERSTATETYPE)t, &v); o[k++] = v; }
    for (DWORD s = 0; s < 8; s++) for (DWORD t = 1; t < 33; t++) { v = 0xDEAD; d->GetTextureStageState(s, (D3DTEXTURESTAGESTATETYPE)t, &v); o[k++] = v; }
    D3DVIEWPORT9 vp; d->GetViewport(&vp); memcpy(o + k, &vp, sizeof vp); k += sizeof vp / 4; RECT sc; d->GetScissorRect(&sc); memcpy(o + k, &sc, sizeof sc); k += 4;
    D3DMATRIX m; d->GetTransform(D3DTS_WORLD, &m); memcpy(o + k, &m, sizeof m); k += 16; D3DMATERIAL9 mt; d->GetMaterial(&mt); memcpy(o + k, &mt, sizeof mt); k += sizeof mt / 4; *n = k;
}
static void dirty(IDirect3DDevice9* d) {
    d->SetRenderState(D3DRS_ZWRITEENABLE, FALSE); d->SetRenderState(D3DRS_CULLMODE, D3DCULL_NONE); d->SetRenderState(D3DRS_ALPHABLENDENABLE, TRUE); d->SetRenderState(D3DRS_AMBIENT, 0x12345678); d->SetSamplerState(3, D3DSAMP_MINFILTER, D3DTEXF_POINT);
    d->SetTextureStageState(2, D3DTSS_COLOROP, D3DTOP_ADD); D3DVIEWPORT9 vp = {1, 2, 30, 40, 0.25f, 0.75f}; d->SetViewport(&vp); RECT sc = {3, 4, 5, 6}; d->SetScissorRect(&sc); D3DMATRIX m = {}; m._11 = 5; d->SetTransform(D3DTS_WORLD, &m);
    D3DMATERIAL9 mt = {}; mt.Power = 3; d->SetMaterial(&mt); d->SetRenderState(D3DRS_ZENABLE, D3DZB_FALSE);
}
void t_c() {
    t_part('c'); g_profile = 0; IDirect3D9* d3d = t_d3d(false); NxObj* d3o = O(d3d); uint32_t sa[4096], sb[4096]; int na, nb; HRESULT hr;
    // ---- defaults after Reset == a fresh device with the same parameters (auto depth stencil: ZENABLE true)
    D3DPRESENT_PARAMETERS pp = t_pp(true, D3DSWAPEFFECT_DISCARD, 1); pp.EnableAutoDepthStencil = TRUE; pp.AutoDepthStencilFormat = D3DFMT_D24S8;
    IDirect3DDevice9* a = t_dev(d3d, &pp); D3DPRESENT_PARAMETERS p2 = pp; IDirect3DDevice9* fresh = t_dev(d3d, &p2); dirty(a); snapshot(a, sa, &na); snapshot(fresh, sb, &nb);
    CHECK(na == nb && memcmp(sa, sb, na * 4)); CHECK_HR(a->TestCooperativeLevel(), D3D_OK);
    t_clear(); D3DPRESENT_PARAMETERS rp = pp; CHECK_HR(a->Reset(&rp), D3D_OK); CHECK_EQ(t_count(R_DESTROY), 2); CHECK_EQ(t_count(R_CREATE), 2);   // back buffer + auto depth stencil recreated
    snapshot(a, sa, &na); CHECK(na == nb && !memcmp(sa, sb, na * 4)); CHECK_HR(a->TestCooperativeLevel(), D3D_OK);
    IDirect3DSurface9 *rt = nullptr, *bb = nullptr, *ds = nullptr; a->GetRenderTarget(0, &rt); a->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb); a->GetDepthStencilSurface(&ds); CHECK(rt && rt == bb && ds); if (rt) rt->Release(); if (bb) bb->Release(); if (ds) ds->Release();
    // ---- the precondition: DEFAULT pool resources
    IDirect3DTexture9* tex = nullptr; CHECK_HR(a->CreateTexture(16, 16, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &tex, nullptr), D3D_OK);
    rp = pp; CHECK_HR(a->Reset(&rp), D3DERR_INVALIDCALL); CHECK_HR(a->TestCooperativeLevel(), D3DERR_DEVICENOTRESET); CHECK_HR(a->Present(nullptr, nullptr, nullptr, nullptr), D3DERR_DEVICELOST);
    CHECK_HR(a->Reset(&rp), D3DERR_INVALIDCALL); tex->Release(); CHECK_HR(a->Reset(&rp), D3D_OK); CHECK_HR(a->TestCooperativeLevel(), D3D_OK);
    IDirect3DVertexBuffer9* vb = nullptr; a->CreateVertexBuffer(256, 0, 0, D3DPOOL_DEFAULT, &vb, nullptr); CHECK_HR(a->Reset(&rp), D3DERR_INVALIDCALL); vb->Release(); CHECK_HR(a->Reset(&rp), D3D_OK);
    IDirect3DIndexBuffer9* ib = nullptr; a->CreateIndexBuffer(256, 0, D3DFMT_INDEX16, D3DPOOL_DEFAULT, &ib, nullptr); CHECK_HR(a->Reset(&rp), D3DERR_INVALIDCALL); ib->Release(); CHECK_HR(a->Reset(&rp), D3D_OK);
    IDirect3DSurface9* rts = nullptr; a->CreateRenderTarget(16, 16, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_NONE, 0, FALSE, &rts, nullptr); CHECK_HR(a->Reset(&rp), D3DERR_INVALIDCALL); rts->Release(); CHECK_HR(a->Reset(&rp), D3D_OK);
    IDirect3DTexture9* mt = nullptr; IDirect3DVertexBuffer9* sv = nullptr; IDirect3DSurface9* ss = nullptr;   // MANAGED / SYSTEMMEM / SCRATCH do not block
    a->CreateTexture(16, 16, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &mt, nullptr); a->CreateVertexBuffer(256, 0, 0, D3DPOOL_SYSTEMMEM, &sv, nullptr); a->CreateOffscreenPlainSurface(8, 8, D3DFMT_A8R8G8B8, D3DPOOL_SCRATCH, &ss, nullptr);
    CHECK_HR(a->Reset(&rp), D3D_OK); mt->Release(); sv->Release(); ss->Release();
    a->CreateTexture(16, 16, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &tex, nullptr); a->SetTexture(0, tex); tex->Release();   // released but bound: the Reset unbinds first (wined3d state_unbind_resources)
    CHECK_HR(a->Reset(&rp), D3D_OK); CHECK_HR(a->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb), D3D_OK);
    CHECK_HR(a->Reset(&rp), D3DERR_INVALIDCALL); bb->Release(); CHECK_HR(a->Reset(&rp), D3D_OK);                               // a held back buffer is a resource with a reference
    // ---- documented decision: state blocks and additional chains do not block (Wine); a recording block is dropped; the cursor image is dropped
    IDirect3DStateBlock9* blk = nullptr; a->CreateStateBlock(D3DSBT_ALL, &blk); IDirect3DSwapChain9* ac = nullptr; D3DPRESENT_PARAMETERS ap = t_pp(); ap.hDeviceWindow = (HWND)W_SMALL; a->CreateAdditionalSwapChain(&ap, &ac);
    a->BeginStateBlock(); CHECK_HR(a->Reset(&rp), D3D_OK); IDirect3DStateBlock9* eb = nullptr; CHECK_HR(a->EndStateBlock(&eb), D3DERR_INVALIDCALL); blk->Release(); ac->Release();
    // ---- invalid parameters: INVALIDCALL, nothing changes (state stays, device stays OK); each of the d3d9-level rules
    dirty(a); snapshot(a, sa, &na); D3DPRESENT_PARAMETERS bad = pp; bad.SwapEffect = (D3DSWAPEFFECT)0; CHECK_HR(a->Reset(&bad), D3DERR_INVALIDCALL); bad = pp; bad.BackBufferCount = 4; CHECK_HR(a->Reset(&bad), D3DERR_INVALIDCALL);
    bad = pp; bad.SwapEffect = D3DSWAPEFFECT_COPY; bad.BackBufferCount = 2; CHECK_HR(a->Reset(&bad), D3DERR_INVALIDCALL); bad = pp; bad.PresentationInterval = 3; CHECK_HR(a->Reset(&bad), D3DERR_INVALIDCALL); CHECK_HR(a->Reset(nullptr), D3DERR_INVALIDCALL);
    snapshot(a, sb, &nb); CHECK(!memcmp(sa, sb, na * 4)); CHECK_HR(a->TestCooperativeLevel(), D3D_OK);
    // ---- sizes, count, write-back (only width, height, format, count), full screen
    D3DPRESENT_PARAMETERS z = t_pp(true, D3DSWAPEFFECT_FLIP, 3); z.BackBufferWidth = z.BackBufferHeight = 0; z.BackBufferFormat = D3DFMT_UNKNOWN; z.Flags = 0xf000; z.hDeviceWindow = (HWND)W_A; D3DPRESENT_PARAMETERS zb = z;
    CHECK_HR(a->Reset(&z), D3D_OK); CHECK(z.BackBufferWidth == 800 && z.BackBufferHeight == 600 && z.BackBufferFormat == D3DFMT_X8R8G8B8 && z.BackBufferCount == 3 && z.Flags == 0xf000); IDirect3DSurface9* b2 = nullptr; CHECK_HR(a->GetBackBuffer(0, 2, D3DBACKBUFFER_TYPE_MONO, &b2), D3D_OK); if (b2) b2->Release();
    D3DVIEWPORT9 vp; a->GetViewport(&vp); CHECK(vp.Width == 800 && vp.Height == 600); IDirect3DSwapChain9* sc = nullptr; a->GetSwapChain(0, &sc); D3DPRESENT_PARAMETERS gp; sc->GetPresentParameters(&gp); CHECK(gp.BackBufferCount == 3 && gp.SwapEffect == D3DSWAPEFFECT_FLIP && gp.Flags == 0 + (0xf000 & 0xfff)); sc->Release();
    z = zb; z.hDeviceWindow = nullptr; CHECK_HR(a->Reset(&z), D3D_OK); a->GetSwapChain(0, &sc); sc->GetPresentParameters(&gp); CHECK(gp.hDeviceWindow == (HWND)W_A); sc->Release();   // no window given: the chain keeps its window
    uint32_t sw, sh; t_screen(&sw, &sh); D3DPRESENT_PARAMETERS fs = t_pp(false); fs.BackBufferWidth = sw; fs.BackBufferHeight = sh; fs.BackBufferFormat = D3DFMT_X8R8G8B8; fs.hDeviceWindow = (HWND)W_SCREEN; D3DPRESENT_PARAMETERS f0 = fs;
    CHECK_HR(a->Reset(&fs), D3D_OK); fs = f0; fs.BackBufferFormat = D3DFMT_UNKNOWN; CHECK_HR(a->Reset(&fs), D3DERR_INVALIDCALL); CHECK_HR(a->TestCooperativeLevel(), D3DERR_DEVICENOTRESET);
    fs = f0; fs.BackBufferWidth = 640; CHECK_HR(a->Reset(&fs), D3DERR_NOTAVAILABLE); CHECK_HR(a->Reset(&rp), D3D_OK); CHECK_HR(a->TestCooperativeLevel(), D3D_OK);
    fs = f0; fs.MultiSampleType = D3DMULTISAMPLE_4_SAMPLES; hr = a->Reset(&fs); CHECK(hr == D3D_OK || hr == D3DERR_NOTAVAILABLE); a->Reset(&rp);
    NxDevLife* L = nx_life(O(a)); L->state = NX_DEV_LOST; CHECK_HR(a->Reset(&rp), D3DERR_DEVICELOST); CHECK_HR(a->TestCooperativeLevel(), D3DERR_DEVICELOST); L->state = NX_DEV_NOTRESET; CHECK_HR(a->Reset(&rp), D3D_OK);
    CHECK_EQ(a->Release(), 0); CHECK_EQ(fresh->Release(), 0);
    // ---- Ex: resources survive, state is kept, RT 0 = the new back buffer, viewport / scissor follow the size, a held old back buffer outlives the Reset
    IDirect3D9* d3x = t_d3d(true); D3DPRESENT_PARAMETERS ep = t_pp(true, D3DSWAPEFFECT_DISCARD, 1); IDirect3DDevice9Ex* e = static_cast<IDirect3DDevice9Ex*>(t_dev(d3x, &ep)); CHECK(e);
    IDirect3DTexture9* et = nullptr; e->CreateTexture(16, 16, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &et, nullptr); IDirect3DSurface9* old = nullptr; e->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &old); e->SetRenderState(D3DRS_CULLMODE, D3DCULL_NONE);
    D3DPRESENT_PARAMETERS ex2 = ep; ex2.BackBufferWidth = 320; ex2.BackBufferHeight = 200; t_clear(); CHECK_HR(e->Reset(&ex2), D3D_OK); CHECK_EQ(t_count(R_CREATE), 1); CHECK_EQ(t_count(R_DESTROY), 0);
    DWORD cm = 0; e->GetRenderState(D3DRS_CULLMODE, &cm); CHECK_EQ(cm, D3DCULL_NONE); D3DVIEWPORT9 evp; e->GetViewport(&evp); CHECK(evp.Width == 320 && evp.Height == 200); RECT esc; e->GetScissorRect(&esc); CHECK(esc.right == 320 && esc.bottom == 200);
    IDirect3DSurface9* nrt = nullptr; e->GetRenderTarget(0, &nrt); CHECK(nrt && nrt != old); D3DSURFACE_DESC dd; nrt->GetDesc(&dd); CHECK(dd.Width == 320); nrt->Release(); D3DSURFACE_DESC od; CHECK_HR(old->GetDesc(&od), D3D_OK); CHECK_EQ(od.Width, 640);
    t_clear(); CHECK_EQ(old->Release(), 0); CHECK_EQ(t_count(R_DESTROY), 1); et->Release();
    D3DDISPLAYMODEEX mx = {sizeof mx, sw, sh, 0, D3DFMT_X8R8G8B8, D3DSCANLINEORDERING_PROGRESSIVE}; D3DPRESENT_PARAMETERS xf = fs; xf = f0;
    CHECK_HR(e->ResetEx(&ep, &mx), D3DERR_INVALIDCALL); CHECK_HR(e->ResetEx(&xf, nullptr), D3DERR_INVALIDCALL); D3DDISPLAYMODEEX bm = mx; bm.Width = 1; CHECK_HR(e->ResetEx(&xf, &bm), D3DERR_INVALIDCALL); CHECK_HR(e->ResetEx(&xf, &mx), D3D_OK); CHECK_HR(e->ResetEx(&ep, nullptr), D3D_OK);
    e->Release(); d3x->Release(); CHECK_EQ(d3o->pub, 1); d3d->Release();
}
