// Texture containers (2D / cube / volume) and the image storage shared with surfaces. Sources: Wine 9.0 d3d9/texture.c (d3d9_texture_{2d,cube,3d}_*, d3d9_texture_init, d3d9_texture_gen_auto_mipmap),
// wined3d/texture.c (wined3d_texture_add_dirty_region, wined3d_texture_get_sub_resource_desc, texture_resource_sub_resource_map: dirty regions only for level 0), wined3d/stateblock.c
// (wined3d_stateblock_set_texture_lod: ignored unless managed, clamped to level_count - 1), MS IDirect3DBaseTexture9 pages.
// R4 (README): level / face objects are created with the texture (identity: GetSurfaceLevel twice = same pointer), public 0, their refcount is the texture's.
// Dirty model: MANAGED / SYSTEMMEM record regions (initially the whole image, wined3d_texture_init), level 0 as a union box per face, levels > 0 whole; DEFAULT lockable: any write = whole level.
// A flush (`upload`, called by the draw path, PreLoad, GenerateMipSubLevels) sends one nxd3d_image_update per dirty sub-resource (face-major, level order). DEFAULT images are zero-uploaded at creation
// (nxgpu4.h: the device writes every level it creates); MANAGED ones get their zeros by the initial whole-image dirty region. Formats with NXFMT_CONVERT go through nx_d3d9_convert per box.
// Allocation failure (README "Allocation failure"): NxImg::init = E_OUTOFMEMORY for the CPU copies and the conversion buffers (wined3d_texture_init / texture_resource_sub_resource_map: E_OUTOFMEMORY), D3DERR_OUTOFVIDEOMEMORY
// when the backend refuses the image (wined3d resource_init "Out of adapter memory"). The failed NxImg owns what it made; its destructor returns it. upload() / sync() are all or nothing per sub-resource: a sub-resource
// whose push failed stays dirty (a later flush retries it), every earlier one is clean; they return false for it.
#include "res_tex.h"
#include "../gen/d9impl.gen.h"
#include <string.h>
static uint32_t dim(uint32_t v, uint32_t l) { v = l > 31 ? 0 : v >> l; return v ? v : 1; }
NxImg::~NxImg() {
    if (gpu) nxd3d_image_destroy(owner, gpu);
    if (!ext) nxd3d_free(raw);
    nxd3d_free(sub);
}
HRESULT NxImg::init(NxObj* own, uint32_t k, uint32_t w_, uint32_t h_, uint32_t d_, uint32_t lv, uint32_t fc, uint32_t f, uint32_t u, uint32_t p, uint32_t acc, bool ag, void* um,
                    uint32_t ms_, uint32_t msq_, uint32_t smp, uint32_t bind) {
    owner = own; kind = k; w = w_; h = h_; d = d_; levels = lv; faces = fc; fmt = f; usage = u; pool = p; access = acc; ms = ms_; msq = msq_; samples = smp; autogen = (u & D3DUSAGE_AUTOGENMIPMAP) != 0;
    nx_texfmt(f, &tf); ni = nx_d3d9_format(f);
    uint32_t mx = w > h ? w : h; if (d > mx) mx = d; uint32_t chain = 1; while (mx >>= 1) chain++;
    nlev = ag ? chain : lv; filter = autogen ? D3DTEXF_LINEAR : D3DTEXF_NONE; lod = 0;
    rec_dirty = p == D3DPOOL_MANAGED || p == D3DPOOL_SYSTEMMEM;
    bool cpu = (acc & 12) || p != D3DPOOL_DEFAULT;                                 // CPU copy: lockable (MAP_R|MAP_W) and every non-DEFAULT pool
    sub = (NxSub*)nxd3d_alloc(sizeof(NxSub) * lv * fc); if (!sub) return E_OUTOFMEMORY;
    memset(sub, 0, sizeof(NxSub) * lv * fc);
    uint64_t tot = 0;
    for (uint32_t fi = 0; fi < fc; fi++) for (uint32_t l = 0; l < lv; l++) {
        NxSub* s = at(l, fi); s->w = dim(w, l); s->h = dim(h, l); s->d = dim(d, l);
        s->size = (uint32_t)nx_tex_size(tf, s->w, s->h, s->d); s->store = (uint32_t)nx_tex_store(tf, s->w, s->h, s->d);
        if (tf.flags & NXTF_BROKEN) { s->row = s->w * tf.bytes; s->slice = s->h * s->row; } else nx_tex_pitch(tf, s->w, s->h, &s->row, &s->slice);   // texture_resource_sub_resource_get_map_pitch
        tot += (s->store + 15u) & ~15u;
    }
    if (cpu) {
        if (um) { raw = (uint8_t*)um; ext = true; sub[0].mem = raw; }
        else {
            raw = (uint8_t*)nxd3d_alloc((size_t)tot + 15); if (!raw) return E_OUTOFMEMORY;
            memset(raw, 0, (size_t)tot + 15);
            uint8_t* c = (uint8_t*)(((uintptr_t)raw + 15) & ~(uintptr_t)15);
            for (uint32_t i = 0; i < lv * fc; i++) { sub[i].mem = c; c += (sub[i].store + 15u) & ~15u; }
        }
    }
    if (ni.format != NxFmt_None && (p == D3DPOOL_DEFAULT || p == D3DPOOL_MANAGED)) {
        NxImageDesc4 g; memset(&g, 0, sizeof g);
        g.width = (uint16_t)w; g.height = (uint16_t)h; g.depth = (uint16_t)d; g.type = k == NXK_CUBE ? NxImgType_Cube : k == NXK_VOLUME ? NxImgType_3D : NxImgType_2D;
        g.format = ni.format; g.swizzle = NXIMG_SWIZZLE(ni.swizzle[0], ni.swizzle[1], ni.swizzle[2], ni.swizzle[3]);   // the row's view swizzle: plain sampling returns the D3D9 (r,g,b,a)
        g.mips = (uint8_t)nlev; g.samples = (uint8_t)samples; g.flags = (uint8_t)((((u | bind) & D3DUSAGE_RENDERTARGET) || (k == NXK_SURFACE && (ni.flags & NXFMT_RENDER)) ? NxImg_RenderTarget : 0) | ((u | bind) & D3DUSAGE_DEPTHSTENCIL ? NxImg_Depth : 0) | (k == NXK_CUBE ? NxImg_Cube : 0));
        gpu = nxd3d_image_create(own, &g); if (!gpu) return D3DERR_OUTOFVIDEOMEMORY;   // the backend refused: out of video memory / image ids / stream memory
    }
    for (uint32_t fi = 0; fi < fc; fi++) for (uint32_t l = 0; l < nlev; l++) {
        NxBox3 whole = {0, 0, 0, dim(w, l), dim(h, l), dim(d, l)};
        if (l < lv && rec_dirty) add_dirty(l, fi, nullptr);                          // wined3d_texture_init: the whole image is dirty
        else if (gpu && samples <= 1 && (p == D3DPOOL_DEFAULT || l >= lv)) {   // a multisampled image takes no ImageUpdate (nxgpu4.h): its contents are undefined until a pass writes it
                                      // zero upload: D3D-layout zero texels through the same path as any upload
            uint32_t sz = (uint32_t)nx_tex_store(tf, whole.r, whole.b, whole.k); uint8_t* z = (uint8_t*)nxd3d_alloc(sz ? sz : 1); if (!z) return E_OUTOFMEMORY;
            memset(z, 0, sz ? sz : 1);
            NxSub t = {}; t.mem = z; t.w = whole.r; t.h = whole.b; t.d = whole.k; if (tf.flags & NXTF_BROKEN) { t.row = t.w * tf.bytes; t.slice = t.h * t.row; } else nx_tex_pitch(tf, t.w, t.h, &t.row, &t.slice);
            bool ok = nx_img_push(this, l, fi, &t, &whole); nxd3d_free(z); if (!ok) return E_OUTOFMEMORY;
        }
    }
    return D3D_OK;
}
void NxImg::add_dirty(uint32_t level, uint32_t face, const NxBox3* b) {
    NxSub* s = at(level, face); NxBox3 n = b ? *b : NxBox3{0, 0, 0, s->w, s->h, s->d};
    if (!s->dirty) { s->dbox = n; s->dirty = 1; return; }
    NxBox3& o = s->dbox; if (n.l < o.l) o.l = n.l; if (n.t < o.t) o.t = n.t; if (n.k > o.k) o.k = n.k; if (n.r > o.r) o.r = n.r; if (n.b > o.b) o.b = n.b;
    uint32_t f0 = n.f < o.f ? n.f : o.f; o.f = f0;   // front / back union (k = back)
}
// push: box of a D3D-layout sub-resource `s` (mem, row, slice) -> nxd3d_image_update in native texels. nx_img_push_at: the same for texels given as `p` = the first texel of the box (rows `row`, slices `slice` apart)
bool nx_img_push_at(NxImg* im, uint32_t level, uint32_t face, const NxBox3* bx, const uint8_t* p, uint32_t row, uint32_t slice) {
    const NxTexFmt& f = im->tf; const NxBox3& b = *bx; bool blk = (f.flags & NXTF_BLOCKS) != 0;
    uint32_t rb = blk ? ((b.r - b.l + f.bw - 1) / f.bw) * f.bb : (b.r - b.l) * f.bytes, rows = blk ? (b.b - b.t + f.bh - 1) / f.bh : b.b - b.t, ns = b.k - b.f;
    if (!(im->ni.flags & NXFMT_CONVERT)) { nxd3d_image_update(im->owner, im->gpu, level, face, bx, p, row, slice); return true; }
    uint32_t w = b.r - b.l, h = b.b - b.t, ob = ((im->fmt == NXD3D_FOURCC('A', 'T', 'I', '1')) ? w * 4 : w * im->ni.bytes);   // native tight row bytes (ATI1: decoded RGBA8)
    uint32_t nsz = nx_d3d9_converted_size(im->fmt, w, h, 1, 1); uint8_t* src = (uint8_t*)nxd3d_alloc((size_t)rb * rows * ns + 1); uint8_t* dst = (uint8_t*)nxd3d_alloc((size_t)nsz * ns + 1);
    if (!src || !dst) { nxd3d_free(src); nxd3d_free(dst); return false; }                                  // nothing was sent
    for (uint32_t z = 0; z < ns; z++) {
        for (uint32_t y = 0; y < rows; y++) memcpy(src + ((size_t)z * rows + y) * rb, p + (size_t)z * slice + (size_t)y * row, rb);
        nx_d3d9_convert(im->fmt, w, h, 1, 1, src + (size_t)z * rows * rb, rows * rb, dst + (size_t)z * nsz, nsz);
    }
    nxd3d_image_update(im->owner, im->gpu, level, face, bx, dst, ob, ob * h); nxd3d_free(src); nxd3d_free(dst); return true;
}
bool nx_img_push(NxImg* im, uint32_t level, uint32_t face, const NxSub* s, const NxBox3* bx) {
    const NxTexFmt& f = im->tf; NxBox3 b = *bx; const uint8_t* base = s->mem; uint32_t row = s->row, slice = s->slice;
    if (f.flags & NXTF_BROKEN) { b = {0, 0, 0, s->w, s->h, s->d}; row = ((s->w + f.bw - 1) / f.bw) * f.bb; slice = row * ((s->h + f.bh - 1) / f.bh); }   // blocks stored contiguously
    bool blk = (f.flags & NXTF_BLOCKS) != 0;
    const uint8_t* p = base + (size_t)b.f * slice + (size_t)(blk ? b.t / f.bh : b.t) * row + (size_t)(blk ? b.l / f.bw : b.l) * (blk ? f.bb : f.bytes);
    return nx_img_push_at(im, level, face, &b, p, row, slice);
}
bool NxImg::upload() {
    if (!gpu) return true;
    for (uint32_t fi = 0; fi < faces; fi++) for (uint32_t l = 0; l < levels; l++) {
        NxSub* s = at(l, fi); if (!s->dirty || !s->mem) continue;
        if (!nx_img_push(this, l, fi, s, &s->dbox)) return false;   // stays dirty: retried by the next flush
        s->dirty = 0;
    }
    return true;
}
bool NxImg::sync(bool gen) { if (!upload()) return false; if (gen && autogen && mip_dirty && gpu) { nxd3d_gen_mips(owner, gpu, filter == D3DTEXF_POINT ? 0 : 1); mip_dirty = false; } return true; }
void nx_img_gpu_wrote(NxImg* im, uint32_t level, uint32_t face) { im->at(level, face)->gpu_newer = 1; if (im->autogen) im->mip_dirty = true; }
NxImg* nx_img_of(NxObj* o) {
    if (!o) return nullptr; uint8_t t = o->ci->rtype;
    if (t == D3DRTYPE_TEXTURE || t == D3DRTYPE_CUBETEXTURE || t == D3DRTYPE_VOLUMETEXTURE) return static_cast<NxTexCore*>(o)->img;
    if (t == D3DRTYPE_SURFACE) return static_cast<NxSurfCore*>(o)->img;
    return t == D3DRTYPE_VOLUME ? static_cast<NxVolCore*>(o)->img : nullptr;
}
void nx_tex_preload(NxObj* o) { if (NxImg* i = nx_img_of(o)) nx_ignore(i->upload()); }   // IDirect3DResource9::PreLoad is void (wined3d_resource_preload has no result): a failed upload leaves the dirty regions, the use of the texture retries it and reports

// ---- NxTexCore
NxTexCore::~NxTexCore() { for (uint32_t i = 0; i < nkids; i++) kids[i]->ci->destroy(kids[i]); nxd3d_free(kids); delete img; }
DWORD NxTexCore::setLOD(DWORD l) { if (!managed()) return 0; DWORD o = img->lod; if (l >= img->nlev) l = img->nlev - 1; img->lod = l; return o; }   // wined3d_stateblock_set_texture_lod
DWORD NxTexCore::getLOD() { return img->lod; }
DWORD NxTexCore::getLevelCount() { return img->levels; }                                                                          // AUTOGENMIPMAP: 1 (d3d9_texture_2d_GetLevelCount)
HRESULT NxTexCore::setFilter(D3DTEXTUREFILTERTYPE t) { if (img->kind == NXK_VOLUME || t == D3DTEXF_NONE) return D3DERR_INVALIDCALL; img->filter = t; return D3D_OK; }
D3DTEXTUREFILTERTYPE NxTexCore::getFilter() { return img->kind == NXK_VOLUME ? D3DTEXF_NONE : (D3DTEXTUREFILTERTYPE)img->filter; }
void NxTexCore::genMips() { if (img->kind != NXK_VOLUME && img->autogen && img->mip_dirty) nx_ignore(img->sync(true)); }   // GenerateMipSubLevels is void: a failed sync keeps mip_dirty / the dirty regions, the next flush retries                        // d3d9_texture_gen_auto_mipmap
HRESULT NxTexCore::levelDesc2(UINT level, D3DSURFACE_DESC* d) {
    if (!d || level >= img->levels) return D3DERR_INVALIDCALL;                                                                    // AUTOGENMIPMAP: levels == 1
    d->Format = (D3DFORMAT)img->fmt; d->Type = D3DRTYPE_SURFACE; d->Usage = img->usage; d->Pool = (D3DPOOL)img->pool; d->MultiSampleType = D3DMULTISAMPLE_NONE; d->MultiSampleQuality = 0;
    d->Width = img->at(level, 0)->w; d->Height = img->at(level, 0)->h; return D3D_OK;
}
HRESULT NxTexCore::levelDesc3(UINT level, D3DVOLUME_DESC* d) {
    if (!d || level >= img->levels) return D3DERR_INVALIDCALL;
    d->Format = (D3DFORMAT)img->fmt; d->Type = D3DRTYPE_VOLUME; d->Usage = img->usage; d->Pool = (D3DPOOL)img->pool; NxSub* s = img->at(level, 0); d->Width = s->w; d->Height = s->h; d->Depth = s->d; return D3D_OK;
}
HRESULT NxTexCore::kid(uint32_t level, uint32_t face, void** pp) {
    if (!pp) return D3DERR_INVALIDCALL;
    if (level >= img->levels || face >= img->faces) return D3DERR_INVALIDCALL;
    NxObj* k = kids[face * img->levels + level]; *pp = k->self; k->addref(); return D3D_OK;
}
HRESULT NxTexCore::lock2(uint32_t level, uint32_t face, D3DLOCKED_RECT* lr, const RECT* r, DWORD f) {
    if (level >= img->levels || face >= img->faces) return D3DERR_INVALIDCALL;
    return static_cast<NxSurfCore*>(kids[face * img->levels + level])->lockRect(lr, r, f);
}
HRESULT NxTexCore::unlock2(uint32_t level, uint32_t face) {
    if (level >= img->levels || face >= img->faces) return D3DERR_INVALIDCALL;
    return static_cast<NxSurfCore*>(kids[face * img->levels + level])->unlockRect();
}
HRESULT NxTexCore::dirty2(uint32_t face, const RECT* r) {                                                                        // wined3d_texture_add_dirty_region on level 0
    if (face >= img->faces) return D3DERR_INVALIDCALL;
    D3DBOX b = {}; if (r) { b.Left = r->left; b.Top = r->top; b.Right = r->right; b.Bottom = r->bottom; b.Front = 0; b.Back = 1; if (!nx_box_valid(img, 0, &b)) return D3DERR_INVALIDCALL; }
    if (img->rec_dirty) { NxBox3 x = {b.Left, b.Top, 0, b.Right, b.Bottom, 1}; img->add_dirty(0, face, r ? &x : nullptr); }
    return D3D_OK;
}
#define TEXBASE(C) \
    DWORD STDMETHODCALLTYPE C::SetLOD(DWORD l) { return setLOD(l); } DWORD STDMETHODCALLTYPE C::GetLOD() { return getLOD(); } DWORD STDMETHODCALLTYPE C::GetLevelCount() { return getLevelCount(); } \
    HRESULT STDMETHODCALLTYPE C::SetAutoGenFilterType(D3DTEXTUREFILTERTYPE t) { return setFilter(t); } D3DTEXTUREFILTERTYPE STDMETHODCALLTYPE C::GetAutoGenFilterType() { return getFilter(); } \
    void STDMETHODCALLTYPE C::GenerateMipSubLevels() { genMips(); }
TEXBASE(NxTexture9) TEXBASE(NxCubeTexture9) TEXBASE(NxVolumeTexture9) TEXBASE(NxBaseTexture9)
HRESULT STDMETHODCALLTYPE NxTexture9::GetLevelDesc(UINT l, D3DSURFACE_DESC* d) { return levelDesc2(l, d); }
HRESULT STDMETHODCALLTYPE NxTexture9::GetSurfaceLevel(UINT l, IDirect3DSurface9** pp) { return kid(l, 0, (void**)pp); }
HRESULT STDMETHODCALLTYPE NxTexture9::LockRect(UINT l, D3DLOCKED_RECT* lr, const RECT* r, DWORD f) { return lock2(l, 0, lr, r, f); }
HRESULT STDMETHODCALLTYPE NxTexture9::UnlockRect(UINT l) { return unlock2(l, 0); }
HRESULT STDMETHODCALLTYPE NxTexture9::AddDirtyRect(const RECT* r) { return dirty2(0, r); }
HRESULT STDMETHODCALLTYPE NxCubeTexture9::GetLevelDesc(UINT l, D3DSURFACE_DESC* d) { return levelDesc2(l, d); }
HRESULT STDMETHODCALLTYPE NxCubeTexture9::GetCubeMapSurface(D3DCUBEMAP_FACES f, UINT l, IDirect3DSurface9** pp) { return kid(l, (uint32_t)f, (void**)pp); }
HRESULT STDMETHODCALLTYPE NxCubeTexture9::LockRect(D3DCUBEMAP_FACES f, UINT l, D3DLOCKED_RECT* lr, const RECT* r, DWORD fl) { return lock2(l, (uint32_t)f, lr, r, fl); }
HRESULT STDMETHODCALLTYPE NxCubeTexture9::UnlockRect(D3DCUBEMAP_FACES f, UINT l) { return unlock2(l, (uint32_t)f); }
HRESULT STDMETHODCALLTYPE NxCubeTexture9::AddDirtyRect(D3DCUBEMAP_FACES f, const RECT* r) { return dirty2((uint32_t)f, r); }
HRESULT STDMETHODCALLTYPE NxVolumeTexture9::GetLevelDesc(UINT l, D3DVOLUME_DESC* d) { return levelDesc3(l, d); }
HRESULT STDMETHODCALLTYPE NxVolumeTexture9::GetVolumeLevel(UINT l, IDirect3DVolume9** pp) { return kid(l, 0, (void**)pp); }
HRESULT STDMETHODCALLTYPE NxVolumeTexture9::LockBox(UINT l, D3DLOCKED_BOX* lb, const D3DBOX* b, DWORD f) { if (l >= img->levels) return D3DERR_INVALIDCALL; return static_cast<NxVolCore*>(kids[l])->lockBox(lb, b, f); }
HRESULT STDMETHODCALLTYPE NxVolumeTexture9::UnlockBox(UINT l) { if (l >= img->levels) return D3DERR_INVALIDCALL; return static_cast<NxVolCore*>(kids[l])->unlockBox(); }
HRESULT STDMETHODCALLTYPE NxVolumeTexture9::AddDirtyBox(const D3DBOX* b) {
    if (b && !nx_box_valid(img, 0, b)) return D3DERR_INVALIDCALL;
    if (img->rec_dirty) { NxBox3 x = b ? NxBox3{b->Left, b->Top, b->Front, b->Right, b->Bottom, b->Back} : NxBox3{}; img->add_dirty(0, 0, b ? &x : nullptr); }
    return D3D_OK;
}
