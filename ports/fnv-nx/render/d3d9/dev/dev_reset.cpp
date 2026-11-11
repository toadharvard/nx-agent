// D7: IDirect3DDevice9::Reset / IDirect3DDevice9Ex::ResetEx. Sources (Wine 9.0): d3d9/device.c d3d9_device_reset (+ d3d9_device_ResetEx: "mode if and only if not windowed", mode size = back buffer size), wined3d/device.c
// wined3d_device_reset (state_unbind_resources, the resource callback loop = reset_enum_callback, wined3d_swapchain_resize_buffers, auto depth stencil recreated, cursor image dropped), d3d9/tests/device.c test_reset
// (Windows-validated: a DEFAULT pool resource makes Reset fail with D3DERR_INVALIDCALL and the device then reports D3DERR_DEVICENOTRESET until a Reset succeeds), test_swapchain_parameters (invalid parameters: INVALIDCALL,
// no state change), test_reset_resources. Non-Ex Reset: recording state block dropped, ALL state back to its defaults (NxDeviceCore::init), RT 0 / depth stencil = the new implicit objects; Ex Reset keeps the state, RT 0 =
// the new back buffer, RT 1-3 empty, depth stencil = the new auto depth stencil, viewport / scissor = the new size. Implicit objects are RECREATED (new identities; an Ex back buffer the application still holds survives
// as an ordinary object). M1 recheck: the back buffer count changes as requested (MS D3DPRESENT_PARAMETERS BackBufferCount is a parameter of the new swap chain; Windows rows of test_swapchain_parameters: Reset with 0 / 1 / 2 / 3 = D3D_OK, 4 and COPY with 2 = INVALIDCALL; Wine keeps the old count, a Wine limitation, no Windows row shows that); DECISIONS (doubt): state blocks and additional swap chains do not block Reset
// (Wine: only DEFAULT pool resources do; MS lists state blocks / additional chains: not checked by any source we have); the gamma ramp and present count are kept; the gamma ramp is the chain's (task rule).
#include "res_tex.h"
#include "../gen/d9impl.gen.h"
#include "dev_core.h"
#include "dev_hooks.h"
#include "d3d9_adapter.h"

static HRESULT reset_core(NxDeviceCore* d, D3DPRESENT_PARAMETERS* pp) {
    NxDevLife* L = nx_life(d); NxObj* dev = d; int prof = (int)L->prof;
    if (!pp) return D3DERR_INVALIDCALL;
    if (!L->ex && L->state == NX_DEV_LOST) return D3DERR_DEVICELOST;
    HRESULT hr = nx_pp_check(L->ex, pp); if (FAILED(hr)) return hr;               // no state change
    NxChainCore* c = L->chains[0]; NxPPEff e;
    if (!L->ex) {
        if (d->rec) { d->rec->unbindRef(); d->rec = nullptr; }
        d->init(d->cp);                                                          // state_unbind_resources + defaults (bound resources die with their last bind reference)
        L->cur.w = L->cur.h = 0; nxd3d_free(L->cur.bgra); L->cur.bgra = nullptr;  // the cursor image is dropped (reset_state)
        if (nx_dev_default_pool_blocks(dev)) { L->state = NX_DEV_NOTRESET; return D3DERR_INVALIDCALL; }
    }
    void* focus = L->cp.hFocusWindow ? (void*)L->cp.hFocusWindow : (void*)c->pp.hDeviceWindow;
    if (FAILED(hr = nx_pp_resolve(prof, L->ex, NX_PP_RESET, pp, focus, &e))) { if (!L->ex) L->state = NX_DEV_NOTRESET; return hr; }
    nxd3d_wait_idle(dev);
    c->destroy_bbs(); if (L->ds) { NxObj* o = L->ds; L->ds = nullptr; o->owned = 0; if (o->pub || o->binds) o->owner = dev, o->container = dev; else o->ci->destroy(o); }
    c->pp = *pp; c->pp.BackBufferWidth = e.w; c->pp.BackBufferHeight = e.h; c->pp.BackBufferFormat = (D3DFORMAT)e.fmt; c->pp.BackBufferCount = e.count; c->pp.hDeviceWindow = (HWND)e.win; c->pp.Flags &= 0xfffu;
    NxObj* ds = nullptr;
    if (FAILED(hr = c->make_buffers(e, c->pp)) || (pp->EnableAutoDepthStencil && FAILED(hr = nx_make_auto_ds(d, e, *pp, &ds)))) { L->state = NX_DEV_NOTRESET; return hr; }
    L->ds = ds;
    if (!L->ex) { d->cp.auto_depth = ds != nullptr; nx_dev_bind_defaults(d); }
    else {
        IDirect3DSurface9* none = nullptr; IDirect3DSurface9* rt = static_cast<NxSurface9*>(c->bb[0]); IDirect3DSurface9* dsf = ds ? static_cast<IDirect3DSurface9*>(static_cast<NxSurface9*>(ds)) : nullptr;
        for (uint32_t i = 1; i < NX_D3D_MAX_RTS; i++) d->put_direct(NX_S_rt + i, &none);
        d->put_direct(NX_S_rt, &rt); d->put_direct(NX_S_ds, &dsf);
        D3DVIEWPORT9 v = d->cur.viewport[0]; v.X = v.Y = 0; v.Width = e.w; v.Height = e.h; RECT sc = {0, 0, (LONG)e.w, (LONG)e.h}; d->put_direct(NX_S_viewport, &v); d->put_direct(NX_S_scissor, &sc);
    }
    L->state = NX_DEV_OK; nx_pp_write_back(pp, e, true); return D3D_OK;
}
HRESULT STDMETHODCALLTYPE NxDevice9::Reset(D3DPRESENT_PARAMETERS* pp) { return reset_core(this, pp); }
HRESULT STDMETHODCALLTYPE NxDevice9Ex::Reset(D3DPRESENT_PARAMETERS* pp) { return reset_core(this, pp); }
HRESULT STDMETHODCALLTYPE NxDevice9Ex::ResetEx(D3DPRESENT_PARAMETERS* pp, D3DDISPLAYMODEEX* mode) {
    if (!pp) return D3DERR_INVALIDCALL;
    if (!pp->Windowed == !mode) return D3DERR_INVALIDCALL;                                              // "Mode can be passed if and only if Windowed is FALSE."
    if (mode && (mode->Width != pp->BackBufferWidth || mode->Height != pp->BackBufferHeight)) return D3DERR_INVALIDCALL;
    return reset_core(this, pp);
}
