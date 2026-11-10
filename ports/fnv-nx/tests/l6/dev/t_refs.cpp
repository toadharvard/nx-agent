// part e: the object model. Numbers are the real D3D9 behaviour as recorded by Wine's d3d9 tests (device.c test_refcount / test_private_data /
// test_resource_priority, written against Windows) and MS IUnknown / IDirect3DResource9 / IDirect3DSurface9 documentation.
#include "t_tables.h"
static const GUID IID_IUnknown = {0, 0, 0, {0xc0, 0, 0, 0, 0, 0, 0, 0x46}};
static ULONG rc(IUnknown* p) { p->AddRef(); return p->Release(); }  // the public count, as the Wine tests read it
static const GUID G1 = {0xe5aa4a21, 1, 2, {3, 3, 3, 3, 3, 3, 3, 3}}, G2 = {0xe5aa4a22, 1, 2, {3, 3, 3, 3, 3, 3, 3, 3}};
void t_refs() {
    t_part("e");
    TEnv e = t_env(1); NxDevice9* d = e.dev; IUnknown* du = d; IUnknown* d3 = e.d3d;
    CHECK_EQ(rc(d3), 2); CHECK_EQ(rc(du), 1);                                       // device holds IDirect3D9 (count 2), device itself 1
    IDirect3D9* g3 = nullptr; CHECK_HR(d->GetDirect3D(&g3), D3D_OK); CHECK(g3 == e.d3d); CHECK_EQ(rc(d3), 3); g3->Release(); CHECK_EQ(rc(d3), 2);
    // ---- children: public 1, each holds one device reference while public > 0
    NxVertexBuffer9* vb = t_child<NxVertexBuffer9>(e); TTracker tv; tv.watch(vb);
    CHECK_EQ(rc(vb), 1); CHECK_EQ(rc(du), 2);
    CHECK_HR(d->SetStreamSource(0, vb, 0, 12), D3D_OK); CHECK_EQ(rc(vb), 1); CHECK_HR(d->SetStreamSource(0, nullptr, 0, 0), D3D_OK); CHECK_EQ(rc(vb), 1);  // SetStreamSource: no public AddRef
    IDirect3DVertexBuffer9* gv; UINT o, s; d->SetStreamSource(0, vb, 0, 12); d->GetStreamSource(0, &gv, &o, &s); CHECK_EQ(rc(vb), 2); gv->Release();   // Get AddRefs
    CHECK_EQ(vb->Release(), 0); CHECK_EQ(rc(du), 1); CHECK(tv.alive());                  // last public Release while bound: device reference dropped, object kept alive
    d->GetStreamSource(0, &gv, &o, &s); CHECK_EQ(rc(vb), 1); CHECK_EQ(rc(du), 2);         // handed out again: public 0 -> 1 takes the device reference again
    CHECK_EQ(gv->Release(), 0); CHECK_EQ(rc(du), 1); d->SetStreamSource(0, nullptr, 0, 0); CHECK(!tv.alive());   // unbound and unreferenced: destroyed
    NxPixelShader9* ps = t_child<NxPixelShader9>(e, ~0u); CHECK_EQ(rc(du), 2); NxTexture9* tx = t_child<NxTexture9>(e, D3DPOOL_MANAGED); CHECK_EQ(rc(du), 3);
    // ---- sub-resources (level / face): the refcount IS the container's, the device count is untouched
    NxSurface9* lv = t_subsurface(e, tx, 0, 32, 32); ULONG t0 = rc(tx);
    CHECK_EQ(rc(lv), t0); CHECK_EQ(lv->AddRef(), t0 + 1); CHECK_EQ(rc(tx), t0 + 1); CHECK_EQ(rc(du), 3); CHECK_EQ(lv->Release(), t0); CHECK_EQ(rc(tx), t0);
    TTracker tt; tt.watch(tx); d->SetTexture(1, tx); CHECK_HR(d->SetRenderTarget(0, t_subsurface(e, tx, D3DUSAGE_RENDERTARGET, 16, 16)), D3D_OK);
    d->SetTexture(1, nullptr); CHECK_EQ(tx->Release(), 0); CHECK(tt.alive());   // the level bound as render target keeps its container alive
    CHECK_HR(d->SetRenderTarget(0, t_surface(e, D3DUSAGE_RENDERTARGET, 8, 8)), D3D_OK); CHECK(!tt.alive());
    // ---- implicit surfaces / chain: public 0, hold the device only while public > 0, never freed by their count, no forwarding
    NxSwapChain9* sc = t_child<NxSwapChain9>(e); NxSurface9* bb = new NxSurface9; bb->asImplicit(e.dev, sc); TTracker tb; tb.watch(bb); ULONG dv0 = rc(du);
    CHECK_EQ(rc(bb), 0); CHECK_EQ(rc(du), dv0); CHECK_EQ(bb->AddRef(), 1); CHECK_EQ(rc(du), dv0 + 1); CHECK_EQ(bb->AddRef(), 2); CHECK_EQ(rc(du), dv0 + 1); CHECK_EQ(rc(sc), 1);
    CHECK_EQ(bb->Release(), 1); CHECK_EQ(bb->Release(), 0); CHECK_EQ(rc(du), dv0); CHECK_EQ(bb->Release(), 0); CHECK_EQ(bb->Release(), 0); CHECK(tb.alive());
    void* cont = nullptr; CHECK_HR(bb->GetContainer(IID_IDirect3DSwapChain9, &cont), D3D_OK); CHECK(cont == (IDirect3DSwapChain9*)sc); CHECK_EQ(rc(sc), 2); sc->Release();   // GetContainer AddRefs the container
    CHECK_EQ(bb->AddRef(), 1); CHECK_EQ(rc(du), dv0 + 1); CHECK_EQ(bb->Release(), 0);
    // ---- GetDevice AddRefs the device; every class
    IDirect3DDevice9* gd; ULONG before = rc(du); CHECK_HR(ps->GetDevice(&gd), D3D_OK); CHECK(gd == d); CHECK_EQ(rc(du), before + 1); gd->Release(); CHECK_HR(ps->GetDevice(nullptr), D3DERR_INVALIDCALL);
    // ---- QueryInterface: the IID lattice of each class (single chain: same address), E_NOINTERFACE + null otherwise, AddRef on success
    struct Q { IUnknown* o; const GUID* yes[5]; const GUID* no[3]; };
    NxSurface9* su = t_surface(e, 0, 4, 4); NxTexture9* t2 = t_child<NxTexture9>(e); NxVolumeTexture9* vt = t_child<NxVolumeTexture9>(e); NxVolume9* vo = new NxVolume9; vo->asSubResource(e.dev, vt);
    NxDevice9Ex* dx = new NxDevice9Ex; dx->asDevice(e.d3d); NxVertexDeclaration9* vd = t_decl(e); NxSwapChain9Ex* sx = t_child<NxSwapChain9Ex>(e);
    Q qs[] = {{su, {&IID_IUnknown, &IID_IDirect3DSurface9, &IID_IDirect3DResource9}, {&IID_IDirect3DTexture9, &IID_IDirect3DBaseTexture9, &IID_IDirect3DDevice9}},
              {t2, {&IID_IUnknown, &IID_IDirect3DTexture9, &IID_IDirect3DBaseTexture9, &IID_IDirect3DResource9}, {&IID_IDirect3DSurface9, &IID_IDirect3DCubeTexture9, &IID_IDirect3DVolumeTexture9}},
              {vt, {&IID_IUnknown, &IID_IDirect3DVolumeTexture9, &IID_IDirect3DBaseTexture9, &IID_IDirect3DResource9}, {&IID_IDirect3DTexture9, &IID_IDirect3DVolume9, &IID_IDirect3D9}},
              {vo, {&IID_IUnknown, &IID_IDirect3DVolume9}, {&IID_IDirect3DResource9, &IID_IDirect3DSurface9, &IID_IDirect3DBaseTexture9}},
              {d, {&IID_IUnknown, &IID_IDirect3DDevice9}, {&IID_IDirect3DDevice9Ex, &IID_IDirect3D9, &IID_IDirect3DResource9}},
              {dx, {&IID_IUnknown, &IID_IDirect3DDevice9, &IID_IDirect3DDevice9Ex}, {&IID_IDirect3D9, &IID_IDirect3DResource9, &IID_IDirect3DSurface9}},
              {sx, {&IID_IUnknown, &IID_IDirect3DSwapChain9, &IID_IDirect3DSwapChain9Ex}, {&IID_IDirect3DResource9, &IID_IDirect3DDevice9, &IID_IDirect3DSurface9}},
              {vd, {&IID_IUnknown, &IID_IDirect3DVertexDeclaration9}, {&IID_IDirect3DResource9, &IID_IDirect3DVertexShader9, &IID_IDirect3DPixelShader9}},
              {e.d3d, {&IID_IUnknown, &IID_IDirect3D9}, {&IID_IDirect3D9Ex, &IID_IDirect3DDevice9, &IID_IDirect3DResource9}}};
    for (auto& q : qs) {
        void* p; ULONG r0 = rc(q.o);
        for (const GUID* g : q.yes) if (g) { p = nullptr; CHECK_HR(q.o->QueryInterface(*g, &p), D3D_OK); CHECK(p == (void*)q.o); CHECK_EQ(rc(q.o), r0 + 1); ((IUnknown*)p)->Release(); }
        for (const GUID* g : q.no) if (g) { p = (void*)1; CHECK_HR(q.o->QueryInterface(*g, &p), E_NOINTERFACE); CHECK(p == nullptr); CHECK_EQ(rc(q.o), r0); }
        CHECK_HR(q.o->QueryInterface(G1, nullptr), E_POINTER);
    }
    // ---- types and priority (managed textures / buffers only; Set returns the previous value; surfaces always 0) and PreLoad (managed only; a level through its container)
    CHECK_EQ(su->GetType(), D3DRTYPE_SURFACE); CHECK_EQ(t2->GetType(), D3DRTYPE_TEXTURE); CHECK_EQ(vt->GetType(), D3DRTYPE_VOLUMETEXTURE); CHECK_EQ(t_child<NxCubeTexture9>(e)->GetType(), D3DRTYPE_CUBETEXTURE);
    CHECK_EQ(t_child<NxVertexBuffer9>(e)->GetType(), D3DRTYPE_VERTEXBUFFER); CHECK_EQ(t_child<NxIndexBuffer9>(e)->GetType(), D3DRTYPE_INDEXBUFFER);
    NxTexture9* mt = t_child<NxTexture9>(e, D3DPOOL_MANAGED); NxVertexBuffer9* mvb = t_child<NxVertexBuffer9>(e, D3DPOOL_MANAGED); NxTexture9* st = t_child<NxTexture9>(e, D3DPOOL_SYSTEMMEM);
    CHECK_EQ(mt->GetPriority(), 0); CHECK_EQ(mt->SetPriority(1), 0); CHECK_EQ(mt->GetPriority(), 1); CHECK_EQ(mt->SetPriority(2), 1); CHECK_EQ(mvb->SetPriority(5), 0); CHECK_EQ(mvb->GetPriority(), 5);
    CHECK_EQ(st->SetPriority(1), 0); CHECK_EQ(st->SetPriority(2), 0); CHECK_EQ(st->GetPriority(), 0); CHECK_EQ(t2->SetPriority(1), 0); CHECK_EQ(t2->GetPriority(), 0); CHECK_EQ(su->SetPriority(1), 0); CHECK_EQ(su->GetPriority(), 0);
    NxSurface9* ml = t_subsurface(e, mt, 0, 4, 4); CHECK_EQ(ml->SetPriority(1), 0); CHECK_EQ(ml->GetPriority(), 0); CHECK_EQ(mt->GetPriority(), 2);
    g_preloads = 0; mt->PreLoad(); mvb->PreLoad(); CHECK_EQ(g_preloads, 2); t2->PreLoad(); st->PreLoad(); su->PreLoad(); CHECK_EQ(g_preloads, 2); ml->PreLoad(); CHECK_EQ(g_preloads, 3);
    // ---- private data (Wine test_private_data): IUNKNOWN needs sizeof(IUnknown*), failures keep the old entry, Set AddRefs before releasing the old one, Get rules, per-object store
    IUnknown* dp = d; ULONG r1 = rc(du); IUnknown* ptr; DWORD size;
    CHECK_HR(su->SetPrivateData(G1, dp, 0, D3DSPD_IUNKNOWN), D3DERR_INVALIDCALL); CHECK_HR(su->SetPrivateData(G1, dp, 5, D3DSPD_IUNKNOWN), D3DERR_INVALIDCALL); CHECK_HR(su->SetPrivateData(G1, &dp, 2 * sizeof dp, D3DSPD_IUNKNOWN), D3DERR_INVALIDCALL);
    CHECK_HR(su->SetPrivateData(G1, &dp, sizeof dp, D3DSPD_IUNKNOWN), D3D_OK); CHECK_EQ(rc(du), r1 + 1);
    CHECK_HR(su->SetPrivateData(G1, &dp, 2 * sizeof dp, D3DSPD_IUNKNOWN), D3DERR_INVALIDCALL); size = sizeof ptr; CHECK_HR(su->GetPrivateData(G1, &ptr, &size), D3D_OK); ptr->Release();  // old entry survived
    CHECK_HR(su->SetPrivateData(G1, &dp, sizeof dp, D3DSPD_IUNKNOWN), D3D_OK); CHECK_EQ(rc(du), r1 + 1);     // replaced by itself: net 0
    IUnknown* sp = su; CHECK_HR(su->SetPrivateData(G1, &sp, sizeof sp, D3DSPD_IUNKNOWN), D3D_OK); CHECK_EQ(rc(du), r1); CHECK_EQ(rc(su), 2);  // device released, the surface AddRef'd by its own entry
    CHECK_HR(su->FreePrivateData(G1), D3D_OK); CHECK_HR(su->FreePrivateData(G1), D3DERR_NOTFOUND);
    CHECK_HR(su->SetPrivateData(G1, &dp, sizeof dp, D3DSPD_IUNKNOWN), D3D_OK); size = 2 * sizeof ptr; CHECK_HR(su->GetPrivateData(G1, &ptr, &size), D3D_OK); CHECK(size == sizeof dp && ptr == dp); CHECK_EQ(rc(du), r1 + 2); ptr->Release();
    size = 1; CHECK_HR(su->GetPrivateData(G1, nullptr, &size), D3D_OK); CHECK_EQ(size, sizeof dp);
    ptr = (IUnknown*)0xdeadbeef; size = 1; CHECK_HR(su->GetPrivateData(G1, &ptr, &size), D3DERR_MOREDATA); CHECK(size == sizeof dp && ptr == (IUnknown*)0xdeadbeef);
    ptr = (IUnknown*)0xdeadbeef; size = 0xdeadbabe; CHECK_HR(su->GetPrivateData(G2, &ptr, &size), D3DERR_NOTFOUND); CHECK(ptr == (IUnknown*)0xdeadbeef && size == 0xdeadbabe); CHECK_HR(su->GetPrivateData(G2, nullptr, &size), D3DERR_NOTFOUND);
    TTracker tp; tp.watch(su); su->Release(); CHECK(!tp.alive()); CHECK_EQ(rc(du), r1 - 1);   // destroying the surface frees its entries (device reference of the entry) and drops its own device reference
    uint32_t data[4] = {1, 2, 3, 4}, back[4] = {}; NxSurface9* lv0 = t_subsurface(e, t2, 0, 4, 4); NxSurface9* lv1 = t_subsurface(e, t2, 0, 2, 2);
    CHECK_HR(t2->SetPrivateData(G1, data, sizeof data, 0), D3D_OK); size = sizeof back; CHECK_HR(lv0->GetPrivateData(G1, back, &size), D3DERR_NOTFOUND); CHECK_HR(t2->GetPrivateData(G1, back, &size), D3D_OK); CHECK(!memcmp(data, back, 16));
    CHECK_HR(lv0->SetPrivateData(G2, data, sizeof data, 0), D3D_OK); CHECK_HR(lv1->GetPrivateData(G2, back, &size), D3DERR_NOTFOUND); CHECK_HR(lv0->FreePrivateData(G2), D3D_OK);
    // ---- state blocks hold a device reference like every child; releasing past zero stays at zero; the last device reference destroys the device and drops the IDirect3D9 reference
    IDirect3DStateBlock9* sb; ULONG r2 = rc(du); d->CreateStateBlock(D3DSBT_ALL, &sb); CHECK_EQ(rc(du), r2 + 1); CHECK_EQ(sb->Release(), 0); CHECK_EQ(rc(du), r2); CHECK_EQ(sb->Release(), 0);
}
