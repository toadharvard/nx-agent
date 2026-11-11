// ValidateDevice and the patch calls of both device classes.
// VALIDATEDEVICE (Wine 9.0 wined3d_device_validate_device over the current device state; the d3d9 wrapper only forwards): V1 for every sampler (0-15 and the four vertex-texture samplers) the minification
//  and magnification filters are not D3DTEXF_NONE, else D3DERR_UNSUPPORTEDTEXTUREFILTER. V2 a sampler whose bound texture format is not filterable (env table: CheckDeviceFormat D3DUSAGE_QUERY_FILTER for the
//  texture's resource type) needs POINT mag and min filters and a NONE / POINT mip filter, else E_FAIL. V3 with ZENABLE, ZWRITEENABLE or STENCILENABLE set, a depth-stencil surface smaller than render target 0
//  = D3DERR_CONFLICTINGRENDERSTATE. Else D3D_OK with *pass_count = 1 (wined3d returns "a sensible default"). A null pass_count = INVALIDCALL (Windows faults).
// PATCHES: D3DCAPS9.DevCaps of the reference environment has neither D3DDEVCAPS_RTPATCHES nor RTPATCHHANDLEZERO nor NPATCHES (gen_env.py asserts it from the recorded caps and stops when a recording
//  changes it), so no higher-order surface can be drawn: P1 DrawRectPatch / DrawTriPatch = D3DERR_INVALIDCALL (MS: the only failure code), P2 DeletePatch: no handle was ever accepted, every handle
//  is unknown = D3DERR_INVALIDCALL (Wine's DeletePatch answer). Wine's DrawRectPatch / DrawTriPatch return D3D_OK without drawing (a FIXME stub): that is a no-op and is not copied.
// NOT IMPLEMENTED (no complete contract): ProcessVertices (Wine ignores the vertex shader and the output declaration, FIXME; MS requires running the shader: needs the shader interpreter stream),
//  SetClipStatus / GetClipStatus (the status is produced by the draw path; no contract before it exists).
#include "copy_util.h"
#include "env_table.gen.h"
#include "d9impl.gen.h"

static HRESULT do_validate(NxDeviceCore* d, DWORD* passes) {
    if (!passes) return D3DERR_INVALIDCALL;
    int p = nx_dev_profile(d);
    for (uint32_t k = 0; k < 20; k++) {
        uint32_t idx = k < 16 ? k : (uint32_t)D3DVERTEXTEXTURESAMPLER0 + (k - 16); int slot = nx_dsamp(idx); if (slot < 0) continue;
        const uint32_t* st = d->cur.samp[slot]; uint32_t mn = st[D3DSAMP_MINFILTER], mg = st[D3DSAMP_MAGFILTER], mp = st[D3DSAMP_MIPFILTER];
        if (mn == D3DTEXF_NONE || mg == D3DTEXF_NONE) return D3DERR_UNSUPPORTEDTEXTUREFILTER;                                                // V1
        NxObj* t = nx_obj_of((IUnknown*)d->cur.tex[slot]); NxImg* im = nx_img_of(t);
        if (!im || nxenv_check_device_format(p, 0, 1, 22, 0x20000, t->ci->rtype, im->fmt) == 0) continue;                                      // filterable (or nothing bound)
        if (mg != D3DTEXF_POINT || mn != D3DTEXF_POINT || (mp != D3DTEXF_NONE && mp != D3DTEXF_POINT)) return E_FAIL;                          // V2
    }
    if (d->cur.rs[D3DRS_ZENABLE] || d->cur.rs[D3DRS_ZWRITEENABLE] || d->cur.rs[D3DRS_STENCILENABLE]) {                                         // V3
        NxSurfCore* rt = nx_surf_core(d->cur.rt[0]); NxSurfCore* ds = nx_surf_core(d->cur.ds[0]);
        if (rt && ds) { const NxSub* a = nx_surf_sub(ds); const NxSub* b = nx_surf_sub(rt); if (a->w < b->w || a->h < b->h) return D3DERR_CONFLICTINGRENDERSTATE; }
    }
    *passes = 1; return D3D_OK;
}
#define MISC(C) \
    HRESULT STDMETHODCALLTYPE C::ValidateDevice(DWORD* n) { return do_validate(this, n); } \
    HRESULT STDMETHODCALLTYPE C::DrawRectPatch(UINT, const float*, const D3DRECTPATCH_INFO*) { return D3DERR_INVALIDCALL; } \
    HRESULT STDMETHODCALLTYPE C::DrawTriPatch(UINT, const float*, const D3DTRIPATCH_INFO*) { return D3DERR_INVALIDCALL; } \
    HRESULT STDMETHODCALLTYPE C::DeletePatch(UINT) { return D3DERR_INVALIDCALL; }
MISC(NxDevice9) MISC(NxDevice9Ex)
