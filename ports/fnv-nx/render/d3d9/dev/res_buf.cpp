// NxVertexBuffer9 / NxIndexBuffer9 (CPU memory part). Sources: Wine 9.0 d3d9 buffer.c (vertexbuffer_init, indexbuffer_init, Lock/Unlock/GetDesc), d3d9 device.c
// (d3d9_device_CreateVertexBuffer / CreateIndexBuffer, wined3dmapflags_from_d3dmapflags), wined3d device.c (sanitise_map_flags) and buffer.c (buffer_resource_sub_resource_map);
// MS IDirect3DVertexBuffer9::Lock / D3DLOCK / D3DUSAGE pages; Wine tests device.c test_vb_lock_flags (Windows XP: Lock never fails on odd flag combinations).
// Creation (HRESULTs are the documented ones; Wine's E_INVALIDARG for Length 0 is mapped to D3DERR_INVALIDCALL): Length 0, pool SCRATCH or > SYSTEMMEM,
//  D3DUSAGE_RENDERTARGET / DEPTHSTENCIL, MANAGED on an Ex device, an index format other than INDEX16 / INDEX32, an FVF outside the grammar (nx_fvf_to_decl) = D3DERR_INVALIDCALL;
//  Length > NX_MAX_BUFFER_BYTES = D3DERR_OUTOFVIDEOMEMORY; a shared handle: non-Ex = E_NOTIMPL, pool != DEFAULT = D3DERR_NOTAVAILABLE, else ignored (Wine shares nothing).
//  Allocation failure: the object = D3DERR_OUTOFVIDEOMEMORY (d3d9_device_CreateVertexBuffer / CreateIndexBuffer), its memory = E_OUTOFMEMORY (wined3d_buffer_init); nothing is left behind (init runs before asChild).
//  NOT validated (no source): FVF vs Length (vertex size), INDEX32 vs caps MaxVertexIndex.
// Lock: (0,0) = whole buffer; size 0 = to the end (spec lock.bytes rule); offset + size > Length, offset >= Length, null ppbData = D3DERR_INVALIDCALL (Wine continues for an out-of-range box: it
//  would hand out memory outside the buffer). Flags: never an error (XP behaviour). Wine mapping: READ unless WRITEONLY usage or DISCARD/NOOVERWRITE; WRITE unless READONLY, or when neither would be set.
//  DISCARD / NOOVERWRITE only act on DYNAMIC usage in a GPU-accessible pool (pool != SYSTEMMEM); both given = NOOVERWRITE only. Nested locks share the one memory (count).
//  RD7 (OUR deterministic choice where D3D9 says "undefined contents"): an effective DISCARD zero-fills the whole buffer; the recorder adaptor must treat the buffer content after
//  such a Lock as all zero (it must not read back the previous content). Dirty range: a write lock marks [0, Length) (Wine: native uploads the whole buffer for write maps and
//  applications depend on it), MANAGED pool only the locked range, D3DLOCK_NO_DIRTY_UPDATE (MS: no dirty change) suppresses it for MANAGED only. rev counts write locks.
//  Unlock without Lock = D3D_OK (Wine).
#include "res_core.h"
#include "../gen/d9impl.gen.h"
#include <string.h>

HRESULT NxBufCore::init(uint32_t len, uint32_t use, uint32_t fmt_, uint32_t) {   // E_OUTOFMEMORY: raw stays null
    raw = (uint8_t*)nxd3d_alloc((size_t)len + 15); if (!raw) return E_OUTOFMEMORY;   // wined3d_buffer_init: wined3d_resource_prepare_sysmem failure -> E_OUTOFMEMORY
    memset(raw, 0, (size_t)len + 15);
    mem = (uint8_t*)(((uintptr_t)raw + 15) & ~(uintptr_t)15); length = len; usage = use; fmt = fmt_; return D3D_OK;
}
HRESULT NxBufCore::lock(uint32_t off, uint32_t size, void** pp, DWORD flags) {
    if (!pp) return D3DERR_INVALIDCALL;
    *pp = nullptr;
    uint64_t end = size ? (uint64_t)off + size : length;
    if (off >= length || end > length) return D3DERR_INVALIDCALL;
    bool read = !(usage & D3DUSAGE_WRITEONLY) && !(flags & (D3DLOCK_NOOVERWRITE | D3DLOCK_DISCARD));
    bool write = !(flags & D3DLOCK_READONLY); if (!read && !write) write = true;
    bool gpu = pool != (uint8_t)D3DPOOL_SYSTEMMEM, dyn = (usage & D3DUSAGE_DYNAMIC) != 0;
    bool discard = !read && (flags & D3DLOCK_DISCARD) && gpu && dyn && !(flags & D3DLOCK_NOOVERWRITE);
    if (discard) memset(mem, 0, length);
    if (write) {
        uint32_t lo = 0, hi = length; bool dirty = true;
        if (managed()) { lo = off; hi = (uint32_t)end; dirty = !(flags & D3DLOCK_NO_DIRTY_UPDATE); }
        if (dirty) { if (dirty_lo >= dirty_hi) { dirty_lo = lo; dirty_hi = hi; } else { if (lo < dirty_lo) dirty_lo = lo; if (hi > dirty_hi) dirty_hi = hi; } rev++; }
    }
    locks++; *pp = mem + off; return D3D_OK;
}
HRESULT NxBufCore::unlock() { if (locks) locks--; return D3D_OK; }
bool NxBufCore::take_dirty(uint32_t* lo, uint32_t* hi) {
    if (dirty_lo >= dirty_hi) return false;
    *lo = dirty_lo; *hi = dirty_hi; dirty_lo = ~0u; dirty_hi = 0; return true;
}
HRESULT STDMETHODCALLTYPE NxVertexBuffer9::Lock(UINT o, UINT s, void** pp, DWORD f) { return NxBufCore::lock(o, s, pp, f); }
HRESULT STDMETHODCALLTYPE NxVertexBuffer9::Unlock() { return NxBufCore::unlock(); }
HRESULT STDMETHODCALLTYPE NxVertexBuffer9::GetDesc(D3DVERTEXBUFFER_DESC* d) {
    if (!d) return D3DERR_INVALIDCALL;
    d->Format = D3DFMT_VERTEXDATA; d->Type = D3DRTYPE_VERTEXBUFFER; d->Usage = usage; d->Pool = (D3DPOOL)pool; d->Size = length; d->FVF = fmt; return D3D_OK;
}
HRESULT STDMETHODCALLTYPE NxIndexBuffer9::Lock(UINT o, UINT s, void** pp, DWORD f) { return NxBufCore::lock(o, s, pp, f); }
HRESULT STDMETHODCALLTYPE NxIndexBuffer9::Unlock() { return NxBufCore::unlock(); }
HRESULT STDMETHODCALLTYPE NxIndexBuffer9::GetDesc(D3DINDEXBUFFER_DESC* d) {
    if (!d) return D3DERR_INVALIDCALL;
    d->Format = (D3DFORMAT)fmt; d->Type = D3DRTYPE_INDEXBUFFER; d->Usage = usage; d->Pool = (D3DPOOL)pool; d->Size = length; return D3D_OK;
}

static bool is_ex(NxObj* dev) { return strcmp(dev->ci->name, "IDirect3DDevice9Ex") == 0; }
static HRESULT common(NxObj* dev, UINT len, DWORD usage, D3DPOOL pool, HANDLE* shared) {
    if (shared) {
        if (!is_ex(dev)) return E_NOTIMPL;
        if (pool != D3DPOOL_DEFAULT) return D3DERR_NOTAVAILABLE;
    }
    if (!len || (uint32_t)pool >= (uint32_t)D3DPOOL_SCRATCH ||   // SCRATCH and every value that is no D3DPOOL
         (usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL))) return D3DERR_INVALIDCALL;
    if (pool == D3DPOOL_MANAGED && is_ex(dev)) return D3DERR_INVALIDCALL;
    return len > NX_MAX_BUFFER_BYTES ? D3DERR_OUTOFVIDEOMEMORY : D3D_OK;
}
static HRESULT make_vb(NxObj* dev, UINT len, DWORD usage, DWORD fvf, D3DPOOL pool, IDirect3DVertexBuffer9** pp, HANDLE* sh) {
    if (!pp) return D3DERR_INVALIDCALL;
    *pp = nullptr; NxFvfLayout lay;
    HRESULT hr = common(dev, len, usage, pool, sh); if (hr != D3D_OK) return hr;
    if (fvf && !nx_fvf_to_decl(fvf, &lay)) return D3DERR_INVALIDCALL;
    NxVertexBuffer9* b = nx_make<NxVertexBuffer9>(); if (!b) return D3DERR_OUTOFVIDEOMEMORY;   // d3d9_device_CreateVertexBuffer: calloc failure -> D3DERR_OUTOFVIDEOMEMORY
    hr = b->init(len, usage, fvf, pool); if (FAILED(hr)) { nx_destroy(b); return hr; }
    b->asChild(dev, pool); *pp = b; return D3D_OK;
}
static HRESULT make_ib(NxObj* dev, UINT len, DWORD usage, D3DFORMAT fmt, D3DPOOL pool, IDirect3DIndexBuffer9** pp, HANDLE* sh) {
    if (!pp) return D3DERR_INVALIDCALL;
    *pp = nullptr;
    HRESULT hr = common(dev, len, usage, pool, sh); if (hr != D3D_OK) return hr;
    if (fmt != D3DFMT_INDEX16 && fmt != D3DFMT_INDEX32) return D3DERR_INVALIDCALL;
    NxIndexBuffer9* b = nx_make<NxIndexBuffer9>(); if (!b) return D3DERR_OUTOFVIDEOMEMORY;     // d3d9_device_CreateIndexBuffer: calloc failure -> D3DERR_OUTOFVIDEOMEMORY
    hr = b->init(len, usage, fmt, pool); if (FAILED(hr)) { nx_destroy(b); return hr; }
    b->asChild(dev, pool); *pp = b; return D3D_OK;
}
HRESULT STDMETHODCALLTYPE NxDevice9::CreateVertexBuffer(UINT l, DWORD u, DWORD f, D3DPOOL p, IDirect3DVertexBuffer9** pp, HANDLE* sh) { return make_vb(this, l, u, f, p, pp, sh); }
HRESULT STDMETHODCALLTYPE NxDevice9Ex::CreateVertexBuffer(UINT l, DWORD u, DWORD f, D3DPOOL p, IDirect3DVertexBuffer9** pp, HANDLE* sh) { return make_vb(this, l, u, f, p, pp, sh); }
HRESULT STDMETHODCALLTYPE NxDevice9::CreateIndexBuffer(UINT l, DWORD u, D3DFORMAT f, D3DPOOL p, IDirect3DIndexBuffer9** pp, HANDLE* sh) { return make_ib(this, l, u, f, p, pp, sh); }
HRESULT STDMETHODCALLTYPE NxDevice9Ex::CreateIndexBuffer(UINT l, DWORD u, D3DFORMAT f, D3DPOOL p, IDirect3DIndexBuffer9** pp, HANDLE* sh) { return make_ib(this, l, u, f, p, pp, sh); }
