// part e: gamma ramp, software cursor, dialog-box mode, raster status, texture memory accounting (rows DEV.Mem* of the env table through mem.txt), eviction
#include "t_common.h"
static IDirect3DSurface9* sys_surf(IDirect3DDevice9* d, UINT w, UINT h, D3DFORMAT f, D3DPOOL p, uint8_t fill) {
    IDirect3DSurface9* s = nullptr; if (FAILED(d->CreateOffscreenPlainSurface(w, h, f, p, &s, nullptr))) return nullptr;
    D3DLOCKED_RECT lr; if (SUCCEEDED(s->LockRect(&lr, nullptr, 0))) { for (UINT y = 0; y < h; y++) memset((uint8_t*)lr.pBits + (size_t)y * lr.Pitch, fill, w * 4); s->UnlockRect(); } return s;
}
struct TT { UINT w, h; D3DFORMAT f; };
static const TT TTAB[8] = {{1024, 1024, D3DFMT_A8R8G8B8}, {512, 512, D3DFMT_R5G6B5}, {256, 256, D3DFMT_A8R8G8B8}, {1023, 255, D3DFMT_A8R8G8B8}, {1024, 1024, (D3DFORMAT)0x31545844}, {512, 512, D3DFMT_A16B16G16R16F}, {17, 5, D3DFMT_L8}, {1024, 1024, D3DFMT_D24S8}};
static void mem_row(char* line, IDirect3DDevice9* dv[2]) {   // T i pool lv k ex delta | T i pool lv k ex F hr | V pool ex delta | I pool ex delta | S pool ex delta | R ex | D ex
    unsigned i, pool, lv, k, ex, delta, hr = 0; char kind = line[0]; bool fail = false; IDirect3DDevice9* d; UINT m0, m1, m2; IUnknown* o = nullptr; HRESULT r = D3D_OK; D3DPOOL pl;
    if (kind == 'T') { char f = 0; if (sscanf(line, "T %u %u %u %u %u F %x", &i, &pool, &lv, &k, &ex, &hr) == 6) fail = true; else if (sscanf(line, "T %u %u %u %u %u %u", &i, &pool, &lv, &k, &ex, &delta) != 6) { CHECK(false); return; } (void)f; }
    else if (kind == 'V' || kind == 'I' || kind == 'S') { if (sscanf(line + 2, "%u %u %u", &pool, &ex, &delta) != 3) { CHECK(false); return; } }
    else { if (sscanf(line + 2, "%u", &ex) != 1) { CHECK(false); return; } delta = 0; pool = 0; }
    d = dv[ex ? 1 : 0]; pl = pool == 0 ? D3DPOOL_DEFAULT : pool == 1 ? D3DPOOL_MANAGED : D3DPOOL_SYSTEMMEM; m0 = d->GetAvailableTextureMem();
    if (kind == 'T') { DWORD us = TTAB[i].f == D3DFMT_D24S8 ? D3DUSAGE_DEPTHSTENCIL : (k ? D3DUSAGE_RENDERTARGET : 0); IDirect3DTexture9* t = nullptr; r = d->CreateTexture(TTAB[i].w, TTAB[i].h, lv ? 0 : 1, us, TTAB[i].f, pl, &t, nullptr); o = t; }
    else if (kind == 'V') { IDirect3DVertexBuffer9* v = nullptr; r = d->CreateVertexBuffer(1 << 20, 0, 0, pl, &v, nullptr); o = v; }
    else if (kind == 'I') { IDirect3DIndexBuffer9* v = nullptr; r = d->CreateIndexBuffer(1 << 18, 0, D3DFMT_INDEX16, pl, &v, nullptr); o = v; }
    else if (kind == 'S') { IDirect3DSurface9* v = nullptr; r = d->CreateOffscreenPlainSurface(640, 480, D3DFMT_A8R8G8B8, pl, &v, nullptr); o = v; }
    else if (kind == 'R') { IDirect3DSurface9* v = nullptr; r = d->CreateRenderTarget(1024, 1024, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_NONE, 0, FALSE, &v, nullptr); o = v; }
    else { IDirect3DSurface9* v = nullptr; r = d->CreateDepthStencilSurface(1024, 1024, D3DFMT_D24S8, D3DMULTISAMPLE_NONE, 0, TRUE, &v, nullptr); o = v; }
    if (fail) { CHECK_HR(r, hr); CHECK_EQ(d->GetAvailableTextureMem(), m0); return; }
    if (!CHECK_HR(r, D3D_OK)) { printf("  case: %s", line); return; }
    m1 = d->GetAvailableTextureMem(); o->Release(); m2 = d->GetAvailableTextureMem(); CHECK_EQ(m2, m0); if (!CHECK_EQ(m0 - m1, delta)) printf("  case: %s  got %u\n", line, m0 - m1);
}
void t_e() {
    t_part('e'); g_profile = 0; IDirect3D9* d3d = t_d3d(false); D3DPRESENT_PARAMETERS pp = t_pp(); IDirect3DDevice9* d = t_dev(d3d, &pp); t_clear_hooks();
    // ---- gamma: per chain, identity before any Set, flags accepted, a missing chain / null ramp changes nothing, Present passes the ramp only after a Set
    D3DGAMMARAMP g, r; d->GetGammaRamp(0, &g); bool ident = true; for (int i = 0; i < 256; i++) ident &= g.red[i] == i * 257 && g.green[i] == i * 257 && g.blue[i] == i * 257; CHECK(ident);
    for (int i = 0; i < 256; i++) { r.red[i] = 1000 + i; r.green[i] = 2000 + i; r.blue[i] = 3000 + i; } d->SetGammaRamp(0, D3DSGR_CALIBRATE, &r); D3DGAMMARAMP g2; d->GetGammaRamp(0, &g2); CHECK(!memcmp(&g2, &r, sizeof r));
    D3DGAMMARAMP junk; memset(&junk, 0x5A, sizeof junk); d->GetGammaRamp(1, &junk); CHECK(junk.red[0] == 0x5A5A); d->SetGammaRamp(1, 0, &g); d->SetGammaRamp(0, 0, nullptr); d->GetGammaRamp(0, &g2); CHECK(!memcmp(&g2, &r, sizeof r));
    CHECK_HR(d->SetDialogBoxMode(TRUE), D3D_OK); CHECK_HR(d->SetDialogBoxMode(FALSE), D3D_OK);
    // ---- raster status: Wine's formula over the performance counter (60 Hz when the mode has none, 20 blank lines)
    g_qpf = 60ull * 740 * 1000; D3DRASTER_STATUS rs; static const struct { uint64_t c; uint32_t scan; int vb; } RC[] = {{0, 0, 0}, {5000, 5, 0}, {719000, 719, 0}, {720000, 0, 1}, {739999, 0, 1}, {740000, 0, 0}, {740000 + 123456, 123, 0}};
    for (auto& c : RC) { g_qpc = c.c; CHECK_HR(d->GetRasterStatus(0, &rs), D3D_OK); CHECK(rs.ScanLine == c.scan && rs.InVBlank == c.vb); }
    CHECK_HR(d->GetRasterStatus(1, &rs), D3DERR_INVALIDCALL); CHECK_HR(d->GetRasterStatus(0, nullptr), D3DERR_INVALIDCALL); g_qpf = 1; CHECK_HR(d->GetRasterStatus(0, &rs), D3DERR_INVALIDCALL); g_qpf = 1000000;
    IDirect3DSwapChain9* sc; d->GetSwapChain(0, &sc); g_qpf = 60ull * 740 * 1000; g_qpc = 719000; CHECK_HR(sc->GetRasterStatus(&rs), D3D_OK); CHECK(rs.ScanLine == 719); sc->Release();
    // ---- cursor (Wine wined3d_device_set_cursor_properties / show_cursor): image copy, A8R8G8B8, power of two, <= display size, readable; visible only with an image; composited by Present
    IDirect3DSurface9* c32 = sys_surf(d, 32, 32, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, 0x44); IDirect3DSurface9* c24 = sys_surf(d, 24, 32, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, 1); IDirect3DSurface9* cx = sys_surf(d, 32, 32, D3DFMT_X8R8G8B8, D3DPOOL_SYSTEMMEM, 1);
    IDirect3DSurface9* big = sys_surf(d, 2048, 2048, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, 1); IDirect3DSurface9* rtg = nullptr; d->CreateRenderTarget(32, 32, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_NONE, 0, FALSE, &rtg, nullptr);
    CHECK_EQ(d->ShowCursor(TRUE), FALSE); CHECK_EQ(d->ShowCursor(TRUE), FALSE); d->Present(nullptr, nullptr, nullptr, nullptr); CHECK_EQ(g_ncur, 0);                      // no image: never visible
    CHECK_HR(d->SetCursorProperties(3, 4, nullptr), D3DERR_INVALIDCALL); CHECK_HR(d->SetCursorProperties(3, 4, c32), D3D_OK); d->SetCursorPosition(1, 2, 0); CHECK_EQ(d->ShowCursor(TRUE), FALSE); CHECK_EQ(d->ShowCursor(TRUE), TRUE);
    d->Present(nullptr, nullptr, nullptr, nullptr); CHECK_EQ(g_ncur, 1); CHECK(g_cur[0].w == 32 && g_cur[0].h == 32 && g_cur[0].x == 77 - 3 - 10 && g_cur[0].y == 88 - 4 - 20 && g_cur[0].px0[0] == 0x44 && g_cur[0].seq > g_pres[g_npres - 1].seq - 1);   // position = OS position when it became visible
    d->SetCursorPosition(100, 200, D3DCURSOR_IMMEDIATE_UPDATE); d->Present(nullptr, nullptr, nullptr, nullptr); CHECK(g_cur[1].x == 100 - 3 - 10 && g_cur[1].y == 200 - 4 - 20 && g_cur[1].seq == g_pres[g_npres - 1].seq + 1);
    CHECK_HR(d->SetCursorProperties(0, 0, big), D3DERR_INVALIDCALL); d->Present(nullptr, nullptr, nullptr, nullptr); CHECK_EQ(g_ncur, 3);                                  // too large: the old image stays
    CHECK_HR(d->SetCursorProperties(0, 0, c24), D3DERR_INVALIDCALL); d->Present(nullptr, nullptr, nullptr, nullptr); CHECK_EQ(g_ncur, 3);                                  // a failed set (format / size) drops the old image
    CHECK_HR(d->SetCursorProperties(0, 0, c32), D3D_OK); CHECK_HR(d->SetCursorProperties(0, 0, cx), D3DERR_INVALIDCALL); CHECK_HR(d->SetCursorProperties(0, 0, c32), D3D_OK); CHECK_HR(d->SetCursorProperties(0, 0, rtg), D3DERR_INVALIDCALL); CHECK_HR(d->SetCursorProperties(0, 0, c32), D3D_OK);
    CHECK_EQ(d->ShowCursor(FALSE), TRUE); CHECK_EQ(d->ShowCursor(FALSE), FALSE); int nc = g_ncur; d->Present(nullptr, nullptr, nullptr, nullptr); CHECK_EQ(g_ncur, nc);
    c32->Release(); c24->Release(); cx->Release(); big->Release(); rtg->Release();
    // ---- texture memory: rows DEV.Mem* of the env table (both profiles, Ex devices account nothing); eviction
    IDirect3D9* dx = t_d3d(true); D3DPRESENT_PARAMETERS ep = t_pp(); IDirect3DDevice9* dv[2] = {d, t_dev(dx, &ep)}; CHECK_EQ(dv[0]->GetAvailableTextureMem(), 0x3c000000u); CHECK_EQ(dv[1]->GetAvailableTextureMem(), 0x3c000000u);
    FILE* f = t_open("mem.txt"); char line[256]; int n = 0; while (f && fgets(line, sizeof line, f)) { mem_row(line, dv); n++; } if (f) fclose(f); CHECK(n > 100);
    IDirect3DTexture9 *t1 = nullptr, *t2 = nullptr; IDirect3DVertexBuffer9* vb = nullptr; UINT m0 = d->GetAvailableTextureMem(); d->CreateTexture(256, 256, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &t1, nullptr); d->CreateTexture(128, 128, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t2, nullptr); d->CreateVertexBuffer(4096, 0, 0, D3DPOOL_DEFAULT, &vb, nullptr);
    CHECK_EQ(m0 - d->GetAvailableTextureMem(), 256 * 256 * 4 + 128 * 128 * 4 + 4096); t1->Release(); CHECK_EQ(m0 - d->GetAvailableTextureMem(), 128 * 128 * 4 + 4096); vb->Release(); t2->Release(); CHECK_EQ(d->GetAvailableTextureMem(), m0);
    IDirect3DTexture9* mt = nullptr; d->CreateTexture(16, 16, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &mt, nullptr); mt->PreLoad(); t_clear(); mt->PreLoad(); CHECK_EQ(t_count(R_UPDATE), 0); CHECK_HR(d->EvictManagedResources(), D3D_OK); mt->PreLoad(); CHECK(t_count(R_UPDATE) >= 1);
    D3DLOCKED_RECT lr; mt->LockRect(0, &lr, nullptr, D3DLOCK_NO_DIRTY_UPDATE); t_clear(); d->EvictManagedResources(); mt->UnlockRect(0); mt->PreLoad(); CHECK_EQ(t_count(R_UPDATE), 0); mt->Release();   // a locked resource is skipped
    dv[1]->Release(); dx->Release(); d->Release(); d3d->Release();
}
