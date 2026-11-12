#include "state_rt.h"
#include "../gen/d9impl.gen.h"

bool nx_chk_obj_iid(IUnknown* p, const GUID& iid) { if (!p) return true; NxObj* o = nx_obj_of(p); return o && o->is(iid); }
bool nx_chk_tex(IDirect3DBaseTexture9* t) {
    if (!nx_chk_obj(t)) return false;
    if (!t) return true;
    uint8_t pool = nx_obj_of(t)->pool;
    return pool != (uint8_t)D3DPOOL_SCRATCH && pool != (uint8_t)D3DPOOL_SYSTEMMEM;
}

// A slot's bytes. Object families hold the pointer first and one internal bind reference on it (NxObj::bindRef): bind the new one, store, then unbind the old
// one (which may destroy it); this order makes a self-store and a store of the last reference safe.
static char* slot_ptr(NxDeviceState& s, uint32_t slot) { const NxFam& f = nx_fams[nx_tab.slot_fam[slot]]; return (char*)&s + f.off + (slot - f.first) * f.esz; }
void nx_slot_write(NxDeviceState& s, uint32_t slot, const void* e) {
    const NxFam& f = nx_fams[nx_tab.slot_fam[slot]]; char* d = slot_ptr(s, slot);
    if (!f.obj) { memmove(d, e, f.esz); return; }
    IUnknown* nw = *(IUnknown* const*)e; IUnknown* old = *(IUnknown**)d;
    if (nw) nx_obj_of(nw)->bindRef();
    memmove(d, e, f.esz);
    if (old) nx_obj_of(old)->unbindRef();
}
void nx_state_drop(NxDeviceState* s) {
    for (uint32_t i = 0; i < NX_FAMS; i++) {
        const NxFam& f = nx_fams[i]; if (!f.obj) continue;
        for (uint32_t k = 0; k < f.n; k++) { char* d = (char*)s + f.off + k * f.esz; IUnknown* o = *(IUnknown**)d; *(IUnknown**)d = nullptr; if (o) nx_obj_of(o)->unbindRef(); }
    }
}
const void* NxDeviceCore::cur_at(uint32_t slot) const { return slot_ptr(const_cast<NxDeviceState&>(cur), slot); }
static inline bool bit(const uint32_t* m, uint32_t s) { return (m[s / 32] >> (s % 32)) & 1; }
void NxDeviceCore::put_direct(uint32_t slot, const void* e) {
    nx_slot_write(cur, slot, e); uint8_t g = nx_tab.slot_group[slot]; if (g != 255) dirty |= 1ull << g;
}
void NxDeviceCore::put(uint32_t slot, const void* e) {
    if (rec && bit(nx_tab.mask[0], slot)) { rec->st.mask[slot / 32] |= 1u << (slot % 32); nx_slot_write(rec->st.v, slot, e); }  // recorded: the device state does not change
    else put_direct(slot, e);
}
void NxDeviceCore::init(const NxCreateParams& p) {
    cp = p; nx_state_defaults(&cur, &cp); lights.clear(); pals.clear(); swvp = 0; dirty = ~0ull;
}
NxDeviceCore::~NxDeviceCore() { nx_state_drop(&cur); if (rec) rec->unbindRef(); delete life; delete draw; }

// Capture: re-read every member from the device; Apply: write every member to the device. Block members are the mask bits (a type's mask or the recorded set).
HRESULT NxStateBlockCore::Capture() {
    NxDeviceCore* d = devc;
    for (uint32_t w = 0; w < NX_MASK_WORDS; w++)
        for (uint32_t m = st.mask[w]; m; m &= m - 1) { uint32_t slot = w * 32 + __builtin_ctz(m); nx_slot_write(st.v, slot, d->cur_at(slot)); }
    for (uint32_t i = 0; i < st.lights.n; i++) {
        NxLightEnt& e = st.lights.a[i]; NxLightEnt* c = d->lights.find(e.idx);
        if (!c) continue;  // the device has no such light now: the recorded value stays
        if (e.has_light) e.light = c->light;
        if (e.has_enable) e.enabled = c->enabled;
    }
    return D3D_OK;
}
HRESULT NxStateBlockCore::Apply() {
    NxDeviceCore* d = devc;
    // All or nothing (README "Allocation failure"): the lights this block writes may need new entries in the set they land in (the recording block's while the device records, else the device's). The capacity is
    // reserved BEFORE the first write, so the writes below cannot run out of memory and a failed Apply leaves the device as it was (wined3d_stateblock_apply has no failure; E_OUTOFMEMORY is ours).
    NxSparse<NxLightEnt>& dst = d->rec ? d->rec->st.lights : d->lights;
    if (!dst.reserve(st.lights.n)) return E_OUTOFMEMORY;
    for (uint32_t w = 0; w < NX_MASK_WORDS; w++)
        for (uint32_t m = st.mask[w]; m; m &= m - 1) { uint32_t slot = w * 32 + __builtin_ctz(m); d->put_direct(slot, slot_ptr(st.v, slot)); }
    for (uint32_t i = 0; i < st.lights.n; i++) {
        const NxLightEnt& e = st.lights.a[i];
        if (e.has_light) { D3DLIGHT9 l = e.light; d->SetLight(e.idx, &l); }
        if (e.has_enable) d->LightEnable(e.idx, e.enabled);
    }
    return D3D_OK;
}
