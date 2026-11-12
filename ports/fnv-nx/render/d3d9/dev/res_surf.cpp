// Surfaces and volumes: GetDesc, LockRect / LockBox / Unlock. Sources: Wine 9.0 d3d9/surface.c (d3d9_surface_{GetDesc,LockRect,UnlockRect}), d3d9/volume.c (d3d9_volume_{GetDesc,LockBox,UnlockBox}),
// d3d9/device.c wined3dmapflags_from_d3dmapflags (usage 0 for surfaces: READ unless DISCARD / NOOVERWRITE, WRITE unless READONLY, neither -> WRITE), wined3d/device.c wined3d_device_context_map +
// sanitise_map_flags (access MAP_R / MAP_W else E_INVALIDARG -> D3DERR_INVALIDCALL; DISCARD acts only without READ on a DYNAMIC GPU-pool resource, with NOOVERWRITE it is dropped),
// wined3d/resource.c wined3d_resource_check_box_dimensions + resource_offset_map_pointer, wined3d/texture.c texture_resource_sub_resource_{map,unmap}.
// DECISION (doubt): an invalid rect / box (empty, flipped, outside the level, misaligned for blocks) is D3DERR_INVALIDCALL: the Windows 7 answer of Wine's tests/test_lockrect_invalid (column win7_result),
// not Windows XP / Wine, which accept it and return a pointer outside the level.
// UnlockRect of an unlocked sub-resource: D3D_OK for a level of a 2D texture, D3DERR_INVALIDCALL otherwise (d3d9_surface_UnlockRect WINEDDERR_NOTLOCKED). LockBox writes its outputs also on failure (null pBits, pitch 0).
// GPU readback: a read lock of a sub-resource the GPU wrote (nx_img_gpu_wrote) first refreshes the CPU copy through nxd3d_image_read, unless the lock is an effective DISCARD. MS: Lockable RT + MSAA is refused at creation.
// GetDC / ReleaseDC are left out: they need a GDI DIB section, which the backend ABI cannot express (README of dev/: open point).
#include "res_tex.h"
#include <fnvnx_render_hooks.h>
#include "../gen/d9impl.gen.h"
#include <string.h>
static const uint32_t USAGE_MASK = 0x10007bf0u | 3u;   // WINED3DUSAGE_MASK | RENDERTARGET | DEPTHSTENCIL (d3dusage_from_wined3dusage)
bool nx_box_valid(const NxImg* im, uint32_t level, const D3DBOX* b) {
    const NxSub* s = &im->sub[level];
    if (b->Left >= b->Right || b->Right > s->w || b->Top >= b->Bottom || b->Bottom > s->h || b->Front >= b->Back || b->Back > s->d) return false;
    if (im->tf.flags & NXTF_BLOCKS) {
        uint32_t wm = im->tf.bw - 1, hm = im->tf.bh - 1;
        if ((b->Left & wm) || (b->Top & hm) || ((b->Right & wm) && b->Right != s->w) || ((b->Bottom & hm) && b->Bottom != s->h)) return false;
    }
    return true;
}
HRESULT nx_img_lock(NxImg* im, uint32_t level, uint32_t face, const D3DBOX* box, DWORD flags, void** bits, uint32_t* row, uint32_t* slice) {
    NxSub* s = im->at(level, face);
    if (bits) *bits = nullptr; if (row) *row = 0; if (slice) *slice = 0;
    bool read = !(flags & (D3DLOCK_NOOVERWRITE | D3DLOCK_DISCARD)), write = !(flags & D3DLOCK_READONLY); if (!read && !write) write = true;
    if ((read && !(im->access & 4)) || (write && !(im->access & 8)) || !s->mem || !bits) return D3DERR_INVALIDCALL;
    bool discard = !read && (flags & D3DLOCK_DISCARD) && !(flags & D3DLOCK_NOOVERWRITE) && im->pool == D3DPOOL_DEFAULT && (im->usage & D3DUSAGE_DYNAMIC);
    D3DBOX whole = {0, 0, s->w, s->h, 0, s->d}; if (box && !nx_box_valid(im, level, box)) return D3DERR_INVALIDCALL; const D3DBOX* b = box ? box : &whole;
    if (s->locked) return D3DERR_INVALIDCALL;
    if (s->gpu_newer) { if (!discard && im->gpu) nxd3d_image_read(im->owner, im->gpu, level, face, s->mem, s->row, s->slice); s->gpu_newer = 0; }
    if (write) {
        if (!im->rec_dirty || level > 0) im->add_dirty(level, face, nullptr);                          // only level 0 records regions; elsewhere the whole level is re-uploaded
        else if (!(flags & D3DLOCK_NO_DIRTY_UPDATE)) { NxBox3 x = {b->Left, b->Top, b->Front, b->Right, b->Bottom, b->Back}; im->add_dirty(level, face, &x); }
    }
    const NxTexFmt& f = im->tf; bool blk = (f.flags & (NXTF_BLOCKS | NXTF_BROKEN)) == NXTF_BLOCKS;   // resource_offset_map_pointer
    *bits = s->mem + (size_t)b->Front * s->slice + (size_t)(blk ? b->Top / f.bh : b->Top) * s->row + (size_t)(blk ? b->Left / f.bw * f.bb : b->Left * f.bytes);
    if (row) *row = s->row; if (slice) *slice = s->slice;
    s->locked = 1; s->lock_w = write; return D3D_OK;
}
HRESULT nx_img_unlock(NxImg* im, uint32_t level, uint32_t face, bool* was) {
    NxSub* s = im->at(level, face); *was = s->locked != 0; if (!s->locked) return D3D_OK;
    s->locked = 0; if (im->kind != NXK_SURFACE && im->kind != NXK_VOLUME && im->autogen) im->mip_dirty = true;   // d3d9_texture_flag_auto_gen_mipmap on every successful unmap
    return D3D_OK;
}
// ---- surfaces
NxSurfCore::~NxSurfCore() { if (dc) nxd3d_dc_release(dc); delete own; }   // a DC still handed out (GetDC without ReleaseDC) goes with the surface (wined3d_texture_destroy_dc)
HRESULT NxSurfCore::desc(D3DSURFACE_DESC* d) {
    if (!d) return D3DERR_INVALIDCALL;
    const NxSub* s = img->at(level, face);
    d->Format = (D3DFORMAT)img->fmt; d->Type = D3DRTYPE_SURFACE; d->Usage = img->usage & USAGE_MASK; d->Pool = (D3DPOOL)img->pool; d->MultiSampleType = (D3DMULTISAMPLE_TYPE)img->ms; d->MultiSampleQuality = img->msq; d->Width = s->w; d->Height = s->h;
    return D3D_OK;
}
HRESULT NxSurfCore::lockRect(D3DLOCKED_RECT* lr, const RECT* r, DWORD f) {
    if (!lr) return D3DERR_INVALIDCALL;
    D3DBOX b = {}; if (r) { b.Left = r->left; b.Top = r->top; b.Right = r->right; b.Bottom = r->bottom; b.Front = 0; b.Back = 1; }
    void* p; uint32_t row, slice; HRESULT hr = nx_img_lock(img, level, face, r ? &b : nullptr, f, &p, &row, &slice);
    if (hr == D3D_OK) { lr->pBits = p; lr->Pitch = row; }                                                  // outputs are written iff the call succeeds (d3d9_surface_LockRect)
    return hr;
}
HRESULT NxSurfCore::unlockRect() {
    bool was; nx_img_unlock(img, level, face, &was);
    if (was) return D3D_OK;
    return container && container->ci->rtype == D3DRTYPE_TEXTURE ? D3D_OK : D3DERR_INVALIDCALL;
}
HRESULT STDMETHODCALLTYPE NxSurface9::GetDesc(D3DSURFACE_DESC* d) { return desc(d); }
HRESULT STDMETHODCALLTYPE NxSurface9::LockRect(D3DLOCKED_RECT* lr, const RECT* r, DWORD f) { return lockRect(lr, r, f); }
HRESULT STDMETHODCALLTYPE NxSurface9::UnlockRect() { return unlockRect(); }
// ---- volumes
HRESULT NxVolCore::desc(D3DVOLUME_DESC* d) {
    if (!d) return D3DERR_INVALIDCALL;
    const NxSub* s = img->at(level, 0);
    d->Format = (D3DFORMAT)img->fmt; d->Type = D3DRTYPE_VOLUME; d->Usage = img->usage & USAGE_MASK; d->Pool = (D3DPOOL)img->pool; d->Width = s->w; d->Height = s->h; d->Depth = s->d; return D3D_OK;
}
HRESULT NxVolCore::lockBox(D3DLOCKED_BOX* lb, const D3DBOX* b, DWORD f) {
    if (!lb) return D3DERR_INVALIDCALL;
    void* p; uint32_t row, slice; HRESULT hr = nx_img_lock(img, level, 0, b, f, &p, &row, &slice);
    lb->pBits = hr == D3D_OK ? p : nullptr; lb->RowPitch = row; lb->SlicePitch = slice; return hr;          // LockBox writes the outputs also on failure
}
HRESULT NxVolCore::unlockBox() { bool was; nx_img_unlock(img, level, 0, &was); return was ? D3D_OK : D3DERR_INVALIDCALL; }
HRESULT STDMETHODCALLTYPE NxVolume9::GetDesc(D3DVOLUME_DESC* d) { return desc(d); }
HRESULT STDMETHODCALLTYPE NxVolume9::LockBox(D3DLOCKED_BOX* lb, const D3DBOX* b, DWORD f) { return lockBox(lb, b, f); }
HRESULT STDMETHODCALLTYPE NxVolume9::UnlockBox() { return unlockBox(); }
