// part a: creation legality over the whole (type x format x usage x pool x levels x device) domain against the python oracle (test_tex.py: env tsv + stated rules), then the non-domain rules
#include "t_common.h"
static TEnv E0, E1;
static NxObj* O(IUnknown* p) { return nx_obj_of(p); }
static void one_tex(char* line) {
    unsigned kind, w, h, d, lv, usage, fmt, pool, ex, prof, hr, nlev, gpu, lock, zup, pre;
    if (sscanf(line, "T %u %u %u %u %u %u %u %u %u %u %x %u %u %u %u %u", &kind, &w, &h, &d, &lv, &usage, &fmt, &pool, &ex, &prof, &hr, &nlev, &gpu, &lock, &zup, &pre) != 16) { CHECK(false); return; }
    g_profile = prof; t_clear(); NxDevice9* dv = E0.dev; NxDevice9Ex* dx = E1.devex; IUnknown* out = (IUnknown*)1; HRESULT r;
    if (kind == 0) { IDirect3DTexture9* t = (IDirect3DTexture9*)1; r = ex ? dx->CreateTexture(w, h, lv, usage, (D3DFORMAT)fmt, (D3DPOOL)pool, &t, nullptr) : dv->CreateTexture(w, h, lv, usage, (D3DFORMAT)fmt, (D3DPOOL)pool, &t, nullptr); out = t; }
    else if (kind == 1) { IDirect3DCubeTexture9* t = (IDirect3DCubeTexture9*)1; r = ex ? dx->CreateCubeTexture(w, lv, usage, (D3DFORMAT)fmt, (D3DPOOL)pool, &t, nullptr) : dv->CreateCubeTexture(w, lv, usage, (D3DFORMAT)fmt, (D3DPOOL)pool, &t, nullptr); out = t; }
    else { IDirect3DVolumeTexture9* t = (IDirect3DVolumeTexture9*)1; r = ex ? dx->CreateVolumeTexture(w, h, d, lv, usage, (D3DFORMAT)fmt, (D3DPOOL)pool, &t, nullptr) : dv->CreateVolumeTexture(w, h, d, lv, usage, (D3DFORMAT)fmt, (D3DPOOL)pool, &t, nullptr); out = t; }
    if (!CHECK((uint32_t)r == hr)) { printf("  case: %s  got %x\n", line, (unsigned)r); if (r == D3D_OK) O(out)->release(); return; }
    if (r != D3D_OK) { CHECK(out == nullptr); CHECK_EQ(g_nrec, 0); return; }
    NxObj* o = O(out); NxTexCore* tc = static_cast<NxTexCore*>(o); NxImg* im = tc->img;
    CHECK_EQ(tc->getLevelCount(), nlev); CHECK_EQ(t_count(R_CREATE), gpu); if (!CHECK_EQ(t_count(R_UPDATE), zup)) printf("  case: %s  got %d\n", line, t_count(R_UPDATE));
    if (gpu) CHECK_EQ(g_rec[0].desc.mips, im->nlev);
    D3DLOCKED_RECT lr; D3DLOCKED_BOX lb; HRESULT lh = kind == 2 ? static_cast<NxVolumeTexture9*>(o)->LockBox(0, &lb, nullptr, 0) : kind == 1 ? static_cast<NxCubeTexture9*>(o)->LockRect(D3DCUBEMAP_FACE_POSITIVE_X, 0, &lr, nullptr, 0) : static_cast<NxTexture9*>(o)->LockRect(0, &lr, nullptr, 0);
    CHECK_HR(lh, lock ? D3D_OK : D3DERR_INVALIDCALL);
    if (lock) { if (kind == 2) static_cast<NxVolumeTexture9*>(o)->UnlockBox(0); else if (kind == 1) static_cast<NxCubeTexture9*>(o)->UnlockRect(D3DCUBEMAP_FACE_POSITIVE_X, 0); else static_cast<NxTexture9*>(o)->UnlockRect(0); }
    if (pool == D3DPOOL_MANAGED && gpu) { t_clear(); o->preLoad(); CHECK_EQ(t_count(R_UPDATE), pre); }
    t_clear(); CHECK_EQ(o->release(), 0); CHECK_EQ(t_count(R_DESTROY), gpu); CHECK_EQ(E0.obj->pub, 1);
}
static void one_surf(char* line) {
    unsigned what, w, h, fmt, ms, q, flag, pool, ex, usage, prof, hr, gpu, lock, zup;
    if (sscanf(line, "S %u %u %u %u %u %u %u %u %u %u %u %x %u %u %u", &what, &w, &h, &fmt, &ms, &q, &flag, &pool, &ex, &usage, &prof, &hr, &gpu, &lock, &zup) != 15) { CHECK(false); return; }
    g_profile = prof; t_clear(); IDirect3DSurface9* s = (IDirect3DSurface9*)1; HRESULT r; NxDevice9* dv = E0.dev; NxDevice9Ex* dx = E1.devex; D3DMULTISAMPLE_TYPE m = (D3DMULTISAMPLE_TYPE)ms; D3DFORMAT f = (D3DFORMAT)fmt;
    if (what == 0) r = ex ? (usage == 0xFFFFFFFFu ? dx->CreateRenderTarget(w, h, f, m, q, flag, &s, nullptr) : dx->CreateRenderTargetEx(w, h, f, m, q, flag, &s, nullptr, usage)) : dv->CreateRenderTarget(w, h, f, m, q, flag, &s, nullptr);
    else if (what == 1) r = ex ? (usage == 0xFFFFFFFFu ? dx->CreateDepthStencilSurface(w, h, f, m, q, flag, &s, nullptr) : dx->CreateDepthStencilSurfaceEx(w, h, f, m, q, flag, &s, nullptr, usage)) : dv->CreateDepthStencilSurface(w, h, f, m, q, flag, &s, nullptr);
    else r = ex ? dx->CreateOffscreenPlainSurface(w, h, f, (D3DPOOL)pool, &s, nullptr) : dv->CreateOffscreenPlainSurface(w, h, f, (D3DPOOL)pool, &s, nullptr);
    if (!CHECK((uint32_t)r == hr)) { printf("  case: %s  got %x\n", line, (unsigned)r); if (r == D3D_OK) O(s)->release(); return; }
    if (r != D3D_OK) { CHECK(s == nullptr); CHECK_EQ(g_nrec, 0); return; }
    CHECK_EQ(t_count(R_CREATE), gpu); if (!CHECK_EQ(t_count(R_UPDATE), zup)) printf("  case: %s  got %d\n", line, t_count(R_UPDATE));
    D3DSURFACE_DESC d; CHECK_HR(s->GetDesc(&d), D3D_OK); CHECK(d.Width == w && d.Height == h && d.Format == f && d.Type == D3DRTYPE_SURFACE && d.Pool == (what == 2 ? pool : 0) && d.MultiSampleType == m && d.MultiSampleQuality == q);
    D3DLOCKED_RECT lr; HRESULT lh = s->LockRect(&lr, nullptr, 0); CHECK_HR(lh, lock ? D3D_OK : D3DERR_INVALIDCALL); if (lock) s->UnlockRect();
    t_clear(); CHECK_EQ(O(s)->release(), 0); CHECK_EQ(t_count(R_DESTROY), gpu);
}
void t_a() {
    t_part('a'); E0 = t_env(false); E1 = t_env(true);
    FILE* f = t_open("create.txt"); char line[512]; int n = 0;
    while (f && fgets(line, sizeof line, f)) { if (line[0] == 'T') one_tex(line); else if (line[0] == 'S') one_surf(line); n++; }
    if (f) fclose(f);
    CHECK(n > 1000);
    // ---- outside the domain
    g_profile = 0; t_clear(); NxDevice9* d = E0.dev; NxDevice9Ex* x = E1.devex; IDirect3DTexture9* t = nullptr; IDirect3DSurface9* s = nullptr; HANDLE h = (HANDLE)1; uint8_t mem[64 * 64 * 4 + 16];
    CHECK_HR(d->CreateTexture(4, 4, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, nullptr, nullptr), D3DERR_INVALIDCALL);               // null output (Windows faults)
    CHECK_HR(d->CreateTexture(4, 4, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &t, &h), E_NOTIMPL); CHECK(t == nullptr);                  // shared handle on a non-Ex device
    CHECK_HR(x->CreateTexture(4, 4, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, &h), D3DERR_INVALIDCALL);
    CHECK_HR(x->CreateTexture(4, 4, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &t, &h), D3D_OK); O(t)->release();                        // sharing ignored
    CHECK_HR(x->CreateTexture(4, 4, 2, 0, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &t, &h), D3DERR_INVALIDCALL);                           // user memory: one level
    h = (HANDLE)(((uintptr_t)mem + 15) & ~15u); memset(h, 0x5A, 64);
    CHECK_HR(x->CreateTexture(4, 4, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &t, &h), D3D_OK);                                      // user memory texture: the application's bytes
    D3DLOCKED_RECT lr; CHECK_HR(t->LockRect(0, &lr, nullptr, 0), D3D_OK); CHECK(lr.pBits == (void*)h && ((uint8_t*)lr.pBits)[0] == 0x5A && lr.Pitch == 16); t->UnlockRect(0); O(t)->release();
    CHECK_HR(x->CreateOffscreenPlainSurface(4, 4, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &s, &h), D3D_OK); CHECK_HR(s->LockRect(&lr, nullptr, 0), D3D_OK); CHECK(lr.pBits == (void*)h); s->UnlockRect(); O(s)->release();
    CHECK_HR(x->CreateOffscreenPlainSurface(4, 4, D3DFMT_A8R8G8B8, D3DPOOL_SCRATCH, &s, &h), D3DERR_INVALIDCALL);
    CHECK_HR(d->CreateOffscreenPlainSurface(4, 4, D3DFMT_A8R8G8B8, D3DPOOL_SCRATCH, &s, &h), E_NOTIMPL);
    CHECK_HR(d->CreateTexture(0xFFFF, 0xFFFF, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &t, nullptr), D3DERR_NOTAVAILABLE);              // above MaxTextureWidth
    CHECK_HR(d->CreateTexture(0xFFFF, 0xFFFF, 1, 0, D3DFMT_A32B32G32R32F, D3DPOOL_SCRATCH, &t, nullptr), E_OUTOFMEMORY);                // 64 GiB
    CHECK_HR(d->CreateTexture(8192, 8192, 1, 0, D3DFMT_A32B32G32R32F, D3DPOOL_DEFAULT, &t, nullptr), D3DERR_OUTOFVIDEOMEMORY);          // 1 GiB image over the 960 MiB of the env
    CHECK_HR(d->CreateTexture(4, 4, 33, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr), D3DERR_INVALIDCALL);
    CHECK_HR(d->CreateOffscreenPlainSurface(4, 4, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &s, nullptr), D3DERR_INVALIDCALL);
    CHECK_HR(d->CreateOffscreenPlainSurface(0, 4, D3DFMT_A8R8G8B8, D3DPOOL_SCRATCH, &s, nullptr), D3DERR_INVALIDCALL);                  // Wine test_surface_dimensions
    CHECK_HR(d->CreateOffscreenPlainSurface(4, 0, D3DFMT_A8R8G8B8, D3DPOOL_SCRATCH, &s, nullptr), D3DERR_INVALIDCALL);
    CHECK_HR(d->CreateRenderTarget(4, 4, D3DFMT_D16, D3DMULTISAMPLE_NONE, 0, FALSE, &s, nullptr), D3DERR_INVALIDCALL); CHECK(s == nullptr);   // Wine test_create_rt_ds_fail
    CHECK_HR(d->CreateDepthStencilSurface(4, 4, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_NONE, 0, TRUE, &s, nullptr), D3DERR_INVALIDCALL);
    CHECK_HR(d->CreateRenderTarget(4, 4, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_NONE, 0, FALSE, nullptr, nullptr), D3DERR_INVALIDCALL);
    g_refuse = false; CHECK_HR(d->CreateDepthStencilSurface(4, 4, D3DFMT_D24S8, D3DMULTISAMPLE_NONE, 0, TRUE, &s, nullptr), D3D_OK); CHECK(static_cast<NxSurfCore*>(O(s))->img->flags & NXI_DISCARD); O(s)->release();
    CHECK_HR(d->CreateDepthStencilSurface(4, 4, D3DFMT_D24S8, D3DMULTISAMPLE_NONE, 0, FALSE, &s, nullptr), D3D_OK); CHECK(!(static_cast<NxSurfCore*>(O(s))->img->flags & NXI_DISCARD)); O(s)->release();
    IDirect3DVolumeTexture9* v; CHECK_HR(d->CreateVolumeTexture(4, 4, 0, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &v, nullptr), D3DERR_INVALIDCALL);
    IDirect3DCubeTexture9* c; CHECK_HR(d->CreateCubeTexture(0, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &c, nullptr), D3DERR_INVALIDCALL);
    CHECK_HR(d->CreateCubeTexture(8, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &c, nullptr), D3D_OK); CHECK_EQ(c->GetLevelCount(), 1); O(c)->release();
}
