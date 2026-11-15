// D7: IDirect3D9 / IDirect3D9Ex ::CreateDevice(Ex) and the implicit objects of a device. Sources (Wine 9.0): d3d9/directx.c d3d9_CreateDevice / d3d9_CreateDeviceEx, d3d9/device.c device_init (+ present_parameters_from_
// wined3d_swapchain_desc: only width / height / format / count are rewritten, Flags are masked 0xfff, formats pass through wined3dformat -> d3dformat), wined3d/device.c wined3d_device_init (the creation parameters are
// echoed, nothing about type or behaviour flags is validated: GetDeviceCaps answers for every device type, README env), swapchain.c (auto depth stencil), d3d9 tests test_swapchain_parameters / test_refcount.
// Object model: README R2 (the device holds IDirect3D9, count 2 after CreateDevice), R3 (implicit chain / back buffers / auto depth stencil: public 0, owned). An IDirect3D9Ex creates the Ex device class for both
// CreateDevice and CreateDeviceEx (Wine: the device is "extended" iff its d3d9 is). DECISIONS (doubt): null pp / null out = INVALIDCALL (Windows: same for pp); the D3DDISPLAYMODEEX of CreateDeviceEx is ignored
// (Wine FIXME "Ignoring display mode"); a full-screen device whose focus window (else hDeviceWindow) is no window fails E_FAIL (wined3d_device_acquire_focus_window); AdapterOrdinal >= count = INVALIDCALL.
#include "res_tex.h"
#include "../gen/d9impl.gen.h"
#include "dev_core.h"
#include "dev_hooks.h"
#include "d3d9_adapter.h"
#include "../gen/env_table.gen.h"
#include <string.h>

HRESULT nx_make_auto_ds(NxDeviceCore* d, const NxPPEff& e, const D3DPRESENT_PARAMETERS& p, NxObj** out) {
    NxTexFmt f; nx_texfmt(p.AutoDepthStencilFormat, &f);
    // Allocation failure: E_OUTOFMEMORY (objects), D3DERR_OUTOFVIDEOMEMORY (NxImg::init / no native format); what was made is destroyed (README "Allocation failure").
    NxImg* img = nx_make_plain<NxImg>(); if (!img) return E_OUTOFMEMORY;
    NxSurface9* s = nx_make<NxSurface9>(); if (!s) { delete img; return E_OUTOFMEMORY; }
    s->img = s->own = img; NxObj* o = s; o->asImplicit(d, d); o->pool = D3DPOOL_DEFAULT;
    HRESULT hr = img->init(s, NXK_SURFACE, e.w, e.h, 1, 1, 1, p.AutoDepthStencilFormat, D3DUSAGE_DEPTHSTENCIL, D3DPOOL_DEFAULT, (f.flags & NXTF_MAPPABLE) ? 12 : 0, false, nullptr, p.MultiSampleType, p.MultiSampleQuality, e.ds_samples, D3DUSAGE_DEPTHSTENCIL);
    if (hr == D3D_OK && !img->gpu && !(img->ni.flags & NXFMT_NULL)) hr = D3DERR_OUTOFVIDEOMEMORY;
    if (hr != D3D_OK) { o->owned = 0; o->ci->destroy(o); return hr; }
    *out = o; return D3D_OK;
}
void nx_dev_bind_defaults(NxDeviceCore* d) {
    NxDevLife* L = nx_life(d); NxChainCore* c = L->chains[0];
    NxCreateParams cp = {L->ds ? 1u : 0u, c->pp.BackBufferWidth, c->pp.BackBufferHeight, L->cp.BehaviorFlags};
    d->init(cp);
    IDirect3DSurface9* rt = static_cast<NxSurface9*>(c->bb[0]); d->put_direct(NX_S_rt, &rt);
    if (L->ds) { IDirect3DSurface9* ds = static_cast<NxSurface9*>(L->ds); d->put_direct(NX_S_ds, &ds); }
}
// the parameters the application sees after CreateDevice / Reset: device_init's present_parameters_from_wined3d_swapchain_desc over the d3d9 copy of the description
void nx_pp_write_back(D3DPRESENT_PARAMETERS* pp, const NxPPEff& e, bool reset) {
    pp->BackBufferWidth = e.w; pp->BackBufferHeight = e.h; pp->BackBufferFormat = nx_pp_fmt_out(e.fmt); pp->BackBufferCount = e.count;
    if (!reset) { pp->Flags &= 0xfffu; pp->AutoDepthStencilFormat = nx_pp_fmt_out(pp->AutoDepthStencilFormat); }
}
template <class C> static HRESULT make_device(NxObj* d3d, bool ex, UINT adapter, D3DDEVTYPE type, HWND focus, DWORD flags, D3DPRESENT_PARAMETERS* pp, void** out) {
    if (!pp || !out) return D3DERR_INVALIDCALL;
    C* dev = nx_make<C>(); if (!dev) return E_OUTOFMEMORY;   // d3d9_CreateDevice: calloc failure -> E_OUTOFMEMORY
    NxObj* o = dev; o->asDevice(d3d); int prof = nx_env_profile_of(o);
    NxDeviceCore* core = dev; HRESULT hr; NxPPEff e;
    if (adapter >= nxenv_adapter_count(prof)) hr = D3DERR_INVALIDCALL;
    else if (!pp->Windowed && !nxd3d_window_client(focus ? (void*)focus : (void*)pp->hDeviceWindow, nullptr)) hr = E_FAIL;       // wined3d_device_acquire_focus_window
    else if (SUCCEEDED(hr = nx_pp_resolve(prof, ex, NX_PP_CREATE, pp, (void*)focus, &e))) {
        NxDevLife* L = nx_life_make(core, ex, prof);
        if (!L) hr = E_OUTOFMEMORY; else {
        L->cp.AdapterOrdinal = adapter; L->cp.DeviceType = type; L->cp.hFocusWindow = focus; L->cp.BehaviorFlags = flags;
        NxChainCore* c = nullptr; NxObj* ds = nullptr;
        if (SUCCEEDED(hr = nx_chain_create(core, *pp, e, true, &c)) && (!pp->EnableAutoDepthStencil || SUCCEEDED(hr = nx_make_auto_ds(core, e, *pp, &ds)))) {
            L->ds = ds; nx_dev_bind_defaults(core); nx_pp_write_back(pp, e, false);
            *out = static_cast<IDirect3DDevice9*>(dev); return D3D_OK;
        }
        }
    }
    o->release();   // public 1 -> 0: destroys the device (and what it owns), releases IDirect3D9
    return hr;
}
#define CREATE_DEV(C, EXD)                                                                                                                                                                         \
    HRESULT STDMETHODCALLTYPE C::CreateDevice(UINT a, D3DDEVTYPE t, HWND f, DWORD b, D3DPRESENT_PARAMETERS* pp, IDirect3DDevice9** out) {                                                      \
        if (out) *out = nullptr; return EXD ? make_device<NxDevice9Ex>(this, true, a, t, f, b, pp, (void**)out) : make_device<NxDevice9>(this, false, a, t, f, b, pp, (void**)out); }
CREATE_DEV(NxDirect3D9, false) CREATE_DEV(NxDirect3D9Ex, true)
HRESULT STDMETHODCALLTYPE NxDirect3D9Ex::CreateDeviceEx(UINT a, D3DDEVTYPE t, HWND f, DWORD b, D3DPRESENT_PARAMETERS* pp, D3DDISPLAYMODEEX*, IDirect3DDevice9Ex** out) {
    if (out) *out = nullptr; return make_device<NxDevice9Ex>(this, true, a, t, f, b, pp, (void**)out);
}
