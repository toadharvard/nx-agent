/* fnv-nx L6 deko3d backend: the swapchain, PRESENT and WAIT_VBLANK (rules P1-P5 of nxgpu4.h).
 *
 * Image 0 (rule P3) is `nxdk.front`: an RGBA8 image, zero filled at creation, that the compose passes (dk_present.c) write and that image_read of image 0
 * reads (rule P4). It is not a display image: the display images are the two swapchain images, which only the flip below writes (a copy-engine copy of
 * the whole front image), so a present that fails validation changes nothing. Both layouts are the same size and format, so the copy is exact.
 * Swapchain images carry no HwCompression (the display cannot scan out a compressed image).
 * Ordering (rule P1): compose passes and the flip are on the one queue, the flip starts with a full barrier. The present's own command buffer holds two
 * regions, one per swapchain slot; a region is rewritten only after the fence signalled behind its last use. */
#include "dk.h"
#include <string.h>

static uint64_t up(uint64_t v, uint64_t a) { return (v + a - 1) / a * a; }

static uint32_t swap_fail(const char* what) { nxdk_err("swapchain: %s failed", what); return 1; }

uint32_t nxdk_swap_create(uint32_t width, uint32_t height) {
    if (viOpenDefaultDisplay(&nxdk.vi) != 0) return swap_fail("viOpenDefaultDisplay");
    nxdk.haveVi = 1;
    if (viGetDisplayVsyncEvent(&nxdk.vi, &nxdk.vsync) != 0) return swap_fail("viGetDisplayVsyncEvent");
    nxdk.haveVsync = 1;
    DkImageLayoutMaker lm;
    DkImageLayout swapLay, frontLay;
    dkImageLayoutMakerDefaults(&lm, nxdk.dev);
    lm.format = DkImageFormat_RGBA8_Unorm;
    lm.dimensions[0] = width; lm.dimensions[1] = height;
    lm.flags = DkImageFlags_UsageRender | DkImageFlags_UsagePresent;
    dkImageLayoutInitialize(&swapLay, &lm);
    lm.flags = DkImageFlags_UsageRender | DkImageFlags_Usage2DEngine;
    dkImageLayoutInitialize(&frontLay, &lm);
    uint64_t swapAlign = dkImageLayoutGetAlignment(&swapLay) > DK_MEMBLOCK_ALIGNMENT ? dkImageLayoutGetAlignment(&swapLay) : DK_MEMBLOCK_ALIGNMENT;
    uint64_t frontAlign = dkImageLayoutGetAlignment(&frontLay) > DK_MEMBLOCK_ALIGNMENT ? dkImageLayoutGetAlignment(&frontLay) : DK_MEMBLOCK_ALIGNMENT;
    uint64_t swapSize = up(dkImageLayoutGetSize(&swapLay), swapAlign), frontSize = up(dkImageLayoutGetSize(&frontLay), frontAlign);
    /* deko3d sizes and offsets are 32-bit: a size that does not fit is refused, never truncated */
    if (NXDK_FRAMES * swapSize > UINT32_MAX - DK_MEMBLOCK_ALIGNMENT || frontSize > UINT32_MAX - DK_MEMBLOCK_ALIGNMENT) return swap_fail("image size above 4 GiB");
    DkMemBlockMaker mk;
    dkMemBlockMakerDefaults(&mk, nxdk.dev, (uint32_t)up(frontSize, DK_MEMBLOCK_ALIGNMENT));
    mk.flags = DkMemBlockFlags_GpuCached | DkMemBlockFlags_Image | DkMemBlockFlags_ZeroFillInit;
    nxdk.frontMem = dkMemBlockCreate(&mk);
    if (!nxdk.frontMem) return swap_fail("front image memory");
    dkMemBlockMakerDefaults(&mk, nxdk.dev, (uint32_t)up(NXDK_FRAMES * swapSize, DK_MEMBLOCK_ALIGNMENT));
    mk.flags = DkMemBlockFlags_GpuCached | DkMemBlockFlags_Image;
    nxdk.swapMem = dkMemBlockCreate(&mk);
    if (!nxdk.swapMem) return swap_fail("swapchain image memory");
    dkMemBlockMakerDefaults(&mk, nxdk.dev, NXDK_FRAMES * NXDK_PRESENT_CMD);
    mk.flags = DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached;
    nxdk.cmdMem = dkMemBlockCreate(&mk);
    if (!nxdk.cmdMem) return swap_fail("present command memory");
    DkCmdBufMaker cb;
    dkCmdBufMakerDefaults(&cb, nxdk.dev);
    nxdk.cmd = dkCmdBufCreate(&cb);
    if (!nxdk.cmd) return swap_fail("present command buffer");
    dkImageInitialize(&nxdk.front.dk, &frontLay, nxdk.frontMem, 0);
    nxdk.front.w = width; nxdk.front.h = height; nxdk.front.fmt = NxFmt_RGBA8; nxdk.front.flags = NxImg_RenderTarget;
    DkImage const* images[NXDK_FRAMES];
    for (uint32_t i = 0; i < NXDK_FRAMES; i++) {
        dkImageInitialize(&nxdk.swap[i], &swapLay, nxdk.swapMem, (uint32_t)(i * swapSize));
        images[i] = &nxdk.swap[i];
    }
    DkSwapchainMaker sm;
    dkSwapchainMakerDefaults(&sm, nxdk.dev, nwindowGetDefault(), images, NXDK_FRAMES);
    nxdk.sc = dkSwapchainCreate(&sm);
    if (!nxdk.sc) return swap_fail("dkSwapchainCreate");
    return 0;
}

void nxdk_swap_destroy(void) {
    if (nxdk.sc) dkSwapchainDestroy(nxdk.sc);
    if (nxdk.cmd) dkCmdBufDestroy(nxdk.cmd);
    if (nxdk.cmdMem) dkMemBlockDestroy(nxdk.cmdMem);
    if (nxdk.swapMem) dkMemBlockDestroy(nxdk.swapMem);
    if (nxdk.frontMem) dkMemBlockDestroy(nxdk.frontMem);
    if (nxdk.haveVsync) eventClose(&nxdk.vsync);
    if (nxdk.haveVi) viCloseDisplay(&nxdk.vi);
}

/* a rect is non-empty and inside a w x h image (64-bit sums: no overflow of 32-bit coordinates) */
static int rect_in(int32_t x, int32_t y, int32_t w, int32_t h, uint32_t iw, uint32_t ih) {
    return w > 0 && h > 0 && x >= 0 && y >= 0 && (int64_t)x + w <= (int64_t)iw && (int64_t)y + h <= (int64_t)ih;
}

static uint64_t bad(const char* what) { nxdk_err("present: %s", what); return NXGPU_BAD_CALL; }

uint64_t nxdk_present(uint64_t a) {
    if (!nxdk.sc) return bad("no swapchain (width 0)");
    const NxPresent* p = (const NxPresent*)nxdk_guest_ptr(a, sizeof *p);
    if (!p) return bad("descriptor is not readable");
    if (p->interval > 4) return bad("interval is above 4");
    const NxDkImage* src = nxdk_img(p->image);
    if (!src || (src->flags & NxImg_Depth)) return bad("image is not a live colour image");
    if (!rect_in(p->sx, p->sy, p->sw, p->sh, src->w, src->h)) return bad("source rect");
    if (!rect_in(p->dx, p->dy, p->dw, p->dh, nxdk.front.w, nxdk.front.h)) return bad("destination rect");
    const uint16_t* gamma = p->gamma ? (const uint16_t*)nxdk_guest_ptr(p->gamma, 3 * 256 * sizeof(uint16_t)) : NULL;
    if (p->gamma && !gamma) return bad("gamma ramp is not readable");
    const uint8_t* cursor = NULL;
    if (p->cursor) {
        if (!p->cw || !p->ch || p->cw > 4096 || p->ch > 4096) return bad("cursor size");
        cursor = (const uint8_t*)nxdk_guest_ptr(p->cursor, (uint64_t)p->cw * p->ch * 4);
        if (!cursor) return bad("cursor pixels are not readable");
    }
    nxdk_present_compose(p, src, gamma, cursor);
    uint32_t slot = nxdk.slot;
    if (nxdk.fenceLive[slot] && dkFenceWait(&nxdk.fence[slot], -1) != DkResult_Success) return bad("fence wait failed");
    dkCmdBufClear(nxdk.cmd);
    dkCmdBufAddMemory(nxdk.cmd, nxdk.cmdMem, slot * NXDK_PRESENT_CMD, NXDK_PRESENT_CMD);
    int image = dkQueueAcquireImage(nxdk.q, nxdk.sc);
    DkImageView from, to;
    DkImageRect whole = { 0, 0, 0, nxdk.front.w, nxdk.front.h, 1 };
    dkImageViewDefaults(&from, &nxdk.front.dk);
    dkImageViewDefaults(&to, &nxdk.swap[image]);
    dkCmdBufBarrier(nxdk.cmd, DkBarrier_Full, DkInvalidateFlags_Image | DkInvalidateFlags_L2Cache);
    dkCmdBufCopyImage(nxdk.cmd, &from, &whole, &to, &whole, 0);
    dkQueueSubmitCommands(nxdk.q, dkCmdBufFinishList(nxdk.cmd));
    dkSwapchainSetSwapInterval(nxdk.sc, p->interval);
    dkQueuePresentImage(nxdk.q, nxdk.sc, image);
    dkQueueSignalFence(nxdk.q, &nxdk.fence[slot], true);
    nxdk.fenceLive[slot] = 1;
    nxdk.slot = (slot + 1) % NXDK_FRAMES;
    nxdk_on_present(p->frame);
    return 0;
}

uint64_t nxdk_wait_vblank(uint64_t output) {
    if (output != 0) { nxdk_err("wait_vblank: output %llu (the only output is 0)", (unsigned long long)output); return NXGPU_BAD_CALL; }
    if (!nxdk.sc) { nxdk_err("wait_vblank: no swapchain (width 0)"); return NXGPU_BAD_CALL; }
    eventClear(&nxdk.vsync);
    if (eventWait(&nxdk.vsync, UINT64_MAX) != 0) { nxdk_err("wait_vblank: the vsync event wait failed"); return NXGPU_BAD_CALL; }
    return 0;
}
