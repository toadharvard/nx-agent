// D7: swap chains (IDirect3DSwapChain9 / 9Ex). Sources (Wine 9.0): d3d9/swapchain.c (d3d9_swapchain_*), wined3d/swapchain.c (wined3d_swapchain_present, _get_back_buffer, _get_front_buffer_data,
// _get_raster_status, swapchain_gl_present -> wined3d_swapchain_gl_rotate), wined3d/directx.c wined3d_output_get_raster_status (the scan line is derived from QueryPerformanceCounter).
// BACK BUFFER ROTATION RULE (source: wined3d_swapchain_gl_rotate, reached by every Present of the GL swap chain, whatever the swap effect): with count >= 2, after a Present the buffer i holds the former
// contents of buffer i + 1 (mod count); the buffer OBJECTS (GetBackBuffer identity) never move, their images do. With count 1 nothing changes (DISCARD / COPY / FLIP alike). Lockable buffers: the CPU
// copies are stale after a rotation (Wine invalidates every location but the GPU ones).
// A NULL render target format (NXFMT_NULL, wined3d WINED3DFMT_NULL) has no storage: its chain is created without images and a Present of it copies no pixels (nothing to present; the frame counter and the rotation still run).
// DECISIONS (doubt): Present rectangles must be non-empty and inside the back buffer (source) / the client area of the chain's window (destination, in client coordinates; the platform's presented image is
// the window's client area), else D3DERR_INVALIDCALL (Wine clips in the GL blit, the backend ABI refuses); an absent destination with an invalid window = INVALIDCALL; the destination window override
// is ignored (one output); GetFrontBufferData accepts dest format A8R8G8B8 only (MS), dest in SYSTEMMEM-like lockable pools only (the surface must be CPU writable), windowed: the client origin offset
// of wined3d_swapchain_get_front_buffer_data; GetLastPresentCount = number of presents of this chain (MS; Wine returns 0); GetPresentStats: m1_stats.cpp.
#include "res_tex.h"
#include "../gen/d9impl.gen.h"
#include "dev_core.h"
#include "dev_hooks.h"
#include "../gen/env_table.gen.h"
#include <string.h>
#define INV D3DERR_INVALIDCALL

bool NxDevLife::add_chain(NxChainCore* c) {   // false = out of memory, the list is unchanged
    NxChainCore** n = (NxChainCore**)nxd3d_alloc(sizeof(void*) * (nchains + 1)); if (!n) return false;
    if (nchains) memcpy(n, chains, sizeof(void*) * nchains);
    n[nchains] = c; c->index = nchains++; nxd3d_free(chains); chains = n; return true;
}
void NxDevLife::del_chain(NxChainCore* c) {
    uint32_t k = 0; for (uint32_t i = 0; i < nchains; i++) if (chains[i] != c) { chains[k] = chains[i]; chains[k]->index = k; k++; }
    nchains = k;
}
NxDevLife::~NxDevLife() {
    while (nchains) { NxChainCore* c = chains[nchains - 1]; c->ci->destroy(c); }   // the chain removes itself; chains with public references cannot exist (they hold the device)
    if (ds) ds->ci->destroy(ds);
    nxd3d_free(chains);
}
NxDevLife* nx_life_make(NxDeviceCore* d, bool ex, int prof) { NxDevLife* l = nx_make_plain<NxDevLife>(); if (!l) return nullptr; l->ex = ex; l->prof = (uint32_t)prof; d->life = l; return l; }

// destroys the buffers without public references; a buffer the application still holds (Ex Reset) becomes an ordinary object of the device that dies with its last reference
NxObj* NxChainCore::destroy_bbs() {
    for (uint32_t i = 0; i < nbb; i++) {
        NxObj* o = bb[i];
        if (o->pub) { o->owned = 0; o->owner = d; o->container = d; }
        else { o->owned = 0; if (!o->binds) o->ci->destroy(o); else o->owner = d; }   // bound (state): the last unbind destroys it
    }
    nxd3d_free(bb); bb = nullptr; nbb = 0; return nullptr;
}
NxChainCore::~NxChainCore() { destroy_bbs(); if (d && d->life) nx_life(d)->del_chain(this); }

// Allocation failure (README "Allocation failure"): E_OUTOFMEMORY for the objects / the array (d3d9 swapchain.c / wined3d swapchain init), D3DERR_OUTOFVIDEOMEMORY when the image cannot be made (NxImg::init) or has no native format;
// every buffer made so far is destroyed, the chain keeps its old (empty after destroy_bbs) buffer set.
HRESULT NxChainCore::make_buffers(const NxPPEff& e, const D3DPRESENT_PARAMETERS& p) {
    NxObj** nb = (NxObj**)nxd3d_alloc(sizeof(NxObj*) * (e.count ? e.count : 1)); if (!nb) return E_OUTOFMEMORY;
    NxTexFmt f; nx_texfmt(e.fmt, &f); bool lock = (p.Flags & D3DPRESENTFLAG_LOCKABLE_BACKBUFFER) != 0; uint32_t made = 0; HRESULT hr = D3D_OK;
    for (uint32_t n = 0; n < e.count && hr == D3D_OK; n++) {
        NxImg* img = nx_make_plain<NxImg>(); if (!img) { hr = E_OUTOFMEMORY; break; }
        NxSurface9* s = nx_make<NxSurface9>(); if (!s) { delete img; hr = E_OUTOFMEMORY; break; }
        s->img = s->own = img; NxObj* o = s; o->asImplicit(d, this); o->pool = D3DPOOL_DEFAULT; if (index) o->owner = this; nb[made++] = o;   // from here the surface owns the image
        hr = img->init(s, NXK_SURFACE, e.w, e.h, 1, 1, 1, e.fmt, D3DUSAGE_RENDERTARGET, D3DPOOL_DEFAULT, (lock || (f.flags & NXTF_MAPPABLE)) ? 12 : 0, false, nullptr, p.MultiSampleType, p.MultiSampleQuality, e.samples, D3DUSAGE_RENDERTARGET);
        if (hr == D3D_OK && !img->gpu && !(img->ni.flags & NXFMT_NULL)) hr = D3DERR_OUTOFVIDEOMEMORY;   // no native representation of the format
    }
    if (hr != D3D_OK) { for (uint32_t k = 0; k < made; k++) { nb[k]->owned = 0; nb[k]->ci->destroy(nb[k]); } nxd3d_free(nb); return hr; }
    bb = nb; nbb = e.count; return D3D_OK;
}
HRESULT nx_chain_create(NxDeviceCore* d, const D3DPRESENT_PARAMETERS& p, const NxPPEff& e, bool implicit, NxChainCore** out) {
    NxDevLife* L = nx_life(d); NxChainCore* c = nullptr; NxObj* o = nullptr;
    if (L->ex) { NxSwapChain9Ex* x = nx_make<NxSwapChain9Ex>(); if (x) { c = x; o = x; } } else { NxSwapChain9* x = nx_make<NxSwapChain9>(); if (x) { c = x; o = x; } }
    if (!c) return E_OUTOFMEMORY;   // d3d9 swapchain.c: calloc failure
    if (implicit) o->asImplicit(d, d); else o->asChild(d, ~0u);
    c->d = d; c->pp = p; c->pp.BackBufferWidth = e.w; c->pp.BackBufferHeight = e.h; c->pp.BackBufferFormat = (D3DFORMAT)e.fmt; c->pp.BackBufferCount = e.count; c->pp.hDeviceWindow = (HWND)e.win; c->pp.Flags &= 0xfffu;
    for (int i = 0; i < 256; i++) c->ramp.red[i] = c->ramp.green[i] = c->ramp.blue[i] = (WORD)(i * 257);   // the identity ramp (the display's initial ramp)
    HRESULT hr = L->add_chain(c) ? c->make_buffers(e, c->pp) : E_OUTOFMEMORY;
    if (FAILED(hr)) { if (implicit) { o->owned = 0; o->ci->destroy(o); } else o->release(); return hr; }
    *out = c; return D3D_OK;
}

static NxImg* bbimg(NxChainCore* c, uint32_t i) { return static_cast<NxSurfCore*>(c->bb[i])->img; }
static bool upload_all(NxChainCore* c) { for (uint32_t i = 0; i < c->nbb; i++) if (!bbimg(c, i)->upload()) return false; return true; }   // pending CPU writes of every buffer, BEFORE anything of the present changes
static void rotate(NxChainCore* c) {   // see the header: images rotate, objects stay; in place, nothing can fail (the buffers were uploaded by upload_all)
    if (c->nbb < 2) return;
    uint32_t first = bbimg(c, 0)->gpu; for (uint32_t i = 0; i + 1 < c->nbb; i++) bbimg(c, i)->gpu = bbimg(c, i + 1)->gpu; bbimg(c, c->nbb - 1)->gpu = first;
    for (uint32_t i = 0; i < c->nbb; i++) if (bbimg(c, i)->sub[0].mem) bbimg(c, i)->sub[0].gpu_newer = 1;
}
HRESULT NxChainCore::present(const RECT* s, const RECT* dr, void* ov, DWORD flags) {
    (void)ov; NxDevLife* L = nx_life(d);
    if (L->state != NX_DEV_OK) return L->ex ? S_PRESENT_OCCLUDED : D3DERR_DEVICELOST;
    RECT src = {0, 0, (LONG)pp.BackBufferWidth, (LONG)pp.BackBufferHeight}, dst, cr = {0, 0, 0, 0};
    bool win = nxd3d_window_client(pp.hDeviceWindow, &cr); LONG cw = cr.right - cr.left, ch = cr.bottom - cr.top;
    if (s) src = *s;
    if (dr) dst = *dr; else { if (!win) return INV; dst.left = dst.top = 0; dst.right = cw; dst.bottom = ch; }
    if (src.left < 0 || src.top < 0 || src.left >= src.right || src.top >= src.bottom || src.right > (LONG)pp.BackBufferWidth || src.bottom > (LONG)pp.BackBufferHeight) return INV;
    if (!win || dst.left < 0 || dst.top < 0 || dst.left >= dst.right || dst.top >= dst.bottom || dst.right > cw || dst.bottom > ch) return INV;
    if (!upload_all(this)) return E_OUTOFMEMORY;   // a conversion buffer; nothing was presented, nothing rotated, the dirty regions stay
    NxImg* img = static_cast<NxSurfCore*>(bb[0])->img;
    if (!img->gpu) { L->frame++; rotate(this); presented(); return D3D_OK; }   // NXFMT_NULL: no pixels
    NxPresent p = {}; p.image = img->gpu; p.sx = src.left; p.sy = src.top; p.sw = src.right - src.left; p.sh = src.bottom - src.top; p.dx = dst.left; p.dy = dst.top; p.dw = dst.right - dst.left; p.dh = dst.bottom - dst.top;
    p.interval = (flags & D3DPRESENT_FORCEIMMEDIATE) ? 0 : nx_pp_interval(pp.PresentationInterval); p.gamma = ramp_set ? (uint64_t)(uintptr_t)&ramp : 0; p.frame = ++L->frame;
    if (L->cur.visible && L->cur.bgra) { p.cursor = (uint64_t)(uintptr_t)L->cur.bgra; p.cw = L->cur.w; p.ch = L->cur.h; p.cx = L->cur.x - (int32_t)L->cur.hx - cr.left; p.cy = L->cur.y - (int32_t)L->cur.hy - cr.top; }   // nxgpu4.h P2: blended onto the presented image, never into the back buffer
    if (int rc = nxd3d_present(this, &p)) return rc == NXD3D_HOOK_NOMEM ? D3DERR_OUTOFVIDEOMEMORY : D3DERR_DRIVERINTERNALERROR;   // the rects were validated: a refusal is a backend defect
    rotate(this); presented(); return D3D_OK;
}
HRESULT NxChainCore::backbuffer(UINT i, IDirect3DSurface9** out) {   // backbuffer type ignored by native (Wine comment); the out pointer is untouched on failure (test_swapchain)
    if (!out || i >= nbb) return INV;
    IDirect3DSurface9* s = static_cast<IDirect3DSurface9*>(bb[i]->self); s->AddRef(); *out = s; return D3D_OK;   // `self` = the object's interface (obj.h nx_obj_init): no pointer adjustment, so no null path for the compiler to turn into a trap
}
HRESULT NxChainCore::frontbuffer_data(IDirect3DSurface9* dst) {
    NxObj* o = nx_obj_of(dst); if (!o || o->ci->rtype != D3DRTYPE_SURFACE) return INV;
    NxImg* im = static_cast<NxSurfCore*>(o)->img; uint32_t w = pp.BackBufferWidth, h = pp.BackBufferHeight; RECT cr = {0, 0, 0, 0};
    if (im->fmt != D3DFMT_A8R8G8B8) return INV;   // MS GetFrontBufferData: "converted to the standard 32 bits per pixel format D3DFMT_A8R8G8B8"; Windows rows (visual.c, SYSTEMMEM surface and texture level) use only that
    uint32_t ox = 0, oy = 0; if (pp.Windowed) { if (!nxd3d_window_client(pp.hDeviceWindow, &cr) || cr.left < 0 || cr.top < 0) return INV; ox = (uint32_t)cr.left; oy = (uint32_t)cr.top; }   // wined3d: MapWindowPoints
    D3DBOX box = {ox, oy, ox + w, oy + h, 0, 1}; void* bits; uint32_t row, slice;
    if (!nx_box_valid(im, 0, &box) || FAILED(nx_img_lock(im, 0, 0, &box, 0, &bits, &row, &slice))) return INV;
    uint8_t* tmp = (uint8_t*)nxd3d_alloc((size_t)w * h * 4); int rc = tmp ? nxd3d_frontbuffer_read(this, w, h, tmp, w * 4) : NXD3D_HOOK_NOMEM;
    if (rc) { nxd3d_free(tmp); bool w0; nx_img_unlock(im, 0, 0, &w0); return rc == NXD3D_HOOK_NOMEM ? D3DERR_OUTOFVIDEOMEMORY : D3DERR_DRIVERINTERNALERROR; }
    for (uint32_t y = 0; y < h; y++) { const uint8_t* sp = tmp + (size_t)y * w * 4; uint8_t* dp = (uint8_t*)bits + (size_t)y * row; for (uint32_t x = 0; x < w; x++, sp += 4, dp += 4) { dp[0] = sp[2]; dp[1] = sp[1]; dp[2] = sp[0]; dp[3] = sp[3]; } }
    nxd3d_free(tmp); bool was; nx_img_unlock(im, 0, 0, &was); return D3D_OK;
}
HRESULT NxChainCore::raster(D3DRASTER_STATUS* r) {   // wined3d_output_get_raster_status: a scan line faked from the performance counter, 20 lines of vertical blank, refresh 60 when the mode has none
    if (!r) return INV;
    NxEnvMode m; if (nxenv_display_mode((int)nx_life(d)->prof, nx_life(d)->cp.AdapterOrdinal, &m)) return INV;
    uint64_t fq = 0, ctr = nxd3d_perf_counter(&fq); uint64_t pf = fq / (m.RefreshRate ? m.RefreshRate : 60), pl = pf / (m.Height + 20); if (!pf || !pl) return INV;
    uint32_t sl = (uint32_t)((ctr % pf) / pl); if (sl < m.Height) { r->ScanLine = sl; r->InVBlank = FALSE; } else { r->ScanLine = 0; r->InVBlank = TRUE; }
    return D3D_OK;
}
HRESULT NxChainCore::display(D3DDISPLAYMODE* o) {
    NxEnvMode m; if (!o) return INV; HRESULT hr = (HRESULT)nxenv_display_mode((int)nx_life(d)->prof, nx_life(d)->cp.AdapterOrdinal, &m);
    if (SUCCEEDED(hr)) { o->Width = m.Width; o->Height = m.Height; o->RefreshRate = m.RefreshRate; o->Format = (D3DFORMAT)m.Format; } return hr;
}
HRESULT NxChainCore::display_ex(D3DDISPLAYMODEEX* o, D3DDISPLAYROTATION* rot) {
    NxEnvMode m; uint32_t sc = 0, ro = 0; if (!o || o->Size != sizeof(*o)) return INV;
    HRESULT hr = (HRESULT)nxenv_display_mode_ex((int)nx_life(d)->prof, nx_life(d)->cp.AdapterOrdinal, o->Size, &m, &sc, &ro);
    if (SUCCEEDED(hr)) { o->Width = m.Width; o->Height = m.Height; o->RefreshRate = m.RefreshRate; o->Format = (D3DFORMAT)m.Format; o->ScanLineOrdering = (D3DSCANLINEORDERING)sc; if (rot) *rot = (D3DDISPLAYROTATION)ro; } return hr;
}
HRESULT NxChainCore::present_params(D3DPRESENT_PARAMETERS* o) { if (!o) return INV; *o = pp; return D3D_OK; }
HRESULT NxChainCore::last_present_count(UINT* c) { if (!c) return INV; *c = pcount; return D3D_OK; }

#define CHAIN_COMMON(C)                                                                                                                                                                       \
    HRESULT STDMETHODCALLTYPE C::Present(const RECT* s, const RECT* dr, HWND ov, const RGNDATA*, DWORD fl) { return present(s, dr, ov, fl); }                                                           \
    HRESULT STDMETHODCALLTYPE C::GetFrontBufferData(IDirect3DSurface9* dst) { return frontbuffer_data(dst); }                                                                                \
    HRESULT STDMETHODCALLTYPE C::GetBackBuffer(UINT i, D3DBACKBUFFER_TYPE, IDirect3DSurface9** out) { return backbuffer(i, out); }                                                           \
    HRESULT STDMETHODCALLTYPE C::GetRasterStatus(D3DRASTER_STATUS* r) { return raster(r); }                                                                                                  \
    HRESULT STDMETHODCALLTYPE C::GetDisplayMode(D3DDISPLAYMODE* m) { return display(m); }                                                                                                    \
    HRESULT STDMETHODCALLTYPE C::GetPresentParameters(D3DPRESENT_PARAMETERS* p) { return present_params(p); }
CHAIN_COMMON(NxSwapChain9) CHAIN_COMMON(NxSwapChain9Ex)
HRESULT STDMETHODCALLTYPE NxSwapChain9Ex::GetLastPresentCount(UINT* c) { return last_present_count(c); }
HRESULT STDMETHODCALLTYPE NxSwapChain9Ex::GetDisplayModeEx(D3DDISPLAYMODEEX* m, D3DDISPLAYROTATION* r) { return display_ex(m, r); }
