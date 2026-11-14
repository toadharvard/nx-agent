// StretchRect of both device classes. Sources: the Windows-validated tables of Wine 9.0 d3d9 tests visual.c (stretchrect_test error_condition_tests, test_depth_stencil_blit, test_multisample_stretch_rect),
// d3d9/device.c d3d9_device_StretchRect + wined3d_device_context_blt (rect checks, depth rules, filters), Mesa Nine device9.c NineDevice9_StretchRect (the plain-surface rules), MS StretchRect remarks.
// RULES (every violation = D3DERR_INVALIDCALL, checked in this order; one rule per bullet)
//  S1 both surfaces are ours and of this device; both pools DEFAULT.
//  S2 filter: NONE, POINT, LINEAR only, and POINT / LINEAR only when D3DCAPS9.StretchRectFilterCaps offers them (env table: nxenv_stretch_filter_ok). NONE and POINT are the point rule.
//  S3 depth-stencil: the destination has a depth format iff the source has one; then same format and same size, both rects absent or the whole surface, and no BeginScene .. EndScene (in_scene).
//     A depth copy is always POINT (the backend, nxgpu4.h NxOp_Blit).
//  S4 the destination is an offscreen plain surface, or has D3DUSAGE_RENDERTARGET / DEPTHSTENCIL (not a plain texture level); an offscreen plain destination needs an offscreen plain source.
//  S5 rects: absent = whole; present = non-empty, not flipped, inside the level, block aligned for compressed formats (wined3d_resource_check_box_dimensions; the Windows table rejects the flipped ones).
//  S6 scaling (different rect sizes): not between a surface and itself, destination not a plain surface, neither format compressed (Nine). Same sub-resource, unscaled: the rects must not overlap (MS).
//  S7 the formats convert: nxenv_check_conversion (env table: blit-capable source, blit-capable destination, equal formats always). Both surfaces have GPU storage, or neither (then the formats are equal
//     and the rect is copied in CPU memory; the table makes every pair with only one side CPU-only illegal - asserted by tests/l6/copy part a).
//  S8 neither sub-resource is locked (MS; wined3d WINEDDERR_SURFACEBUSY is D3DERR_INVALIDCALL on Windows).
//  S9 a multisampled destination is not supported by the backend (nxgpu4.h Blit: "a multisampled source is resolved first", a multisampled destination is BAD_CALL in gl4_blit.c): INVALIDCALL.
//     Windows accepts a single-sample -> multisampled StretchRect (Wine test_multisample_stretch_rect): REPORTED gap, needs a backend op (replicate to all samples).
//  S10 a compressed source or destination only copies (nxgpu4.h NxOp_Blit): the native formats must be equal (DXT2 -> DXT3 is a BC2 copy); DXT1 -> A8R8G8B8 is INVALIDCALL although the env table
//     converts it (Windows converts in the sampler): REPORTED gap, needs a backend decompress-on-blit.
//  A destination without storage (D3DFMT_NULL target) discards the copy: D3D_OK. A source without storage is INVALIDCALL (no contents).
// Effect: pending CPU writes of both images are uploaded first; NxOp_Blit (outside a pass); the destination sub-resource is then GPU-newer (nx_img_gpu_wrote: stale CPU copy, AUTOGENMIPMAP flag).
#include "copy_util.h"
#include "../gen/env_table.gen.h"

static bool full(const RECT* r, const NxSub* s) { return !r || (r->left == 0 && r->top == 0 && r->right == (LONG)s->w && r->bottom == (LONG)s->h); }
static bool valid_rect(const NxImg* im, uint32_t level, const RECT* r) { if (!r) return true; D3DBOX b = {(UINT)r->left, (UINT)r->top, (UINT)r->right, (UINT)r->bottom, 0, 1}; return r->left >= 0 && r->top >= 0 && nx_box_valid(im, level, &b); }
static HRESULT do_stretch(NxObj* dev, IDirect3DSurface9* ps, const RECT* sr, IDirect3DSurface9* pd, const RECT* dr, D3DTEXTUREFILTERTYPE filter) {
    NxSurfCore* s = nx_surf_core(ps); NxSurfCore* d = nx_surf_core(pd);
    if (!s || !d || s->dev != dev || d->dev != dev || s->img->pool != D3DPOOL_DEFAULT || d->img->pool != D3DPOOL_DEFAULT) return D3DERR_INVALIDCALL;                  // S1
    int p = nx_dev_profile(dev); NxImg* si = s->img; NxImg* di = d->img; const NxSub* ss = nx_surf_sub(s); NxSub* ds = di->at(d->level, d->face);
    if (!nxenv_stretch_filter_ok(p, (uint32_t)filter)) return D3DERR_INVALIDCALL;                                                                                        // S2
    bool zs = nx_img_is_depth(di), zsrc = nx_img_is_depth(si);
    if (zs != zsrc) return D3DERR_INVALIDCALL;                                                                                                                           // S3
    if (!nx_img_plain(di) && !(di->usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL))) return D3DERR_INVALIDCALL;                                                  // S4
    if (nx_img_plain(di) && !nx_img_plain(si)) return D3DERR_INVALIDCALL;
    if (!valid_rect(si, s->level, sr) || !valid_rect(di, d->level, dr)) return D3DERR_INVALIDCALL;                                                                       // S5
    if (zs && (nx_device_in_scene(dev) || !full(sr, ss) || !full(dr, ds) || ss->w != ds->w || ss->h != ds->h || si->fmt != di->fmt)) return D3DERR_INVALIDCALL;           // S3
    NxRc a = sr ? NxRc{sr->left, sr->top, sr->right, sr->bottom} : NxRc{0, 0, (int32_t)ss->w, (int32_t)ss->h}, b = dr ? NxRc{dr->left, dr->top, dr->right, dr->bottom} : NxRc{0, 0, (int32_t)ds->w, (int32_t)ds->h};
    bool scaled = (a.r - a.l) != (b.r - b.l) || (a.b - a.t) != (b.b - b.t), same = si == di && s->level == d->level && s->face == d->face;
    if (scaled && (same || nx_img_plain(di) || (si->ni.flags & NXFMT_COMPRESSED) || (di->ni.flags & NXFMT_COMPRESSED))) return D3DERR_INVALIDCALL;                       // S6
    if (same && !nx_rc_empty(nx_rc_and(a, b))) return D3DERR_INVALIDCALL;
    if (nxenv_check_conversion(p, 0, 1, si->fmt, di->fmt) != 0) return D3DERR_INVALIDCALL;                                                                               // S7
    bool sg = si->gpu != 0, dg = di->gpu != 0, dnull = nx_img_null(di), snull = nx_img_null(si);
    if (snull) return D3DERR_INVALIDCALL;
    if (!dnull && sg != dg && si->fmt != di->fmt) return D3DERR_INVALIDCALL;
    if (((si->ni.flags | di->ni.flags) & NXFMT_COMPRESSED) && si->ni.format != di->ni.format) return D3DERR_INVALIDCALL;                                                  // S10
    if (ss->locked || ds->locked) return D3DERR_INVALIDCALL;                                                                                                             // S8
    if (di->ms > 1 && !dnull) return D3DERR_INVALIDCALL;                                                                                                                 // S9
    if (dnull) return D3D_OK;
    if (sg && dg) {
        if (!si->upload() || !di->upload()) return E_OUTOFMEMORY;                                                                                                       // allocation failure: a conversion buffer
        if (!nxs_blit(nx_target(s), a.l, a.t, a.r - a.l, a.b - a.t, nx_target(d), b.l, b.t, b.r - b.l, b.b - b.t, (!zs && filter == D3DTEXF_LINEAR) ? 1u : 0u)) return D3DERR_OUTOFVIDEOMEMORY;   // glue_stream.h E1
        nx_img_gpu_wrote(di, d->level, d->face);
    } else if (!sg && !dg && ss->mem && ds->mem) nx_cpu_copy_rect(si, ss, a, di, ds, b.l, b.t);                                                                          // CPU-only formats: equal, unscaled
    else return D3DERR_INVALIDCALL;
    return D3D_OK;
}
#include "../gen/d9impl.gen.h"
#define STRETCH(C) HRESULT STDMETHODCALLTYPE C::StretchRect(IDirect3DSurface9* s, const RECT* sr, IDirect3DSurface9* d, const RECT* dr, D3DTEXTUREFILTERTYPE f) { return do_stretch(this, s, sr, d, dr, f); }
STRETCH(NxDevice9) STRETCH(NxDevice9Ex)
