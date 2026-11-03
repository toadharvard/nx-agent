// fnv-nx: guest (ILP32) runtime of the D3D9 proxy (stream S6c-nine): proxy identity map, wrap functions, the
// hand-written bodies of the manual methods (gen_proxy.py MANUAL), and Direct3DCreate9.
#include <d3d9.h>
#include <string.h>
#include <stdlib.h>
#include <stddef.h>
#include "nine_proxy.h"
#include "nine_proxy_ids.h"

static_assert(sizeof(void*) == 4, "guest is ILP32");

// ---- identity map: (remote, interface index) -> proxy ---------------------------------------------------------
namespace {
struct Entry { uint64_t remote; uint32_t iface; void* ptr; NxProxy* proxy; };
Entry* g_map; uint32_t g_cap, g_count;

uint32_t hash(uint64_t r, uint32_t i) { return (uint32_t)((r >> 3) * 2654435761u ^ i * 40503u); }

Entry* find(uint64_t r, uint32_t i) {
    if (!g_cap) return nullptr;
    for (uint32_t k = hash(r, i) & (g_cap - 1), n = 0; n < g_cap; k = (k + 1) & (g_cap - 1), n++) {
        if (!g_map[k].remote) return nullptr;
        if (g_map[k].remote == r && g_map[k].iface == i) return &g_map[k];
    }
    return nullptr;
}
void insert(const Entry& e);
void grow() {
    Entry* old = g_map; uint32_t oc = g_cap;
    g_cap = g_cap ? g_cap * 2 : 1024; g_count = 0;
    g_map = (Entry*)calloc(g_cap, sizeof(Entry));
    for (uint32_t k = 0; k < oc; k++) if (old[k].remote) insert(old[k]);
    free(old);
}
void insert(const Entry& e) {
    if ((g_count + 1) * 2 > g_cap) grow();
    uint32_t k = hash(e.remote, e.iface) & (g_cap - 1);
    while (g_map[k].remote) k = (k + 1) & (g_cap - 1);
    g_map[k] = e; g_count++;
}
void erase_at(uint32_t k) {   // backward-shift deletion: re-insert the rest of the probe run
    g_map[k] = Entry{}; g_count--;
    for (uint32_t j = (k + 1) & (g_cap - 1); g_map[j].remote; j = (j + 1) & (g_cap - 1)) {
        Entry e = g_map[j]; g_map[j] = Entry{}; g_count--; insert(e);
    }
}
void erase(NxProxy* p) {     // a proxy is keyed by (remote, interface index); try each interface index
    for (uint32_t i = 0; i < 64; i++) {
        Entry* e = find(p->remote, i);
        if (e && e->proxy == p) { erase_at((uint32_t)(e - g_map)); return; }
    }
}

typedef void* (*NewFn)(uint64_t);
template <class I, I* (*New)(uint64_t)> I* wrap(uint64_t r, uint32_t idx) {
    if (!r) return nullptr;
    if (Entry* e = find(r, idx)) return (I*)e->ptr;
    I* p = New(r);
    insert(Entry{r, idx, p, static_cast<NxProxy*>(reinterpret_cast<NxProxy*>((char*)p + 4))});
    return p;
}
} // namespace

// NxProxy subobject sits right after the interface vptr (offset 4) in every Px class: single inheritance chain
// for the interface, then NxProxy. remote is at +8 (checked in nine_proxy.h).
#define NX_WRAP(I) I* nx_wrap_##I(uint64_t r) { return wrap<I, nx_new_##I>(r, NXI_##I); }
NX_WRAP(IDirect3D9) NX_WRAP(IDirect3DDevice9) NX_WRAP(IDirect3DSwapChain9) NX_WRAP(IDirect3DSurface9)
NX_WRAP(IDirect3DVolume9) NX_WRAP(IDirect3DTexture9) NX_WRAP(IDirect3DCubeTexture9) NX_WRAP(IDirect3DVolumeTexture9)
NX_WRAP(IDirect3DVertexBuffer9) NX_WRAP(IDirect3DIndexBuffer9) NX_WRAP(IDirect3DVertexDeclaration9)
NX_WRAP(IDirect3DVertexShader9) NX_WRAP(IDirect3DPixelShader9) NX_WRAP(IDirect3DStateBlock9) NX_WRAP(IDirect3DQuery9)
NX_WRAP(IDirect3D9Ex) NX_WRAP(IDirect3DDevice9Ex) NX_WRAP(IDirect3DSwapChain9Ex)

// Abstract types: pick the concrete proxy class from the remote's resource type.
static uint32_t remote_type(uint64_t r) {
    uint64_t a[1] = {0};
    return (uint32_t)nx_call(r, NXM_IDirect3DResource9_GetType, FNVNX_ADDR(a));
}
IDirect3DBaseTexture9* nx_wrap_IDirect3DBaseTexture9(uint64_t r) {
    switch (remote_type(r)) {
    case D3DRTYPE_CUBETEXTURE: return nx_wrap_IDirect3DCubeTexture9(r);
    case D3DRTYPE_VOLUMETEXTURE: return nx_wrap_IDirect3DVolumeTexture9(r);
    default: return nx_wrap_IDirect3DTexture9(r);
    }
}
IDirect3DResource9* nx_wrap_IDirect3DResource9(uint64_t r) {
    switch (remote_type(r)) {
    case D3DRTYPE_SURFACE: return nx_wrap_IDirect3DSurface9(r);
    case D3DRTYPE_VERTEXBUFFER: return nx_wrap_IDirect3DVertexBuffer9(r);
    case D3DRTYPE_INDEXBUFFER: return nx_wrap_IDirect3DIndexBuffer9(r);
    default: return nx_wrap_IDirect3DBaseTexture9(r);
    }
}

// riid -> wrapped interface (QueryInterface, GetContainer)
static void* wrap_by_iid(REFIID riid, uint64_t r) {
#define T(I) if (!memcmp(&riid, &IID_##I, sizeof(GUID))) return nx_wrap_##I(r);
    T(IDirect3D9) T(IDirect3D9Ex) T(IDirect3DDevice9) T(IDirect3DDevice9Ex) T(IDirect3DSwapChain9)
    T(IDirect3DSwapChain9Ex) T(IDirect3DSurface9) T(IDirect3DVolume9) T(IDirect3DTexture9) T(IDirect3DCubeTexture9)
    T(IDirect3DVolumeTexture9) T(IDirect3DBaseTexture9) T(IDirect3DResource9) T(IDirect3DVertexBuffer9)
    T(IDirect3DIndexBuffer9) T(IDirect3DVertexDeclaration9) T(IDirect3DVertexShader9) T(IDirect3DPixelShader9)
    T(IDirect3DStateBlock9) T(IDirect3DQuery9)
#undef T
    return nullptr;
}

// ---- manual method bodies ------------------------------------------------------------------------------------
static inline uint64_t A(const void* p) { return FNVNX_ADDR(p); }
#define CALL(...) do { uint64_t a_[] = {__VA_ARGS__}; return (HRESULT)nx_call(self->remote, mid, A(a_)); } while (0)

ULONG nx_m_AddRef(NxProxy* self, uint32_t mid) { uint64_t a[1] = {0}; return (ULONG)nx_call(self->remote, mid, A(a)); }
ULONG nx_m_Release(NxProxy* self, uint32_t mid) {
    uint64_t a[1] = {0};
    ULONG r = (ULONG)nx_call(self->remote, mid, A(a));
    if (r == 0) { erase(self); delete self; }
    return r;
}
HRESULT nx_m_QueryInterface(NxProxy* self, uint32_t mid, REFIID riid, void** ppv) {
    if (!ppv) return E_POINTER;
    uint64_t o = 0, a[2] = {A(&riid), A(&o)};
    HRESULT hr = (HRESULT)nx_call(self->remote, mid, A(a));
    *ppv = SUCCEEDED(hr) ? wrap_by_iid(riid, o) : nullptr;
    if (SUCCEEDED(hr) && !*ppv) {   // IUnknown or an interface we do not proxy: answer with this object
        *ppv = (char*)self - 4;
    }
    return hr;
}
HRESULT nx_m_GetContainer(NxProxy* self, uint32_t mid, REFIID riid, void** pp) {
    if (!pp) return D3DERR_INVALIDCALL;
    uint64_t o = 0, a[2] = {A(&riid), A(&o)};
    HRESULT hr = (HRESULT)nx_call(self->remote, mid, A(a));
    *pp = SUCCEEDED(hr) ? wrap_by_iid(riid, o) : nullptr;
    return hr;
}
HRESULT nx_m_GetDevice(NxProxy* self, uint32_t mid, IDirect3DDevice9** pp) {
    if (!pp) return D3DERR_INVALIDCALL;
    uint64_t o = 0, a[1] = {A(&o)};
    HRESULT hr = (HRESULT)nx_call(self->remote, mid, A(a));
    *pp = SUCCEEDED(hr) ? nx_wrap_IDirect3DDevice9(o) : nullptr;   // same remote -> same proxy
    return hr;
}
HRESULT nx_m_GetDirect3D(NxProxy* self, uint32_t mid, IDirect3D9** pp) {
    if (!pp) return D3DERR_INVALIDCALL;
    uint64_t o = 0, a[1] = {A(&o)};
    HRESULT hr = (HRESULT)nx_call(self->remote, mid, A(a));
    *pp = SUCCEEDED(hr) ? nx_wrap_IDirect3D9(o) : nullptr;
    return hr;
}
HRESULT nx_m_SetPrivateData(NxProxy* self, uint32_t mid, REFGUID g, const void* data, DWORD size, DWORD flags) {
    if (flags & D3DSPD_IUNKNOWN) {   // data is an IUnknown* of ours: hand the host the remote object (it AddRefs)
        if (size != sizeof(void*) || !data) return D3DERR_INVALIDCALL;
        uint64_t remote = nx_unwrap(*(void* const*)data);
        uint64_t box = remote;
        CALL(A(&g), A(&box), 8, flags);
    }
    CALL(A(&g), A(data), size, flags);
}
HRESULT nx_m_GetPrivateData(NxProxy* self, uint32_t mid, REFGUID g, void* data, DWORD* size) {
    // IUnknown payloads come back as an 8-byte LP64 pointer; give the engine 4 bytes (its object pointer).
    uint64_t wide[2] = {0, 0}; DWORD wsz = sizeof wide;
    uint64_t a[3] = {A(&g), A(wide), A(&wsz)};
    HRESULT hr = (HRESULT)nx_call(self->remote, mid, A(a));
    if (hr == D3DERR_MOREDATA || (SUCCEEDED(hr) && wsz > sizeof wide) || wsz != 8) {
        uint64_t b[3] = {A(&g), A(data), A(size)};   // plain data: let the host write directly
        return (HRESULT)nx_call(self->remote, mid, A(b));
    }
    // 8 bytes: either 8 bytes of data or an IUnknown*; the engine stores only plain data, so treat as data
    if (!size) return D3DERR_INVALIDCALL;
    if (!data) { *size = 8; return D3D_OK; }
    if (*size < 8) { *size = 8; return D3DERR_MOREDATA; }
    memcpy(data, wide, 8); *size = 8;
    return hr;
}
HRESULT nx_m_GetDC(NxProxy*, uint32_t, HDC* phdc) { if (phdc) *phdc = nullptr; return D3DERR_INVALIDCALL; }
HRESULT nx_m_ReleaseDC(NxProxy*, uint32_t, HDC) { return D3DERR_INVALIDCALL; }
HMONITOR nx_m_GetAdapterMonitor(NxProxy*, uint32_t, UINT adapter) { return adapter == 0 ? (HMONITOR)0x4D4F4E31 : nullptr; }
HRESULT nx_m_RegisterSoftwareDevice(NxProxy*, uint32_t, void*) { return D3DERR_NOTAVAILABLE; }
HRESULT nx_m_SetCursorProperties(NxProxy*, uint32_t, UINT, UINT, IDirect3DSurface9*) { return D3D_OK; }
HRESULT nx_m_CheckDeviceState(NxProxy* self, uint32_t mid, HWND) { CALL(0); }
HRESULT nx_m_Present(NxProxy* self, uint32_t mid, const RECT* s, const RECT* d, HWND, const RGNDATA* dirty) {
    CALL(A(s), A(d), 0, A(dirty));
}
HRESULT nx_m_Present(NxProxy* self, uint32_t mid, const RECT* s, const RECT* d, HWND, const RGNDATA* dirty, DWORD flags) {
    CALL(A(s), A(d), 0, A(dirty), flags);
}
HRESULT nx_m_PresentEx(NxProxy* self, uint32_t mid, const RECT* s, const RECT* d, HWND, const RGNDATA* dirty, DWORD flags) {
    CALL(A(s), A(d), 0, A(dirty), flags);
}
HRESULT nx_m_CreateDevice(NxProxy* self, uint32_t mid, UINT adapter, D3DDEVTYPE type, HWND, DWORD flags,
                          D3DPRESENT_PARAMETERS* pp, IDirect3DDevice9** out) {
    if (!pp || !out) return D3DERR_INVALIDCALL;
    NxW_D3DPRESENT_PARAMETERS w; nx_to_wide_D3DPRESENT_PARAMETERS(&w, pp);
    w.hDeviceWindow = 0;   // the host presents to the default nwindow
    uint64_t o = 0, a[6] = {adapter, (uint64_t)type, 0, flags, A(&w), A(&o)};
    HRESULT hr = (HRESULT)nx_call(self->remote, mid, A(a));
    nx_from_wide_D3DPRESENT_PARAMETERS(pp, &w);   // nine fills in defaults (back buffer size/format)
    pp->hDeviceWindow = nullptr;
    *out = SUCCEEDED(hr) ? nx_wrap_IDirect3DDevice9(o) : nullptr;
    return hr;
}
HRESULT nx_m_CreateDeviceEx(NxProxy* self, uint32_t mid, UINT adapter, D3DDEVTYPE type, HWND, DWORD flags,
                            D3DPRESENT_PARAMETERS* pp, D3DDISPLAYMODEEX* mode, IDirect3DDevice9Ex** out) {
    if (!pp || !out) return D3DERR_INVALIDCALL;
    NxW_D3DPRESENT_PARAMETERS w; nx_to_wide_D3DPRESENT_PARAMETERS(&w, pp); w.hDeviceWindow = 0;
    uint64_t o = 0, a[7] = {adapter, (uint64_t)type, 0, flags, A(&w), A(mode), A(&o)};
    HRESULT hr = (HRESULT)nx_call(self->remote, mid, A(a));
    nx_from_wide_D3DPRESENT_PARAMETERS(pp, &w); pp->hDeviceWindow = nullptr;
    *out = SUCCEEDED(hr) ? nx_wrap_IDirect3DDevice9Ex(o) : nullptr;
    return hr;
}
HRESULT nx_m_CreateAdditionalSwapChain(NxProxy* self, uint32_t mid, D3DPRESENT_PARAMETERS* pp, IDirect3DSwapChain9** out) {
    if (!pp || !out) return D3DERR_INVALIDCALL;
    NxW_D3DPRESENT_PARAMETERS w; nx_to_wide_D3DPRESENT_PARAMETERS(&w, pp); w.hDeviceWindow = 0;
    uint64_t o = 0, a[2] = {A(&w), A(&o)};
    HRESULT hr = (HRESULT)nx_call(self->remote, mid, A(a));
    nx_from_wide_D3DPRESENT_PARAMETERS(pp, &w); pp->hDeviceWindow = nullptr;
    *out = SUCCEEDED(hr) ? nx_wrap_IDirect3DSwapChain9(o) : nullptr;
    return hr;
}

// ---- entry point: the engine resolves Direct3DCreate9 through GetProcAddress (S4 maps the name here) ----------
extern "C" IDirect3D9* WINAPI Direct3DCreate9(UINT sdk) {
    // needs host API v3 (ext_call) and a host that registered NINE_EXT_MODULE
    if (!fnvnx_host || fnvnx_host->version < 3 ||
        fnvnx_host->size < offsetof(FnvnxHostApi, ext_call) + sizeof(uint64_t) || !fnvnx_host->ext_call)
        return nullptr;
    uint64_t a[1] = {sdk};
    uint64_t r = nx_call(0, NINE_FN_CREATE9, A(a));
    if (r == FNVNX_EXT_NO_MODULE) return nullptr;
    return nx_wrap_IDirect3D9(r);
}
