// part b: level chains and descs (python oracle), sub-resource identity, refcount rule R4, GetContainer, LOD
#include "t_common.h"
static NxObj* O(IUnknown* p) { return nx_obj_of(p); }
void t_b() {
    t_part('b'); g_profile = 0; TEnv e = t_env(); NxDevice9* d = e.dev; char line[2048];
    FILE* f = t_open("chain.txt");
    while (f && fgets(line, sizeof line, f)) {       // C kind w h d levels fmt n w,h,d ...
        unsigned kind, w, h, dd, lv, fmt, n; int off = 0; if (sscanf(line, "C %u %u %u %u %u %u %u%n", &kind, &w, &h, &dd, &lv, &fmt, &n, &off) < 7) { CHECK(false); continue; }
        IUnknown* t = nullptr; HRESULT hr = kind == 0 ? d->CreateTexture(w, h, lv, 0, (D3DFORMAT)fmt, D3DPOOL_SCRATCH, (IDirect3DTexture9**)&t, nullptr) : kind == 1 ? d->CreateCubeTexture(w, lv, 0, (D3DFORMAT)fmt, D3DPOOL_SCRATCH, (IDirect3DCubeTexture9**)&t, nullptr)
                                                                                           : d->CreateVolumeTexture(w, h, dd, lv, 0, (D3DFORMAT)fmt, D3DPOOL_SCRATCH, (IDirect3DVolumeTexture9**)&t, nullptr);
        if (!CHECK_HR(hr, D3D_OK)) continue;
        NxTexCore* tc = static_cast<NxTexCore*>(O(t)); CHECK_EQ(tc->getLevelCount(), n);
        const char* p = line + off;
        for (unsigned i = 0; i < n; i++) {
            unsigned lw, lh, ld; int k = 0; sscanf(p, " %u,%u,%u%n", &lw, &lh, &ld, &k); p += k;
            if (kind == 2) { D3DVOLUME_DESC vd; CHECK_HR(tc->levelDesc3(i, &vd), D3D_OK); CHECK(vd.Width == lw && vd.Height == lh && vd.Depth == ld && vd.Format == fmt && vd.Type == D3DRTYPE_VOLUME && vd.Pool == D3DPOOL_SCRATCH); }
            else { D3DSURFACE_DESC sd; CHECK_HR(tc->levelDesc2(i, &sd), D3D_OK); CHECK(sd.Width == lw && sd.Height == lh && sd.Format == fmt && sd.Type == D3DRTYPE_SURFACE && sd.Pool == D3DPOOL_SCRATCH && sd.Usage == 0);
                IDirect3DSurface9* s = nullptr; CHECK_HR(tc->kid(i, kind == 1 ? 5 : 0, (void**)&s), D3D_OK); D3DSURFACE_DESC g; CHECK_HR(s->GetDesc(&g), D3D_OK); CHECK(g.Width == lw && g.Height == lh); s->Release(); }
        }
        D3DSURFACE_DESC sd; CHECK_HR(tc->levelDesc2(n, &sd), D3DERR_INVALIDCALL); CHECK_HR(tc->levelDesc2(n + 1, &sd), D3DERR_INVALIDCALL); CHECK_HR(tc->levelDesc2(0, nullptr), D3DERR_INVALIDCALL);
        O(t)->release();
    }
    if (f) fclose(f);
    // ---- identity and R4 (README): sub-resource counts are the container's, the device is untouched
    IDirect3DTexture9* t; CHECK_HR(d->CreateTexture(16, 16, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr), D3D_OK); NxObj* to = O(t); CHECK_EQ(to->pub, 1); CHECK_EQ(e.obj->pub, 1 + 1);
    CHECK_EQ(t->GetLevelCount(), 5);
    IDirect3DSurface9 *s0 = nullptr, *s0b = nullptr, *s1 = nullptr; CHECK_HR(t->GetSurfaceLevel(0, &s0), D3D_OK); CHECK_EQ(to->pub, 2); CHECK_HR(t->GetSurfaceLevel(0, &s0b), D3D_OK); CHECK(s0 == s0b); CHECK_EQ(to->pub, 3); CHECK_HR(t->GetSurfaceLevel(1, &s1), D3D_OK); CHECK(s1 != s0);
    CHECK_EQ(e.obj->pub, 2); CHECK_EQ(s0->AddRef(), 5); CHECK_EQ(s0->Release(), 4); CHECK_EQ(s1->Release(), 3); CHECK_EQ(s0b->Release(), 2); CHECK_EQ(s0->Release(), 1);
    IDirect3DSurface9* bad = (IDirect3DSurface9*)7; CHECK_HR(t->GetSurfaceLevel(5, &bad), D3DERR_INVALIDCALL); CHECK(bad == (IDirect3DSurface9*)7); CHECK_HR(t->GetSurfaceLevel(0, nullptr), D3DERR_INVALIDCALL);
    t->GetSurfaceLevel(2, &s1); void* cont = nullptr; CHECK_HR(s1->GetContainer(IID_IDirect3DTexture9, &cont), D3D_OK); CHECK(cont == (void*)t); CHECK_EQ(to->pub, 3); ((IUnknown*)cont)->Release();
    CHECK_HR(s1->GetContainer(IID_IDirect3DCubeTexture9, &cont), E_NOINTERFACE); CHECK_EQ(to->pub, 2);
    IDirect3DDevice9* gd = nullptr; CHECK_HR(s1->GetDevice(&gd), D3D_OK); CHECK(gd == static_cast<IDirect3DDevice9*>(d)); gd->Release(); CHECK_EQ(O(s1)->getType(), D3DRTYPE_SURFACE); CHECK_EQ(s1->GetPriority(), 0); CHECK_EQ(s1->SetPriority(5), 0);
    IUnknown* q = nullptr; CHECK_HR(s1->QueryInterface(IID_IDirect3DResource9, (void**)&q), D3D_OK); q->Release(); CHECK_HR(s1->QueryInterface(IID_IDirect3DBaseTexture9, (void**)&q), E_NOINTERFACE);
    s1->Release(); CHECK_EQ(to->pub, 1); CHECK_EQ(t->SetPriority(3), 0); CHECK_EQ(t->GetPriority(), 3); CHECK_EQ(O(t)->getType(), D3DRTYPE_TEXTURE);
    // ---- LOD: managed only, clamped to the chain (wined3d_stateblock_set_texture_lod)
    CHECK_EQ(t->SetLOD(2), 0); CHECK_EQ(t->GetLOD(), 2); CHECK_EQ(t->SetLOD(100), 2); CHECK_EQ(t->GetLOD(), 4); CHECK_EQ(t->SetLOD(0), 4); CHECK_EQ(O(t)->release(), 0); CHECK_EQ(e.obj->pub, 1);
    IDirect3DTexture9* u; d->CreateTexture(16, 16, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &u, nullptr); CHECK_EQ(u->SetLOD(3), 0); CHECK_EQ(u->GetLOD(), 0); O(u)->release();
    d->CreateTexture(16, 16, 0, D3DUSAGE_AUTOGENMIPMAP, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &u, nullptr); CHECK_EQ(u->GetLevelCount(), 1); u->SetLOD(100); CHECK_EQ(u->GetLOD(), 4); O(u)->release();   // the clamp is the GPU chain
    // ---- cube faces
    IDirect3DCubeTexture9* c; d->CreateCubeTexture(8, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &c, nullptr); IDirect3DSurface9* fs[6]; CHECK_EQ(c->GetLevelCount(), 4);
    for (int i = 0; i < 6; i++) { CHECK_HR(c->GetCubeMapSurface((D3DCUBEMAP_FACES)i, 3, &fs[i]), D3D_OK); for (int j = 0; j < i; j++) CHECK(fs[i] != fs[j]); }
    CHECK_HR(c->GetCubeMapSurface((D3DCUBEMAP_FACES)6, 0, &bad), D3DERR_INVALIDCALL); CHECK_HR(c->GetCubeMapSurface((D3DCUBEMAP_FACES)0xFFFFFFFF, 0, &bad), D3DERR_INVALIDCALL); CHECK_HR(c->GetCubeMapSurface(D3DCUBEMAP_FACE_POSITIVE_X, 4, &bad), D3DERR_INVALIDCALL);
    for (int i = 0; i < 6; i++) fs[i]->Release(); CHECK_EQ(O(c)->release(), 0);
}
