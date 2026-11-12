// D7: the non-drawing device methods of IDirect3DDevice9 (both device classes). Sources (Wine 9.0): d3d9/device.c d3d9_device_{TestCooperativeLevel, GetAvailableTextureMem, EvictManagedResources, GetDeviceCaps,
// GetDisplayMode, GetCreationParameters, SetCursorProperties, SetCursorPosition, ShowCursor, CreateAdditionalSwapChain, GetSwapChain, GetNumberOfSwapChains, Present, GetBackBuffer, GetRasterStatus,
// SetDialogBoxMode, SetGammaRamp, GetGammaRamp, GetFrontBufferData, BeginScene, EndScene}; wined3d/device.c wined3d_device_{begin_scene, end_scene, set_cursor_properties, set_cursor_position, show_cursor,
// set_gamma_ramp, get_gamma_ramp, get_swapchain, get_creation_parameters, set_dialog_box_mode}; d3d9/tests/device.c test_cursor, test_scene, test_swapchain, test_refcount (Windows-validated).
// Addressing: the device methods with a swap chain index address the IMPLICIT chains only (Wine: implicit_swapchains = 1 here); GetNumberOfSwapChains = the implicit chain count (wined3d_device_get_swapchain_count: device->swapchain_count, the field set where the device creates its implicit chain); additional chains are reachable through their own interface.
// Gamma: ramp per chain (task rule), the identity ramp before any Set, D3DSGR flags accepted and ignored (wined3d_swapchain_set_gamma_ramp: "Ignoring flags"); an index without a chain changes nothing.
// Cursor (Wine: image copy at Set time, no AddRef of the surface; visible only with an image): A8R8G8B8, power-of-two size, not larger than the display mode, readable; composited by Present (dev_chain.cpp).
// DECISIONS (doubt): SetCursorProperties reading a non-lockable surface = INVALIDCALL; SetDialogBoxMode: Wine returns WINED3D_OK always; one fixme per call (Wine logs only when disabling; every path passes exactly one id, L4 rule), id 46.
#include "res_tex.h"
#include "../gen/d9impl.gen.h"
#include "dev_core.h"
#include "dev_hooks.h"
#include "const_fixme.h"
#include "../gen/env_table.gen.h"
#include <string.h>
#define INV D3DERR_INVALIDCALL
static NxChainCore* implicit_chain(NxObj* d, UINT i) { NxDevLife* L = nx_life(d); return i < L->implicit_chains ? L->chains[i] : nullptr; }   // wined3d_device_get_swapchain over the implicit chains

static HRESULT d_coop(NxObj* d) {
    NxDevLife* L = nx_life(d); if (L->ex) return D3D_OK;
    return L->state == NX_DEV_LOST ? D3DERR_DEVICELOST : L->state == NX_DEV_NOTRESET ? D3DERR_DEVICENOTRESET : D3D_OK;
}
static HRESULT d_caps(NxObj* d, D3DCAPS9* c) {
    if (!c) return INV; NxDevLife* L = nx_life(d); memset(c, 0, sizeof *c);
    HRESULT hr = (HRESULT)nxenv_caps((int)L->prof, L->cp.AdapterOrdinal, (uint32_t)L->cp.DeviceType, c); c->AdapterOrdinal = L->cp.AdapterOrdinal; return hr;
}
static HRESULT d_creation(NxObj* d, D3DDEVICE_CREATION_PARAMETERS* p) { if (!p) return INV; *p = nx_life(d)->cp; return D3D_OK; }
static HRESULT d_cursor_props(NxObj* d, UINT hx, UINT hy, IDirect3DSurface9* bmp) {
    NxDevLife* L = nx_life(d); NxObj* o = nx_obj_of(bmp); if (!bmp || !o || o->ci->rtype != D3DRTYPE_SURFACE) return INV;
    NxImg* im = static_cast<NxSurfCore*>(o)->img; NxEnvMode m; nxenv_display_mode((int)L->prof, L->cp.AdapterOrdinal, &m);
    if (im->w > m.Width || im->h > m.Height) return INV;
    // wined3d_device_set_cursor_properties drops the old image first: for every refusal below. A failure for lack of memory changes nothing (the old image stays: README "Allocation failure"): E_OUTOFMEMORY.
    if (im->fmt != D3DFMT_A8R8G8B8 || (im->w & (im->w - 1)) || (im->h & (im->h - 1))) { nxd3d_free(L->cur.bgra); L->cur.bgra = nullptr; L->cur.w = L->cur.h = 0; return INV; }
    void* bits; uint32_t row, slice; if (FAILED(nx_img_lock(im, 0, 0, nullptr, D3DLOCK_READONLY, &bits, &row, &slice))) { nxd3d_free(L->cur.bgra); L->cur.bgra = nullptr; L->cur.w = L->cur.h = 0; return INV; }
    uint8_t* px = (uint8_t*)nxd3d_alloc((size_t)im->w * im->h * 4);
    if (!px) { bool w0; nx_img_unlock(im, 0, 0, &w0); return E_OUTOFMEMORY; }
    for (uint32_t y = 0; y < im->h; y++) memcpy(px + (size_t)y * im->w * 4, (uint8_t*)bits + (size_t)y * row, (size_t)im->w * 4);
    bool was; nx_img_unlock(im, 0, 0, &was); nxd3d_free(L->cur.bgra); L->cur.bgra = px; L->cur.w = im->w; L->cur.h = im->h; L->cur.hx = hx; L->cur.hy = hy; return D3D_OK;
}
static BOOL d_show_cursor(NxObj* d, BOOL show) {
    NxCursorState& c = nx_life(d)->cur; BOOL old = c.visible;
    if (show && !old) nxd3d_os_cursor_pos(&c.x, &c.y);                                       // "the OS's last known cursor position"
    if (c.bgra) c.visible = show != 0;
    return old;
}
static HRESULT d_additional(NxDeviceCore* d, D3DPRESENT_PARAMETERS* pp, IDirect3DSwapChain9** out) {
    NxDevLife* L = nx_life(d); if (!pp || !out) return INV;
    if (!pp->Windowed) return INV;
    for (uint32_t i = 0; i < L->nchains; i++) if (!L->chains[i]->pp.Windowed) return INV;     // no additional chain next to a full-screen one
    NxPPEff e; HRESULT hr = nx_pp_resolve((int)L->prof, L->ex, NX_PP_CREATE, pp, (void*)L->cp.hFocusWindow, &e); if (FAILED(hr)) return hr;
    NxChainCore* c; if (FAILED(hr = nx_chain_create(d, *pp, e, false, &c))) return hr;
    if (pp->EnableAutoDepthStencil && !L->ds) { NxObj* ds; if (SUCCEEDED(nx_make_auto_ds(d, e, *pp, &ds))) L->ds = ds; else { static_cast<NxObj*>(c)->release(); return D3DERR_OUTOFVIDEOMEMORY; } }   // swapchain init: the device's auto depth stencil, once
    nx_pp_write_back(pp, e, false); *out = L->ex ? static_cast<IDirect3DSwapChain9*>(static_cast<NxSwapChain9Ex*>(c)) : static_cast<IDirect3DSwapChain9*>(static_cast<NxSwapChain9*>(c)); return D3D_OK;
}
static HRESULT d_swapchain(NxObj* d, UINT i, IDirect3DSwapChain9** out) {
    if (!out) return INV; NxChainCore* c = implicit_chain(d, i); *out = nullptr; if (!c) return INV;
    IDirect3DSwapChain9* s = nx_life(d)->ex ? static_cast<IDirect3DSwapChain9*>(static_cast<NxSwapChain9Ex*>(c)) : static_cast<IDirect3DSwapChain9*>(static_cast<NxSwapChain9*>(c)); s->AddRef(); *out = s; return D3D_OK;
}
static HRESULT d_present(NxObj* d, const RECT* s, const RECT* dr, HWND ov, DWORD flags) { return nx_life(d)->chains[0]->present(s, dr, ov, flags); }
static HRESULT d_backbuffer(NxObj* d, UINT sc, UINT i, IDirect3DSurface9** out) { if (!out) return INV; *out = nullptr; NxChainCore* c = implicit_chain(d, sc); return c ? c->backbuffer(i, out) : INV; }
static void d_set_gamma(NxObj* d, UINT i, const D3DGAMMARAMP* r) { NxChainCore* c = implicit_chain(d, i); if (c && r) { c->ramp = *r; c->ramp_set = true; } }
static void d_get_gamma(NxObj* d, UINT i, D3DGAMMARAMP* r) { NxChainCore* c = implicit_chain(d, i); if (c && r) *r = c->ramp; }
static HRESULT d_begin(NxObj* d) { NxDevLife* L = nx_life(d); if (L->in_scene) return INV; L->in_scene = true; return D3D_OK; }
static HRESULT d_end(NxObj* d) { NxDevLife* L = nx_life(d); if (!L->in_scene) return INV; L->in_scene = false; return D3D_OK; }

#define DEV_MISC(C)                                                                                                                                                                              \
    HRESULT STDMETHODCALLTYPE C::TestCooperativeLevel() { return d_coop(this); }                                                                                                                 \
    UINT STDMETHODCALLTYPE C::GetAvailableTextureMem() { return (UINT)nx_dev_mem_available(this); }                                                                                              \
    HRESULT STDMETHODCALLTYPE C::EvictManagedResources() { return nx_dev_evict(this); }                                                                                                          \
    HRESULT STDMETHODCALLTYPE C::GetDeviceCaps(D3DCAPS9* c) { return d_caps(this, c); }                                                                                                          \
    HRESULT STDMETHODCALLTYPE C::GetDisplayMode(UINT i, D3DDISPLAYMODE* m) { NxChainCore* c = implicit_chain(this, i); return c ? c->display(m) : INV; }                                         \
    HRESULT STDMETHODCALLTYPE C::GetCreationParameters(D3DDEVICE_CREATION_PARAMETERS* p) { return d_creation(this, p); }                                                                         \
    HRESULT STDMETHODCALLTYPE C::SetCursorProperties(UINT x, UINT y, IDirect3DSurface9* b) { return d_cursor_props(this, x, y, b); }                                                              \
    void STDMETHODCALLTYPE C::SetCursorPosition(int x, int y, DWORD) { nx_life(this)->cur.x = x; nx_life(this)->cur.y = y; }                                                                     \
    BOOL STDMETHODCALLTYPE C::ShowCursor(BOOL b) { return d_show_cursor(this, b); }                                                                                                              \
    HRESULT STDMETHODCALLTYPE C::CreateAdditionalSwapChain(D3DPRESENT_PARAMETERS* p, IDirect3DSwapChain9** s) { return d_additional(this, p, s); }                                               \
    HRESULT STDMETHODCALLTYPE C::GetSwapChain(UINT i, IDirect3DSwapChain9** s) { return d_swapchain(this, i, s); }                                                                               \
    UINT STDMETHODCALLTYPE C::GetNumberOfSwapChains() { return nx_life(this)->implicit_chains; }                                                                                                    \
    HRESULT STDMETHODCALLTYPE C::Present(const RECT* s, const RECT* d, HWND o, const RGNDATA*) { return d_present(this, s, d, o, 0); }                                                            \
    HRESULT STDMETHODCALLTYPE C::GetBackBuffer(UINT sc, UINT i, D3DBACKBUFFER_TYPE, IDirect3DSurface9** o) { return d_backbuffer(this, sc, i, o); }                                              \
    HRESULT STDMETHODCALLTYPE C::GetRasterStatus(UINT i, D3DRASTER_STATUS* r) { NxChainCore* c = implicit_chain(this, i); return c ? c->raster(r) : INV; }                                       \
    HRESULT STDMETHODCALLTYPE C::SetDialogBoxMode(BOOL) { fnvnx_wine_fixme(NXW_wined3d_device_set_dialog_box_mode); return D3D_OK; }                                                                                                                       \
    void STDMETHODCALLTYPE C::SetGammaRamp(UINT i, DWORD, const D3DGAMMARAMP* r) { d_set_gamma(this, i, r); }                                                                                    \
    void STDMETHODCALLTYPE C::GetGammaRamp(UINT i, D3DGAMMARAMP* r) { d_get_gamma(this, i, r); }                                                                                                 \
    HRESULT STDMETHODCALLTYPE C::GetFrontBufferData(UINT i, IDirect3DSurface9* s) { NxChainCore* c = implicit_chain(this, i); return c ? c->frontbuffer_data(s) : INV; }                          \
    HRESULT STDMETHODCALLTYPE C::BeginScene() { return d_begin(this); }                                                                                                                          \
    HRESULT STDMETHODCALLTYPE C::EndScene() { return d_end(this); }
DEV_MISC(NxDevice9) DEV_MISC(NxDevice9Ex)
