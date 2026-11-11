/* fnv-nx L6 deko3d backend: NxOp_Blit and NxOp_GenMips; the behaviour is that of gl4_blit.c, the acceptance and messages likewise.
 *
 * Blit by rule, in the reference's order (sources and destinations are base images, a view id names its base, id 0 is the swapchain image):
 *   B1 destination multisampled                                -> nxdk_err (reference message)
 *   B2 same format, same rect size, same sample count          -> dkCmdBufCopyImage: an exact copy of any format (the copy engine; no usage flag)
 *   B3 a compressed side, or depth against colour, or depth with different formats -> nxdk_err (reference messages)
 *   B4 multisampled source: the reference resolves the whole image into a temporary and blits from it. deko3d expresses the resolve itself
 *      (dkCmdBufResolveImage: whole source into a destination of the same size) when the source rect is the whole image and the destination
 *      rect the whole 2D destination level of the same format and a colour format: that is one dkCmdBufResolveImage. Every other case needs a
 *      temporary target whose lifetime this file cannot end (CONTRACT-REQUEST below) and is rejected, naming the case (S7).
 *   B5 point sampling (filter 0, or a depth format: GL's nearest blit takes the texel floor((d + 0.5) * sw / dw), the rule of gl4_scale.c)
 *      with a different rect size                             -> nxdk_scale_point, with the occlusion counter paused around it
 *   B6 the rest (linear colour, or equal size with a format conversion) -> dkCmdBufBlitImage on the 2D engine, raw values, filter flag = filter;
 *      the engine needs DkImageFlags_Usage2DEngine on both sides: the rule of dk_image.c, dkImageFormatGetFlags(format) with no 3D image;
 *      a side without it is rejected, naming the case (S7).
 * Every recorded transfer sits between two full barriers (invalidating the image and L2 caches): it reads what the commands before it wrote and the
 * commands after it read its result. Rects are in the texel space of the subresource (row 0 = top), like the reference.
 * GenMips(image, filter) = for every level l >= 1 and layer z: the Blit above of level l - 1 (3D: slice floor((2z + 1) * srcDepth / (2 * dstDepth)),
 * whole level) onto level l (whole level). A 3D image with the linear filter is the reference's glGenerateTextureMipmap: no deko3d expression (S7).
 * CONTRACT-REQUEST: nxdk_tmp_image(NxDkImage*) = nxdk_img_storage for an image that dk_stream.c frees after the SUBMIT wait; it would let B4 resolve
 * into a temporary and then blit from it, which closes the multisampled cases B4 rejects. */
#include "dk.h"

#define NEED(n, name) do { if (argc < (n)) { nxdk_err(#name ": %u words, need %u", argc, (unsigned)(n)); return; } } while (0)
#define BAR_CACHES (DkInvalidateFlags_Image | DkInvalidateFlags_L2Cache)

static uint32_t lvl(uint32_t v, uint32_t l) { v >>= l; return v ? v : 1; }
static const NxDkImage* base_of(const NxDkImage* im) { return im && im->isView ? nxdk_img(im->base) : im; }
static uint32_t layers_of(const NxDkImage* im, uint32_t l) { return im->type == NxImgType_Cube ? 6 : im->type == NxImgType_3D ? lvl(im->d, l) : 1; }
static int engine2d(const NxDkImage* im) {
    return im->type != NxImgType_3D && (dkImageFormatGetFlags(dk_fmt(im->fmt).format) & DkImageFlags_Usage2DEngine);
}
static int sub_ok(const NxDkImage* im, uint32_t l, uint32_t z, uint32_t x, uint32_t y, uint32_t w, uint32_t h) {
    return l < im->mips && z < layers_of(im, l) && w && h && (uint64_t)x + w <= lvl(im->w, l) && (uint64_t)y + h <= lvl(im->h, l);
}
static void view(const NxDkSub* s, DkImageView* v, DkImageRect* r) {
    const uint32_t box[6] = { s->x, s->y, s->layer, s->w, s->h, 1 };
    nxdk_box_view(s->im, s->level, s->layer, box, v, r);
}
static void bar(void) { dkCmdBufBarrier(nxdk_s.cmd, DkBarrier_Full, BAR_CACHES); }

static int resolve(const NxDkSub* s, const NxDkSub* d) {   /* B4; 0 = recorded, 1 = nxdk_err'd */
    const NxDkImage *si = s->im, *di = d->im;
    if (dk_fmt(si->fmt).flags & DKFMT_DEPTH) { nxdk_err("Blit: resolve of a multisampled depth source has no deko3d expression (S7)"); return 1; }
    const int whole = s->w == lvl(si->w, s->level) && s->h == lvl(si->h, s->level) && d->w == s->w && d->h == s->h &&   /* x, y = 0 follow from w, h */
                      d->w == lvl(di->w, d->level) && d->h == lvl(di->h, d->level);
    if (!whole || si->fmt != di->fmt || di->type != NxImgType_2D || !engine2d(si) || !engine2d(di)) {
        nxdk_err("Blit: multisampled source %ux%u onto format %u %ux%u: only a whole-image resolve into the same format has a deko3d expression (S7)",
                 s->w, s->h, di->fmt, d->w, d->h);
        return 1;
    }
    DkImageView sv, dv; DkImageRect sr, dr;
    view(s, &sv, &sr); view(d, &dv, &dr);
    bar(); dkCmdBufResolveImage(nxdk_s.cmd, &sv, &dv); bar();
    return 0;
}

static int xfer(const NxDkSub* s, const NxDkSub* d, uint32_t filter) {   /* validated sub-resources -> B1..B6; 0 = recorded, 1 = nxdk_err'd */
    const NxDkImage *si = s->im, *di = d->im;
    const DkFmt sf = dk_fmt(si->fmt), df = dk_fmt(di->fmt);
    if (di->samples > 1) { nxdk_err("Blit: multisampled destination"); return 1; }
    const int sameSize = s->w == d->w && s->h == d->h;
    if (si->fmt == di->fmt && sameSize && si->samples == di->samples) {
        DkImageView sv, dv; DkImageRect sr, dr;
        view(s, &sv, &sr); view(d, &dv, &dr);
        bar(); dkCmdBufCopyImage(nxdk_s.cmd, &sv, &sr, &dv, &dr, 0); bar();
        return 0;
    }
    const int depth = (sf.flags & DKFMT_DEPTH) != 0;
    if ((sf.flags | df.flags) & DKFMT_COMPRESSED) { nxdk_err("Blit: compressed formats only copy (same format and size)"); return 1; }
    if (depth != ((df.flags & DKFMT_DEPTH) != 0) || (depth && si->fmt != di->fmt)) {
        nxdk_err("Blit: depth-stencil blit needs identical formats (%u -> %u)", si->fmt, di->fmt);
        return 1;
    }
    if (si->samples > 1) return resolve(s, d);
    if ((!filter || depth) && !sameSize) {
        bar(); nxdk_query_pause(); nxdk_scale_point(nxdk_s.cmd, s, d, NULL); nxdk_query_resume(); bar();
        return 0;
    }
    if (!engine2d(si) || !engine2d(di)) {
        nxdk_err("Blit: format %u -> %u (%s) needs the 2D engine, which this image pair does not support (S7)", si->fmt, di->fmt,
                 si->type == NxImgType_3D || di->type == NxImgType_3D ? "3D image" : "format");
        return 1;
    }
    DkImageView sv, dv; DkImageRect sr, dr;
    view(s, &sv, &sr); view(d, &dv, &dr);
    bar(); dkCmdBufBlitImage(nxdk_s.cmd, &sv, &sr, &dv, &dr, filter ? DkBlitFlag_FilterLinear : DkBlitFlag_FilterNearest, 0); bar();
    return 0;
}

/* NxOp_Blit: src image, src level | layer << 16, sx, sy, sw, sh, dst image, dst level | layer << 16, dx, dy, dw, dh, filter */
void nxdk_op_blit(const uint32_t* a, uint32_t argc) {
    NEED(13, Blit);
    const NxDkImage *sr = nxdk_img(a[0]), *dr = nxdk_img(a[6]);
    const NxDkImage *si = base_of(sr), *di = base_of(dr);
    const NxDkSub s = { si, sr && sr->isView ? sr->base : a[0], a[1] & 0xFFFF, a[1] >> 16, a[2], a[3], a[4], a[5] };
    const NxDkSub d = { di, dr && dr->isView ? dr->base : a[6], a[7] & 0xFFFF, a[7] >> 16, a[8], a[9], a[10], a[11] };
    if (!si || !di || a[12] > 1 || !sub_ok(si, s.level, s.layer, s.x, s.y, s.w, s.h) || !sub_ok(di, d.level, d.layer, d.x, d.y, d.w, d.h)) {
        nxdk_err("Blit: bad source %u or destination %u", a[0], a[6]);
        return;
    }
    (void)xfer(&s, &d, a[12]);
}

/* NxOp_GenMips: image, filter */
void nxdk_op_gen_mips(const uint32_t* a, uint32_t argc) {
    NEED(2, GenMips);
    const NxDkImage* raw = nxdk_img(a[0]);
    const NxDkImage* im = base_of(raw);
    if (!im || a[1] > 1 || im->samples > 1) { nxdk_err("GenMips: bad image %u / filter %u", a[0], a[1]); return; }
    if (im->type == NxImgType_3D && a[1] == 1 && im->mips > 1) {
        nxdk_err("GenMips: the linear filter on a 3D image has no deko3d expression (S7: the reference's glGenerateTextureMipmap)");
        return;
    }
    const uint32_t id = raw->isView ? raw->base : a[0];
    for (uint32_t l = 1; l < im->mips; l++) {
        const uint32_t layers = layers_of(im, l), sd = lvl(im->d, l - 1);
        for (uint32_t z = 0; z < layers; z++) {
            const uint32_t zs = im->type == NxImgType_3D ? (uint32_t)(((2ull * z + 1) * sd) / (2ull * layers)) : z;
            const NxDkSub s = { im, id, l - 1, zs, 0, 0, lvl(im->w, l - 1), lvl(im->h, l - 1) };
            const NxDkSub d = { im, id, l, z, 0, 0, lvl(im->w, l), lvl(im->h, l) };
            if (xfer(&s, &d, a[1])) return;
        }
    }
}
