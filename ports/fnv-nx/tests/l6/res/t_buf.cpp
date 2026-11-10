// parts a (buffer creation rules) and b (locks, flags, dirty range, DISCARD zero-fill)
#include "t_common.h"
#include "res_core.h"
static NxBufCore* B(IUnknown* p) { return static_cast<NxBufCore*>(nx_obj_of(p)); }
static NxVertexBuffer9* mkvb(NxDevice9* d, UINT len, DWORD usage, D3DPOOL pool, DWORD fvf = 0) { IDirect3DVertexBuffer9* v = nullptr; return d->CreateVertexBuffer(len, usage, fvf, pool, &v, nullptr) == D3D_OK ? static_cast<NxVertexBuffer9*>(v) : nullptr; }
void t_buf() {
    t_part('a'); TEnv e = t_env(); NxDevice9* d = e.dev; TEnv x = t_env(true); NxDevice9Ex* dx = x.devex; IDirect3DVertexBuffer9* v = nullptr; IDirect3DIndexBuffer9* ib = nullptr; HANDLE h = (HANDLE)1;
    struct C { UINT len; DWORD usage; DWORD fvf; D3DPOOL pool; HRESULT hr; } vb[] = {
        {64, 0, 0, D3DPOOL_DEFAULT, D3D_OK}, {64, D3DUSAGE_DYNAMIC | D3DUSAGE_WRITEONLY, 0, D3DPOOL_DEFAULT, D3D_OK}, {64, 0, 0, D3DPOOL_MANAGED, D3D_OK}, {64, 0, 0, D3DPOOL_SYSTEMMEM, D3D_OK},
        {64, D3DUSAGE_DYNAMIC, 0, D3DPOOL_MANAGED, D3D_OK}, {64, 0xFFFFFFFF & ~(D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL), 0, D3DPOOL_DEFAULT, D3D_OK},
        {0, 0, 0, D3DPOOL_DEFAULT, D3DERR_INVALIDCALL}, {64, 0, 0, D3DPOOL_SCRATCH, D3DERR_INVALIDCALL}, {64, 0, 0, (D3DPOOL)4, D3DERR_INVALIDCALL}, {64, 0, 0, (D3DPOOL)0x7fffffff, D3DERR_INVALIDCALL},
        {64, D3DUSAGE_RENDERTARGET, 0, D3DPOOL_DEFAULT, D3DERR_INVALIDCALL}, {64, D3DUSAGE_DEPTHSTENCIL, 0, D3DPOOL_DEFAULT, D3DERR_INVALIDCALL}, {64, D3DUSAGE_RENDERTARGET | D3DUSAGE_DYNAMIC, 0, D3DPOOL_SYSTEMMEM, D3DERR_INVALIDCALL},
        {64, 0, D3DFVF_XYZ | D3DFVF_TEX1, D3DPOOL_DEFAULT, D3D_OK}, {64, 0, 0x1, D3DPOOL_DEFAULT, D3DERR_INVALIDCALL}, {64, 0, 0x9 << 8, D3DPOOL_DEFAULT, D3DERR_INVALIDCALL}, {64, 0, 0xFFFF, D3DPOOL_DEFAULT, D3DERR_INVALIDCALL},
        {NX_MAX_BUFFER_BYTES + 1, 0, 0, D3DPOOL_DEFAULT, D3DERR_OUTOFVIDEOMEMORY}, {0xFFFFFFFFu, 0, 0, D3DPOOL_SYSTEMMEM, D3DERR_OUTOFVIDEOMEMORY}};
    for (auto& c : vb) { v = (IDirect3DVertexBuffer9*)1; HRESULT hr = d->CreateVertexBuffer(c.len, c.usage, c.fvf, c.pool, &v, nullptr); CHECK_EQ(hr, c.hr); if (hr == D3D_OK) { CHECK(v && nx_obj_of(v)); v->Release(); } else CHECK(v == nullptr); }
    CHECK_HR(d->CreateVertexBuffer(64, 0, 0, D3DPOOL_DEFAULT, nullptr, nullptr), D3DERR_INVALIDCALL);
    CHECK_HR(dx->CreateVertexBuffer(64, 0, 0, D3DPOOL_MANAGED, &v, nullptr), D3DERR_INVALIDCALL);         // Ex: MANAGED refused (Wine vertexbuffer_init)
    CHECK_HR(dx->CreateVertexBuffer(64, 0, 0, D3DPOOL_DEFAULT, &v, nullptr), D3D_OK); v->Release();
    CHECK_HR(d->CreateVertexBuffer(64, 0, 0, D3DPOOL_DEFAULT, &v, &h), E_NOTIMPL);                           // shared handle on a non-Ex device
    CHECK_HR(dx->CreateVertexBuffer(64, 0, 0, D3DPOOL_SYSTEMMEM, &v, &h), D3DERR_NOTAVAILABLE);
    CHECK_HR(dx->CreateVertexBuffer(64, 0, 0, D3DPOOL_DEFAULT, &v, &h), D3D_OK); v->Release();
    struct I { UINT len; DWORD usage; D3DFORMAT f; D3DPOOL pool; HRESULT hr; } ibc[] = {
        {64, 0, D3DFMT_INDEX16, D3DPOOL_DEFAULT, D3D_OK}, {64, 0, D3DFMT_INDEX32, D3DPOOL_MANAGED, D3D_OK}, {64, D3DUSAGE_DYNAMIC, D3DFMT_INDEX16, D3DPOOL_SYSTEMMEM, D3D_OK},
        {0, 0, D3DFMT_INDEX16, D3DPOOL_DEFAULT, D3DERR_INVALIDCALL}, {64, 0, D3DFMT_UNKNOWN, D3DPOOL_DEFAULT, D3DERR_INVALIDCALL}, {64, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, D3DERR_INVALIDCALL},
        {64, 0, D3DFMT_VERTEXDATA, D3DPOOL_DEFAULT, D3DERR_INVALIDCALL}, {64, 0, D3DFMT_INDEX16, D3DPOOL_SCRATCH, D3DERR_INVALIDCALL}, {64, D3DUSAGE_DEPTHSTENCIL, D3DFMT_INDEX16, D3DPOOL_DEFAULT, D3DERR_INVALIDCALL},
        {NX_MAX_BUFFER_BYTES + 1, 0, D3DFMT_INDEX16, D3DPOOL_DEFAULT, D3DERR_OUTOFVIDEOMEMORY}};
    for (auto& c : ibc) { ib = (IDirect3DIndexBuffer9*)1; HRESULT hr = d->CreateIndexBuffer(c.len, c.usage, c.f, c.pool, &ib, nullptr); CHECK_EQ(hr, c.hr); if (hr == D3D_OK) ib->Release(); else CHECK(ib == nullptr); }
    CHECK_HR(dx->CreateIndexBuffer(64, 0, D3DFMT_INDEX16, D3DPOOL_MANAGED, &ib, nullptr), D3DERR_INVALIDCALL);
    CHECK_HR(d->CreateIndexBuffer(64, 0, D3DFMT_INDEX16, D3DPOOL_DEFAULT, &ib, &h), E_NOTIMPL);
    // GetDesc / resource part through the object model
    NxVertexBuffer9* m = mkvb(d, 100, D3DUSAGE_WRITEONLY, D3DPOOL_MANAGED, D3DFVF_XYZ | D3DFVF_DIFFUSE); D3DVERTEXBUFFER_DESC vd = {};
    CHECK_HR(m->GetDesc(&vd), D3D_OK); CHECK(vd.Format == D3DFMT_VERTEXDATA && vd.Type == D3DRTYPE_VERTEXBUFFER && vd.Usage == D3DUSAGE_WRITEONLY && vd.Pool == D3DPOOL_MANAGED && vd.Size == 100 && vd.FVF == (D3DFVF_XYZ | D3DFVF_DIFFUSE));
    CHECK_HR(m->GetDesc(nullptr), D3DERR_INVALIDCALL); CHECK_EQ(m->GetType(), D3DRTYPE_VERTEXBUFFER); CHECK_EQ(m->SetPriority(7), 0); CHECK_EQ(m->GetPriority(), 7);
    IDirect3DDevice9* gd = nullptr; CHECK_HR(m->GetDevice(&gd), D3D_OK); CHECK(gd == static_cast<IDirect3DDevice9*>(d)); gd->Release();
    d->CreateIndexBuffer(40, D3DUSAGE_DYNAMIC, D3DFMT_INDEX32, D3DPOOL_DEFAULT, &ib, nullptr); D3DINDEXBUFFER_DESC id = {};
    CHECK_HR(ib->GetDesc(&id), D3D_OK); CHECK(id.Format == D3DFMT_INDEX32 && id.Type == D3DRTYPE_INDEXBUFFER && id.Usage == D3DUSAGE_DYNAMIC && id.Pool == D3DPOOL_DEFAULT && id.Size == 40);
    CHECK_EQ(ib->GetType(), D3DRTYPE_INDEXBUFFER); CHECK_EQ(ib->Release(), 0); CHECK_EQ(m->Release(), 0);

    // ---- b: locks
    t_part('b'); uint8_t* p; uint32_t lo, hi;
    NxVertexBuffer9* a = mkvb(d, 256, 0, D3DPOOL_DEFAULT); void* q = nullptr;
    CHECK(a && a->Lock(0, 0, &q, 0) == D3D_OK); p = (uint8_t*)q; CHECK(p != nullptr && ((uintptr_t)p & 15) == 0);
    bool zero = true; for (int i = 0; i < 256; i++) zero &= p[i] == 0; CHECK(zero);                      // zero-filled at creation
    memset(p, 0x5A, 256); CHECK_HR(a->Unlock(), D3D_OK);
    CHECK_HR(a->Lock(16, 32, &q, 0), D3D_OK); CHECK(q == p + 16 && ((uint8_t*)q)[0] == 0x5A);          // content persists, pointer fixed for the lifetime
    void* q2 = nullptr; CHECK_HR(a->Lock(0, 0, &q2, 0), D3D_OK); CHECK(q2 == p); CHECK_EQ(B(a)->locks, 2);   // nested
    a->Unlock(); CHECK_EQ(B(a)->locks, 1); a->Unlock(); CHECK_EQ(B(a)->locks, 0); CHECK_HR(a->Unlock(), D3D_OK); CHECK_EQ(B(a)->locks, 0);   // extra Unlock: D3D_OK (Wine)
    struct R { UINT off, size; HRESULT hr; UINT delta; } rng[] = {{0, 0, D3D_OK, 0}, {0, 256, D3D_OK, 0}, {255, 1, D3D_OK, 255}, {100, 0, D3D_OK, 100}, {255, 0, D3D_OK, 255}, {0, 257, D3DERR_INVALIDCALL, 0},
        {256, 0, D3DERR_INVALIDCALL, 0}, {256, 1, D3DERR_INVALIDCALL, 0}, {1, 256, D3DERR_INVALIDCALL, 0}, {0xFFFFFFF0u, 0x20, D3DERR_INVALIDCALL, 0}, {0xFFFFFFFFu, 0xFFFFFFFFu, D3DERR_INVALIDCALL, 0}, {1000, 0, D3DERR_INVALIDCALL, 0}};
    for (auto& r : rng) { q = (void*)1; HRESULT hr = a->Lock(r.off, r.size, &q, 0); CHECK_EQ(hr, r.hr); if (hr == D3D_OK) { CHECK(q == p + r.delta); a->Unlock(); } else CHECK(q == nullptr); }
    CHECK_HR(a->Lock(0, 0, nullptr, 0), D3DERR_INVALIDCALL);
    for (DWORD f : std::initializer_list<DWORD>{0u, (DWORD)D3DLOCK_READONLY, (DWORD)D3DLOCK_DISCARD, (DWORD)D3DLOCK_NOOVERWRITE, (DWORD)(D3DLOCK_DISCARD | D3DLOCK_NOOVERWRITE), (DWORD)(D3DLOCK_DISCARD | D3DLOCK_READONLY), (DWORD)0xdeadbeefu, (DWORD)0xFFFFFFFFu, (DWORD)D3DLOCK_NOSYSLOCK, (DWORD)D3DLOCK_DONOTWAIT}) {
        CHECK_HR(a->Lock(0, 0, &q, f), D3D_OK); a->Unlock(); }                                          // flag combinations are never an error (XP behaviour, Shaiya)
    // DISCARD / NOOVERWRITE act only on DYNAMIC buffers of a GPU pool; zero-fill (RD7)
    struct F { DWORD usage; D3DPOOL pool; DWORD flags; bool zeroed; } fl[] = {
        {D3DUSAGE_DYNAMIC, D3DPOOL_DEFAULT, D3DLOCK_DISCARD, true}, {D3DUSAGE_DYNAMIC | D3DUSAGE_WRITEONLY, D3DPOOL_DEFAULT, D3DLOCK_DISCARD, true}, {0, D3DPOOL_DEFAULT, D3DLOCK_DISCARD, false},
        {D3DUSAGE_DYNAMIC, D3DPOOL_DEFAULT, D3DLOCK_NOOVERWRITE, false}, {D3DUSAGE_DYNAMIC, D3DPOOL_DEFAULT, D3DLOCK_DISCARD | D3DLOCK_NOOVERWRITE, false}, {D3DUSAGE_DYNAMIC, D3DPOOL_DEFAULT, 0, false},
        {D3DUSAGE_DYNAMIC, D3DPOOL_DEFAULT, D3DLOCK_DISCARD | D3DLOCK_READONLY, true}, {D3DUSAGE_DYNAMIC, D3DPOOL_SYSTEMMEM, D3DLOCK_DISCARD, false}, {D3DUSAGE_DYNAMIC, D3DPOOL_MANAGED, D3DLOCK_DISCARD, true},
        {0, D3DPOOL_MANAGED, D3DLOCK_DISCARD, false}, {D3DUSAGE_DYNAMIC, D3DPOOL_DEFAULT, D3DLOCK_READONLY, false}};
    for (auto& c : fl) {
        NxVertexBuffer9* v2 = mkvb(d, 64, c.usage, c.pool); v2->Lock(0, 0, &q, 0); memset(q, 0xEE, 64); v2->Unlock();
        v2->Lock(0, 0, &q, c.flags); bool z = true, kept = true; for (int i = 0; i < 64; i++) { z &= ((uint8_t*)q)[i] == 0; kept &= ((uint8_t*)q)[i] == 0xEE; }
        CHECK(c.zeroed ? z : kept); v2->Unlock(); v2->Release();
    }
    // dirty range + revision: DEFAULT pool = whole buffer on a write lock; MANAGED = the locked range; READONLY none; NO_DIRTY_UPDATE only on MANAGED
    NxVertexBuffer9* g = mkvb(d, 256, 0, D3DPOOL_DEFAULT); NxBufCore* gb = B(g); CHECK(!gb->take_dirty(&lo, &hi)); uint32_t r0 = gb->rev;
    g->Lock(4, 8, &q, 0); g->Unlock(); CHECK(gb->take_dirty(&lo, &hi) && lo == 0 && hi == 256); CHECK(!gb->take_dirty(&lo, &hi)); CHECK_EQ(gb->rev, r0 + 1);
    g->Lock(4, 8, &q, D3DLOCK_READONLY); g->Unlock(); CHECK(!gb->take_dirty(&lo, &hi)); CHECK_EQ(gb->rev, r0 + 1);
    g->Lock(4, 8, &q, D3DLOCK_NO_DIRTY_UPDATE); g->Unlock(); CHECK(gb->take_dirty(&lo, &hi) && lo == 0 && hi == 256);
    NxVertexBuffer9* mg = mkvb(d, 256, 0, D3DPOOL_MANAGED); NxBufCore* mb = B(mg); r0 = mb->rev;
    mg->Lock(8, 8, &q, 0); mg->Unlock(); mg->Lock(100, 20, &q, 0); mg->Unlock(); CHECK(mb->take_dirty(&lo, &hi) && lo == 8 && hi == 120); CHECK(!mb->take_dirty(&lo, &hi)); CHECK_EQ(mb->rev, r0 + 2);
    mg->Lock(200, 0, &q, 0); mg->Unlock(); mg->Lock(0, 4, &q, 0); mg->Unlock(); CHECK(mb->take_dirty(&lo, &hi) && lo == 0 && hi == 256);
    mg->Lock(8, 8, &q, D3DLOCK_NO_DIRTY_UPDATE); mg->Unlock(); CHECK(!mb->take_dirty(&lo, &hi)); CHECK_EQ(mb->rev, r0 + 4);   // MS: no change of the dirty state; rev: only dirtying locks count
    g->Release(); mg->Release(); a->Release();
    NxBufCore* nb; IDirect3DIndexBuffer9* i2 = nullptr; d->CreateIndexBuffer(64, D3DUSAGE_DYNAMIC, D3DFMT_INDEX16, D3DPOOL_DEFAULT, &i2, nullptr); nb = B(i2);
    CHECK_HR(i2->Lock(0, 0, &q, 0), D3D_OK); memset(q, 1, 64); i2->Unlock(); i2->Lock(0, 0, &q, D3DLOCK_DISCARD); CHECK_EQ(((uint8_t*)q)[63], 0); i2->Unlock(); CHECK_HR(i2->Lock(65, 0, &q, 0), D3DERR_INVALIDCALL); i2->Release(); (void)nb;
}
