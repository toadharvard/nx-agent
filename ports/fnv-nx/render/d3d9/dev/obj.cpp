#include "obj.h"
#include <stdlib.h>
#include <string.h>
void* nxd3d_alloc(size_t n) { return malloc(n ? n : 1); }  // NULL on exhaustion: a result (obj.h)
void nxd3d_free(void* p) { free(p); }
void* nx_alloc_face(size_t n) { uint8_t* r = (uint8_t*)nxd3d_alloc(n + NX_FACE_SIZE); if (!r) return nullptr; for (int i = 0; i < NX_FACE_SIZE; i++) r[i] = 0; return r + NX_FACE_SIZE; }
void nx_free_face(void* object) { if (object) nxd3d_free((uint8_t*)object - NX_FACE_SIZE); }
const GUID IID_NxObj = {0x4e784f62, 0x6a5b, 0x4cf1, {0x9d, 0x01, 0x6e, 0x3f, 0xa0, 0x5b, 0x00, 0x01}};
static bool eq(const GUID& a, const GUID& b) { return memcmp(&a, &b, sizeof(GUID)) == 0; }

NxObj* nx_obj_of(IUnknown* p) {
    void* o = nullptr;
    if (!p || FAILED(p->QueryInterface(IID_NxObj, &o))) return nullptr;
    return static_cast<NxObj*>(o);
}

// ---- construction. Rules (source: Wine d3d9 tests device.c test_refcount "Findings", MS docs IDirect3DDevice9 / IDirect3DSurface9):
//  R1 every object created by Create* has public count 1 and, while its public count is > 0, one public reference on the device.
//  R2 the device holds a public reference on IDirect3D9 for its whole life (IDirect3D9 count 2 after CreateDevice).
//  R3 implicit surfaces and the implicit swap chain start at public 0, are never freed by their count, hold the device only while public > 0.
//  R4 level / face sub-resources have no count of their own: AddRef/Release/count are the container's; the device count is untouched.
void NxObj::asRoot(uint32_t refs) { pub = refs; }
void NxObj::asDevice(NxObj* d3d) { pub = 1; owner = d3d; if (d3d) d3d->addref(); }
void NxObj::asChild(NxObj* device, uint32_t pool) {
    pub = 1; dev = owner = device; this->pool = (uint8_t)pool; device->addref();
    if (this->pool != 0xFF) { rnext = device->rhead; if (rnext) rnext->rprev = this; device->rhead = this; rlinked = 1; }
}
void NxObj::asImplicit(NxObj* device, NxObj* cont) { pub = 0; dev = owner = device; container = cont; owned = 1; }
void NxObj::asSubResource(NxObj* device, NxObj* cont) { dev = device; container = cont; fwd = 1; pool = cont ? cont->pool : 0xFF; }
void NxObj::asDeferred(NxObj* device) { dev = owner = device; binds = 1; }
ULONG NxObj::publish() { ULONG n = addref(); unbindRef(); return n; }  // addref first: the bind reference must not be the last one
bool NxObj::is(const GUID& iid) const { for (unsigned i = 0; i < ci->n_iids; ++i) if (eq(iid, ci->iids[i])) return true; return false; }

ULONG NxObj::addref() {
    if (fwd) return container->addref();
    uint32_t n = ++pub;
    if (n == 1 && owner) owner->addref();
    return n;
}
ULONG NxObj::release() {
    if (fwd) return container->release();
    if (!pub) return 0;  // Wine: releasing an implicit surface at 0 stays 0
    uint32_t n = --pub;
    if (n) return n;
    NxObj* o = owner;
    if (!owned && !binds) destroyNow();  // nothing may touch *this after
    if (o) o->release();                 // may destroy the device, which drops the bind references it still holds
    return 0;
}
void NxObj::destroyNow() { ci->destroy(this); }
void NxObj::bindRef() { if (fwd) container->bindRef(); else ++binds; }
void NxObj::unbindRef() {
    if (fwd) { container->unbindRef(); return; }
    if (binds && --binds == 0 && pub == 0 && !owned) destroyNow();
}

HRESULT NxObj::qi(REFIID riid, void** pp) {
    if (!pp) return E_POINTER;
    *pp = nullptr;
    if (eq(riid, IID_NxObj)) { *pp = this; return S_OK; }
    for (unsigned i = 0; i < ci->n_iids; ++i)
        if (eq(riid, ci->iids[i])) { *pp = self; addref(); return S_OK; }  // all interfaces of one object are one single-inheritance chain: same address
    return E_NOINTERFACE;
}
HRESULT NxObj::getDevice(IDirect3DDevice9** pp) {
    if (!pp) return D3DERR_INVALIDCALL;
    *pp = static_cast<IDirect3DDevice9*>(dev->self); dev->addref(); return D3D_OK;
}
HRESULT NxObj::getDirect3D(IDirect3D9** pp) {
    if (!pp) return D3DERR_INVALIDCALL;
    *pp = static_cast<IDirect3D9*>(owner->self); owner->addref(); return D3D_OK;
}
HRESULT NxObj::getContainer(REFIID riid, void** pp) {
    if (!pp) return D3DERR_INVALIDCALL;
    *pp = nullptr;
    return container ? container->qi(riid, pp) : D3DERR_INVALIDCALL;  // QueryInterface AddRefs through R4/R3
}

// ---- private data. Facts: Wine device.c test_private_data (checked against Windows): IUNKNOWN needs size == sizeof(IUnknown*), a failing Set keeps the old
// entry, Set AddRefs the new object before releasing the replaced one, Get with null data reports the size and succeeds, Get with a too small size
// is D3DERR_MOREDATA with the size set and the buffer untouched, an unknown guid is D3DERR_NOTFOUND with outputs untouched, sub-resources have their own store.
static NxPriv** find(NxPriv** h, REFGUID g) { while (*h && !eq((*h)->g, g)) h = &(*h)->next; return h; }
// An IUnknown entry holds either an object of the APPLICATION (set through the interface: its AddRef / Release are calls into the application, nxd3d_app_*)
// or one of the layer's own native objects (set by the layer with NX_SPD_NATIVE, a bit the interface never passes on: `flags & D3DSPD_IUNKNOWN` below).
static void uref(NxPriv* e) { if (e->flags & NX_SPD_NATIVE) e->u->AddRef(); else nxd3d_app_addref(e->u); }
static void drop(NxPriv* e) { if (e->flags & D3DSPD_IUNKNOWN) { if (e->flags & NX_SPD_NATIVE) e->u->Release(); else nxd3d_app_release(e->u); } else nxd3d_free(e->d); nxd3d_free(e); }
HRESULT NxObj::setPrivateData(REFGUID g, const void* data, DWORD size, DWORD flags) {
    bool unk = (flags & D3DSPD_IUNKNOWN) != 0;
    if (!data || (unk && size != sizeof(IUnknown*))) return D3DERR_INVALIDCALL;
    NxPriv* e = (NxPriv*)nxd3d_alloc(sizeof(NxPriv)); if (!e) return E_OUTOFMEMORY;
    e->g = g; e->size = size; e->flags = flags & (D3DSPD_IUNKNOWN | (unk ? NX_SPD_NATIVE : 0)); e->next = nullptr;
    if (unk) { e->u = *(IUnknown* const*)data; uref(e); }
    else { e->d = (uint8_t*)nxd3d_alloc(size ? size : 1); if (!e->d) { nxd3d_free(e); return E_OUTOFMEMORY; } memcpy(e->d, data, size); }
    NxPriv** s = find(&priv, g);
    if (*s) { NxPriv* old = *s; e->next = old->next; *s = e; drop(old); } else { e->next = priv; priv = e; }
    return D3D_OK;
}
HRESULT NxObj::getPrivateData(REFGUID g, void* data, DWORD* size) {
    if (!size) return D3DERR_INVALIDCALL;  // Windows faults; a defined error is the closed choice (README: doubt)
    NxPriv* e = *find(&priv, g);
    if (!e) return D3DERR_NOTFOUND;
    if (!data) { *size = e->size; return D3D_OK; }
    if (*size < e->size) { *size = e->size; return D3DERR_MOREDATA; }
    if (e->flags & D3DSPD_IUNKNOWN) { *(IUnknown**)data = e->u; uref(e); } else memcpy(data, e->d, e->size);
    *size = e->size; return D3D_OK;
}
HRESULT NxObj::freePrivateData(REFGUID g) {
    NxPriv** s = find(&priv, g);
    if (!*s) return D3DERR_NOTFOUND;
    NxPriv* e = *s; *s = e->next; drop(e); return D3D_OK;
}
NxObj::~NxObj() {
    if (rlinked) { if (rprev) rprev->rnext = rnext; else dev->rhead = rnext; if (rnext) rnext->rprev = rprev; }
    while (priv) { NxPriv* e = priv; priv = e->next; drop(e); } }

// ---- priority: MS IDirect3DResource9::SetPriority "managed resources only"; Wine test_resource_priority: textures / buffers of D3DPOOL_MANAGED keep it,
// everything else (other pools, all surfaces, render targets) reads 0 after Set; Set returns the previous value.
DWORD NxObj::setPriority(DWORD p) {
    if (!managed() || ci->rtype == D3DRTYPE_SURFACE) return 0;
    DWORD old = priority; priority = p; return old;
}
DWORD NxObj::getPriority() { return managed() && ci->rtype != D3DRTYPE_SURFACE ? priority : 0; }
void NxObj::preLoad() { if (fwd) container->preLoad(); else if (managed()) nxd3d_preload(this); }  // MS: PreLoad applies to managed resources; a level preloads its whole container
D3DRESOURCETYPE NxObj::getType() { return (D3DRESOURCETYPE)ci->rtype; }
