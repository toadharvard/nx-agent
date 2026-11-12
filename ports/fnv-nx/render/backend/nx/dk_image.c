/* fnv-nx L6 deko3d backend: images (IMAGE_CREATE, IMAGE_CREATE4, IMAGE_DESTROY), the image table and the descriptor sets. The rules are the ones of
 * the GL backend (gl4_image.c create / image_destroy), case for case: the same rejections give 0 (create) or BAD_CALL (destroy); an image with an
 * sRGB variant takes two consecutive ids (id, id + 1 = the sRGB view); mips are the full chain bound floor(log2(max extent)) + 1.
 *
 * Memory strategy (decision): every image owns one DkMemBlock (GpuCached | Image), size = layout size rounded up to the layout alignment (at least
 * 4 KiB), offset 0. Reason: GL gives every texture its own storage; destroy is then one dkMemBlockDestroy and no sub-allocator has to be proven
 * free of fragmentation; the 4096-image cap of the ABI bounds the block count. A size above 4 GiB - 4 KiB (deko3d sizes are 32-bit) is refused loudly.
 * Usage flags (rule): dkImageFormatGetFlags(format) & (UsageRender | Usage2DEngine); UsageRender only for a row with DKFMT_RENDER or DKFMT_DEPTH
 * (dk_fmt.c: a render target has no view swizzle); Usage2DEngine never for a 3D image (dk_image.cpp). NxImg_RenderTarget / NxImg_Depth in the
 * v3 descriptor are ignored at create exactly as in GL; dk_stream.c rejects a pass on an image whose usage lacks UsageRender.
 * Cases GL accepts and deko3d cannot express (loud nxdk_err, create = 0), each proven in the report and in tests/l6/dkimage:
 *   X1 samples > 1 of a format without DKFMT_RENDER / DKFMT_DEPTH (deko3d renders a multisampled image only through UsageRender)
 *   X2 an extent above NXDK_MAX_DIM (16384, the Maxwell texture limit; GL's limit is the driver's)
 *   X3 a block above 4 GiB - 4 KiB (32-bit memory block sizes) */
#include "dk.h"
#include "../nxgpu4_check.h"
#include <string.h>

_Static_assert(sizeof(DkImageDescriptor) == 32 && sizeof(DkSamplerDescriptor) == 32, "descriptor size: the tables are indexed by 32-byte slots");
_Static_assert(NXDK_IMG_SLOTS <= (1 << 20) && NX_MAX_SAMPLERS <= (1 << 12), "texture handle: 20 bits of image, 12 bits of sampler");
enum { DESC_BYTES = (NXDK_IMG_SLOTS + NX_MAX_SAMPLERS) * 32 };

static uint64_t up(uint64_t v, uint64_t a) { return (v + a - 1) / a * a; }

uint32_t nxdk_img_init(void) {
    DkMemBlockMaker mk;
    dkMemBlockMakerDefaults(&mk, nxdk.dev, (uint32_t)up(DESC_BYTES, DK_MEMBLOCK_ALIGNMENT));
    mk.flags = DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached;   /* gpu_console.c: descriptors are CPU written, GPU read */
    nxdk.descMem = dkMemBlockCreate(&mk);
    if (!nxdk.descMem) { nxdk_err("image tables: deko3d refused the descriptor memory"); return 1; }
    uint8_t* cpu = (uint8_t*)dkMemBlockGetCpuAddr(nxdk.descMem);
    memset(cpu, 0, DESC_BYTES);
    nxdk.imgDesc = (DkImageDescriptor*)cpu;
    nxdk.smpDesc = (DkSamplerDescriptor*)(cpu + NXDK_IMG_SLOTS * 32);
    nxdk.imgDescAddr = dkMemBlockGetGpuAddr(nxdk.descMem);
    nxdk.smpDescAddr = nxdk.imgDescAddr + NXDK_IMG_SLOTS * 32;
    return 0;
}

void nxdk_img_shutdown(void) {
    nxdk_stage_free();
    for (uint32_t i = 1; i < NXDK_IMG_SLOTS; i++)
        if (nxdk.img[i].used && !nxdk.img[i].isView) nxdk_img_release(&nxdk.img[i]);
    if (nxdk.descMem) dkMemBlockDestroy(nxdk.descMem);
}

void nxdk_bind_descriptors(DkCmdBuf cmd) {
    dkCmdBufBindImageDescriptorSet(cmd, nxdk.imgDescAddr, NXDK_IMG_SLOTS);
    dkCmdBufBindSamplerDescriptorSet(cmd, nxdk.smpDescAddr, NX_MAX_SAMPLERS);
}

const NxDkImage* nxdk_img(uint32_t id) {
    if (id == 0) return nxdk.front.used ? &nxdk.front : NULL;
    return id < NXDK_IMG_SLOTS && nxdk.img[id].used ? &nxdk.img[id] : NULL;
}

uint32_t nxdk_img_view_id(uint32_t id, int srgb) {
    const NxDkImage* v = srgb && id && id <= NX_MAX_IMAGES ? &nxdk.img[id + 1] : NULL;
    return v && v->used && v->isView && v->base == id ? id + 1 : id;
}

void nxdk_img_describe(uint32_t slot, const NxDkImage* im, int srgb) {
    const DkFmt f = dk_fmt(im->fmt);
    DkImageView v;
    dkImageViewDefaults(&v, &im->dk);
    if (srgb) v.format = f.srgb;
    if (!(f.flags & DKFMT_DEPTH)) {   /* the swizzle is not applied to depth (gl4_image.c) */
        uint16_t sw = im->swizzle ? (uint16_t)im->swizzle : NXIMG_SWIZZLE_IDENTITY;
        const uint8_t n[4] = { (uint8_t)(sw & 15), (uint8_t)(sw >> 4 & 15), (uint8_t)(sw >> 8 & 15), (uint8_t)(sw >> 12 & 15) };
        dk_fmt_view_swizzle(&f, n, v.swizzle);
    }
    dkImageDescriptorInitialize(&nxdk.imgDesc[slot], &v, false, false);
}

void nxdk_img_release(NxDkImage* im) {
    if (im->blk) dkMemBlockDestroy(im->blk);
    memset(im, 0, sizeof *im);
}

uint32_t nxdk_img_storage(NxDkImage* im) {
    const DkFmt f = dk_fmt(im->fmt);
    const uint32_t renderable = f.flags & (DKFMT_RENDER | DKFMT_DEPTH);
    if (im->w > NXDK_MAX_DIM || im->h > NXDK_MAX_DIM || im->d > NXDK_MAX_DIM) { nxdk_err("image: extent %ux%ux%u above the deko3d limit %d (X2)", im->w, im->h, im->d, NXDK_MAX_DIM); return 1; }
    if (im->samples > 1 && !renderable) { nxdk_err("image: format %u cannot be a multisampled image on deko3d (X1)", im->fmt); return 1; }
    uint32_t fl = dkImageFormatGetFlags(f.format) & (DkImageFlags_UsageRender | DkImageFlags_Usage2DEngine);
    if (!renderable) fl &= ~(uint32_t)DkImageFlags_UsageRender;
    if (im->type == NxImgType_3D) fl &= ~(uint32_t)DkImageFlags_Usage2DEngine;
    DkImageLayoutMaker m;
    dkImageLayoutMakerDefaults(&m, nxdk.dev);
    m.flags = fl;
    m.format = f.format;
    m.type = im->type == NxImgType_Cube ? DkImageType_Cubemap : im->type == NxImgType_3D ? DkImageType_3D : im->samples > 1 ? DkImageType_2DMS : DkImageType_2D;
    m.msMode = im->samples == 2 ? DkMsMode_2x : im->samples == 4 ? DkMsMode_4x : im->samples == 8 ? DkMsMode_8x : DkMsMode_1x;
    m.dimensions[0] = im->w; m.dimensions[1] = im->h;
    m.dimensions[2] = im->type == NxImgType_Cube ? 6 : im->d;
    m.mipLevels = im->mips;
    dkImageLayoutInitialize(&im->lay, &m);
    uint64_t al = dkImageLayoutGetAlignment(&im->lay);
    uint64_t size = up(dkImageLayoutGetSize(&im->lay), al > DK_MEMBLOCK_ALIGNMENT ? al : DK_MEMBLOCK_ALIGNMENT);
    if (size > UINT32_MAX - DK_MEMBLOCK_ALIGNMENT) { nxdk_err("image: %llu bytes do not fit a 32-bit memory block (X3)", (unsigned long long)size); return 1; }
    DkMemBlockMaker mk;
    dkMemBlockMakerDefaults(&mk, nxdk.dev, (uint32_t)size);
    mk.flags = DkMemBlockFlags_GpuCached | DkMemBlockFlags_Image;
    im->blk = dkMemBlockCreate(&mk);
    if (!im->blk) { nxdk_err("image: deko3d refused a block of %llu bytes", (unsigned long long)size); return 1; }
    dkImageInitialize(&im->dk, &im->lay, im->blk, 0);
    return 0;
}

typedef struct { uint32_t w, h, d, type, fmt, mips, samples, swizzle; } Make;

static uint32_t max_mips(const Make* m) {
    uint32_t e = m->w > m->h ? m->w : m->h, n = 1;
    if (m->type == NxImgType_3D && m->d > e) e = m->d;
    while (e >>= 1) n++;
    return n;
}

static uint32_t alloc_ids(uint32_t n) {
    for (uint32_t id = 1; id + n - 1 <= NX_MAX_IMAGES; id++) {
        uint32_t k = 0;
        while (k < n && !nxdk.img[id + k].used) k++;
        if (k == n) return id;
    }
    return 0;
}

static uint32_t upload_all(uint32_t id, const Make* m, const DkFmt* f, const uint8_t* data) {
    NxDkSync s;
    if (nxdk_sync_begin(&s)) return 1;
    uint32_t bad = 0;
    for (uint32_t face = 0; !bad && face < nxdk.img[id].layers; face++)
        for (uint32_t l = 0; !bad && l < m->mips; l++) {
            const uint32_t box[6] = { 0, 0, 0, nxchk_lvl(m->w, l), nxchk_lvl(m->h, l), nxchk_lvl(m->d, l) };
            const uint32_t rb = nxdk_row_bytes(f, box[3]);
            bad = nxdk_image_upload(s.cmd, id, l, face, box, data, rb);
            data += (uint64_t)rb * nxdk_rows(f, box[4]) * box[5];
        }
    return nxdk_sync_end(&s) || bad;
}

/* data: NULL, or tightly packed mips (face-major for a cube); returns the image id, 0 on failure */
static uint32_t create(const Make* m, const uint8_t* data, uint64_t data_size) {
    const DkFmt f = dk_fmt(m->fmt);
    if (!f.format || m->type > NxImgType_3D) { nxdk_err("image_create: format %u type %u", m->fmt, m->type); return 0; }
    if (!m->w || !m->h || !m->d || m->mips < 1 || m->mips > max_mips(m)) { nxdk_err("image_create: extent %ux%ux%u mips %u", m->w, m->h, m->d, m->mips); return 0; }
    if (m->type != NxImgType_3D && m->d != 1) { nxdk_err("image_create: depth %u of a non-3D image", m->d); return 0; }
    if (m->type == NxImgType_Cube && m->w != m->h) { nxdk_err("image_create: cube %ux%u is not square", m->w, m->h); return 0; }
    if (m->samples != 1 && m->samples != 2 && m->samples != 4 && m->samples != 8) { nxdk_err("image_create: samples %u", m->samples); return 0; }
    if (m->samples > 1 && (m->type != NxImgType_2D || m->mips != 1 || (f.flags & DKFMT_COMPRESSED))) { nxdk_err("image_create: multisampled needs one level of a 2D uncompressed image"); return 0; }
    if (m->type == NxImgType_3D && ((f.flags & DKFMT_DEPTH) || m->fmt == NxFmt_BC4 || m->fmt == NxFmt_BC5)) { nxdk_err("image_create: format %u has no 3D form", m->fmt); return 0; }
    for (int i = 0; i < 4; i++)
        if ((m->swizzle >> (4 * i) & 15) > NxSwz_1) { nxdk_err("image_create: swizzle %x", m->swizzle); return 0; }
    const int srgb = nx_format_has_srgb(m->fmt) && f.srgb != DkImageFormat_None;
    const uint32_t id = alloc_ids(srgb ? 2 : 1), layers = m->type == NxImgType_Cube ? 6 : 1;
    if (!id) { nxdk_err("image_create: no %d consecutive free image ids", srgb ? 2 : 1); return 0; }
    if (data) {   /* the whole upload must fit before anything is created */
        uint64_t need = 0;
        for (uint32_t l = 0; l < m->mips; l++) need += (uint64_t)nxdk_row_bytes(&f, nxchk_lvl(m->w, l)) * nxdk_rows(&f, nxchk_lvl(m->h, l)) * nxchk_lvl(m->d, l);
        if (need * layers > data_size) { nxdk_err("image_create: data %llu bytes, %llu needed", (unsigned long long)data_size, (unsigned long long)(need * layers)); return 0; }
    }
    NxDkImage* im = &nxdk.img[id];
    memset(im, 0, sizeof *im);
    im->w = m->w; im->h = m->h; im->d = m->d; im->fmt = m->fmt; im->type = m->type; im->mips = m->mips; im->samples = m->samples;
    im->layers = layers; im->swizzle = m->swizzle; im->used = 1;
    im->flags = ((f.flags & DKFMT_DEPTH) ? NxImg_Depth : 0) | (m->type == NxImgType_Cube ? NxImg_Cube : 0);
    if (nxdk_img_storage(im)) { memset(im, 0, sizeof *im); return 0; }
    if (data && upload_all(id, m, &f, data)) { nxdk_img_release(im); return 0; }
    nxdk_img_describe(id, im, 0);
    if (srgb) {
        NxDkImage* v = &nxdk.img[id + 1];
        *v = *im;
        v->blk = NULL; v->isView = 1; v->base = id;
        nxdk_img_describe(id + 1, v, 1);
    }
    return id;
}

uint64_t nxdk_image_create(uint64_t desc) {
    const NxImageDesc* d = (const NxImageDesc*)nxdk_guest_ptr(desc, sizeof *d);
    if (!d) { nxdk_err("image_create: descriptor %llx is not readable", (unsigned long long)desc); return 0; }
    const Make m = { d->width, d->height, 1, d->flags & NxImg_Cube ? NxImgType_Cube : NxImgType_2D, d->format, d->mips ? d->mips : 1u, 1, d->swizzle };
    const uint8_t* data = d->data ? (const uint8_t*)nxdk_guest_ptr(d->data, d->data_size) : NULL;
    if (d->data && !data) { nxdk_err("image_create: data %llx is not readable", (unsigned long long)d->data); return 0; }
    return create(&m, data, d->data_size);
}

uint64_t nxdk_image_create4(uint64_t desc) {
    const NxImageDesc4* d = (const NxImageDesc4*)nxdk_guest_ptr(desc, sizeof *d);
    if (!d) { nxdk_err("image_create4: descriptor %llx is not readable", (unsigned long long)desc); return 0; }
    const Make m = { d->width, d->height, d->depth, d->type, d->format, d->mips, d->samples, d->swizzle };
    return create(&m, NULL, 0);
}

uint64_t nxdk_image_destroy(uint64_t id) {
    if (id == 0 || id > NX_MAX_IMAGES || !nxdk.img[id].used || nxdk.img[id].isView) {
        nxdk_err("image_destroy: %llu is not a live image of the device", (unsigned long long)id);
        return NXGPU_BAD_CALL;
    }
    dkQueueWaitIdle(nxdk.q);   /* no queued command may still name the memory: after the wait none does (proof: the one queue is the only user) */
    const uint32_t n = nxdk_img_view_id((uint32_t)id, 1) != id;
    nxdk_img_release(&nxdk.img[id]);
    if (n) memset(&nxdk.img[id + 1], 0, sizeof nxdk.img[id + 1]);
    return 0;
}
