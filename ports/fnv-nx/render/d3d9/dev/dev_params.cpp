// D7: the resolver of D3DPRESENT_PARAMETERS (CreateDevice, CreateAdditionalSwapChain, Reset). One function = the rule; the callers only differ in `how`.
// Sources (Wine 9.0): d3d9/device.c wined3d_swapchain_desc_from_d3d9 (swap effect 1..3 / Ex 1..5, back buffer count <= 3 / Ex 30, COPY with count > 1, the six D3DPRESENT_INTERVAL_* values; Windows-validated
// by d3d9/tests/device.c test_swapchain_parameters rows), wined3d/swapchain.c wined3d_swapchain_state_init (windowed: 0 size = client size or 8, UNKNOWN format = display format), swapchain_create_texture +
// wined3d/texture.c wined3d_texture_create (multisample: NOTAVAILABLE) + wined3d_texture_init (zero size, UNKNOWN format: INVALIDCALL) + resource.c resource_init (render target / depth stencil format caps:
// INVALIDCALL, block alignment), wined3d_output_set_display_mode (full screen: a mode of the output with this size and bits per pixel, else NOTAVAILABLE; the refresh rate is dropped when it fails),
// wined3d_swapchain_resize_buffers (Reset: 0 size = client size, UNKNOWN format in full screen INVALIDCALL), wined3d_device_reset (a depth stencil failure is INVALIDCALL). Format / multisample / mode answers
// come ONLY from the env table (env_table.gen.h). DECISIONS (doubt): a back buffer image above NX_MAX_TEX_BYTES = D3DERR_OUTOFVIDEOMEMORY (as res_create.cpp); bits per pixel of a format = nx_texfmt bytes * 8.
#include "res_tex.h"
#include "dev_core.h"
#include "dev_hooks.h"
#include "../gen/env_table.gen.h"
#define INV D3DERR_INVALIDCALL
#define NA D3DERR_NOTAVAILABLE
static const uint32_t X8 = 22, R5 = 23;

D3DFORMAT nx_pp_fmt_out(uint32_t f) { return nxenv_format_class(f) == nxenv_format_class(0) ? D3DFMT_UNKNOWN : (D3DFORMAT)f; }
uint32_t nx_pp_interval(DWORD v) { return v == D3DPRESENT_INTERVAL_IMMEDIATE ? 0 : v == D3DPRESENT_INTERVAL_TWO ? 2 : v == D3DPRESENT_INTERVAL_THREE ? 3 : v == D3DPRESENT_INTERVAL_FOUR ? 4 : 1; }
static bool unk(uint32_t f) { return nxenv_format_class(f) == nxenv_format_class(0); }   // wined3dformat_from_d3dformat gives WINED3DFMT_UNKNOWN
// wined3d_texture_create: the multisample check (type NONMASKABLE: quality indexes the supported types, otherwise the type must be in the format's mask and quality 0 or the standard pattern)
static HRESULT ms_ok(int p, uint32_t fmt, uint32_t type, uint32_t q, uint32_t* samples) {
    *samples = 1; if (type == 0) return D3D_OK;
    if (type == 1) {
        uint32_t lv = 0; if (nxenv_check_msaa(p, 0, 1, fmt, 0, 1, &lv) != 0 || q >= lv) return NA;
        for (uint32_t t = 2, n = 0; t <= 16; t++) if (nxenv_check_msaa(p, 0, 1, fmt, 0, t, nullptr) == 0 && n++ == q) { *samples = t; return D3D_OK; }
        return NA;
    }
    if (type > 16 || nxenv_check_msaa(p, 0, 1, fmt, 0, type, nullptr) != 0 || (q && q != 0xFFFFFFFFu)) return NA;
    *samples = type; return D3D_OK;
}
// wined3d_texture_init + resource_init for a swap chain texture of (w, h, fmt): bind = 0 front buffer, 1 render target, 2 depth stencil
static HRESULT tex_ok(int p, uint32_t w, uint32_t h, uint32_t fmt, uint32_t bind) {
    NxTexFmt f;
    if (!w || !h || unk(fmt) || !nx_texfmt(fmt, &f)) return INV;
    if (bind && nxenv_check_device_format(p, 0, 1, X8, bind, 1, fmt) != 0) return INV;     // SURFACE class = texture 2D without SHADER_RESOURCE
    if ((f.flags & (NXTF_BLOCKS | NXTF_NOVERIFY)) == NXTF_BLOCKS && ((w & (f.bw - 1)) || (h & (f.bh - 1)))) return INV;
    if (nx_tex_size(f, w, h, 1) > (uint64_t)NX_MAX_TEX_BYTES) return D3DERR_OUTOFVIDEOMEMORY;
    return D3D_OK;
}
HRESULT nx_pp_check(bool ex, const D3DPRESENT_PARAMETERS* pp) {
    uint32_t se = pp->SwapEffect, cnt = pp->BackBufferCount, iv = pp->PresentationInterval;
    if (!se || se > (ex ? (uint32_t)D3DSWAPEFFECT_FLIPEX : (uint32_t)D3DSWAPEFFECT_COPY)) return INV;
    if (cnt > (ex ? 30u : 3u) || (se == D3DSWAPEFFECT_COPY && cnt > 1)) return INV;
    if (iv != D3DPRESENT_INTERVAL_DEFAULT && iv != D3DPRESENT_INTERVAL_ONE && iv != D3DPRESENT_INTERVAL_TWO && iv != D3DPRESENT_INTERVAL_THREE && iv != D3DPRESENT_INTERVAL_FOUR && iv != D3DPRESENT_INTERVAL_IMMEDIATE) return INV;
    return D3D_OK;
}
HRESULT nx_pp_resolve(int p, bool ex, int how, const D3DPRESENT_PARAMETERS* pp, void* focus, NxPPEff* e) {
    HRESULT hr0 = nx_pp_check(ex, pp); if (FAILED(hr0)) return hr0;
    uint32_t cnt = pp->BackBufferCount;
    NxEnvMode dm; nxenv_display_mode(p, 0, &dm);
    void* win = pp->hDeviceWindow ? (void*)pp->hDeviceWindow : focus; uint32_t w = pp->BackBufferWidth, h = pp->BackBufferHeight, fmt = pp->BackBufferFormat; RECT r = {0, 0, 0, 0};
    bool ok = nxd3d_window_client(win, &r);
    if (how == NX_PP_RESET) {                                                  // wined3d_swapchain_resize_buffers
        if ((!w || !h) && !ok) return INV;
        if (!w) w = (uint32_t)(r.right - r.left); if (!h) h = (uint32_t)(r.bottom - r.top);
        if (unk(fmt)) { if (!pp->Windowed) return INV; fmt = dm.Format; }
    } else if (pp->Windowed) {                                                 // wined3d_swapchain_state_init
        if (!w) { uint32_t c = ok ? (uint32_t)(r.right - r.left) : 0; w = c ? c : 8; }
        if (!h) { uint32_t c = ok ? (uint32_t)(r.bottom - r.top) : 0; h = c ? c : 8; }
        if (unk(fmt)) fmt = dm.Format;
    }
    uint32_t sm = 1, ds_sm = 1; HRESULT hr;
    if (FAILED(hr = ms_ok(p, fmt, pp->MultiSampleType, pp->MultiSampleQuality, &sm))) return hr;       // front buffer
    if (FAILED(hr = tex_ok(p, w, h, fmt, 0))) return hr;
    if (!pp->Windowed && how == NX_PP_CREATE) {                                // wined3d_output_set_display_mode
        NxTexFmt f; nx_texfmt(fmt, &f); uint32_t bits = f.bytes * 8u;
        if (w != dm.Width || h != dm.Height || (bits != 32 && bits != 16) || (bits == 32 ? nxenv_mode_count(p, 0, X8) : nxenv_mode_count(p, 0, R5)) == 0) return NA;
    }
    if (FAILED(hr = tex_ok(p, w, h, fmt, 1))) return hr;                       // back buffers
    if (pp->EnableAutoDepthStencil) {
        uint32_t dfmt = pp->AutoDepthStencilFormat;
        if (FAILED(hr = ms_ok(p, dfmt, pp->MultiSampleType, pp->MultiSampleQuality, &ds_sm)) || FAILED(hr = tex_ok(p, w, h, dfmt, 2))) return how == NX_PP_RESET ? (HRESULT)INV : hr;
    }
    if (!pp->Windowed && how == NX_PP_RESET) {                                 // wined3d_swapchain_state_set_fullscreen -> the same mode rule
        NxTexFmt f; nx_texfmt(fmt, &f); uint32_t bits = f.bytes * 8u;
        if (w != dm.Width || h != dm.Height || (bits != 32 && bits != 16)) return NA;
    }
    e->w = w; e->h = h; e->fmt = fmt; e->count = cnt ? cnt : 1; e->win = win; e->samples = sm; e->ds_samples = ds_sm; return D3D_OK;
}
