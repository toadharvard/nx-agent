/* fnv-nx native renderer, LP64 backend on deko3d (stream S6n, docs/RENDER_NATIVE.md §3, nx/gpu_abi.h).
 *
 * Lives in the host module next to libnx (S5 model). The ILP32 front end creates resources through NxGpuApi and
 * submits one command stream per frame; a render thread replays it into a deko3d command buffer one frame behind
 * the game thread, the GPU runs up to NXGPU_FRAMES frames behind that.
 *
 * State blocks: each distinct blend / depth / rasterizer+colour-write word, shader pair and vertex layout is
 * captured once into Maxwell command words (dkCmdBufBeginCaptureCmds); binding a state id replays only the blocks
 * whose id differs from the bound one (dkCmdBufReplayCmds = a memcpy into the command buffer).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <malloc.h>
#include <switch.h>
#include <deko3d.h>

#include "nx/gpu_abi.h"
#include "nxgpu.h"

#define NXGPU_FRAMES      3
#define NXGPU_SWAP_IMAGES 3
#define MAX_MEMBLOCKS     64
#define MAX_IMAGES        4096
#define MAX_SAMPLERS      8
#define MAX_SHADERS       512
#define MAX_STATES        1024
#define MAX_LAYOUTS       64
#define MAX_BLOCKS        256      /* captured state blocks per kind */
#define CAPTURE_WORDS     64
#define CMDMEM_PER_FRAME  (2u << 20)
#define CODE_POOL         (8u << 20)
#define STAGING_SIZE      (16u << 20)

enum { BK_BLEND, BK_DEPTH, BK_RASTER, BK_SHADERS, BK_LAYOUT, BK_COUNT };

typedef struct { uint64_t key; uint32_t words; uint32_t w[CAPTURE_WORDS]; } Block;
typedef struct { uint16_t blk[BK_COUNT]; } StateRec;

static struct {
    DkDevice dev;
    DkQueue queue;
    DkSwapchain swapchain;
    DkMemBlock fbMem, cmdMem, descMem, codeMem, stagingMem, tsMem;
    DkImage fb[NXGPU_SWAP_IMAGES];
    DkCmdBuf cmd[NXGPU_FRAMES], upload, capture;
    DkFence fence[NXGPU_FRAMES];
    uint32_t width, height;

    DkMemBlock mem[MAX_MEMBLOCKS + 1];
    uint32_t memKind[MAX_MEMBLOCKS + 1], nMem;
    uint32_t imagePool, imagePoolHead;            /* first NxMem_Image block, bump-allocated */

    DkImage image[MAX_IMAGES];
    uint8_t imageIsRT[MAX_IMAGES];
    uint32_t nImages;                              /* slot 0 unused */
    DkImageDescriptor* imageDesc;
    DkSamplerDescriptor* samplerDesc;
    bool descDirty;

    DkShader shader[MAX_SHADERS];
    uint32_t nShaders, codeHead;

    NxVtxLayout layout[MAX_LAYOUTS + 1];
    StateRec state[MAX_STATES + 1];
    Block block[BK_COUNT][MAX_BLOCKS];
    uint32_t nBlock[BK_COUNT];

    /* render thread hand-off: one pending submission (the front double-buffers its stream) */
    Thread thread;
    Mutex lock;
    CondVar cvSubmit, cvTaken, cvDone;
    NxSubmit pending;
    bool hasPending, quit;
    uint32_t recorded;                             /* frames recorded+submitted: frame numbers < recorded */
    uint32_t retired;                              /* frames whose fence was waited: < retired */
    uint64_t slotStats[NXGPU_FRAMES];              /* guest NxFrameStats address per frame slot */
    uint32_t slotFrame[NXGPU_FRAMES];
    NxFrameStats slotPartial[NXGPU_FRAMES];
    uint64_t lastPresentTick;
} g;

/* ---- helpers ---------------------------------------------------------------------------------------------- */
static void dk_debug(void* u, const char* ctx, DkResult r, const char* msg) {
    (void)u;
    char buf[256];
    int n = snprintf(buf, sizeof buf, "deko3d: %s: %d %s\n", ctx, (int)r, msg ? msg : "");
    svcOutputDebugString(buf, n > 0 ? (u64)n : 0);
    nxgpu_log(buf);
    if (r != DkResult_Success) diagAbortWithResult(MAKERESULT(Module_Libnx, LibnxError_BadGfxInit));
}

static DkImageFormat dk_format(uint32_t f) {
    switch (f) {
    case NxFmt_RGBA8: return DkImageFormat_RGBA8_Unorm;
    case NxFmt_BGRA8: return DkImageFormat_BGRA8_Unorm;
    case NxFmt_BC1: return DkImageFormat_RGBA_BC1;
    case NxFmt_BC2: return DkImageFormat_RGBA_BC2;
    case NxFmt_BC3: return DkImageFormat_RGBA_BC3;
    case NxFmt_R8: return DkImageFormat_R8_Unorm;
    case NxFmt_RG8: return DkImageFormat_RG8_Unorm;
    case NxFmt_RGB565: return DkImageFormat_RGB565_Unorm;
    case NxFmt_RGBA16F: return DkImageFormat_RGBA16_Float;
    case NxFmt_R32F: return DkImageFormat_R32_Float;
    case NxFmt_Z24S8: return DkImageFormat_Z24S8;
    case NxFmt_Z32F: return DkImageFormat_ZF32;
    default: return DkImageFormat_None;
    }
}
static int is_bc(uint32_t f) { return f == NxFmt_BC1 || f == NxFmt_BC2 || f == NxFmt_BC3; }
static uint32_t bc_bytes(uint32_t f) { return f == NxFmt_BC1 ? 8 : 16; }
static uint32_t px_bytes(uint32_t f) {
    switch (f) { case NxFmt_R8: return 1; case NxFmt_RG8: case NxFmt_RGB565: return 2; case NxFmt_RGBA16F: return 8;
                 default: return 4; }
}
static DkBlendFactor dk_bf(uint32_t b) {
    static const DkBlendFactor t[] = {DkBlendFactor_Zero, DkBlendFactor_One, DkBlendFactor_SrcAlpha,
        DkBlendFactor_InvSrcAlpha, DkBlendFactor_SrcColor, DkBlendFactor_InvSrcColor, DkBlendFactor_DstColor,
        DkBlendFactor_DstAlpha, DkBlendFactor_InvDstAlpha};
    return b < 9 ? t[b] : DkBlendFactor_One;
}
static DkGpuAddr mem_addr(uint32_t id, uint32_t off) {
    return id && id <= g.nMem ? dkMemBlockGetGpuAddr(g.mem[id]) + off : DK_GPU_ADDR_INVALID;
}

/* ---- state block capture ------------------------------------------------------------------------------------ */
static uint16_t block_get(int kind, uint64_t key, const void* arg) {
    for (uint32_t i = 0; i < g.nBlock[kind]; i++)
        if (g.block[kind][i].key == key) return (uint16_t)(i + 1);
    if (g.nBlock[kind] >= MAX_BLOCKS) return 0;
    Block* b = &g.block[kind][g.nBlock[kind]];
    b->key = key;
    dkCmdBufBeginCaptureCmds(g.capture, b->w, CAPTURE_WORDS);
    switch (kind) {
    case BK_BLEND: {
        DkColorState cs; dkColorStateDefaults(&cs);
        DkBlendState bs; dkBlendStateDefaults(&bs);
        if (key & 1) {
            dkColorStateSetBlendEnable(&cs, 0, true);
            dkBlendStateSetFactors(&bs, dk_bf((key >> 1) & 15), dk_bf((key >> 5) & 15), dk_bf((key >> 1) & 15),
                                   dk_bf((key >> 5) & 15));
        }
        dkCmdBufBindColorState(g.capture, &cs);
        dkCmdBufBindBlendStates(g.capture, 0, &bs, 1);
        break;
    }
    case BK_DEPTH: {
        DkDepthStencilState ds; dkDepthStencilStateDefaults(&ds);
        ds.depthTestEnable = key & 1; ds.depthWriteEnable = (key >> 1) & 1;
        ds.depthCompareOp = (DkCompareOp)(((key >> 2) & 7) + 1);   /* NxCompare -> DkCompareOp */
        dkCmdBufBindDepthStencilState(g.capture, &ds);
        break;
    }
    case BK_RASTER: {   /* key = cull(2) | colour write mask << 8 */
        DkRasterizerState rs; dkRasterizerStateDefaults(&rs);
        rs.cullMode = (DkFace)(key & 3);
        DkColorWriteState cw; dkColorWriteStateDefaults(&cw);
        dkColorWriteStateSetMask(&cw, 0, (uint32_t)(key >> 8) & 15);
        dkCmdBufBindRasterizerState(g.capture, &rs);
        dkCmdBufBindColorWriteState(g.capture, &cw);
        break;
    }
    case BK_SHADERS: {
        uint32_t vs = (uint32_t)key & 0xFFFF, ps = (uint32_t)(key >> 16) & 0xFFFF;
        DkShader const* sh[2] = {&g.shader[vs], &g.shader[ps]};
        dkCmdBufBindShaders(g.capture, DkStageFlag_GraphicsMask, sh, 2);
        break;
    }
    case BK_LAYOUT: {
        const NxVtxLayout* l = (const NxVtxLayout*)arg;
        DkVtxAttribState at[8];
        static const DkVtxAttribSize sz32[] = {DkVtxAttribSize_1x32, DkVtxAttribSize_2x32, DkVtxAttribSize_3x32, DkVtxAttribSize_4x32};
        static const DkVtxAttribSize sz16[] = {DkVtxAttribSize_1x16, DkVtxAttribSize_2x16, DkVtxAttribSize_3x16, DkVtxAttribSize_4x16};
        static const DkVtxAttribSize sz8[] = {DkVtxAttribSize_1x8, DkVtxAttribSize_2x8, DkVtxAttribSize_3x8, DkVtxAttribSize_4x8};
        uint32_t n = l->count > 8 ? 8 : l->count;
        for (uint32_t i = 0; i < n; i++) {
            uint32_t c = (l->attr[i].components - 1) & 3;
            memset(&at[i], 0, sizeof at[i]);
            at[i].bufferId = 0; at[i].offset = l->attr[i].offset;
            switch (l->attr[i].type) {
            case 1: at[i].size = sz8[c]; at[i].type = DkVtxAttribType_Unorm; break;
            case 2: at[i].size = sz16[c]; at[i].type = DkVtxAttribType_Snorm; break;
            case 3: at[i].size = sz16[c]; at[i].type = DkVtxAttribType_Float; break;
            default: at[i].size = sz32[c]; at[i].type = DkVtxAttribType_Float; break;
            }
        }
        DkVtxBufferState vb = {l->stride, 0};
        dkCmdBufBindVtxAttribState(g.capture, at, n);
        dkCmdBufBindVtxBufferState(g.capture, &vb, 1);
        break;
    }
    }
    b->words = dkCmdBufEndCaptureCmds(g.capture);
    g.nBlock[kind]++;
    return (uint16_t)g.nBlock[kind];
}

static void define_state(const uint32_t* a) {
    uint32_t id = a[0];
    if (!id || id > MAX_STATES) return;
    uint64_t w = a[4] | (uint64_t)a[5] << 32;
    StateRec* s = &g.state[id];
    s->blk[BK_BLEND] = block_get(BK_BLEND, (w >> NXRS_BLEND_SHIFT) & 0xFFFF, NULL);
    s->blk[BK_DEPTH] = block_get(BK_DEPTH, (w >> NXRS_DEPTH_SHIFT) & 0xFF, NULL);
    s->blk[BK_RASTER] = block_get(BK_RASTER, ((w >> NXRS_RASTER_SHIFT) & 3) | ((w >> NXRS_CWRITE_SHIFT) & 15) << 8, NULL);
    s->blk[BK_SHADERS] = block_get(BK_SHADERS, (a[1] & 0xFFFF) | (a[2] & 0xFFFF) << 16, NULL);
    uint32_t lay = a[3] <= MAX_LAYOUTS ? a[3] : 0;
    s->blk[BK_LAYOUT] = block_get(BK_LAYOUT, lay, &g.layout[lay]);
}

/* ---- API: resources --------------------------------------------------------------------------------------- */
static u64 api_mem_create(u64 descAddr) {
    const NxMemBlockDesc* d = (const NxMemBlockDesc*)descAddr;
    if (g.nMem >= MAX_MEMBLOCKS || (d->storage & 0xFFF) || (d->size & 0xFFF) || !d->size) return 0;
    DkMemBlockMaker m;
    dkMemBlockMakerDefaults(&m, g.dev, d->size);
    m.storage = (void*)d->storage;   /* caller-backed: guest memory below 4 GiB */
    m.flags = d->kind == NxMem_Code ? (DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached | DkMemBlockFlags_Code)
            : d->kind == NxMem_Image ? (DkMemBlockFlags_GpuCached | DkMemBlockFlags_Image)
            : (DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached);
    DkMemBlock b = dkMemBlockCreate(&m);
    if (!b) return 0;
    uint32_t id = ++g.nMem;
    g.mem[id] = b; g.memKind[id] = d->kind;
    if (d->kind == NxMem_Image && !g.imagePool) { g.imagePool = id; g.imagePoolHead = 0; }
    return id;
}

static u64 api_mem_gpu_addr(u64 id) { return mem_addr((uint32_t)id, 0); }

static u64 api_shader_create(u64 dksh, u64 size, u64 stage) {
    (void)stage;
    typedef struct { uint32_t magic, header_sz, control_sz, code_sz, programs_off, num_programs; } DkshHeader;
    const uint8_t* p = (const uint8_t*)dksh;
    DkshHeader h; memcpy(&h, p, sizeof h);
    if (h.magic != 0x48534B44u /* "DKSH" */ || h.control_sz + h.code_sz > size || g.nShaders + 1 >= MAX_SHADERS) return 0;
    uint32_t off = (g.codeHead + DK_SHADER_CODE_ALIGNMENT - 1) & ~(DK_SHADER_CODE_ALIGNMENT - 1);
    if (off + h.code_sz > CODE_POOL - DK_SHADER_CODE_UNUSABLE_SIZE) return 0;
    memcpy((uint8_t*)dkMemBlockGetCpuAddr(g.codeMem) + off, p + h.control_sz, h.code_sz);
    g.codeHead = off + h.code_sz;
    DkShaderMaker m;
    dkShaderMakerDefaults(&m, g.codeMem, off);
    m.control = p;   /* read during initialization only */
    uint32_t id = ++g.nShaders;
    dkShaderInitialize(&g.shader[id], &m);
    return dkShaderIsValid(&g.shader[id]) ? id : 0;
}

static void submit_and_wait(DkCmdBuf cb) {
    dkQueueSubmitCommands(g.queue, dkCmdBufFinishList(cb));
    dkQueueWaitIdle(g.queue);
    dkCmdBufClear(cb);
}

static u64 api_image_create(u64 descAddr) {
    const NxImageDesc* d = (const NxImageDesc*)descAddr;
    DkImageFormat fmt = dk_format(d->format);
    if (fmt == DkImageFormat_None || !g.imagePool || g.nImages + 1 >= MAX_IMAGES) return 0;
    DkImageLayoutMaker lm;
    dkImageLayoutMakerDefaults(&lm, g.dev);
    lm.format = fmt;
    lm.flags = (d->flags & (NxImg_RenderTarget | NxImg_Depth)) ? DkImageFlags_UsageRender | DkImageFlags_HwCompression : 0;
    lm.dimensions[0] = d->width; lm.dimensions[1] = d->height;
    lm.mipLevels = d->mips ? d->mips : 1;
    DkImageLayout layout;
    dkImageLayoutInitialize(&layout, &lm);
    uint32_t align = dkImageLayoutGetAlignment(&layout);
    uint64_t size = dkImageLayoutGetSize(&layout);
    uint32_t off = (g.imagePoolHead + align - 1) & ~(align - 1);
    if (off + size > dkMemBlockGetSize(g.mem[g.imagePool])) return 0;
    g.imagePoolHead = off + (uint32_t)size;
    uint32_t id = ++g.nImages;
    dkImageInitialize(&g.image[id], &layout, g.mem[g.imagePool], off);
    g.imageIsRT[id] = (d->flags & NxImg_RenderTarget) != 0;

    DkImageView view;
    dkImageViewDefaults(&view, &g.image[id]);
    static const DkImageSwizzle sw[4][4] = {
        {DkImageSwizzle_Red, DkImageSwizzle_Green, DkImageSwizzle_Blue, DkImageSwizzle_Alpha},
        {DkImageSwizzle_Red, DkImageSwizzle_Red, DkImageSwizzle_Red, DkImageSwizzle_One},     /* L8 */
        {DkImageSwizzle_Zero, DkImageSwizzle_Zero, DkImageSwizzle_Zero, DkImageSwizzle_Red},  /* A8 */
        {DkImageSwizzle_Red, DkImageSwizzle_Red, DkImageSwizzle_Red, DkImageSwizzle_Green}};  /* A8L8 */
    memcpy(view.swizzle, sw[d->swizzle & 3], sizeof view.swizzle);
    dkImageDescriptorInitialize(&g.imageDesc[id], &view, false, false);
    g.descDirty = true;

    if (d->data) {   /* upload all mips through the staging block, synchronously (load time) */
        const uint8_t* src = (const uint8_t*)d->data;
        uint8_t* stage = (uint8_t*)dkMemBlockGetCpuAddr(g.stagingMem);
        uint32_t sOff = 0, w = d->width, h = d->height;
        DkImageView dst; dkImageViewDefaults(&dst, &g.image[id]);
        for (uint32_t m = 0; m < lm.mipLevels; m++) {
            uint32_t bytes = is_bc(d->format) ? ((w + 3) / 4) * ((h + 3) / 4) * bc_bytes(d->format) : w * h * px_bytes(d->format);
            if (sOff + bytes > STAGING_SIZE) { submit_and_wait(g.upload); sOff = 0; }
            memcpy(stage + sOff, src, bytes);
            DkCopyBuf cb = {dkMemBlockGetGpuAddr(g.stagingMem) + sOff, 0, 0};
            DkImageRect r = {0, 0, 0, w, h, 1};
            dst.mipLevelOffset = (uint8_t)m; dst.mipLevelCount = 1;
            dkCmdBufCopyBufferToImage(g.upload, &cb, &dst, &r, 0);
            src += bytes; sOff = (sOff + bytes + 255) & ~255u;
            w = w > 1 ? w / 2 : 1; h = h > 1 ? h / 2 : 1;
        }
        submit_and_wait(g.upload);
    }
    uint32_t sampler = d->sampler & (MAX_SAMPLERS - 1);
    return (u64)id << 32 | dkMakeTextureHandle(id, sampler);
}

/* ---- render thread: replay one stream ---------------------------------------------------------------------- */
static void record(DkCmdBuf cb, const NxSubmit* s, NxFrameStats* st) {
    const uint32_t* w = (const uint32_t*)s->stream;
    const uint32_t* end = w + s->words;
    uint16_t cur[BK_COUNT] = {0};
    int slot = -1;
    DkImageView colorView, depthView;
    DkGpuAddr ts = dkMemBlockGetGpuAddr(g.tsMem) + (s->frame % NXGPU_FRAMES) * 32;

    dkCmdBufReportCounter(cb, DkCounter_Timestamp, ts);
    if (g.descDirty) {
        dkCmdBufBarrier(cb, DkBarrier_None, DkInvalidateFlags_Descriptors);
        g.descDirty = false;
    }
    dkCmdBufBindImageDescriptorSet(cb, dkMemBlockGetGpuAddr(g.descMem), MAX_IMAGES);
    dkCmdBufBindSamplerDescriptorSet(cb, dkMemBlockGetGpuAddr(g.descMem) + MAX_IMAGES * sizeof(DkImageDescriptor), MAX_SAMPLERS);

    while (w < end) {
        uint32_t op = w[0] & 0xFFFF, argc = w[0] >> 16;
        const uint32_t* a = w + 1;
        w += 1 + argc;
        switch (op) {
        case NxOp_End: w = end; break;
        case NxOp_DefineLayout:
            if (a[0] && a[0] <= MAX_LAYOUTS) memcpy(&g.layout[a[0]], a + 1, sizeof(NxVtxLayout));
            break;
        case NxOp_DefineState: define_state(a); break;
        case NxOp_BeginPass: {
            uint32_t color = a[0], depth = a[1];
            if (color == 0 && slot < 0) slot = dkQueueAcquireImage(g.queue, g.swapchain);
            dkImageViewDefaults(&colorView, color == 0 ? &g.fb[slot] : &g.image[color]);
            if (depth) dkImageViewDefaults(&depthView, &g.image[depth]);
            dkCmdBufBindRenderTarget(cb, &colorView, depth ? &depthView : NULL);
            float wdt = (float)a[5], hgt = (float)a[6];
            DkViewport vp = {0, 0, wdt, hgt, 0.0f, 1.0f};
            DkScissor sc = {0, 0, a[5], a[6]};
            dkCmdBufSetViewports(cb, 0, &vp, 1);
            dkCmdBufSetScissors(cb, 0, &sc, 1);
            if (a[2] & 1) {
                float c[4] = {(a[3] & 255) / 255.0f, (a[3] >> 8 & 255) / 255.0f, (a[3] >> 16 & 255) / 255.0f, (a[3] >> 24) / 255.0f};
                dkCmdBufClearColor(cb, 0, DkColorMask_RGBA, c);
            }
            if ((a[2] & 2) && depth) { float z; memcpy(&z, &a[4], 4); dkCmdBufClearDepthStencil(cb, true, z, 0xFF, 0); }
            memset(cur, 0, sizeof cur);   /* conservative: rebind blocks at pass start */
            break;
        }
        case NxOp_EndPass: break;
        case NxOp_Barrier:
            dkCmdBufBarrier(cb, DkBarrier_Fragments, DkInvalidateFlags_Image);
            break;
        case NxOp_BindState: {
            const StateRec* sr = a[0] && a[0] <= MAX_STATES ? &g.state[a[0]] : NULL;
            st->state_binds++;
            if (!sr) break;
            for (int k = 0; k < BK_COUNT; k++) {
                uint16_t b = sr->blk[k];
                if (b && b != cur[k]) {
                    dkCmdBufReplayCmds(cb, g.block[k][b - 1].w, g.block[k][b - 1].words);
                    cur[k] = b; st->block_binds++;
                }
            }
            break;
        }
        case NxOp_BindUbo: {
            DkGpuAddr ad = mem_addr(a[2], a[3]);
            if (a[0] & 1) dkCmdBufBindUniformBuffer(cb, DkStage_Vertex, a[1], ad, a[4]);
            if (a[0] & 2) dkCmdBufBindUniformBuffer(cb, DkStage_Fragment, a[1], ad, a[4]);
            break;
        }
        case NxOp_BindVtx: dkCmdBufBindVtxBuffer(cb, 0, mem_addr(a[0], a[1]), a[2]); break;
        case NxOp_BindIdx: dkCmdBufBindIdxBuffer(cb, a[3] ? DkIdxFormat_Uint32 : DkIdxFormat_Uint16, mem_addr(a[0], a[1])); break;
        case NxOp_BindTextures: dkCmdBufBindTextures(cb, DkStage_Fragment, a[1], a + 3, a[2]); break;
        case NxOp_Draw: {
            static const DkPrimitive prim[] = {DkPrimitive_Triangles, DkPrimitive_TriangleStrip, DkPrimitive_Lines};
            dkCmdBufDraw(cb, prim[a[0] % 3], a[1], a[2], a[3], a[4]);
            st->draws++;
            break;
        }
        case NxOp_DrawIndexed: {
            static const DkPrimitive prim[] = {DkPrimitive_Triangles, DkPrimitive_TriangleStrip, DkPrimitive_Lines};
            dkCmdBufDrawIndexed(cb, prim[a[0] % 3], a[1], a[2], a[3], (int32_t)a[4], a[5]);
            st->draws++;
            break;
        }
        case NxOp_Viewport: {
            DkViewport vp; memcpy(&vp, a, 16); vp.near = 0.0f; vp.far = 1.0f;
            dkCmdBufSetViewports(cb, 0, &vp, 1);
            break;
        }
        default: break;   /* unknown op: skip its arguments */
        }
    }
    if (slot < 0) slot = dkQueueAcquireImage(g.queue, g.swapchain);   /* nothing drew to the screen */
    dkCmdBufReportCounter(cb, DkCounter_Timestamp, ts + 16);
    dkCmdBufSignalFence(cb, &g.fence[s->frame % NXGPU_FRAMES], false);
    dkQueueSubmitCommands(g.queue, dkCmdBufFinishList(cb));
    dkQueuePresentImage(g.queue, g.swapchain, slot);
}

/* Waits for the frame in slot (if any) and publishes its stats to the guest. Called with g.lock held. */
static void retire_until(uint32_t frame) {
    while (g.retired <= frame && g.retired < g.recorded) {
        uint32_t f = g.retired, slot = f % NXGPU_FRAMES;
        mutexUnlock(&g.lock);
        dkFenceWait(&g.fence[slot], -1);
        mutexLock(&g.lock);
        if (g.retired != f) continue;   /* another thread retired it meanwhile */
        const uint64_t* tsv = (const uint64_t*)((uint8_t*)dkMemBlockGetCpuAddr(g.tsMem) + slot * 32);
        NxFrameStats* out = (NxFrameStats*)g.slotStats[slot];
        if (out && g.slotFrame[slot] == f) {
            NxFrameStats st = g.slotPartial[slot];
            st.gpu_us = tsv[3] > tsv[1] ? (uint32_t)(dkTimestampToNs(tsv[3] - tsv[1]) / 1000) : 0;
            *out = st;
        }
        g.retired = f + 1;
        condvarWakeAll(&g.cvDone);
    }
}

static void render_thread(void* arg) {
    (void)arg;
    mutexLock(&g.lock);
    for (;;) {
        while (!g.hasPending && !g.quit) condvarWait(&g.cvSubmit, &g.lock);
        if (!g.hasPending && g.quit) break;
        NxSubmit s = g.pending;
        g.hasPending = false;
        condvarWakeAll(&g.cvTaken);
        /* the command buffer of this slot is free once the frame NXGPU_FRAMES back has retired */
        if (s.frame >= NXGPU_FRAMES) retire_until(s.frame - NXGPU_FRAMES);
        uint32_t slot = s.frame % NXGPU_FRAMES;
        mutexUnlock(&g.lock);

        u64 t0 = armGetSystemTick();
        dkCmdBufClear(g.cmd[slot]);
        dkCmdBufAddMemory(g.cmd[slot], g.cmdMem, slot * CMDMEM_PER_FRAME, CMDMEM_PER_FRAME);
        NxFrameStats st; memset(&st, 0, sizeof st);
        st.frame = s.frame; st.cmd_words = s.words;
        record(g.cmd[slot], &s, &st);
        u64 t1 = armGetSystemTick();
        st.record_us = (uint32_t)(armTicksToNs(t1 - t0) / 1000);
        st.present_interval_us = g.lastPresentTick ? (uint32_t)(armTicksToNs(t1 - g.lastPresentTick) / 1000) : 0;
        g.lastPresentTick = t1;

        mutexLock(&g.lock);
        g.slotPartial[slot] = st; g.slotStats[slot] = s.stats; g.slotFrame[slot] = s.frame;
        g.recorded = s.frame + 1;
        condvarWakeAll(&g.cvDone);
    }
    mutexUnlock(&g.lock);
}

static u64 api_submit(u64 subAddr) {
    mutexLock(&g.lock);
    while (g.hasPending) condvarWait(&g.cvTaken, &g.lock);   /* at most one stream waits: render thread is 1 frame behind */
    g.pending = *(const NxSubmit*)subAddr;
    g.hasPending = true;
    condvarWakeOne(&g.cvSubmit);
    mutexUnlock(&g.lock);
    return 0;
}

static u64 api_wait_frame(u64 frame) {
    mutexLock(&g.lock);
    while (g.recorded <= frame && !g.quit) condvarWait(&g.cvDone, &g.lock);
    retire_until((uint32_t)frame);
    mutexUnlock(&g.lock);
    return 0;
}

static u64 api_shutdown(void) {
    mutexLock(&g.lock);
    while (g.hasPending) condvarWait(&g.cvTaken, &g.lock);
    g.quit = true;
    condvarWakeAll(&g.cvSubmit);
    mutexUnlock(&g.lock);
    threadWaitForExit(&g.thread);
    threadClose(&g.thread);
    dkQueueWaitIdle(g.queue);
    return 0;
}

/* ---- init ------------------------------------------------------------------------------------------------- */
static DkMemBlock host_block(uint32_t size, uint32_t flags) {
    DkMemBlockMaker m;
    dkMemBlockMakerDefaults(&m, g.dev, size);
    m.flags = flags;
    return dkMemBlockCreate(&m);
}

bool nxgpu_init(NxGpuApi* api, uint32_t width, uint32_t height) {
    memset(&g, 0, sizeof g);
    g.width = width; g.height = height;
    DkDeviceMaker dm;
    dkDeviceMakerDefaults(&dm);
    dm.cbDebug = dk_debug;
    g.dev = dkDeviceCreate(&dm);
    if (!g.dev) return false;

    /* swapchain images */
    DkImageLayoutMaker lm;
    dkImageLayoutMakerDefaults(&lm, g.dev);
    lm.flags = DkImageFlags_UsageRender | DkImageFlags_UsagePresent | DkImageFlags_HwCompression;
    lm.format = DkImageFormat_RGBA8_Unorm;
    lm.dimensions[0] = width; lm.dimensions[1] = height;
    DkImageLayout fbl;
    dkImageLayoutInitialize(&fbl, &lm);
    uint32_t fbAlign = dkImageLayoutGetAlignment(&fbl);
    uint32_t fbSize = ((uint32_t)dkImageLayoutGetSize(&fbl) + fbAlign - 1) & ~(fbAlign - 1);
    g.fbMem = host_block((NXGPU_SWAP_IMAGES * fbSize + 0xFFF) & ~0xFFFu, DkMemBlockFlags_GpuCached | DkMemBlockFlags_Image);
    DkImage const* fbs[NXGPU_SWAP_IMAGES];
    for (int i = 0; i < NXGPU_SWAP_IMAGES; i++) {
        dkImageInitialize(&g.fb[i], &fbl, g.fbMem, i * fbSize);
        fbs[i] = &g.fb[i];
    }

    DkQueueMaker qm;
    dkQueueMakerDefaults(&qm, g.dev);
    qm.flags = DkQueueFlags_Graphics;
    qm.commandMemorySize = 1u << 20;
    qm.flushThreshold = 128u << 10;
    g.queue = dkQueueCreate(&qm);

    DkSwapchainMaker sm;
    dkSwapchainMakerDefaults(&sm, g.dev, nwindowGetDefault(), fbs, NXGPU_SWAP_IMAGES);
    g.swapchain = dkSwapchainCreate(&sm);

    g.cmdMem = host_block(NXGPU_FRAMES * CMDMEM_PER_FRAME, DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached);
    DkCmdBufMaker cm;
    dkCmdBufMakerDefaults(&cm, g.dev);
    for (int i = 0; i < NXGPU_FRAMES; i++) g.cmd[i] = dkCmdBufCreate(&cm);
    g.upload = dkCmdBufCreate(&cm);
    g.capture = dkCmdBufCreate(&cm);
    DkMemBlock upMem = host_block(64u << 10, DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached);
    dkCmdBufAddMemory(g.upload, upMem, 0, 64u << 10);

    uint32_t descSize = (MAX_IMAGES * sizeof(DkImageDescriptor) + MAX_SAMPLERS * sizeof(DkSamplerDescriptor) + 0xFFF) & ~0xFFFu;
    g.descMem = host_block(descSize, DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached);
    g.imageDesc = (DkImageDescriptor*)dkMemBlockGetCpuAddr(g.descMem);
    g.samplerDesc = (DkSamplerDescriptor*)(g.imageDesc + MAX_IMAGES);
    for (uint32_t i = 0; i < MAX_SAMPLERS; i++) {   /* NxSampler bits: point(1) wrap(2) mirror(4) */
        DkSampler s; dkSamplerDefaults(&s);
        s.minFilter = s.magFilter = (i & NxSmp_Point) ? DkFilter_Nearest : DkFilter_Linear;
        s.mipFilter = (i & NxSmp_Point) ? DkMipFilter_Nearest : DkMipFilter_Linear;
        DkWrapMode wm = (i & NxSmp_Mirror) ? DkWrapMode_MirroredRepeat : (i & NxSmp_Wrap) ? DkWrapMode_Repeat : DkWrapMode_ClampToEdge;
        s.wrapMode[0] = s.wrapMode[1] = s.wrapMode[2] = wm;
        dkSamplerDescriptorInitialize(&g.samplerDesc[i], &s);
    }
    g.codeMem = host_block(CODE_POOL, DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached | DkMemBlockFlags_Code);
    g.stagingMem = host_block(STAGING_SIZE, DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached);
    g.tsMem = host_block(0x1000, DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached);
    g.descDirty = true;

    mutexInit(&g.lock);
    condvarInit(&g.cvSubmit); condvarInit(&g.cvTaken); condvarInit(&g.cvDone);
    if (R_FAILED(threadCreate(&g.thread, render_thread, NULL, NULL, 0x10000, 0x2B, 1))) return false;
    threadStart(&g.thread);

    memset(api, 0, sizeof *api);
    api->magic = NXGPU_MAGIC; api->version = NXGPU_VERSION; api->size = sizeof *api;
    api->frames_in_flight = NXGPU_FRAMES; api->width = width; api->height = height;
    api->mem_create = (u64)api_mem_create; api->mem_gpu_addr = (u64)api_mem_gpu_addr;
    api->image_create = (u64)api_image_create; api->shader_create = (u64)api_shader_create;
    api->submit = (u64)api_submit; api->wait_frame = (u64)api_wait_frame; api->shutdown = (u64)api_shutdown;
    return true;
}
