// Clear and ColorFill of both device classes. Sources: Wine 9.0 d3d9/device.c d3d9_device_Clear / d3d9_device_ColorFill, wined3d/device.c wined3d_device_clear (+ wined3d_device_context_clear_rendertarget_view),
// wined3d/cs.c wined3d_cs_emit_clear (the rectangle rule), wined3d/surface.c cpu_blitter_clear; Wine d3d9 tests visual.c (clear_test, test_depth_stencil_blit) written against Windows.
// CLEAR RULES (R-C1 .. R-C6)
//  R-C1 rects: count with a null array = count 0; a non-null array with count 0 = nothing is cleared, D3D_OK (wined3d_device_clear). Flags outside TARGET | ZBUFFER | STENCIL are ignored.
//  R-C2 ZBUFFER / STENCIL without a depth-stencil surface = D3DERR_INVALIDCALL; TARGET together with ZBUFFER / STENCIL and a depth-stencil surface smaller than render target 0 = D3D_OK, nothing cleared.
//  R-C3 the clipping rectangle is the VIEWPORT (x, y, width, height), intersected with the SCISSOR rectangle iff D3DRS_SCISSORTESTENABLE (wined3d_cs_emit_clear: draw_rect). Every clear rect is intersected with
//       it, then with each target's size; empty results clear nothing. No rects = the clipping rectangle itself. (Source: wined3d; MS says "clear the viewport rectangles": the same.)
//  R-C4 TARGET clears render targets 0-3 (all four simultaneous targets), each in a pass of its own (every target of a backend pass must have one size; D3D9 only requires the same size of colour
//       targets, not of the depth surface); then ZBUFFER / STENCIL in a depth-only pass. A target without storage (D3DFMT_NULL) has nothing to clear.
//  R-C5 colour: D3DCOLOR 8 bit per channel -> the format's clear value (nx_d3d9_clear_rgba8: A8 writes alpha only, L8 red only, ...); under D3DRS_SRGBWRITEENABLE the colour is taken as linear and
//       written through the sRGB view when the format has one (NxClear_Srgb). z is clamped to [0, 1] (NaN -> 0), stencil keeps its low 8 bits; a depth-only format ignores STENCIL, a format without
//       depth ignores ZBUFFER (the flag is cut by the format's capability, not an error: wined3d only logs it).
//  R-C6 pending CPU writes of a target (lockable images) are uploaded first (order); afterwards the CPU copy of every cleared sub-resource is stale (nx_img_gpu_wrote).
// ColorFill (d3d9_device_ColorFill): surface must be ours and of this device, pool DEFAULT (no CPU access), not a texture level without D3DUSAGE_RENDERTARGET, not a depth-stencil surface; the format must be
// colour-renderable per the env table (CheckDeviceFormat RENDERTARGET) - a plain surface of any other format has no fill; rect = whole when null, else a valid box of the level (check_box_dimensions:
// non-empty, inside, block aligned) or INVALIDCALL; the colour is not affected by SRGBWRITEENABLE (the view is the plain one).
#include "copy_util.h"
#include "../gen/env_table.gen.h"
#include <math.h>

static NxRc whole(const NxSub* s) { return {0, 0, (int32_t)s->w, (int32_t)s->h}; }
static float clamp01(float z) { return z != z ? 0.0f : z < 0.0f ? 0.0f : z > 1.0f ? 1.0f : z; }
// Allocation failure (README "Allocation failure"): nothing here allocates. Clear takes its rectangles from the application's array one at a time (no copy of the list); a flush of pending CPU writes that cannot get
// its conversion buffer = E_OUTOFMEMORY, a stream that cannot take the ops (glue_stream.h E1) = D3DERR_OUTOFVIDEOMEMORY; both leave the targets as they were up to the last op written.
template <class RectAt> static HRESULT fill(NxSurfCore* sc, uint32_t n, RectAt rect_at, uint32_t flags, uint32_t rgba, float z, uint32_t stencil) {
    NxImg* im = sc->img; if (!im->gpu) return D3D_OK;
    if (!im->upload()) return E_OUTOFMEMORY;
    NxTarget t = nx_target(sc); const NxSub* sub = nx_surf_sub(sc); bool any = false;
    for (uint32_t i = 0; i < n; i++) {
        NxRc c = nx_rc_and(rect_at(i), whole(sub)); if (nx_rc_empty(c)) continue;
        if (!any) { if (!((flags & NxClear_Color) ? nxs_pass_begin(1, &t, nullptr) : nxs_pass_begin(0, nullptr, &t))) return D3DERR_OUTOFVIDEOMEMORY; any = true; }
        if (!nxs_clear(flags, rgba, z, stencil, (uint32_t)c.l, (uint32_t)c.t, (uint32_t)(c.r - c.l), (uint32_t)(c.b - c.t))) { nxs_pass_end(); return D3DERR_OUTOFVIDEOMEMORY; }
    }
    if (any) { nxs_pass_end(); nx_img_gpu_wrote(im, sc->level, sc->face); }
    return D3D_OK;
}
static HRESULT do_clear(NxDeviceCore* d, DWORD count, const D3DRECT* rects, DWORD flags, D3DCOLOR color, float z, DWORD stencil) {
    if (count && !rects) count = 0;
    if (!count && rects) return D3D_OK;                                                                                     // R-C1
    flags &= D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER | D3DCLEAR_STENCIL;
    NxSurfCore* ds = nx_surf_core(d->cur.ds[0]); NxSurfCore* rt0 = nx_surf_core(d->cur.rt[0]);
    if (flags & (D3DCLEAR_ZBUFFER | D3DCLEAR_STENCIL)) {                                                                    // R-C2
        if (!ds) return D3DERR_INVALIDCALL;
        if ((flags & D3DCLEAR_TARGET) && rt0) { const NxSub* a = nx_surf_sub(ds); const NxSub* b = nx_surf_sub(rt0); if (a->w < b->w || a->h < b->h) return D3D_OK; }
    }
    const D3DVIEWPORT9& vp = d->cur.viewport[0];
    NxRc draw = {(int32_t)vp.X, (int32_t)vp.Y, (int32_t)(vp.X + vp.Width), (int32_t)(vp.Y + vp.Height)};                 // R-C3
    if (d->cur.rs[D3DRS_SCISSORTESTENABLE]) { const RECT& s = d->cur.scissor[0]; draw = nx_rc_and(draw, {s.left, s.top, s.right, s.bottom}); }
    uint32_t n = count ? count : 1;   // no rects = the clipping rectangle itself
    auto rect_at = [&](uint32_t i) { NxRc r = count ? NxRc{rects[i].x1, rects[i].y1, rects[i].x2, rects[i].y2} : draw; return nx_rc_and(r, draw); };
    if (flags & D3DCLEAR_TARGET) {                                                                                              // R-C4
        for (uint32_t i = 0; i < NX_D3D_MAX_RTS; i++) {
            NxSurfCore* t = nx_surf_core(d->cur.rt[i]); if (!t || nx_img_null(t->img)) continue;
            uint32_t fl = NxClear_Color | ((d->cur.rs[D3DRS_SRGBWRITEENABLE] && (t->img->ni.flags & NXFMT_SRGB)) ? NxClear_Srgb : 0);   // R-C5
            HRESULT hr = fill(t, n, rect_at, fl, nx_d3d9_clear_rgba8(t->img->fmt, color), 0.0f, 0); if (FAILED(hr)) return hr;
        }
    }
    if (ds) {
        uint32_t fl = ((flags & D3DCLEAR_ZBUFFER) && nx_img_is_depth(ds->img) ? NxClear_Depth : 0) | ((flags & D3DCLEAR_STENCIL) && (ds->img->ni.flags & NXFMT_STENCIL) ? NxClear_Stencil : 0);
        if (fl) { HRESULT hr = fill(ds, n, rect_at, fl, 0, clamp01(z), stencil & 0xFF); if (FAILED(hr)) return hr; }
    }
    return D3D_OK;
}
static HRESULT do_fill(NxObj* dev, IDirect3DSurface9* surface, const RECT* rect, D3DCOLOR color) {
    NxSurfCore* sc = nx_surf_core(surface);
    if (!sc || sc->dev != dev) return D3DERR_INVALIDCALL;
    NxImg* im = sc->img;
    if (im->pool != D3DPOOL_DEFAULT) return D3DERR_INVALIDCALL;                                                           // access & CPU
    if (im->kind != NXK_SURFACE && !(im->usage & D3DUSAGE_RENDERTARGET)) return D3DERR_INVALIDCALL;                       // a non-RT texture level
    if (im->usage & D3DUSAGE_DEPTHSTENCIL) return D3DERR_INVALIDCALL;
    const NxSub* s = nx_surf_sub(sc);
    if (rect) { D3DBOX b = {(UINT)rect->left, (UINT)rect->top, (UINT)rect->right, (UINT)rect->bottom, 0, 1}; if (rect->left < 0 || rect->top < 0 || !nx_box_valid(im, sc->level, &b)) return D3DERR_INVALIDCALL; }
    if (nxenv_check_device_format(nx_dev_profile(dev), 0, 1, 22, D3DUSAGE_RENDERTARGET, D3DRTYPE_SURFACE, im->fmt) != 0) return D3DERR_INVALIDCALL;   // colour-renderable per the env table
    if (nx_img_null(im)) return D3D_OK;                                                                                   // nothing to fill
    NxRc r = rect ? NxRc{rect->left, rect->top, rect->right, rect->bottom} : whole(s);
    return fill(sc, 1, [&](uint32_t) { return r; }, NxClear_Color, nx_d3d9_clear_rgba8(im->fmt, color), 0.0f, 0);
}
#define CLEAR_METHODS(C) \
    HRESULT STDMETHODCALLTYPE C::Clear(DWORD n, const D3DRECT* r, DWORD f, D3DCOLOR c, float z, DWORD s) { return do_clear(this, n, r, f, c, z, s); } \
    HRESULT STDMETHODCALLTYPE C::ColorFill(IDirect3DSurface9* s, const RECT* r, D3DCOLOR c) { return do_fill(this, s, r, c); }
#include "../gen/d9impl.gen.h"
CLEAR_METHODS(NxDevice9) CLEAR_METHODS(NxDevice9Ex)
