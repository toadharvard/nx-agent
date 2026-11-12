// UpdateSurface, UpdateTexture, GetRenderTargetData of both device classes. Sources: Wine 9.0 d3d9/device.c d3d9_device_{UpdateSurface, UpdateTexture, GetRenderTargetData}, wined3d/device.c
// wined3d_device_update_texture (the level matching and dirty region rules) / wined3d_device_context_copy_sub_resource_region, Mesa Nine device9.c NineDevice9_{UpdateSurface, UpdateTexture,
// GetRenderTargetData} (the checks Windows makes), Wine tests device.c test_update_texture_pool / test_mipmap_lock and visual.c (dirty regions, block sizes) written against Windows.
// UPDATESURFACE (U1-U6, INVALIDCALL): U1 both ours, of this device, non-null; source pool SYSTEMMEM, destination pool DEFAULT. U2 neither multisampled; neither sub-resource locked (a lock of ANOTHER level
//  of the texture does not matter: test_mipmap_lock). U3 same D3DFORMAT, the destination is no depth format. U4 source rect (null = whole) non-empty and inside; destination point >= 0 and rect + point
//  inside the destination. U5 compressed formats: point and rect origin aligned to the block, size a multiple of the block unless it is the whole of both surfaces (Nine). U6 ATI1 / ATI2 (BROKEN_PITCH, no
//  sub-rectangle layout in D3D memory): whole surface only. Effect: pending CPU writes of the destination first; nxd3d_image_update of the rect from the source's CPU copy at the point (converted for
//  NXFMT_CONVERT formats); the destination is GPU-newer afterwards (AUTOGENMIPMAP flag). A destination without GPU image (a format without native layout) gets the rect in its CPU memory.
// UPDATETEXTURE (T1-T6, INVALIDCALL): T1 both ours, non-null, different objects, of this device. T2 source SYSTEMMEM, destination DEFAULT (test_update_texture_pool: every other pool pair fails), same
//  resource type; a source with AUTOGENMIPMAP needs a destination with it (Nine). T3 same D3DFORMAT; same face count. T4 level matching (wined3d_device_update_texture): skip = the number of halvings of the
//  source's largest dimension until it is not larger than the destination's; the source level `skip` must have exactly the destination's size, else INVALIDCALL (Windows copies "like a memcpy": Wine test
//  todo_wine, not implemented); level j of the destination is level j + skip of the source for j < min(source levels - skip, destination levels). T5 dirty regions (the model of res_tex.cpp: ONE union
//  box per face, recorded for level 0 only): a face whose level-0 box is clean copies nothing; else level j copies the box shifted to that level exactly as wined3d does (left >>= l, right =
//  min(ceil(right / 2^l), level size), front / back likewise), widened to whole blocks for compressed formats (Nine fit_rect_format_inclusive); ATI1 / ATI2 copy the whole level. D3DLOCK_NO_DIRTY_UPDATE /
//  READONLY locks recorded nothing (res_surf.cpp), so they are not copied (Wine visual.c). T6 afterwards every dirty flag of the source is clear (wined3d_texture_clear_dirty_regions) and every
//  copied destination sub-resource is GPU-newer (d3d9_texture_flag_auto_gen_mipmap).
// GETRENDERTARGETDATA (G1-G4, INVALIDCALL): G1 both ours, non-null, of this device; source pool DEFAULT and not a depth format (a depth surface cannot be read as colour: wined3d depth / colour mismatch),
//  destination pool SYSTEMMEM and not locked. G2 the source is not multisampled (Wine). G3 same size and same D3DFORMAT (MS; Nine checks the size). G4 the source has storage (not D3DFMT_NULL). Effect:
//  pending CPU writes of the source first, then nxd3d_image_read of the whole sub-resource (R3 of glue_stream.h: observes every earlier op) into the destination's CPU copy (a CPU-only source: memcpy);
//  the destination records a dirty region (wined3d_device_context_blt: dirty region of the destination box).
#include "copy_util.h"
#include "../gen/env_table.gen.h"
#include <string.h>

static bool ours(NxObj* dev, const NxSurfCore* s) { return s && s->dev == dev; }
static HRESULT do_update_surface(NxObj* dev, IDirect3DSurface9* ps, const RECT* sr, IDirect3DSurface9* pd, const POINT* pt) {
    NxSurfCore* s = nx_surf_core(ps); NxSurfCore* d = nx_surf_core(pd);
    if (!ours(dev, s) || !ours(dev, d) || d->img->pool != D3DPOOL_DEFAULT || s->img->pool != D3DPOOL_SYSTEMMEM) return D3DERR_INVALIDCALL;                          // U1
    NxImg* si = s->img; NxImg* di = d->img; const NxSub* ss = nx_surf_sub(s); NxSub* ds = di->at(d->level, d->face);
    if (si->ms > 1 || di->ms > 1 || ss->locked || ds->locked) return D3DERR_INVALIDCALL;                                                                            // U2
    if (si->fmt != di->fmt || nx_img_is_depth(di)) return D3DERR_INVALIDCALL;                                                                                       // U3
    int32_t w = sr ? sr->right - sr->left : (int32_t)ss->w, h = sr ? sr->bottom - sr->top : (int32_t)ss->h, sx = sr ? sr->left : 0, sy = sr ? sr->top : 0, dx = pt ? pt->x : 0, dy = pt ? pt->y : 0;
    if (sr && (sr->left < 0 || sr->top < 0 || w <= 0 || h <= 0 || sr->right > (LONG)ss->w || sr->bottom > (LONG)ss->h)) return D3DERR_INVALIDCALL;                      // U4
    if (dx < 0 || dy < 0 || dx + w > (int32_t)ds->w || dy + h > (int32_t)ds->h) return D3DERR_INVALIDCALL;
    const NxTexFmt& f = si->tf;
    if (f.flags & NXTF_BLOCKS) {                                                                                                                                      // U5
        int32_t bw = f.bw, bh = f.bh; bool wholeboth = w == (int32_t)ss->w && w == (int32_t)ds->w && h == (int32_t)ss->h && h == (int32_t)ds->h;
        if ((dx % bw) || (dy % bh) || (sx % bw) || (sy % bh) || (!wholeboth && ((w % bw) || (h % bh)))) return D3DERR_INVALIDCALL;
    }
    if ((f.flags & NXTF_BROKEN) && !(w == (int32_t)ss->w && h == (int32_t)ss->h && w == (int32_t)ds->w && h == (int32_t)ds->h)) return D3DERR_INVALIDCALL;              // U6
    if (di->gpu) {
        if (!di->upload()) return E_OUTOFMEMORY;
        bool blk = (f.flags & NXTF_BLOCKS) != 0; NxBox3 b = {(uint32_t)dx, (uint32_t)dy, 0, (uint32_t)(dx + w), (uint32_t)(dy + h), 1};
        bool ok = (f.flags & NXTF_BROKEN) ? nx_img_push(di, d->level, d->face, ss, &b)
                : nx_img_push_at(di, d->level, d->face, &b, ss->mem + (size_t)(blk ? sy / f.bh : sy) * ss->row + (size_t)(blk ? sx / f.bw * f.bb : sx * f.bytes), ss->row, ss->slice);
        if (!ok) return E_OUTOFMEMORY;                                                                                                                                  // a conversion buffer; the destination is unchanged
        nx_img_gpu_wrote(di, d->level, d->face);
    } else if (ds->mem && ss->mem) nx_cpu_copy_rect(si, ss, {sx, sy, sx + w, sy + h}, di, ds, dx, dy);
    return D3D_OK;
}
static uint32_t dimv(uint32_t v, uint32_t l) { v = l > 31 ? 0 : v >> l; return v ? v : 1; }
static NxImg* tex_img(NxObj* dev, IDirect3DBaseTexture9* p, uint8_t* rtype) {
    NxObj* o = nx_obj_of((IUnknown*)p); if (!o || o->dev != dev) return nullptr; uint8_t t = o->ci->rtype;
    if (t != D3DRTYPE_TEXTURE && t != D3DRTYPE_CUBETEXTURE && t != D3DRTYPE_VOLUMETEXTURE) return nullptr;
    *rtype = t; return static_cast<NxTexCore*>(o)->img;
}
static HRESULT do_update_texture(NxObj* dev, IDirect3DBaseTexture9* ps, IDirect3DBaseTexture9* pd) {
    uint8_t ts, td; NxImg* si = tex_img(dev, ps, &ts); NxImg* di = tex_img(dev, pd, &td);
    if (!si || !di || (void*)ps == (void*)pd) return D3DERR_INVALIDCALL;                                                                                            // T1
    if (si->pool != D3DPOOL_SYSTEMMEM || di->pool != D3DPOOL_DEFAULT || ts != td || ((si->usage & D3DUSAGE_AUTOGENMIPMAP) && !(di->usage & D3DUSAGE_AUTOGENMIPMAP))) return D3DERR_INVALIDCALL;   // T2
    if (si->fmt != di->fmt || si->faces != di->faces) return D3DERR_INVALIDCALL;                                                                                   // T3
    uint32_t ssz = si->w > si->h ? si->w : si->h, dsz = di->w > di->h ? di->w : di->h, skip = 0; if (si->d > ssz) ssz = si->d; if (di->d > dsz) dsz = di->d;
    while (ssz > dsz) { ssz >>= 1; skip++; }                                                                                                                         // T4
    if (dimv(si->w, skip) != di->w || dimv(si->h, skip) != di->h || dimv(si->d, skip) != di->d || skip >= si->levels) return D3DERR_INVALIDCALL;
    uint32_t count = si->levels - skip < di->levels ? si->levels - skip : di->levels; const NxTexFmt& f = si->tf; bool blk = (f.flags & NXTF_BLOCKS) != 0;
    if (di->gpu) {
        if (!di->upload()) return E_OUTOFMEMORY;
        for (uint32_t face = 0; face < si->faces; face++) {                                                                                                          // T5
            const NxSub* s0 = si->at(0, face); if (!s0->dirty) continue;
            for (uint32_t j = 0; j < count; j++) {
                uint32_t l = j + skip; const NxSub* sl = si->at(l, face); NxBox3 b = s0->dbox;
                if (l) {
                    uint32_t r = (b.r + (1u << l) - 1) >> l, bo = (b.b + (1u << l) - 1) >> l, k = (b.k + (1u << l) - 1) >> l; b.l >>= l; b.t >>= l; b.f >>= l;
                    b.r = r < sl->w ? r : sl->w; b.b = bo < sl->h ? bo : sl->h; b.k = k < sl->d ? k : sl->d;
                }
                if (f.flags & NXTF_BROKEN) b = {0, 0, 0, sl->w, sl->h, sl->d};
                else if (blk) { b.l = b.l / f.bw * f.bw; b.t = b.t / f.bh * f.bh; b.r = ((b.r + f.bw - 1) / f.bw) * f.bw; b.b = ((b.b + f.bh - 1) / f.bh) * f.bh; if (b.r > sl->w) b.r = sl->w; if (b.b > sl->h) b.b = sl->h; }
                if (b.l >= b.r || b.t >= b.b || b.f >= b.k || !sl->mem) continue;
                if (!nx_img_push(di, j, face, sl, &b)) return E_OUTOFMEMORY;   // the source keeps its dirty regions (T6 is not reached): the same call again completes the copy
                nx_img_gpu_wrote(di, j, face);
            }
        }
    }
    for (uint32_t i = 0; i < si->levels * si->faces; i++) si->sub[i].dirty = 0;                                                                                      // T6
    return D3D_OK;
}
static HRESULT do_rt_data(NxObj* dev, IDirect3DSurface9* prt, IDirect3DSurface9* pd) {
    NxSurfCore* s = nx_surf_core(prt); NxSurfCore* d = nx_surf_core(pd);
    if (!ours(dev, s) || !ours(dev, d) || s->img->pool != D3DPOOL_DEFAULT || d->img->pool != D3DPOOL_SYSTEMMEM || nx_img_is_depth(s->img) || nx_surf_sub(d)->locked) return D3DERR_INVALIDCALL;   // G1
    NxImg* si = s->img; NxImg* di = d->img; const NxSub* ss = nx_surf_sub(s); NxSub* ds = di->at(d->level, d->face);
    if (si->ms > 1) return D3DERR_INVALIDCALL;                                                                                                                       // G2
    if (ss->w != ds->w || ss->h != ds->h || si->fmt != di->fmt || nx_img_null(si)) return D3DERR_INVALIDCALL;                                                       // G3, G4
    if (!ds->mem) return D3DERR_INVALIDCALL;
    if (si->gpu) { if (!si->upload()) return E_OUTOFMEMORY; nxd3d_image_read(si->owner, si->gpu, s->level, s->face, ds->mem, ds->row, ds->slice); }
    else if (ss->mem) nx_cpu_copy_rect(si, ss, {0, 0, (int32_t)ss->w, (int32_t)ss->h}, di, ds, 0, 0);
    else return D3DERR_INVALIDCALL;
    if (di->rec_dirty) { if (d->level == 0) { NxBox3 b = {0, 0, 0, ds->w, ds->h, 1}; di->add_dirty(0, d->face, &b); } else di->add_dirty(d->level, d->face, nullptr); }
    return D3D_OK;
}
#include "../gen/d9impl.gen.h"
#define UPDATE(C) \
    HRESULT STDMETHODCALLTYPE C::UpdateSurface(IDirect3DSurface9* s, const RECT* r, IDirect3DSurface9* d, const POINT* p) { return do_update_surface(this, s, r, d, p); } \
    HRESULT STDMETHODCALLTYPE C::UpdateTexture(IDirect3DBaseTexture9* s, IDirect3DBaseTexture9* d) { return do_update_texture(this, s, d); } \
    HRESULT STDMETHODCALLTYPE C::GetRenderTargetData(IDirect3DSurface9* s, IDirect3DSurface9* d) { return do_rt_data(this, s, d); }
UPDATE(NxDevice9) UPDATE(NxDevice9Ex)
