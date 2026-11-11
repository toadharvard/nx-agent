/* dk_present.c: the compose passes of PRESENT (rules P1 and P2 of nxgpu4.h; reference gl4_present.c + gl4_scale.c).
 * Pass 1 (GPU): the source rect of `src` is point-scaled (nxdk_scale_point: source texel floor((d + 0.5) * sw / dw), the ramp per channel) into the
 * destination rect of nxdk.front. EVERY multisampled source (2, 4 or 8 samples, any renderable format) is resolved first, by ONE rule, with
 * nxdk_ms_resolve_on(sy.cmd, src, 0, tmp, NXDK_MSRES_AVG) into a temporary single-sample image of the same format and size (the average of the samples, as the
 * reference: gl4_present.c l.116 gl4_resolve_tex(src) then gl4_scale_point on the single-sample copy; gl4_blit.c l.57-67 gl4_resolve_tex is a nearest
 * blit of the multisampled image). dkCmdBufResolveImage (2D engine, 2x / 4x only) is not used. nxdk_ms_resolve_on records into the command buffer it is given,
 * here the private buffer of nxdk_sync_begin: PRESENT runs outside a SUBMIT, no query slice is open there, and the stream's nxdk_s.cmd is never read or
 * written by this file. It refuses an open pass and the same is a precondition of nxdk_scale_point (nxdk_target_bind refuses an open pass),
 * so the resolve adds no new one. Order of the two primitives (both reserve fragment UBO slot 15 and texture slot 31): the
 * resolve ends with its own descriptor sets restored, a full barrier (image) and the neutral state; the scale then binds, by itself, its target,
 * neutral state, viewport, scissor, programs, descriptor sets, texture 31 and UBO 15 before its draw, so no slot or state of the resolve is read by
 * the scale and the temporary (written by the resolve draw) is made visible to the scale's texture fetch by that barrier. Both record in order into the
 * one buffer. The temporary is created here (nxdk_img_storage, not nxdk_tmp_image: that one is freed only after the next SUBMIT's wait) and released
 * by nxdk_img_release after nxdk_sync_end, which waited for the fence that consumed it.
 * Pass 2 (cursor, rule P2): out = (v * a + d * (255 - a) + 127) / 255 per colour channel (v = the cursor channel, ramp[c][v] >> 8 when a ramp is
 * present; d = the front value; the alpha of the front unchanged), integer division. A deko3d blend state cannot give this: UNORM8 blend rounding is the
 * GPU's, not a rule of the API. So the equation is evaluated exactly on the CPU, as the reference does: copy the clipped cursor rect of nxdk.front to a
 * block, wait, blend, upload the rect. Proof: the loop of pc_blend is the reference's loop on the same bytes (RGBA8 read-back and upload are the identity).
 * Queueing and lifetime (P1): both passes record into private command buffers of dk_xfer.c (nxdk_sync_begin / nxdk_sync_end): the barrier at the start
 * orders it after everything queued, sync_end submits on nxdk.q, signals and waits for the fence, so the flip that dk_swap.c records next is ordered
 * after both passes. The guest's ramp and cursor pixels are read only inside this call (nxdk_scale_point copies the ramp into its uniforms during its
 * call, the blend reads them on the CPU). Staging blocks (scale descriptors and uniforms, the upload block) are listed by nxdk_stage_alloc and freed by
 * nxdk_sync_end after its fence wait; the read-back block and the temporary resolve image are created here and destroyed here exactly once, after the
 * wait that consumes them. Nothing outlives the call.
 * Result (rule): nxdk_present_compose returns 0 = composed (a cursor that is NULL or entirely outside the image is composed: nothing to draw) or 1 = refused or
 * failed; every exit that returns 1 issued exactly one nxdk_err first and every exit that returns 0 issued none, so the result is the error state of the call.
 * What a failed pass already executed stays executed (the reference's front is likewise left as the executed passes made it); the caller presents nothing.
 */
#include "dk.h"
#include <string.h>

static uint8_t* pc_block(uint32_t bytes, DkMemBlock* out) {
    DkMemBlockMaker mk;
    dkMemBlockMakerDefaults(&mk, nxdk.dev, (bytes + DK_MEMBLOCK_ALIGNMENT - 1) / DK_MEMBLOCK_ALIGNMENT * DK_MEMBLOCK_ALIGNMENT);
    mk.flags = DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached;
    *out = dkMemBlockCreate(&mk);
    if (!*out) { nxdk_err("present: deko3d refused the cursor block of %u bytes", bytes); return NULL; }
    return (uint8_t*)dkMemBlockGetCpuAddr(*out);
}

/* the cursor over the clipped rect at (x0, y0), w x h; px: cw x ch BGRA; rd: the front's rect (RGBA8), wr: the result (RGBA8) */
static void pc_blend(const NxPresent* p, const uint8_t* px, const uint16_t* ramp, int64_t x0, int64_t y0, uint32_t w, uint32_t h,
                     const uint8_t* rd, uint8_t* wr) {
    for (uint32_t j = 0; j < h; j++)
        for (uint32_t i = 0; i < w; i++) {
            const uint8_t* s = px + ((size_t)(y0 - p->cy + j) * p->cw + (size_t)(x0 - p->cx + i)) * 4;   /* B G R A */
            const uint8_t* d = rd + ((size_t)j * w + i) * 4;
            uint8_t* o = wr + ((size_t)j * w + i) * 4;
            const uint32_t a = s[3];
            for (int c = 0; c < 3; c++) {
                uint32_t v = s[2 - c];
                if (ramp) v = ramp[c * 256 + v] >> 8;
                o[c] = (uint8_t)((v * a + d[c] * (255 - a) + 127) / 255);
            }
            o[3] = d[3];
        }
}

/* a multisampled colour source resolved (average) into `tmp`, a single-sample image of the same format and size; 0 = ok, 1 = nxdk_err'd (see the head) */
static uint32_t pc_resolve(DkCmdBuf cmd, const NxDkImage* src, NxDkImage* tmp) {
    *tmp = *src;
    tmp->blk = NULL; tmp->samples = 1; tmp->mips = 1; tmp->layers = 1;
    if (nxdk_img_storage(tmp)) return 1;
    return nxdk_ms_resolve_on(cmd, src, 0, tmp, NXDK_MSRES_AVG);
}

/* pass 2 after the wait: blend rd (the front's rect) with the cursor and upload the result into the front; consumes and destroys rd; 0 = ok, 1 = nxdk_err'd */
static uint32_t pc_cursor_upload(const NxPresent* p, const uint8_t* px, const uint16_t* ramp, int64_t x0, int64_t y0, uint32_t w, uint32_t h,
                             DkMemBlock rd) {
    NxDkSync up;
    if (nxdk_sync_begin(&up)) { dkMemBlockDestroy(rd); return 1; }
    uint8_t* wr = NULL;
    DkMemBlock wb = nxdk_stage_alloc(w * h * 4);
    if (wb) wr = (uint8_t*)dkMemBlockGetCpuAddr(wb);
    uint32_t bad = !wr;   /* nxdk_stage_alloc refused: its own nxdk_err */
    if (wr) {
        pc_blend(p, px, ramp, x0, y0, w, h, (const uint8_t*)dkMemBlockGetCpuAddr(rd), wr);
        const uint32_t box[6] = { (uint32_t)x0, (uint32_t)y0, 0, w, h, 1 };
        DkImageView v;
        DkImageRect r;
        nxdk_box_view(&nxdk.front, 0, 0, box, &v, &r);
        const DkCopyBuf cb = { dkMemBlockGetGpuAddr(wb), 0, 0 };
        dkCmdBufCopyBufferToImage(up.cmd, &cb, &v, &r, 0);
    }
    dkMemBlockDestroy(rd);   /* the read-back block is consumed (the blend ran on the CPU after the first wait) */
    return nxdk_sync_end(&up) | bad;   /* submit, wait, free the upload block; a failure is its own nxdk_err */
}

uint32_t nxdk_present_compose(const NxPresent* p, const NxDkImage* src, const uint16_t* gamma, const uint8_t* cursor) {
    if (!p || !src || !src->used || src->isView || src->type != NxImgType_2D || (dk_fmt(src->fmt).flags & DKFMT_COMPRESSED)) {
        nxdk_err("present: bad image %u (not a live plain 2D colour image)", p ? p->image : 0u);
        return 1;
    }
    if (cursor && (!p->cw || !p->ch || p->cw > 4096 || p->ch > 4096)) { nxdk_err("present: cursor size %ux%u", p->cw, p->ch); return 1; }
    int64_t x0 = p->cx < 0 ? 0 : p->cx, y0 = p->cy < 0 ? 0 : p->cy, x1 = (int64_t)p->cx + p->cw, y1 = (int64_t)p->cy + p->ch;
    if (x1 > nxdk.front.w) x1 = nxdk.front.w;
    if (y1 > nxdk.front.h) y1 = nxdk.front.h;
    const int vis = cursor && x0 < x1 && y0 < y1;   /* a cursor entirely outside the image changes nothing */
    const uint32_t w = vis ? (uint32_t)(x1 - x0) : 0, h = vis ? (uint32_t)(y1 - y0) : 0;
    NxDkSync sy;
    if (nxdk_sync_begin(&sy)) return 1;
    NxDkImage tmp;
    memset(&tmp, 0, sizeof tmp);
    const NxDkImage* use = src;
    uint32_t bad = 0;
    if (src->samples > 1) { bad = pc_resolve(sy.cmd, src, &tmp); use = &tmp; }
    if (!bad) {
        const uint32_t e0 = nxdk.errCount;
        const NxDkSub s = { use, p->image, 0, 0, (uint32_t)p->sx, (uint32_t)p->sy, (uint32_t)p->sw, (uint32_t)p->sh };
        const NxDkSub d = { &nxdk.front, 0, 0, 0, (uint32_t)p->dx, (uint32_t)p->dy, (uint32_t)p->dw, (uint32_t)p->dh };
        nxdk_scale_point(sy.cmd, &s, &d, gamma);
        bad = nxdk.errCount != e0;
    }
    DkMemBlock rd = NULL;
    if (!bad && vis) {
        if (pc_block(w * h * 4, &rd)) {
            const uint32_t box[6] = { (uint32_t)x0, (uint32_t)y0, 0, w, h, 1 };
            DkImageView v;
            DkImageRect r;
            nxdk_box_view(&nxdk.front, 0, 0, box, &v, &r);
            const DkCopyBuf cb = { dkMemBlockGetGpuAddr(rd), 0, 0 };
            dkCmdBufBarrier(sy.cmd, DkBarrier_Full, DkInvalidateFlags_Image | DkInvalidateFlags_L2Cache);
            dkCmdBufCopyImageToBuffer(sy.cmd, &v, &r, &cb, 0);
        } else bad = 1;
    }
    bad |= nxdk_sync_end(&sy);   /* submit, signal, wait; the staging blocks of the scale are freed */
    nxdk_img_release(&tmp);
    if (bad || !vis) { if (rd) dkMemBlockDestroy(rd); return bad; }
    return pc_cursor_upload(p, cursor, gamma, x0, y0, w, h, rd);
}
