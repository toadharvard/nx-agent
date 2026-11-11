// part c: ComposeRects. Contract (m1_misc.cpp): every call is Wine 9.0's d3d9_device_ComposeRects (E_NOTIMPL + one fixme line, id 47; tests/l6/const checks the count). Facts checked here (no D3DFMT_A1 surface can exist): (1) nx_texfmt has no layout for A1, (2) no creator of the device makes a surface / texture
// level of format A1 over every format x pool x usage (D3DUSAGE_TEXTAPI included), (3) the env table says NOTAVAILABLE for A1, (4) ComposeRects over every kind of surface / vertex buffer pair, operation, count, offset = E_NOTIMPL.
#include "t_common.h"
#include "env_table.gen.h"
#include <vector>
static uint32_t fcc(const char* s) { return (uint32_t)(uint8_t)s[0] | (uint32_t)(uint8_t)s[1] << 8 | (uint32_t)(uint8_t)s[2] << 16 | (uint32_t)(uint8_t)s[3] << 24; }
static bool is_a1(IDirect3DSurface9* s) { D3DSURFACE_DESC d; s->GetDesc(&d); return d.Format == D3DFMT_A1; }
void t_c() {
    t_part('c'); g_profile = 0; IDirect3D9* dx = t_d3d(true); D3DPRESENT_PARAMETERS pp = t_pp(); pp.EnableAutoDepthStencil = TRUE; pp.AutoDepthStencilFormat = D3DFMT_D24S8;
    IDirect3DDevice9Ex* e = static_cast<IDirect3DDevice9Ex*>(t_dev(dx, &pp)); if (!CHECK(e)) return; IDirect3DDevice9* d = e;
    NxTexFmt tf; CHECK(!nx_texfmt(D3DFMT_A1, &tf));                                                                       // (1)
    for (int rt = 1; rt <= 7; rt++) CHECK(rt >= 6 || nxenv_check_device_format(0, 0, 1, D3DFMT_X8R8G8B8, 0, rt, D3DFMT_A1) == (uint32_t)D3DERR_NOTAVAILABLE);   // (3) every texture-like resource type
    CHECK(nxenv_check_device_format(0, 0, 1, D3DFMT_X8R8G8B8, D3DUSAGE_TEXTAPI, D3DRTYPE_SURFACE, D3DFMT_A8R8G8B8) == (uint32_t)D3DERR_NOTAVAILABLE);
    std::vector<uint32_t> fm; for (uint32_t f = 0; f < 140; f++) fm.push_back(f); for (const char* c : {"DXT1", "DXT2", "DXT3", "DXT4", "DXT5", "UYVY", "YUY2", "NULL", "INTZ", "RESZ", "ATI1", "ATI2", "YV12", "NV12", "DF16", "AL16"}) fm.push_back(fcc(c)); fm.push_back(0x7fffffff); fm.push_back(0xdeadbeef);
    const DWORD us[] = {0, D3DUSAGE_RENDERTARGET, D3DUSAGE_DEPTHSTENCIL, D3DUSAGE_DYNAMIC, D3DUSAGE_TEXTAPI, D3DUSAGE_TEXTAPI | D3DUSAGE_RENDERTARGET, D3DUSAGE_AUTOGENMIPMAP};
    int made = 0, a1made = 0;
    for (uint32_t f : fm) for (uint32_t pool = 0; pool < 4; pool++) for (DWORD u : us) {   // (2)
        IDirect3DSurface9* s = nullptr; IDirect3DTexture9* t = nullptr; IDirect3DCubeTexture9* c = nullptr; IDirect3DVolumeTexture9* v = nullptr;
        if (SUCCEEDED(e->CreateOffscreenPlainSurfaceEx(8, 8, (D3DFORMAT)f, (D3DPOOL)pool, &s, nullptr, u))) { made++; a1made += is_a1(s); s->Release(); }
        if (u == 0 && SUCCEEDED(d->CreateOffscreenPlainSurface(8, 8, (D3DFORMAT)f, (D3DPOOL)pool, &s, nullptr))) { made++; a1made += is_a1(s); s->Release(); }
        if (pool == 0 && SUCCEEDED(d->CreateRenderTarget(8, 8, (D3DFORMAT)f, D3DMULTISAMPLE_NONE, 0, FALSE, &s, nullptr))) { made++; a1made += is_a1(s); s->Release(); }
        if (pool == 0 && SUCCEEDED(d->CreateDepthStencilSurface(8, 8, (D3DFORMAT)f, D3DMULTISAMPLE_NONE, 0, FALSE, &s, nullptr))) { made++; a1made += is_a1(s); s->Release(); }
        if (SUCCEEDED(d->CreateTexture(8, 8, 1, u, (D3DFORMAT)f, (D3DPOOL)pool, &t, nullptr))) { made++; D3DSURFACE_DESC dd; t->GetLevelDesc(0, &dd); a1made += dd.Format == D3DFMT_A1; t->Release(); }
        if (SUCCEEDED(d->CreateCubeTexture(8, 1, u, (D3DFORMAT)f, (D3DPOOL)pool, &c, nullptr))) { made++; D3DSURFACE_DESC dd; c->GetLevelDesc(0, &dd); a1made += dd.Format == D3DFMT_A1; c->Release(); }
        if (SUCCEEDED(d->CreateVolumeTexture(8, 8, 8, 1, u, (D3DFORMAT)f, (D3DPOOL)pool, &v, nullptr))) { made++; D3DVOLUME_DESC dd; v->GetLevelDesc(0, &dd); a1made += dd.Format == D3DFMT_A1; v->Release(); }
    }
    CHECK(made > 500); CHECK_EQ(a1made, 0); printf("c: %d resources made over %zu formats, A1 among them: %d\n", made, fm.size(), a1made);
    // (4) the surface zoo x vertex buffers x operations x counts x offsets
    IDirect3DSurface9 *zoo[8] = {}; int nz = 0; IDirect3DSurface9* s; IDirect3DTexture9* t;
    if (SUCCEEDED(d->CreateOffscreenPlainSurface(16, 16, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &s, nullptr))) zoo[nz++] = s;
    if (SUCCEEDED(d->CreateOffscreenPlainSurface(16, 16, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &s, nullptr))) zoo[nz++] = s;
    if (SUCCEEDED(d->CreateTexture(16, 16, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &t, nullptr))) { t->GetSurfaceLevel(0, &s); zoo[nz++] = s; t->Release(); }
    if (SUCCEEDED(d->CreateRenderTarget(16, 16, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_NONE, 0, FALSE, &s, nullptr))) zoo[nz++] = s;
    if (SUCCEEDED(d->CreateDepthStencilSurface(16, 16, D3DFMT_D24S8, D3DMULTISAMPLE_NONE, 0, FALSE, &s, nullptr))) zoo[nz++] = s;
    d->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &s); zoo[nz++] = s;
    IDirect3D9* d2 = t_d3d(true); D3DPRESENT_PARAMETERS p2 = t_pp(); IDirect3DDevice9* od = t_dev(d2, &p2); IDirect3DSurface9* foreign = nullptr; od->CreateOffscreenPlainSurface(16, 16, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &foreign, nullptr);
    IDirect3DVertexBuffer9* vb[3] = {}; d->CreateVertexBuffer(1024, 0, 0, D3DPOOL_DEFAULT, &vb[1], nullptr); d->CreateVertexBuffer(1024, 0, 0, D3DPOOL_SYSTEMMEM, &vb[2], nullptr);
    std::vector<IDirect3DSurface9*> sv(zoo, zoo + nz); sv.push_back(nullptr); sv.push_back(foreign); CHECK(nz == 6);
    const D3DCOMPOSERECTSOP ops[] = {(D3DCOMPOSERECTSOP)0, D3DCOMPOSERECTS_COPY, D3DCOMPOSERECTS_OR, D3DCOMPOSERECTS_AND, D3DCOMPOSERECTS_NEG, (D3DCOMPOSERECTSOP)5, D3DCOMPOSERECTS_FORCE_DWORD};
    const UINT cnt[] = {0, 1, 0xFFFF, 0x10000, 0xFFFFFFFFu}; const INT offs[] = {0, -1, 5, 0x7fffffff, (INT)0x80000000}; int calls = 0, ok = 0; uint32_t rs = 12345;
    for (IDirect3DSurface9* a : sv) for (IDirect3DSurface9* b : sv) for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) for (int k = 0; k < 40; k++) {
        rs = rs * 1664525u + 1013904223u; D3DCOMPOSERECTSOP op = ops[(rs >> 8) % 7]; UINT n = cnt[(rs >> 12) % 5]; INT ox = offs[(rs >> 16) % 5], oy = offs[(rs >> 20) % 5];
        HRESULT hr = e->ComposeRects(a, b, vb[i], n, vb[j], op, ox, oy); calls++; ok += hr == E_NOTIMPL;
    }
    CHECK_EQ(ok, calls); CHECK_HR(e->ComposeRects(zoo[0], zoo[0], vb[1], 1, vb[1], D3DCOMPOSERECTS_COPY, 0, 0), E_NOTIMPL); printf("c: %d ComposeRects calls\n", calls);
    for (int i = 0; i < nz; i++) zoo[i]->Release(); foreign->Release(); od->Release(); d2->Release(); for (int i = 1; i < 3; i++) vb[i]->Release(); e->Release(); dx->Release();
}
