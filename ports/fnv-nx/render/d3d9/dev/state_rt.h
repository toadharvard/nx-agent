// Runtime of the generated device state (state.gen.h): the device core, state blocks, sparse sets. Rules: README.md.
#pragma once
#include "../gen/state.gen.h"
#include <string.h>
inline uint32_t nx_f(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
inline D3DVIEWPORT9 nx_vp(const NxCreateParams* c) { D3DVIEWPORT9 v = {0, 0, c->width, c->height, 0.0f, 1.0f}; return v; }
inline RECT nx_sc(const NxCreateParams* c) { RECT r = {0, 0, (LONG)c->width, (LONG)c->height}; return r; }
inline D3DMATRIX nx_identity() { D3DMATRIX m = D3DMATRIX(); m._11 = m._22 = m._33 = m._44 = 1.0f; return m; }
inline D3DMATRIX nx_mul(const D3DMATRIX& a, const D3DMATRIX& b) {  // a x b, row vectors (D3D convention); MS MultiplyTransform: "pMatrix times State"
    D3DMATRIX r = D3DMATRIX();
    for (int i = 0; i < 4; i++) for (int j = 0; j < 4; j++) { float s = 0; for (int k = 0; k < 4; k++) s += a.m[i][k] * b.m[k][j]; r.m[i][j] = s; }
    return r;
}
// null, or one of OUR objects whose class implements `iid` (foreign pointers are rejected: D3D9 rejects objects of another device/runtime)
bool nx_chk_obj_iid(IUnknown* p, const GUID& iid);
#define nx_chk_obj(p) nx_chk_obj_iid((IUnknown*)(p), NX_IID_OF(p))
#define NX_IID_OF(p) nx_iid_of(p)
const GUID& nx_iid_of(IDirect3DBaseTexture9*); const GUID& nx_iid_of(IDirect3DIndexBuffer9*); const GUID& nx_iid_of(IDirect3DVertexBuffer9*);
const GUID& nx_iid_of(IDirect3DVertexDeclaration9*); const GUID& nx_iid_of(IDirect3DVertexShader9*); const GUID& nx_iid_of(IDirect3DPixelShader9*);
const GUID& nx_iid_of(IDirect3DSurface9*);
bool nx_chk_tex(IDirect3DBaseTexture9* t);  // + MS SetTexture remarks: no D3DPOOL_SCRATCH, no D3DPOOL_SYSTEMMEM (device does not report D3DDEVCAPS_TEXTURESYSTEMMEMORY)
void nx_state_drop(NxDeviceState* s);       // releases every bind reference, nulls the object slots

template <class E> struct NxSparse {  // sorted by idx, O(log n) find; unbounded key domain
    E* a = nullptr; uint32_t n = 0, cap = 0;
    NxSparse() = default; NxSparse(const NxSparse&) = delete; NxSparse& operator=(const NxSparse&) = delete;
    ~NxSparse() { clear(); }
    void clear() { nxd3d_free(a); a = nullptr; n = cap = 0; }
    uint32_t lower(DWORD idx) const { uint32_t lo = 0, hi = n; while (lo < hi) { uint32_t m = (lo + hi) / 2; if (a[m].idx < idx) lo = m + 1; else hi = m; } return lo; }
    E* find(DWORD idx) const { uint32_t i = lower(idx); return i < n && a[i].idx == idx ? &a[i] : nullptr; }
    // capacity for `extra` more entries: false = out of memory, nothing changed. After a successful reserve(k) the next k add() calls (new keys) cannot fail: transactional writers reserve first, then commit.
    NX_MUST_USE bool reserve(uint32_t extra) {
        uint64_t want = (uint64_t)n + extra; if (want <= cap) return true;
        if (want > 0x7fffffffu / sizeof(E)) return false;
        uint32_t c = cap ? cap : 8; while (c < want) c *= 2;
        E* b = (E*)nxd3d_alloc((size_t)c * sizeof(E)); if (!b) return false;
        if (n) memcpy((void*)b, (void*)a, n * sizeof(E));
        nxd3d_free(a); a = b; cap = c; return true;
    }
    // existing entry, or a zeroed new one; nullptr = out of memory (the set is unchanged)
    NX_MUST_USE E* add(DWORD idx) {
        uint32_t i = lower(idx); if (i < n && a[i].idx == idx) return &a[i];
        if (!reserve(1)) return nullptr;
        memmove((void*)(a + i + 1), (void*)(a + i), (n - i) * sizeof(E)); memset((void*)&a[i], 0, sizeof(E)); a[i].idx = idx; n++; return &a[i];
    }
    // replace the contents by a copy of `o`; false = out of memory, this set is unchanged
    NX_MUST_USE bool copy_from(const NxSparse& o) {
        E* b = nullptr; if (o.n) { b = (E*)nxd3d_alloc((size_t)o.n * sizeof(E)); if (!b) return false; memcpy((void*)b, (void*)o.a, o.n * sizeof(E)); }
        clear(); a = b; n = cap = o.n; return true;
    }
};
struct NxLightEnt { DWORD idx; D3DLIGHT9 light; BOOL enabled; uint8_t has_light, has_enable; };  // blocks: which halves the entry records
struct NxPalEnt { DWORD idx; uint8_t e[256 * 4]; };                                                // PALETTEENTRY[256], 4 bytes each (wingdi.h)
enum { NX_D3D_TEXTURECAPS_ALPHAPALETTE = 0 };                                                      // caps we report; MS SetPaletteEntries: alpha must be 255 when not set

struct NxStateSet {
    NxDeviceState v; uint32_t mask[NX_MASK_WORDS]; NxSparse<NxLightEnt> lights;
    NxStateSet() { memset((void*)&v, 0, sizeof v); memset(mask, 0, sizeof mask); }
    ~NxStateSet() { nx_state_drop(&v); }
};
struct NxDeviceCore;
struct NxStateBlockCore : NxObj {  // IDirect3DStateBlock9 data; Capture/Apply: state_rt.cpp
    NxStateSet st; NxDeviceCore* devc = nullptr;
    NX_STATEBLOCK_METHODS
};
struct NxDeclCore : NxObj { DWORD fvf = 0; };  // base of NxVertexDeclaration9: the FVF it was made from (0 = made from elements)
// the implicit declaration of an FVF (device-cached, one per FVF). Defined by the declaration module; undefined = link error.
IDirect3DVertexDeclaration9* nx_device_fvf_decl(NxDeviceCore* dev, DWORD fvf);

struct NxDevLifeBase {  // owner of the lifecycle state of a device (dev_core.h NxDevLife): implicit swap chain, back buffers, auto depth stencil, cursor; destroyed with the device after its bindings are dropped
    virtual ~NxDevLifeBase() {}
    void* operator new(size_t n) noexcept NX_MUST_USE NX_USE_MAKE { return nxd3d_alloc(n); } void operator delete(void* p) { nxd3d_free(p); }   // made by nx_make_plain<T>(); nullptr on exhaustion
};
struct NxDeviceCore : NxObj {
    NxDevLifeBase* life = nullptr;
    NxDevLifeBase* draw = nullptr;   // draw_state.cpp: the per-device draw state (latch of the render state translation, bound-state tracking); destroyed with the device
    NxDeviceState cur; NxStateBlockCore* rec = nullptr; uint64_t dirty = 0; NxCreateParams cp = {};
    NxSparse<NxLightEnt> lights; NxSparse<NxPalEnt> pals; uint32_t swvp = 0;
    NxDeviceCore() { memset((void*)&cur, 0, sizeof cur); }
    ~NxDeviceCore();
    void init(const NxCreateParams& p);                // CreateDevice / Reset: every state to its default; dirty = all
    void put(uint32_t slot, const void* elem);          // THE write: a block member while recording, else the device state + dirty group
    void put_direct(uint32_t slot, const void* elem);   // device state, never recorded
    const void* cur_at(uint32_t slot) const;
    NX_DEVICE_STATE_METHODS
    NX_DEVICE_HAND_METHODS
};
void nx_slot_write(NxDeviceState& s, uint32_t slot, const void* elem);  // bind-aware store of one slot
