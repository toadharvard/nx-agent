/* fnv-nx L6 deko3d backend: IMAGE_READ and v3 READBACK (gl4_read.c, case for case: same rejections, same error code NXGPU_BAD_CALL, same row order,
 * the same rect semantics: a cube reads face `layer`, a 3D image reads slices z .. z+d-1, rows of the destination are rowPitch apart, slices
 * rows * rowPitch apart; image 0 is the swapchain image, a view reads its base).
 * Call sequence (deko3d example graphics/deko3d, gpu_console.c, and dk_image.cpp): a private command buffer (dk_xfer.c, nxdk_sync_begin), a Full
 * barrier, dkCmdBufCopyImageToBuffer of the box into a CpuUncached | GpuCached block (DkCopyBuf rowLength / imageHeight 0 = tight rows and slices),
 * submit, fence wait (nxdk_sync_end). The bytes are then converted back to the NxFormat layout (ROT8 words) and written through nxdk_guest_ptr.
 * A multisampled colour image is first resolved into a single-sample image of the same format with dkCmdBufResolveImage (the 2D engine blit with a
 * bilinear filter centred on the sample grid: the average of the samples when the grid is 2x1 or 2x2, i.e. 2x and 4x).
 * Cases GL accepts and deko3d cannot express (loud nxdk_err, BAD_CALL), each proven in the report and in tests/l6/dkimage:
 *   X4 a multisampled depth / stencil image: GL resolves sample 0; the 2D engine resolve filters and deko3d has no depth sample fetch outside a shader
 *   X5 a multisampled colour image of 8 samples (grid 4x2: the bilinear resolve taps 2 samples per axis, so it is not the average of 8) or of a format
 *      without the 2D engine usage (dkImageFormatGetFlags)
 * v3 READBACK decodes the NxFormat texels on the CPU: colour to RGBA8 (round(clamp(c) * 255)) or RGBA32F with the missing channels (0, 0, 0, 1);
 * Depth32F of Z16 / Z24 / Z32F / Z32FS8; Stencil8 of Z24S8 / Z32FS8. A compressed colour format is rejected (GL: the attachment is incomplete).
 * Assumptions (not verifiable off the console): the copy engine copies the raw bytes of depth formats as it does colour, the x byte of Z24X8 round trips
 * as data, and a synchronous call has no staging block of another recorded command buffer pending (the stream submits synchronously). */
#include "dk.h"
#include <stdlib.h>
#include <string.h>

static uint64_t bad(void) { return NXGPU_BAD_CALL; }

/* image 0 = the swapchain image; a view reads its base */
static const NxDkImage* target(uint32_t id) {
    const NxDkImage* im = nxdk_img(id);
    if (im && im->isView) im = nxdk_img(im->base);
    return im;
}

static uint32_t resolvable(const NxDkImage* im, const DkFmt* f) {
    if (f->flags & (DKFMT_DEPTH | DKFMT_STENCIL)) { nxdk_err("read: a multisampled depth / stencil image cannot be resolved by sample 0 (X4)"); return 1; }
    if (im->samples > 4 || !(dkImageFormatGetFlags(f->format) & DkImageFlags_Usage2DEngine)) {
        nxdk_err("read: %u samples of format %u cannot be resolved by the 2D engine (X5)", im->samples, im->fmt);
        return 1;
    }
    return 0;
}

/* The box of (level, layer) of `im` in the deko3d layout, tight rows and slices, in a malloc'd buffer (NULL = failed, nxdk_err'd). *bytes = its size. */
static uint8_t* fetch(const NxDkImage* im, uint32_t level, uint32_t layer, const uint32_t box[6], uint64_t* bytes) {
    const DkFmt f = dk_fmt(im->fmt);
    const uint64_t total = (uint64_t)nxdk_row_bytes(&f, box[3]) * nxdk_rows(&f, box[4]) * box[5];
    if (total > UINT32_MAX - DK_MEMBLOCK_ALIGNMENT) { nxdk_err("read: %llu bytes do not fit one staging block", (unsigned long long)total); return NULL; }
    NxDkImage tmp;
    memset(&tmp, 0, sizeof tmp);
    const NxDkImage* src = im;
    if (im->samples > 1) {
        if (resolvable(im, &f)) return NULL;
        tmp = *im;
        tmp.blk = NULL; tmp.samples = 1; tmp.type = NxImgType_2D; tmp.mips = 1; tmp.layers = 1;
        if (nxdk_img_storage(&tmp)) return NULL;
        src = &tmp;
    }
    uint8_t* out = (uint8_t*)malloc(total);
    if (!out) { nxdk_err("read: no host memory for %llu bytes", (unsigned long long)total); nxdk_img_release(&tmp); return NULL; }
    DkMemBlockMaker mk;
    dkMemBlockMakerDefaults(&mk, nxdk.dev, (uint32_t)((total + DK_MEMBLOCK_ALIGNMENT - 1) / DK_MEMBLOCK_ALIGNMENT * DK_MEMBLOCK_ALIGNMENT));
    mk.flags = DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached;
    DkMemBlock blk = dkMemBlockCreate(&mk);
    NxDkSync sync;
    if (!blk) nxdk_err("read: deko3d refused the read-back block");
    if (!blk || nxdk_sync_begin(&sync)) { free(out); if (blk) dkMemBlockDestroy(blk); nxdk_img_release(&tmp); return NULL; }
    if (im->samples > 1) {
        DkImageView sv, dv;
        dkImageViewDefaults(&sv, &im->dk);
        dkImageViewDefaults(&dv, &tmp.dk);
        dkCmdBufResolveImage(sync.cmd, &sv, &dv);
        dkCmdBufBarrier(sync.cmd, DkBarrier_Full, DkInvalidateFlags_Image | DkInvalidateFlags_L2Cache);
    }
    DkImageView v;
    DkImageRect r;
    nxdk_box_view(src, level, layer, box, &v, &r);
    const DkCopyBuf cb = { dkMemBlockGetGpuAddr(blk), 0, 0 };
    dkCmdBufCopyImageToBuffer(sync.cmd, &v, &r, &cb, 0);
    const uint32_t failed = nxdk_sync_end(&sync);
    if (!failed) memcpy(out, dkMemBlockGetCpuAddr(blk), total);
    dkMemBlockDestroy(blk);
    nxdk_img_release(&tmp);
    if (failed) { free(out); return NULL; }
    *bytes = total;
    return out;
}

uint64_t nxdk_image_read(uint64_t a) {
    const NxImageRead* r = (const NxImageRead*)nxdk_guest_ptr(a, sizeof *r);
    if (!r) { nxdk_err("image_read: %llx is not readable", (unsigned long long)a); return bad(); }
    const NxDkImage* im = target(r->src.image);
    if (!im) { nxdk_err("image_read: bad image %u", r->src.image); return bad(); }
    const uint32_t box[6] = { r->x, r->y, r->z, r->w, r->h, r->d };
    if (nxdk_box_check(im, "image_read", r->src.level, r->src.layer, box)) return bad();
    const DkFmt f = dk_fmt(im->fmt);
    const uint64_t rb = nxdk_row_bytes(&f, r->w), rows = nxdk_rows(&f, r->h), pitch = r->rowPitch;
    const uint64_t need = ((uint64_t)r->d * rows - 1) * pitch + rb;
    if (!r->dst || pitch < rb || need > r->dst_size) { nxdk_err("image_read: bad destination"); return bad(); }
    uint8_t* dst = (uint8_t*)nxdk_guest_ptr(r->dst, r->dst_size);
    if (!dst) { nxdk_err("image_read: destination %llx is not writable", (unsigned long long)r->dst); return bad(); }
    uint64_t bytes = 0;
    uint8_t* buf = fetch(im, r->src.level, r->src.layer, box, &bytes);
    if (!buf) return bad();
    if (im->fmt == NxFmt_BGRX8 || im->fmt == NxFmt_RGBX8)   /* GL stores these as RGB8 and reads the x byte back as 0xFF (gl4_fmt.c) */
        for (uint64_t i = 3; i < bytes; i += 4) buf[i] = 0xFF;
    for (uint32_t z = 0; z < r->d; z++)
        nxdk_rows_convert(&f, 0, dst + z * rows * pitch, pitch, buf + z * rows * rb, rb, (uint32_t)rb, rows);
    free(buf);
    return 0;
}

/* ---- v3 readback ---------------------------------------------------------------------------------------------------------------- */
static float un(uint32_t v, uint32_t max) { return (float)v / (float)max; }
static float sn(int32_t v, int32_t max) { const float x = (float)v / (float)max; return x < -1.0f ? -1.0f : x; }

static float half(uint16_t h) {
    const uint32_t s = (uint32_t)h >> 15 << 31, e = h >> 10 & 31, m = h & 1023;
    if (e == 0) return (s ? -1.0f : 1.0f) * ((float)m * 5.9604645e-8f);   /* subnormal: m * 2^-24, exact */
    const uint32_t bits = s | (e == 31 ? 255u << 23 : (e + 112) << 23) | m << 13;
    float o;
    memcpy(&o, &bits, 4);
    return o;
}

/* one texel of an uncompressed colour NxFormat (the layouts of state.h) to r, g, b, a; 0 = ok, 1 = not a colour layout */
static uint32_t texel(uint32_t fmt, const uint8_t* p, uint32_t bpb, float c[4]) {
    uint8_t b[16] = { 0 };
    uint16_t h[4];
    uint32_t w;
    float fl[4];
    c[0] = c[1] = c[2] = 0.0f; c[3] = 1.0f;
    memcpy(b, p, bpb);
    memcpy(h, b, 8);
    memcpy(&w, b, 4);
    memcpy(fl, b, 16);
    switch ((enum NxFormat)fmt) {
    case NxFmt_RGBA8: for (int i = 0; i < 4; i++) c[i] = un(b[i], 255); return 0;
    case NxFmt_BGRA8: c[0] = un(b[2], 255); c[1] = un(b[1], 255); c[2] = un(b[0], 255); c[3] = un(b[3], 255); return 0;
    case NxFmt_BGRX8: c[0] = un(b[2], 255); c[1] = un(b[1], 255); c[2] = un(b[0], 255); return 0;
    case NxFmt_RGBX8: c[0] = un(b[0], 255); c[1] = un(b[1], 255); c[2] = un(b[2], 255); return 0;
    case NxFmt_R8: c[0] = un(b[0], 255); return 0;
    case NxFmt_RG8: c[0] = un(b[0], 255); c[1] = un(b[1], 255); return 0;
    case NxFmt_R16: c[0] = un(h[0], 65535); return 0;
    case NxFmt_RG16: c[0] = un(h[0], 65535); c[1] = un(h[1], 65535); return 0;
    case NxFmt_RGBA16: for (int i = 0; i < 4; i++) c[i] = un(h[i], 65535); return 0;
    case NxFmt_RG8S: c[0] = sn((int8_t)b[0], 127); c[1] = sn((int8_t)b[1], 127); return 0;
    case NxFmt_RGBA8S: for (int i = 0; i < 4; i++) c[i] = sn((int8_t)b[i], 127); return 0;
    case NxFmt_RG16S: c[0] = sn((int16_t)h[0], 32767); c[1] = sn((int16_t)h[1], 32767); return 0;
    case NxFmt_RGBA16S: for (int i = 0; i < 4; i++) c[i] = sn((int16_t)h[i], 32767); return 0;
    case NxFmt_R16F: c[0] = half(h[0]); return 0;
    case NxFmt_RG16F: c[0] = half(h[0]); c[1] = half(h[1]); return 0;
    case NxFmt_RGBA16F: for (int i = 0; i < 4; i++) c[i] = half(h[i]); return 0;
    case NxFmt_R32F: c[0] = fl[0]; return 0;
    case NxFmt_RG32F: c[0] = fl[0]; c[1] = fl[1]; return 0;
    case NxFmt_RGBA32F: for (int i = 0; i < 4; i++) c[i] = fl[i]; return 0;
    case NxFmt_R5G6B5: c[0] = un(h[0] >> 11, 31); c[1] = un(h[0] >> 5 & 63, 63); c[2] = un(h[0] & 31, 31); return 0;
    case NxFmt_A1R5G5B5: case NxFmt_X1R5G5B5:   /* GL stores X1R5G5B5 as RGB5_A1: the bit reads back */
        c[0] = un(h[0] >> 10 & 31, 31); c[1] = un(h[0] >> 5 & 31, 31); c[2] = un(h[0] & 31, 31); c[3] = un(h[0] >> 15, 1); return 0;
    case NxFmt_A4R4G4B4: c[0] = un(h[0] >> 8 & 15, 15); c[1] = un(h[0] >> 4 & 15, 15); c[2] = un(h[0] & 15, 15); c[3] = un(h[0] >> 12, 15); return 0;
    case NxFmt_RGB10A2: c[0] = un(w & 1023, 1023); c[1] = un(w >> 10 & 1023, 1023); c[2] = un(w >> 20 & 1023, 1023); c[3] = un(w >> 30, 3); return 0;
    case NxFmt_BGR10A2: c[2] = un(w & 1023, 1023); c[1] = un(w >> 10 & 1023, 1023); c[0] = un(w >> 20 & 1023, 1023); c[3] = un(w >> 30, 3); return 0;
    default: return 1;
    }
}

static uint8_t to8(float v) { return v > 0.0f ? (v >= 1.0f ? 255 : (uint8_t)(v * 255.0f + 0.5f)) : 0; }   /* NaN fails v > 0: 0 */

/* depth of one texel in the deko3d layout (ROT8 formats keep the stencil byte on top) */
static float depth_of(uint32_t fmt, const uint8_t* p) {
    uint16_t h;
    uint32_t w;
    float f;
    memcpy(&h, p, 2); memcpy(&w, p, 4); memcpy(&f, p, 4);
    return fmt == NxFmt_Z16 ? un(h, 65535) : fmt == NxFmt_Z32F || fmt == NxFmt_Z32FS8 ? f : un(w & 0xFFFFFF, 0xFFFFFF);
}

static uint8_t stencil_of(uint32_t fmt, const uint8_t* p) { return fmt == NxFmt_Z24S8 ? p[3] : p[4]; }

uint64_t nxdk_readback(uint64_t a) {
    const NxReadback* r = (const NxReadback*)nxdk_guest_ptr(a, sizeof *r);
    if (!r || !r->dst || r->format > NxRb_Stencil8) { nxdk_err("readback: bad request"); return bad(); }
    const NxDkImage* im = target(r->image);
    if (!im || im->type != NxImgType_2D) { nxdk_err("readback: image %u is not a 2D image", r->image); return bad(); }
    const DkFmt f = dk_fmt(im->fmt);
    const int depthFmt = r->format == NxRb_Depth32F || r->format == NxRb_Stencil8;
    if (depthFmt != ((f.flags & DKFMT_DEPTH) != 0) || (r->format == NxRb_Stencil8 && !(f.flags & DKFMT_STENCIL))) {
        nxdk_err("readback: format %u does not match image %u", r->format, r->image);
        return bad();
    }
    if (!depthFmt && (f.flags & DKFMT_COMPRESSED)) { nxdk_err("readback: image %u is a compressed format", r->image); return bad(); }
    const uint64_t bpp = nx_readback_bpp(r->format);
    if ((uint64_t)r->w * r->h * bpp > r->dst_size || (uint64_t)r->x + r->w > im->w || (uint64_t)r->y + r->h > im->h) {
        nxdk_err("readback: the %ux%u rect at %u,%u does not fit the image or the destination", r->w, r->h, r->x, r->y);
        return bad();
    }
    if (!r->w || !r->h) return 0;   /* an empty rect reads nothing (glReadPixels of 0 pixels) */
    uint8_t* dst = (uint8_t*)nxdk_guest_ptr(r->dst, r->dst_size);
    if (!dst) { nxdk_err("readback: destination %llx is not writable", (unsigned long long)r->dst); return bad(); }
    const uint32_t box[6] = { r->x, r->y, 0, r->w, r->h, 1 };
    uint64_t bytes = 0;
    uint8_t* buf = fetch(im, 0, 0, box, &bytes);
    if (!buf) return bad();
    for (uint64_t i = 0; i < (uint64_t)r->w * r->h; i++) {
        const uint8_t* p = buf + i * f.bpb;
        float c[4];
        if (r->format == NxRb_Depth32F) { const float d = depth_of(im->fmt, p); memcpy(dst + i * 4, &d, 4); continue; }
        if (r->format == NxRb_Stencil8) { dst[i] = stencil_of(im->fmt, p); continue; }
        if (texel(im->fmt, p, f.bpb, c)) { free(buf); nxdk_err("readback: format %u has no colour layout", im->fmt); return bad(); }
        if (r->format == NxRb_RGBA32F) memcpy(dst + i * 16, c, 16);
        else for (int k = 0; k < 4; k++) dst[i * 4 + k] = to8(c[k]);
    }
    free(buf);
    return 0;
}
