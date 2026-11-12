// m1_validate.cpp: IDirect3DDevice9::ValidateDevice (both device classes) = nx_m1_draw_legal + "*pNumPasses = 1". Sources: MS IDirect3DDevice9::ValidateDevice page (result codes; "uses the current render states, textures and
// texture-stage states"), Wine 9.0 wined3d/device.c wined3d_device_validate_device, the Windows-validated rows of d3d9/tests/device.c test_filter (sampler rows, E_FAIL for unfilterable formats; "needed for ValidateDevice": a
// vertex declaration), visual.c depth_stencil_size_test, Mesa Nine NineDevice9_ValidateDevice (render target size rule). Every row below is a condition our pipeline cannot render, taken from the env caps image (env/README.md).
//  *pNumPasses = 1: the fixed-function pixel program (ffp/README: one program, every stage) and every shader pair is ONE backend draw; nothing is multipass. A failing call leaves *pNumPasses untouched (test_filter rows).
//  Rows, in the order they are tested (the first failing row is the result):
//   V0 non-Ex device not OK = D3DERR_DEVICELOST (MS list); Ex devices are never lost.
//   V1 no vertex declaration = D3DERR_INVALIDCALL (draw rule D1; Windows test_filter "Needed for ValidateDevice()").
//   V2 per sampler (pixel 0-15, vertex 17-20 = Wine's WINED3D_MAX_COMBINED_SAMPLERS; D3DDMAPSAMPLER is never read): MIN or MAG filter NONE, or a filter value the caps word of the sampler's texture type does not list = UNSUPPORTEDTEXTUREFILTER
//      (Wine, Nine, test_filter; the MIP value NONE is legal, POINT / LINEAR need MIPF*); a bound texture whose format has no FILTER cap (env table) with MIN/MAG != POINT or MIP not NONE/POINT = E_FAIL (test_filter, Windows rows).
//   V3 fixed-function pixel stages (no pixel shader bound): the cascade ends at the first COLOROP == DISABLE. Per stage: more stages than MaxTextureBlendStages or more texturing stages than MaxSimultaneousTextures = TOOMANYOPERATIONS;
//      COLOROP / ALPHAOP (not DISABLE) not in TextureOpCaps = UNSUPPORTEDCOLOROPERATION / UNSUPPORTEDALPHAOPERATION; an argument the operation reads (ffp/README "slots read") whose selector is 7..15, has bits above D3DTA_ALPHAREPLICATE,
//      or names TEMP / CONSTANT without D3DPMISCCAPS_TSSARGTEMP / PERSTAGECONSTANT = UNSUPPORTEDCOLORARG / UNSUPPORTEDALPHAARG (RESULTARG other than CURRENT / TEMP likewise, colour); BUMPENVMAP* on a stage whose texture is absent
//      or has no LEGACYBUMPMAP cap = WRONGTEXTUREFORMAT. UNSUPPORTEDFACTORVALUE ("not used" in MS) and CONFLICTINGTEXTUREFILTER (our sampler objects are independent per slot: every listed triple renders) cannot occur; DRIVERINTERNALERROR is a backend result of a draw, not of state.
//   V4 targets: render targets of different size or sample count = CONFLICTINGRENDERSTATE (Nine; backend pass rule draw_state S2); with ZENABLE, ZWRITEENABLE or STENCILENABLE (Wine) a depth surface whose size or sample count differs from the
//      colour targets = CONFLICTINGRENDERSTATE (Wine: only smaller; a larger one is not representable by the backend, README Draw path).
#include "copy_util.h"
#include "dev_core.h"
#include "dev_hooks.h"
#include "../gen/d9impl.gen.h"
#include "../gen/env_table.gen.h"
#include "m1_validate.h"

namespace {
const uint32_t MINC[9] = {0, 0x100, 0x200, 0x400, 0, 0, 0x800, 0x1000, 0x40000}, MAGC[9] = {0, 0x1000000, 0x2000000, 0x4000000, 0, 0, 0x8000000, 0x10000000, 0x40000};   // D3DPTFILTERCAPS_* per D3DTEXTUREFILTERTYPE 0-8
bool filt_ok(const uint32_t* t, uint32_t v, uint32_t caps) { return v < 9 && t[v] && (caps & t[v]); }
bool mip_ok(uint32_t v, uint32_t caps) { return v == D3DTEXF_NONE || (v == D3DTEXF_POINT && (caps & D3DPTFILTERCAPS_MIPFPOINT)) || (v == D3DTEXF_LINEAR && (caps & D3DPTFILTERCAPS_MIPFLINEAR)); }
bool op_ok(uint32_t op, uint32_t caps) { return op >= 1 && op <= 26 && (caps >> (op - 1) & 1); }
bool arg_ok(uint32_t a, uint32_t misc) {
    uint32_t sel = a & D3DTA_SELECTMASK;
    return !(a & ~(uint32_t)(D3DTA_SELECTMASK | D3DTA_COMPLEMENT | D3DTA_ALPHAREPLICATE)) && sel <= D3DTA_CONSTANT && (sel != D3DTA_TEMP || (misc & D3DPMISCCAPS_TSSARGTEMP)) && (sel != D3DTA_CONSTANT || (misc & D3DPMISCCAPS_PERSTAGECONSTANT));
}
bool rd(uint32_t op, int k) { return k == 1 ? (op >= 2 && op != 3 && op <= 26) : k == 2 ? (op >= 3 && op != 17 && op <= 26 && op != 2) : (op == 25 || op == 26); }   // ffp/README "slots read": SELECTARG1 / PREMODULATE arg1, SELECTARG2 arg2, MULTIPLYADD / LERP all, the rest arg1 + arg2
bool uses_tex(uint32_t op, uint32_t c1, uint32_t c2, uint32_t c0) { return op == 13 || op == 15 || op == 22 || op == 23 || (rd(op, 1) && (c1 & 15) == 2) || (rd(op, 2) && (c2 & 15) == 2) || (rd(op, 0) && (c0 & 15) == 2); }
NxImg* img_of(const NxDeviceState& s, uint32_t slot) { NxObj* o = s.tex[slot] ? nx_obj_of((IUnknown*)s.tex[slot]) : nullptr; return o ? nx_img_of(o) : nullptr; }
uint32_t rtype(const NxImg* im) { return im->kind == NXK_CUBE ? D3DRTYPE_CUBETEXTURE : im->kind == NXK_VOLUME ? D3DRTYPE_VOLUMETEXTURE : D3DRTYPE_TEXTURE; }
bool cap(int p, uint32_t usage, const NxImg* im) { return nxenv_check_device_format(p, 0, 1, D3DFMT_X8R8G8B8, usage, rtype(im), im->fmt) == 0; }
HRESULT samplers(NxDeviceCore* d, const D3DCAPS9& c, int p) {
    const NxDeviceState& s = d->cur;
    for (uint32_t i = 0; i < 20; i++) {
        uint32_t k = i < 16 ? i : i + 1; NxImg* im = img_of(s, k); const uint32_t mn = s.samp[k][D3DSAMP_MINFILTER], mg = s.samp[k][D3DSAMP_MAGFILTER], mp = s.samp[k][D3DSAMP_MIPFILTER];
        uint32_t caps = i >= 16 ? c.VertexTextureFilterCaps : !im ? c.TextureFilterCaps : im->kind == NXK_CUBE ? c.CubeTextureFilterCaps : im->kind == NXK_VOLUME ? c.VolumeTextureFilterCaps : c.TextureFilterCaps;
        if (mn == D3DTEXF_NONE || mg == D3DTEXF_NONE || !filt_ok(MINC, mn, caps) || !filt_ok(MAGC, mg, caps) || !mip_ok(mp, caps)) return D3DERR_UNSUPPORTEDTEXTUREFILTER;   // V2
        if (im && !cap(p, D3DUSAGE_QUERY_FILTER, im) && (mg != D3DTEXF_POINT || mn != D3DTEXF_POINT || (mp != D3DTEXF_NONE && mp != D3DTEXF_POINT))) return E_FAIL;
    }
    return D3D_OK;
}
HRESULT stages(NxDeviceCore* d, const D3DCAPS9& c, int p) {   // V3
    const NxDeviceState& s = d->cur; uint32_t act = 0, tex = 0;
    for (uint32_t i = 0; i < NX_TSS_STAGES && s.tss[i][D3DTSS_COLOROP] != D3DTOP_DISABLE; i++) {
        const uint32_t* t = s.tss[i]; uint32_t cop = t[D3DTSS_COLOROP], aop = t[D3DTSS_ALPHAOP]; bool ao = aop != D3DTOP_DISABLE;
        if (++act > c.MaxTextureBlendStages) return D3DERR_TOOMANYOPERATIONS;
        bool ut = uses_tex(cop, t[D3DTSS_COLORARG1], t[D3DTSS_COLORARG2], t[D3DTSS_COLORARG0]) || (ao && uses_tex(aop, t[D3DTSS_ALPHAARG1], t[D3DTSS_ALPHAARG2], t[D3DTSS_ALPHAARG0]));
        if (ut && ++tex > c.MaxSimultaneousTextures) return D3DERR_TOOMANYOPERATIONS;
        if (!op_ok(cop, c.TextureOpCaps)) return D3DERR_UNSUPPORTEDCOLOROPERATION;
        if (ao && !op_ok(aop, c.TextureOpCaps)) return D3DERR_UNSUPPORTEDALPHAOPERATION;
        static const uint32_t CA[3] = {D3DTSS_COLORARG0, D3DTSS_COLORARG1, D3DTSS_COLORARG2}, AA[3] = {D3DTSS_ALPHAARG0, D3DTSS_ALPHAARG1, D3DTSS_ALPHAARG2};
        for (int k = 0; k < 3; k++) if (rd(cop, k) && !arg_ok(t[CA[k]], c.PrimitiveMiscCaps)) return D3DERR_UNSUPPORTEDCOLORARG;
        for (int k = 0; k < 3; k++) if (ao && rd(aop, k) && !arg_ok(t[AA[k]], c.PrimitiveMiscCaps)) return D3DERR_UNSUPPORTEDALPHAARG;
        uint32_t ra = t[D3DTSS_RESULTARG]; if (ra != D3DTA_CURRENT && (ra != D3DTA_TEMP || !(c.PrimitiveMiscCaps & D3DPMISCCAPS_TSSARGTEMP))) return D3DERR_UNSUPPORTEDCOLORARG;
        if (cop == D3DTOP_BUMPENVMAP || cop == D3DTOP_BUMPENVMAPLUMINANCE) { NxImg* im = img_of(s, i); if (!im || !cap(p, D3DUSAGE_QUERY_LEGACYBUMPMAP, im)) return D3DERR_WRONGTEXTUREFORMAT; }
    }
    return D3D_OK;
}
HRESULT targets(NxDeviceCore* d) {   // V4
    const NxDeviceState& s = d->cur; const NxSurfCore* c0 = nullptr; uint32_t w = 0, h = 0, sm = 1;
    for (uint32_t i = 0; i < NX_D3D_MAX_RTS; i++) {
        const NxSurfCore* r = nx_surf_core(s.rt[i]); if (!r) continue; const NxSub* b = nx_surf_sub(r); uint32_t n = r->img->samples ? r->img->samples : 1;
        if (!c0) { c0 = r; w = b->w; h = b->h; sm = n; } else if (b->w != w || b->h != h || n != sm) return D3DERR_CONFLICTINGRENDERSTATE;
    }
    const NxSurfCore* z = nx_surf_core(s.ds[0]);
    if (c0 && z && (s.rs[D3DRS_ZENABLE] || s.rs[D3DRS_ZWRITEENABLE] || s.rs[D3DRS_STENCILENABLE])) { const NxSub* b = nx_surf_sub(z); if (b->w != w || b->h != h || (z->img->samples ? z->img->samples : 1) != sm) return D3DERR_CONFLICTINGRENDERSTATE; }
    return D3D_OK;
}
}
HRESULT nx_m1_draw_legal(NxDeviceCore* d) {
    NxDevLife* L = nx_life(d); int p = (int)L->prof; D3DCAPS9 c; memset(&c, 0, sizeof c);
    if (!L->ex && L->state != NX_DEV_OK) return D3DERR_DEVICELOST;                                       // V0
    if (!d->cur.vdecl[0]) return D3DERR_INVALIDCALL;                                                      // V1
    if (nxenv_caps(p, L->cp.AdapterOrdinal, (uint32_t)L->cp.DeviceType, &c) != 0) return D3DERR_INVALIDCALL;
    HRESULT hr = samplers(d, c, p); if (hr != D3D_OK) return hr;
    if (!d->cur.ps[0] && (hr = stages(d, c, p)) != D3D_OK) return hr;
    return targets(d);
}
#define M1_VALIDATE(C) HRESULT STDMETHODCALLTYPE C::ValidateDevice(DWORD* n) { if (!n) return D3DERR_INVALIDCALL; HRESULT hr = nx_m1_draw_legal(this); if (hr == D3D_OK) *n = 1; return hr; }
M1_VALIDATE(NxDevice9) M1_VALIDATE(NxDevice9Ex)
