/* fnv-nx L6 deko3d backend: the transfer rules shared by the image upload (stream op ImageUpdate, image creation with data) and the read-back
 * (IMAGE_READ, READBACK): box validation (the rules of GL's ImageUpdate and image_read, one rule for both), staging memory, row conversion,
 * the upload primitive and the synchronous submit.
 * deko3d sources: dkCmdBufCopyBufferToImage / dkCmdBufCopyImageToBuffer (dk_image.cpp): the rect is in texels of the view's level, DkCopyBuf rows and
 * layers are tight when rowLength / imageHeight are 0, a compressed rect is converted to blocks inside deko3d, the rect z selects the layer of an
 * array view (a cube face) or the slice of a 3D image. The staging blocks are CpuUncached | GpuCached (gpu_console.c: no cache flush needed). */
#include "dk.h"
#include "../nxgpu4_check.h"
#include <stdlib.h>
#include <string.h>

enum { SYNC_CMD_BYTES = 256 * 1024 };   /* assumption: a copy record is far below 1 KiB and one synchronous call records at most 90 copies (6 faces x 13 levels + barriers) */

uint32_t nxdk_row_bytes(const DkFmt* f, uint32_t w) { return (f->flags & DKFMT_COMPRESSED ? (w + 3) / 4 : w) * f->bpb; }
uint32_t nxdk_rows(const DkFmt* f, uint32_t h) { return f->flags & DKFMT_COMPRESSED ? (h + 3) / 4 : h; }

uint32_t nxdk_box_check(const NxDkImage* im, const char* who, uint32_t level, uint32_t layer, const uint32_t box[6]) {
    const DkFmt f = dk_fmt(im->fmt);
    if (level >= im->mips) { nxdk_err("%s: level %u of %u", who, level, im->mips); return 1; }
    if (layer >= im->layers) { nxdk_err("%s: layer %u of %u", who, layer, im->layers); return 1; }
    if (im->type != NxImgType_3D && (box[2] != 0 || box[5] != 1)) { nxdk_err("%s: z %u depth %u of a non-3D image", who, box[2], box[5]); return 1; }
    if (!box[3] || !box[4] || !box[5]) { nxdk_err("%s: empty box %ux%ux%u", who, box[3], box[4], box[5]); return 1; }
    const uint32_t lw = nxchk_lvl(im->w, level), lh = nxchk_lvl(im->h, level), ld = im->type == NxImgType_3D ? nxchk_lvl(im->d, level) : 1u;
    if ((uint64_t)box[0] + box[3] > lw || (uint64_t)box[1] + box[4] > lh || (uint64_t)box[2] + box[5] > ld) { nxdk_err("%s: box outside level %u (%ux%ux%u)", who, level, lw, lh, ld); return 1; }
    if (f.flags & DKFMT_COMPRESSED) {   /* 4x4 blocks: the origin on a block, the size a block multiple unless the box ends at the level's edge */
        if ((box[0] | box[1]) & 3) { nxdk_err("%s: compressed box origin %u,%u is not block aligned", who, box[0], box[1]); return 1; }
        if (((box[3] & 3) && box[0] + box[3] != lw) || ((box[4] & 3) && box[1] + box[4] != lh)) { nxdk_err("%s: compressed box size %ux%u is not block aligned", who, box[3], box[4]); return 1; }
    }
    return 0;
}

void nxdk_rows_convert(const DkFmt* f, int to_dk, uint8_t* dst, uint64_t dp, const uint8_t* src, uint64_t sp, uint32_t rb, uint64_t n) {
    for (uint64_t i = 0; i < n; i++) {
        uint8_t* d = dst + i * dp;
        const uint8_t* s = src + i * sp;
        if (!(f->flags & DKFMT_ROT8)) { memcpy(d, s, rb); continue; }
        for (uint32_t o = 0; o + 4 <= rb; o += 4) {   /* Nx word = rotl32(Dk word, 8): Nx depth24 << 8 | s8, Dk s8 << 24 | depth24 */
            uint32_t v;
            memcpy(&v, s + o, 4);
            v = to_dk ? (v >> 8 | v << 24) : (v << 8 | v >> 24);
            memcpy(d + o, &v, 4);
        }
    }
}

void nxdk_box_view(const NxDkImage* im, uint32_t level, uint32_t layer, const uint32_t box[6], DkImageView* v, DkImageRect* r) {
    dkImageViewDefaults(v, &im->dk);
    v->mipLevelOffset = level;
    v->mipLevelCount = 1;
    *r = (DkImageRect){ box[0], box[1], 0, box[3], box[4], 1 };
    if (im->type == NxImgType_Cube) { v->type = DkImageType_2DArray; r->z = layer; }
    else if (im->type == NxImgType_3D) { r->z = box[2]; r->depth = box[5]; }
}

DkMemBlock nxdk_stage_alloc(uint32_t bytes) {
    if (!bytes || bytes > UINT32_MAX - DK_MEMBLOCK_ALIGNMENT) { nxdk_err("staging: %u bytes", bytes); return NULL; }
    NxDkStage* s = (NxDkStage*)malloc(sizeof *s);
    if (!s) { nxdk_err("staging: no host memory for the list entry"); return NULL; }
    DkMemBlockMaker mk;
    dkMemBlockMakerDefaults(&mk, nxdk.dev, (bytes + DK_MEMBLOCK_ALIGNMENT - 1) / DK_MEMBLOCK_ALIGNMENT * DK_MEMBLOCK_ALIGNMENT);
    mk.flags = DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached;
    s->blk = dkMemBlockCreate(&mk);
    if (!s->blk) { free(s); nxdk_err("staging: deko3d refused a block of %u bytes", bytes); return NULL; }
    s->next = nxdk.stage;
    nxdk.stage = s;
    return s->blk;
}

void nxdk_stage_free(void) {
    while (nxdk.stage) {
        NxDkStage* s = nxdk.stage;
        nxdk.stage = s->next;
        dkMemBlockDestroy(s->blk);
        free(s);
    }
}

uint32_t nxdk_sync_begin(NxDkSync* s) {
    DkMemBlockMaker mk;
    dkMemBlockMakerDefaults(&mk, nxdk.dev, SYNC_CMD_BYTES);
    mk.flags = DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached;
    s->mem = dkMemBlockCreate(&mk);
    if (!s->mem) { nxdk_err("sync: command memory refused"); return 1; }
    DkCmdBufMaker cb;
    dkCmdBufMakerDefaults(&cb, nxdk.dev);
    s->cmd = dkCmdBufCreate(&cb);
    if (!s->cmd) { dkMemBlockDestroy(s->mem); nxdk_err("sync: command buffer refused"); return 1; }
    dkCmdBufAddMemory(s->cmd, s->mem, 0, SYNC_CMD_BYTES);
    dkCmdBufBarrier(s->cmd, DkBarrier_Full, DkInvalidateFlags_Image | DkInvalidateFlags_L2Cache);   /* ordered after every command already queued */
    return 0;
}

uint32_t nxdk_sync_end(NxDkSync* s) {
    DkFence fence;
    dkCmdBufBarrier(s->cmd, DkBarrier_Full, DkInvalidateFlags_Image | DkInvalidateFlags_L2Cache);
    dkQueueSubmitCommands(nxdk.q, dkCmdBufFinishList(s->cmd));
    dkQueueSignalFence(nxdk.q, &fence, true);
    const DkResult r = dkFenceWait(&fence, -1);
    dkCmdBufDestroy(s->cmd);
    dkMemBlockDestroy(s->mem);
    nxdk_stage_free();
    if (r != DkResult_Success) { nxdk_err("sync: the fence wait failed (%d)", (int)r); return 1; }
    return 0;
}

uint32_t nxdk_image_upload(DkCmdBuf cmd, uint32_t id, uint32_t level, uint32_t layer, const uint32_t box[6], const uint8_t* src, uint64_t pitch) {
    const NxDkImage* im = id && id <= NX_MAX_IMAGES && nxdk.img[id].used && !nxdk.img[id].isView ? &nxdk.img[id] : NULL;
    if (!im) { nxdk_err("image_update: %u is not a live image of the device", id); return 1; }
    if (im->samples != 1) { nxdk_err("image_update: image %u is multisampled", id); return 1; }
    if (nxdk_box_check(im, "image_update", level, layer, box)) return 1;
    const DkFmt f = dk_fmt(im->fmt);
    const uint32_t rb = nxdk_row_bytes(&f, box[3]), rows = nxdk_rows(&f, box[4]);
    if (pitch < rb) { nxdk_err("image_update: pitch %llu below the row size %u", (unsigned long long)pitch, rb); return 1; }
    const uint64_t total = (uint64_t)rb * rows * box[5];
    if (total > UINT32_MAX - DK_MEMBLOCK_ALIGNMENT) { nxdk_err("image_update: %llu bytes do not fit one staging block", (unsigned long long)total); return 1; }
    DkMemBlock blk = nxdk_stage_alloc((uint32_t)total);
    if (!blk) return 1;
    uint8_t* dst = (uint8_t*)dkMemBlockGetCpuAddr(blk);
    for (uint32_t z = 0; z < box[5]; z++)
        nxdk_rows_convert(&f, 1, dst + (uint64_t)z * rows * rb, rb, src + (uint64_t)z * rows * pitch, pitch, rb, rows);
    const DkCopyBuf cb = { dkMemBlockGetGpuAddr(blk), 0, 0 };
    DkImageView v;
    DkImageRect r;
    nxdk_box_view(im, level, layer, box, &v, &r);
    dkCmdBufCopyBufferToImage(cmd, &cb, &v, &r, 0);
    return 0;
}
