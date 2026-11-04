/* fnv-nx native renderer, LP64 backend on deko3d (stream S6n, docs/RENDER_NATIVE.md §3, nx/gpu_abi.h).
 *
 * Lives in the Switch host module next to libnx (S5 model); the ILP32 front end reaches it through ext_call
 * (host/nxgpu_host.c registers NXGPU_EXT_MODULE -> nxgpu_dispatch over the table filled here). The front submits
 * one command stream per frame; a render thread replays it into a deko3d command buffer one frame behind the
 * caller, the GPU runs up to NXGPU_FRAMES frames behind that.
 *
 * State blocks: each distinct blend, depth-stencil and rasterizer word of an NxRenderState, shader pair and vertex
 * layout is captured once into Maxwell command words (dkCmdBufBeginCaptureCmds); binding a state id replays only
 * the blocks whose id differs from the bound one. Stencil ref/masks, blend constant, depth bias, viewport and
 * scissor are dynamic.
 *
 * Device: DkDeviceFlags_DepthZeroToOne | OriginUpperLeft | YAxisPointsUp (deko3d's default): clip +y is the top of
 * the image, depth [0,1] - the D3D9 conventions of nx/state.h without shader fix-ups.
 * Unverified on hardware (traps, docs/RENDER_NATIVE.md): NXGPU_FRONT_FACE, the fill rule on pixel-centre ties, the
 * A4R4G4B4 native swizzle. The N0 cull/format scenes check each of them on the console.
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
#define MAX_SHADERS       512
#define MAX_STATES        1024
#define MAX_LAYOUTS       64
#define MAX_BLOCKS        256      /* captured state blocks per kind */
#define CAPTURE_WORDS     96
#define CMDMEM_PER_FRAME  (2u << 20)
#define CODE_POOL         (8u << 20)
#define STAGING_SIZE      (16u << 20)
#define IMAGE_POOL        (192u << 20)
#define READBACK_SIZE     (16u << 20)

/* "CW on screen = front" (nx/state.h). To be confirmed by the cull scene on hardware. */
#define NXGPU_FRONT_FACE  DkFrontFace_CW

enum { BK_BLEND, BK_DEPTH, BK_RASTER, BK_SHADERS, BK_LAYOUT, BK_COUNT };

typedef struct { uint64_t key; uint32_t words; uint32_t w[CAPTURE_WORDS]; } Block;
typedef struct { uint16_t blk[BK_COUNT]; uint8_t defined; NxRenderState rs; } StateRec;
typedef struct { DkImage image; uint8_t format, isRT, isDepth, srgbView; uint16_t w, h; } ImageRec;

static struct {
    DkDevice dev;
    DkQueue queue;
    DkSwapchain swapchain;
    DkMemBlock fbMem, cmdMem, descMem, codeMem, stagingMem, tsMem, imageMem, readbackMem;
    DkImage fb[NXGPU_SWAP_IMAGES];
    DkCmdBuf cmd[NXGPU_FRAMES], upload, capture;
    DkFence fence[NXGPU_FRAMES];
    uint32_t width, height;
    int lastSlot;

    DkMemBlock mem[MAX_MEMBLOCKS + 1];
    DkGpuAddr memAddr[MAX_MEMBLOCKS + 1];   /* cached dkMemBlockGetGpuAddr (record looks it up per bind) */
    uint32_t nMem;
    uint32_t imageHead;

    ImageRec img[NX_MAX_IMAGES + 1];
    uint32_t nImages;                              /* slot 0 unused; an sRGB view takes id + 1 */
    DkImageDescriptor* imageDesc;
    DkSamplerDescriptor* samplerDesc;
    uint32_t nSamplers;
    bool descDirty;

    DkShader shader[MAX_SHADERS + 1];
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
    uint32_t recorded, retired;
    uint64_t slotStats[NXGPU_FRAMES];
    uint32_t slotFrame[NXGPU_FRAMES];
    NxFrameStats slotPartial[NXGPU_FRAMES];
    uint64_t lastPresentTick;
    NxGpuApi api;
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

typedef struct { DkImageFormat fmt, srgb; uint8_t sw[4]; uint8_t bc, bytes; } NativeFmt;
#define SWID {0, 1, 2, 3}
static NativeFmt native(uint32_t f) {
    NativeFmt n = {DkImageFormat_None, DkImageFormat_None, SWID, 0, 0};
#define N(nx, dk, s, b, by) case nx: { NativeFmt t = {dk, s, SWID, b, by}; return t; }
    switch (f) {
    N(NxFmt_RGBA8, DkImageFormat_RGBA8_Unorm, DkImageFormat_RGBA8_Unorm_sRGB, 0, 4)
    N(NxFmt_BGRA8, DkImageFormat_BGRA8_Unorm, DkImageFormat_BGRA8_Unorm_sRGB, 0, 4)
    N(NxFmt_BGRX8, DkImageFormat_BGRX8_Unorm, DkImageFormat_BGRX8_Unorm_sRGB, 0, 4)
    N(NxFmt_RGBX8, DkImageFormat_RGBX8_Unorm, DkImageFormat_RGBX8_Unorm_sRGB, 0, 4)
    N(NxFmt_R5G6B5, DkImageFormat_BGR565_Unorm, DkImageFormat_None, 0, 2)
    N(NxFmt_A1R5G5B5, DkImageFormat_BGR5A1_Unorm, DkImageFormat_None, 0, 2)
    N(NxFmt_X1R5G5B5, DkImageFormat_BGR5_Unorm, DkImageFormat_None, 0, 2)
    N(NxFmt_RGB10A2, DkImageFormat_RGB10A2_Unorm, DkImageFormat_None, 0, 4)
    N(NxFmt_R8, DkImageFormat_R8_Unorm, DkImageFormat_None, 0, 1)
    N(NxFmt_RG8, DkImageFormat_RG8_Unorm, DkImageFormat_None, 0, 2)
    N(NxFmt_R16, DkImageFormat_R16_Unorm, DkImageFormat_None, 0, 2)
    N(NxFmt_RG16, DkImageFormat_RG16_Unorm, DkImageFormat_None, 0, 4)
    N(NxFmt_RGBA16, DkImageFormat_RGBA16_Unorm, DkImageFormat_None, 0, 8)
    N(NxFmt_RG8S, DkImageFormat_RG8_Snorm, DkImageFormat_None, 0, 2)
    N(NxFmt_RGBA8S, DkImageFormat_RGBA8_Snorm, DkImageFormat_None, 0, 4)
    N(NxFmt_RG16S, DkImageFormat_RG16_Snorm, DkImageFormat_None, 0, 4)
    N(NxFmt_RGBA16S, DkImageFormat_RGBA16_Snorm, DkImageFormat_None, 0, 8)
    N(NxFmt_R16F, DkImageFormat_R16_Float, DkImageFormat_None, 0, 2)
    N(NxFmt_RG16F, DkImageFormat_RG16_Float, DkImageFormat_None, 0, 4)
    N(NxFmt_RGBA16F, DkImageFormat_RGBA16_Float, DkImageFormat_None, 0, 8)
    N(NxFmt_R32F, DkImageFormat_R32_Float, DkImageFormat_None, 0, 4)
    N(NxFmt_RG32F, DkImageFormat_RG32_Float, DkImageFormat_None, 0, 8)
    N(NxFmt_RGBA32F, DkImageFormat_RGBA32_Float, DkImageFormat_None, 0, 16)
    N(NxFmt_BC1, DkImageFormat_RGBA_BC1, DkImageFormat_RGBA_BC1_sRGB, 1, 8)
    N(NxFmt_BC2, DkImageFormat_RGBA_BC2, DkImageFormat_RGBA_BC2_sRGB, 1, 16)
    N(NxFmt_BC3, DkImageFormat_RGBA_BC3, DkImageFormat_RGBA_BC3_sRGB, 1, 16)
    N(NxFmt_BC4, DkImageFormat_R_BC4_Unorm, DkImageFormat_None, 1, 8)
    N(NxFmt_BC5, DkImageFormat_RG_BC5_Unorm, DkImageFormat_None, 1, 16)
    N(NxFmt_Z16, DkImageFormat_Z16, DkImageFormat_None, 0, 2)
    N(NxFmt_Z24X8, DkImageFormat_Z24X8, DkImageFormat_None, 0, 4)
    N(NxFmt_Z24S8, DkImageFormat_Z24S8, DkImageFormat_None, 0, 4)
    N(NxFmt_Z32F, DkImageFormat_ZF32, DkImageFormat_None, 0, 4)
    N(NxFmt_Z32FS8, DkImageFormat_ZF32_X24S8, DkImageFormat_None, 0, 8)
    case NxFmt_A4R4G4B4: {   /* RGBA4_Unorm = hw A4B4G4R4: the low nibble (D3D blue) reads as red */
        NativeFmt t = {DkImageFormat_RGBA4_Unorm, DkImageFormat_None, {2, 1, 0, 3}, 0, 2};
        return t;
    }
    default: return n;
    }
#undef N
}
static int is_depth(uint32_t f) {
    return f == NxFmt_Z16 || f == NxFmt_Z24X8 || f == NxFmt_Z24S8 || f == NxFmt_Z32F || f == NxFmt_Z32FS8;
}

static DkGpuAddr mem_addr(uint32_t id, uint32_t off) {
    return id && id <= g.nMem ? g.memAddr[id] + off : DK_GPU_ADDR_INVALID;
}
static DkCompareOp dk_cmp(uint32_t c) { return (DkCompareOp)((c & 7) + 1); }      /* NxCompare + 1 */
static DkStencilOp dk_sop(uint32_t o) {
    static const DkStencilOp t[8] = {DkStencilOp_Keep, DkStencilOp_Zero, DkStencilOp_Replace, DkStencilOp_Incr,
                                     DkStencilOp_Decr, DkStencilOp_Invert, DkStencilOp_IncrWrap, DkStencilOp_DecrWrap};
    return t[o & 7];
}
static DkBlendFactor dk_bf(uint32_t f) {
    static const DkBlendFactor t[NxBf_Count] = {DkBlendFactor_Zero, DkBlendFactor_One, DkBlendFactor_SrcColor,
        DkBlendFactor_InvSrcColor, DkBlendFactor_SrcAlpha, DkBlendFactor_InvSrcAlpha, DkBlendFactor_DstAlpha,
        DkBlendFactor_InvDstAlpha, DkBlendFactor_DstColor, DkBlendFactor_InvDstColor, DkBlendFactor_SrcAlphaSaturate,
        DkBlendFactor_ConstColor, DkBlendFactor_InvConstColor, DkBlendFactor_Src1Color, DkBlendFactor_InvSrc1Color};
    return f < NxBf_Count ? t[f] : DkBlendFactor_Zero;
}
static DkBlendOp dk_bop(uint32_t o) { return o < 5 ? (DkBlendOp)(o + 1) : DkBlendOp_Add; }

/* ---- state block capture ------------------------------------------------------------------------------------ */
static uint16_t block_get(int kind, uint64_t key, const void* arg) {
    for (uint32_t i = 0; i < g.nBlock[kind]; i++)
        if (g.block[kind][i].key == key) return (uint16_t)(i + 1);
    if (g.nBlock[kind] >= MAX_BLOCKS) return 0;
    Block* b = &g.block[kind][g.nBlock[kind]];
    b->key = key;
    dkCmdBufBeginCaptureCmds(g.capture, b->w, CAPTURE_WORDS);
    switch (kind) {
    case BK_BLEND: {   /* w[0] */
        DkColorState cs; dkColorStateDefaults(&cs);
        DkBlendState bs; dkBlendStateDefaults(&bs);
        DkColorWriteState cw; dkColorWriteStateDefaults(&cw);
        if (key & 1) {
            for (int rt = 0; rt < 4; rt++) dkColorStateSetBlendEnable(&cs, rt, true);
            dkBlendStateSetFactors(&bs, dk_bf(NXRS_GET(key, NXRS_B_SRC, 5)), dk_bf(NXRS_GET(key, NXRS_B_DST, 5)),
                                   dk_bf(NXRS_GET(key, NXRS_B_SRCA, 5)), dk_bf(NXRS_GET(key, NXRS_B_DSTA, 5)));
            dkBlendStateSetOps(&bs, dk_bop(NXRS_GET(key, NXRS_B_OP, 3)), dk_bop(NXRS_GET(key, NXRS_B_OPA, 3)));
        }
        for (int rt = 0; rt < 4; rt++) dkColorWriteStateSetMask(&cw, rt, NXRS_GET(key, NXRS_B_MASK(rt), 4));
        DkBlendState all[4] = {bs, bs, bs, bs};
        dkCmdBufBindColorState(g.capture, &cs);
        dkCmdBufBindBlendStates(g.capture, 0, all, 4);
        dkCmdBufBindColorWriteState(g.capture, &cw);
        break;
    }
    case BK_DEPTH: {   /* w[1] bits 0-31 (masks/ref are dynamic) */
        DkDepthStencilState ds; dkDepthStencilStateDefaults(&ds);
        ds.depthTestEnable = NXRS_GET(key, NXRS_D_TEST, 1);
        ds.depthWriteEnable = NXRS_GET(key, NXRS_D_WRITE, 1);
        ds.depthCompareOp = dk_cmp(NXRS_GET(key, NXRS_D_FUNC, 3));
        ds.stencilTestEnable = NXRS_GET(key, NXRS_S_ENABLE, 1);
        ds.stencilFrontCompareOp = dk_cmp(NXRS_GET(key, NXRS_S_FUNC, 3));
        ds.stencilFrontFailOp = dk_sop(NXRS_GET(key, NXRS_S_FAIL, 3));
        ds.stencilFrontDepthFailOp = dk_sop(NXRS_GET(key, NXRS_S_ZFAIL, 3));
        ds.stencilFrontPassOp = dk_sop(NXRS_GET(key, NXRS_S_PASS, 3));
        ds.stencilBackCompareOp = dk_cmp(NXRS_GET(key, NXRS_S_BFUNC, 3));
        ds.stencilBackFailOp = dk_sop(NXRS_GET(key, NXRS_S_BFAIL, 3));
        ds.stencilBackDepthFailOp = dk_sop(NXRS_GET(key, NXRS_S_BZFAIL, 3));
        ds.stencilBackPassOp = dk_sop(NXRS_GET(key, NXRS_S_BPASS, 3));
        dkCmdBufBindDepthStencilState(g.capture, &ds);
        break;
    }
    case BK_RASTER: {  /* w[2] */
        DkRasterizerState rs; dkRasterizerStateDefaults(&rs);
        uint32_t cull = NXRS_GET(key, NXRS_R_CULL, 2), fill = NXRS_GET(key, NXRS_R_FILL, 2);
        rs.cullMode = cull == NxCull_CW ? DkFace_Front : cull == NxCull_CCW ? DkFace_Back : DkFace_None;
        rs.frontFace = NXGPU_FRONT_FACE;
        rs.provokingVertex = DkProvokingVertex_First;
        rs.polygonModeFront = rs.polygonModeBack =
            fill == NxFill_Wireframe ? DkPolygonMode_Line : fill == NxFill_Point ? DkPolygonMode_Point : DkPolygonMode_Fill;
        rs.depthBiasEnableMask = NXRS_GET(key, NXRS_R_BIAS, 1) ? DkPolygonFlag_All : 0;
        dkCmdBufBindRasterizerState(g.capture, &rs);
        break;
    }
    case BK_SHADERS: {
        uint32_t vs = (uint32_t)key & 0xFFFF, ps = (uint32_t)(key >> 16) & 0xFFFF;
        if (vs > g.nShaders || ps > g.nShaders) break;
        DkShader const* sh[2] = {&g.shader[vs], &g.shader[ps]};
        dkCmdBufBindShaders(g.capture, DkStageFlag_GraphicsMask, sh, 2);
        break;
    }
    case BK_LAYOUT: {
        const NxVtxLayout* l = (const NxVtxLayout*)arg;
        DkVtxAttribState at[16];
        static const DkVtxAttribSize sz32[] = {DkVtxAttribSize_1x32, DkVtxAttribSize_2x32, DkVtxAttribSize_3x32, DkVtxAttribSize_4x32};
        static const DkVtxAttribSize sz16[] = {DkVtxAttribSize_1x16, DkVtxAttribSize_2x16, DkVtxAttribSize_3x16, DkVtxAttribSize_4x16};
        static const DkVtxAttribSize sz8[] = {DkVtxAttribSize_1x8, DkVtxAttribSize_2x8, DkVtxAttribSize_3x8, DkVtxAttribSize_4x8};
        uint32_t na = l->count > 8 ? 8 : l->count, n = 0;
        /* attribute i at location i, or at attr[i].location - 1; locations no attribute feeds read a fixed 0 */
        for (uint32_t k = 0; k < 16; k++) { memset(&at[k], 0, sizeof at[k]); at[k].isFixed = 1; at[k].size = DkVtxAttribSize_4x32; at[k].type = DkVtxAttribType_Float; }
        for (uint32_t i = 0; i < na; i++) {
            uint32_t loc = l->attr[i].location ? (uint32_t)l->attr[i].location - 1u : i;
            if (loc > 15) continue;
            DkVtxAttribState* a = &at[loc];
            uint32_t c = (l->attr[i].components - 1) & 3;
            memset(a, 0, sizeof *a);
            a->bufferId = 0; a->offset = l->attr[i].offset;
            switch (l->attr[i].type) {
            case NxAttr_Unorm8: a->size = sz8[c]; a->type = DkVtxAttribType_Unorm; break;
            case NxAttr_Snorm16: a->size = sz16[c]; a->type = DkVtxAttribType_Snorm; break;
            case NxAttr_Float16: a->size = sz16[c]; a->type = DkVtxAttribType_Float; break;
            default: a->size = sz32[c]; a->type = DkVtxAttribType_Float; break;
            }
            if (loc + 1 > n) n = loc + 1;
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
    StateRec* s = &g.state[id];
    for (int i = 0; i < 4; i++) s->rs.w[i] = a[4 + 2 * i] | (uint64_t)a[5 + 2 * i] << 32;
    s->blk[BK_BLEND] = block_get(BK_BLEND, s->rs.w[0], NULL);
    s->blk[BK_DEPTH] = block_get(BK_DEPTH, s->rs.w[1] & 0xFFFFFFFFu, NULL);
    s->blk[BK_RASTER] = block_get(BK_RASTER, s->rs.w[2], NULL);
    s->blk[BK_SHADERS] = block_get(BK_SHADERS, (a[1] & 0xFFFF) | (a[2] & 0xFFFF) << 16, NULL);
    uint32_t lay = a[3] <= MAX_LAYOUTS ? a[3] : 0;
    s->blk[BK_LAYOUT] = block_get(BK_LAYOUT, lay, &g.layout[lay]);
    s->defined = 1;
}

/* ---- API: resources --------------------------------------------------------------------------------------- */
static u64 api_mem_create(u64 descAddr) {
    const NxMemBlockDesc* d = (const NxMemBlockDesc*)descAddr;
    if (!d || g.nMem >= MAX_MEMBLOCKS || (d->storage & 0xFFF) || (d->size & 0xFFF) || !d->size) return 0;
    DkMemBlockMaker m;
    dkMemBlockMakerDefaults(&m, g.dev, d->size);
    m.storage = (void*)d->storage;   /* caller-backed: guest memory below 4 GiB */
    m.flags = d->kind == NxMem_Code ? (DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached | DkMemBlockFlags_Code)
            : (DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached);
    DkMemBlock b = dkMemBlockCreate(&m);
    if (!b) return 0;
    uint32_t id = ++g.nMem;
    g.mem[id] = b;
    g.memAddr[id] = dkMemBlockGetGpuAddr(b);
    return id;
}

static u64 api_mem_gpu_addr(u64 id) { return mem_addr((uint32_t)id, 0); }

static u64 api_shader_create(u64 descAddr) {
    const NxShaderDesc* d = (const NxShaderDesc*)descAddr;
    typedef struct { uint32_t magic, header_sz, control_sz, code_sz, programs_off, num_programs; } DkshHeader;
    if (!d || !d->dksh || d->dksh_size < sizeof(DkshHeader)) return 0;
    const uint8_t* p = (const uint8_t*)d->dksh;
    DkshHeader h; memcpy(&h, p, sizeof h);
    if (h.magic != 0x48534B44u /* "DKSH" */ || h.control_sz + h.code_sz > d->dksh_size || g.nShaders + 1 > MAX_SHADERS) return 0;
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

static DkImageSwizzle dk_swz(uint32_t s) {
    static const DkImageSwizzle t[6] = {DkImageSwizzle_Red, DkImageSwizzle_Green, DkImageSwizzle_Blue,
                                        DkImageSwizzle_Alpha, DkImageSwizzle_Zero, DkImageSwizzle_One};
    return s < 6 ? t[s] : DkImageSwizzle_Zero;
}

static u64 api_image_create(u64 descAddr) {
    const NxImageDesc* d = (const NxImageDesc*)descAddr;
    if (!d || !d->width || !d->height) return 0;
    NativeFmt nf = native(d->format);
    int srgb = nf.srgb != DkImageFormat_None;
    if (nf.fmt == DkImageFormat_None || g.nImages + 1 + srgb > NX_MAX_IMAGES) return 0;
    int cube = (d->flags & NxImg_Cube) != 0;
    DkImageLayoutMaker lm;
    dkImageLayoutMakerDefaults(&lm, g.dev);
    lm.type = cube ? DkImageType_Cubemap : DkImageType_2D;
    lm.format = nf.fmt;
    lm.flags = (d->flags & (NxImg_RenderTarget | NxImg_Depth)) ? DkImageFlags_UsageRender | DkImageFlags_HwCompression : 0;
    lm.dimensions[0] = d->width; lm.dimensions[1] = d->height; lm.dimensions[2] = cube ? 6 : 1;
    lm.mipLevels = d->mips ? d->mips : 1;
    DkImageLayout layout;
    dkImageLayoutInitialize(&layout, &lm);
    uint32_t align = dkImageLayoutGetAlignment(&layout);
    uint64_t size = dkImageLayoutGetSize(&layout);
    uint32_t off = (g.imageHead + align - 1) & ~(align - 1);
    if (off + size > IMAGE_POOL) return 0;
    g.imageHead = off + (uint32_t)size;
    uint32_t id = ++g.nImages;
    ImageRec* im = &g.img[id];
    memset(im, 0, sizeof *im);
    dkImageInitialize(&im->image, &layout, g.imageMem, off);
    im->format = d->format; im->isRT = (d->flags & NxImg_RenderTarget) != 0; im->isDepth = is_depth(d->format);
    im->w = d->width; im->h = d->height; im->srgbView = (uint8_t)srgb;

    /* sampled view: the native swizzle (A4R4G4B4) composed with the caller's */
    uint16_t sw = d->swizzle ? d->swizzle : NXIMG_SWIZZLE_IDENTITY;
    DkImageView view;
    dkImageViewDefaults(&view, &im->image);
    for (int i = 0; i < 4; i++) {
        uint32_t s = sw >> (4 * i) & 15;
        view.swizzle[i] = dk_swz(s < 4 ? nf.sw[s] : s);
    }
    dkImageDescriptorInitialize(&g.imageDesc[id], &view, false, false);
    if (srgb) {
        view.format = nf.srgb;
        g.nImages++;
        g.img[id + 1] = *im;
        dkImageDescriptorInitialize(&g.imageDesc[id + 1], &view, false, false);
    }
    g.descDirty = true;

    if (d->data) {   /* upload all mips (and faces) through the staging block, synchronously (load time) */
        const uint8_t* src = (const uint8_t*)d->data;
        const uint8_t* end = src + d->data_size;
        uint8_t* stage = (uint8_t*)dkMemBlockGetCpuAddr(g.stagingMem);
        uint32_t sOff = 0;
        DkImageView dst; dkImageViewDefaults(&dst, &im->image);
        for (int face = 0; face < (cube ? 6 : 1); face++) {
            uint32_t w = d->width, h = d->height;
            for (uint32_t m = 0; m < lm.mipLevels; m++) {
                uint32_t bytes = nf.bc ? ((w + 3) / 4) * ((h + 3) / 4) * nf.bytes : w * h * nf.bytes;
                if (src + bytes > end) break;
                if (sOff + bytes > STAGING_SIZE) { submit_and_wait(g.upload); sOff = 0; }
                memcpy(stage + sOff, src, bytes);
                DkCopyBuf cb = {dkMemBlockGetGpuAddr(g.stagingMem) + sOff, 0, 0};
                DkImageRect r = {0, 0, (uint32_t)face, w, h, 1};
                dst.mipLevelOffset = (uint8_t)m; dst.mipLevelCount = 1;
                dkCmdBufCopyBufferToImage(g.upload, &cb, &dst, &r, 0);
                src += bytes; sOff = (sOff + bytes + 255) & ~255u;
                w = w > 1 ? w / 2 : 1; h = h > 1 ? h / 2 : 1;
            }
        }
        submit_and_wait(g.upload);
    }
    return id;
}

static DkWrapMode dk_wrap(uint32_t w) {
    switch (w) {
    case NxWrap_Repeat: return DkWrapMode_Repeat;
    case NxWrap_Mirror: return DkWrapMode_MirroredRepeat;
    case NxWrap_Border: return DkWrapMode_ClampToBorder;
    case NxWrap_MirrorOnce: return DkWrapMode_MirrorClampToEdge;
    default: return DkWrapMode_ClampToEdge;
    }
}

static u64 api_sampler_create(u64 a) {
    const NxSamplerState* s = (const NxSamplerState*)a;
    if (!s || g.nSamplers + 1 >= NX_MAX_SAMPLERS) return 0;
    uint32_t id = ++g.nSamplers;
    DkSampler d; dkSamplerDefaults(&d);
    uint32_t mip = s->filter >> NXSMP_MIP_SHIFT & 3;
    d.minFilter = (s->filter & NXSMP_MIN_LINEAR) ? DkFilter_Linear : DkFilter_Nearest;
    d.magFilter = (s->filter & NXSMP_MAG_LINEAR) ? DkFilter_Linear : DkFilter_Nearest;
    d.mipFilter = mip == NxMip_None ? DkMipFilter_None : mip == NxMip_Nearest ? DkMipFilter_Nearest : DkMipFilter_Linear;
    d.wrapMode[0] = dk_wrap(s->wrap & 7); d.wrapMode[1] = dk_wrap(s->wrap >> 3 & 7); d.wrapMode[2] = dk_wrap(s->wrap >> 6 & 7);
    d.lodClampMin = (float)s->minLod; d.lodClampMax = (float)s->maxLod; d.lodBias = s->lodBias;
    d.compareEnable = (s->filter & NXSMP_SHADOW) != 0; d.compareOp = DkCompareOp_Lequal;
    for (int i = 0; i < 4; i++) d.borderColor[i].value_f = (s->border >> (8 * i) & 255) / 255.0f;
    d.maxAnisotropy = s->maxAniso > 1 ? (float)s->maxAniso : 1.0f;
    dkSamplerDescriptorInitialize(&g.samplerDesc[id], &d);
    g.descDirty = true;
    return id;
}

/* ---- render thread: replay one stream ---------------------------------------------------------------------- */
typedef struct {
    DkImageView color, depth;
    uint32_t colorId, depthId, w, h;
    int haveColor, haveDepth, srgbBound;
    DkScissor scissor;                /* the dynamic scissor */
    int scissorOn;
    uint32_t stencilRef;
} PassState;

static void bind_targets(DkCmdBuf cb, PassState* ps, int srgb) {
    DkImageView v = ps->color;
    if (srgb && ps->colorId && g.img[ps->colorId].srgbView) {
        NativeFmt nf = native(g.img[ps->colorId].format);
        v.format = nf.srgb;
    }
    dkCmdBufBindRenderTarget(cb, ps->haveColor ? &v : NULL, ps->haveDepth ? &ps->depth : NULL);
    ps->srgbBound = srgb;
}

static void apply_scissor(DkCmdBuf cb, PassState* ps) {
    DkScissor full = {0, 0, ps->w, ps->h};
    dkCmdBufSetScissors(cb, 0, ps->scissorOn ? &ps->scissor : &full, 1);
}

static void apply_stencil(DkCmdBuf cb, uint64_t w1, uint32_t ref) {
    uint8_t wm = (uint8_t)NXRS_GET(w1, NXRS_S_WRITEMASK, 8), rm = (uint8_t)NXRS_GET(w1, NXRS_S_READMASK, 8);
    dkCmdBufSetStencil(cb, DkFace_FrontAndBack, wm, (uint8_t)ref, rm);
}

static void record(DkCmdBuf cb, const NxSubmit* s, NxFrameStats* st) {
    const uint32_t* w = (const uint32_t*)s->stream;
    const uint32_t* end = w + s->words;
    uint16_t cur[BK_COUNT] = {0};
    uint32_t curState = 0;
    /* uniform buffers bound in this command list, per stage and slot: a bind of the same range is skipped */
    DkGpuAddr uboAddr[2][16] = {{0}};   /* per stage and slot (V1 U3: slots 4-15 aliased slot & 3) */
    uint32_t uboSize[2][16] = {{0}};
    int slot = -1;
    PassState ps;
    memset(&ps, 0, sizeof ps);
    DkGpuAddr ts = dkMemBlockGetGpuAddr(g.tsMem) + (s->frame % NXGPU_FRAMES) * 32;

    dkCmdBufReportCounter(cb, DkCounter_Timestamp, ts);
    if (g.descDirty) {
        dkCmdBufBarrier(cb, DkBarrier_None, DkInvalidateFlags_Descriptors);
        g.descDirty = false;
    }
    dkCmdBufBindImageDescriptorSet(cb, dkMemBlockGetGpuAddr(g.descMem), NX_MAX_IMAGES);
    dkCmdBufBindSamplerDescriptorSet(cb, dkMemBlockGetGpuAddr(g.descMem) + NX_MAX_IMAGES * sizeof(DkImageDescriptor),
                                     NX_MAX_SAMPLERS);

    while (w < end) {
        uint32_t op = w[0] & 0xFFFF, argc = w[0] >> 16;
        const uint32_t* a = w + 1;
        if (a + argc > end) break;
        w += 1 + argc;
        switch (op) {
        case NxOp_End: w = end; break;
        case NxOp_DefineLayout:
            if (argc >= 10 && a[0] && a[0] <= MAX_LAYOUTS) memcpy(&g.layout[a[0]], a + 1, sizeof(NxVtxLayout));
            break;
        case NxOp_DefineState: if (argc >= 12) define_state(a); break;
        case NxOp_BeginPass: {
            if (argc < 8) break;
            uint32_t color = a[0], depth = a[1];
            memset(&ps, 0, sizeof ps);
            ps.w = a[6]; ps.h = a[7];
            if (color == 0) {
                if (slot < 0) slot = dkQueueAcquireImage(g.queue, g.swapchain);
                dkImageViewDefaults(&ps.color, &g.fb[slot]);
                ps.haveColor = 1;
            } else if (color <= g.nImages) {
                dkImageViewDefaults(&ps.color, &g.img[color].image);
                ps.colorId = color; ps.haveColor = 1;
            }
            if (depth && depth <= g.nImages) { dkImageViewDefaults(&ps.depth, &g.img[depth].image); ps.depthId = depth; ps.haveDepth = 1; }
            bind_targets(cb, &ps, 0);
            DkViewport vp = {0.5f, 0.5f, (float)ps.w, (float)ps.h, 0.0f, 1.0f};   /* NxViewport of D3D9's full viewport */
            dkCmdBufSetViewports(cb, 0, &vp, 1);
            ps.scissor = (DkScissor){0, 0, ps.w, ps.h};
            apply_scissor(cb, &ps);
            dkCmdBufSetBlendConst(cb, 1, 1, 1, 1);
            dkCmdBufSetDepthBias(cb, 0, 0, 0);
            if ((a[2] & 1) && ps.haveColor) {
                float c[4] = {(a[3] & 255) / 255.0f, (a[3] >> 8 & 255) / 255.0f, (a[3] >> 16 & 255) / 255.0f, (a[3] >> 24) / 255.0f};
                dkCmdBufClearColor(cb, 0, DkColorMask_RGBA, c);
            }
            if ((a[2] & 6) && ps.haveDepth) {
                float z; memcpy(&z, &a[4], 4);
                dkCmdBufClearDepthStencil(cb, (a[2] & 2) != 0, z, (a[2] & 4) ? 0xFF : 0, (uint8_t)a[5]);
            }
            memset(cur, 0, sizeof cur);   /* conservative: rebind blocks at pass start */
            curState = 0;
            break;
        }
        case NxOp_EndPass: break;
        case NxOp_Barrier:
            dkCmdBufBarrier(cb, DkBarrier_Fragments, DkInvalidateFlags_Image);
            break;
        case NxOp_BindState: {
            const StateRec* sr = a[0] && a[0] <= MAX_STATES && g.state[a[0]].defined ? &g.state[a[0]] : NULL;
            st->state_binds++;
            if (!sr) break;
            for (int k = 0; k < BK_COUNT; k++) {
                uint16_t b = sr->blk[k];
                if (b && b != cur[k]) {
                    dkCmdBufReplayCmds(cb, g.block[k][b - 1].w, g.block[k][b - 1].words);
                    cur[k] = b; st->block_binds++;
                }
            }
            uint64_t w1 = sr->rs.w[1], w2 = sr->rs.w[2];
            ps.stencilRef = NXRS_GET(w1, NXRS_S_REF, 8);
            if (NXRS_GET(w1, NXRS_S_ENABLE, 1)) apply_stencil(cb, w1, ps.stencilRef);
            int srgb = (int)NXRS_GET(w2, NXRS_R_SRGB, 1);
            if (srgb != ps.srgbBound && ps.colorId && g.img[ps.colorId].srgbView) bind_targets(cb, &ps, srgb);
            int sc = (int)NXRS_GET(w2, NXRS_R_SCISSOR, 1);
            if (sc != ps.scissorOn) { ps.scissorOn = sc; apply_scissor(cb, &ps); }
            curState = a[0];
            break;
        }
        case NxOp_BindUbo: {
            if (argc < 5) break;
            DkGpuAddr ad = mem_addr(a[2], a[3]);
            if (a[1] >= 16) break;   /* deko3d has 16 uniform buffer slots per stage */
            const uint32_t sl = a[1];
            if ((a[0] & 1) && (uboAddr[0][sl] != ad || uboSize[0][sl] != a[4])) {
                dkCmdBufBindUniformBuffer(cb, DkStage_Vertex, a[1], ad, a[4]); uboAddr[0][sl] = ad; uboSize[0][sl] = a[4];
            }
            if ((a[0] & 2) && (uboAddr[1][sl] != ad || uboSize[1][sl] != a[4])) {
                dkCmdBufBindUniformBuffer(cb, DkStage_Fragment, a[1], ad, a[4]); uboAddr[1][sl] = ad; uboSize[1][sl] = a[4];
            }
            break;
        }
        case NxOp_BindVtx: if (argc >= 3) dkCmdBufBindVtxBuffer(cb, 0, mem_addr(a[0], a[1]), a[2]); break;
        case NxOp_BindIdx: if (argc >= 4) dkCmdBufBindIdxBuffer(cb, a[3] ? DkIdxFormat_Uint32 : DkIdxFormat_Uint16, mem_addr(a[0], a[1])); break;
        case NxOp_BindTextures:
            if (argc >= 3 && 3 + a[2] <= argc)
                dkCmdBufBindTextures(cb, a[0] == NxStage_Vertex ? DkStage_Vertex : DkStage_Fragment, a[1], a + 3, a[2]);
            break;
        case NxOp_Draw:
        case NxOp_DrawIndexed: {
            static const DkPrimitive prim[6] = {DkPrimitive_Triangles, DkPrimitive_TriangleStrip, DkPrimitive_Lines,
                                                DkPrimitive_LineStrip, DkPrimitive_Points, DkPrimitive_TriangleFan};
            if (!curState) break;
            if (op == NxOp_Draw && argc >= 5) dkCmdBufDraw(cb, prim[a[0] % 6], a[1], a[2], a[3], a[4]);
            else if (op == NxOp_DrawIndexed && argc >= 6) dkCmdBufDrawIndexed(cb, prim[a[0] % 6], a[1], a[2], a[3], (int32_t)a[4], a[5]);
            st->draws++;
            break;
        }
        case NxOp_Viewport: {
            if (argc < 6) break;
            float f[6]; memcpy(f, a, 24);
            /* NxViewport is for a +0.5-centre rasterizer, which Maxwell is (deko3d does not expose
             * PIXEL_CENTER_INTEGER); the fill rule on centre ties is assumed top-left in the y-down space (trap) */
            DkViewport vp = {f[0], f[1], f[2], f[3], f[4], f[5]};
            dkCmdBufSetViewports(cb, 0, &vp, 1);
            break;
        }
        case NxOp_Scissor:
            if (argc >= 4) {
                int32_t x = (int32_t)a[0], y = (int32_t)a[1];
                ps.scissor = (DkScissor){x < 0 ? 0 : (uint32_t)x, y < 0 ? 0 : (uint32_t)y, a[2], a[3]};
                if (ps.scissorOn) apply_scissor(cb, &ps);
            }
            break;
        case NxOp_StencilRef:
            if (argc >= 1 && curState) { ps.stencilRef = a[0] & 0xFF; apply_stencil(cb, g.state[curState].rs.w[1], ps.stencilRef); }
            break;
        case NxOp_BlendConst:
            if (argc >= 4) { float f[4]; memcpy(f, a, 16); dkCmdBufSetBlendConst(cb, f[0], f[1], f[2], f[3]); }
            break;
        case NxOp_DepthBias:
            if (argc >= 3) { float f[3]; memcpy(f, a, 12); dkCmdBufSetDepthBias(cb, f[0], f[2], f[1]); }
            break;
        case NxOp_Clear:
            if (argc >= 8) {   /* Maxwell clears honour the scissor: clear inside the rectangle, then restore */
                DkScissor r = {a[4], a[5], a[6], a[7]};
                dkCmdBufSetScissors(cb, 0, &r, 1);
                if ((a[0] & 1) && ps.haveColor) {
                    float c[4] = {(a[1] & 255) / 255.0f, (a[1] >> 8 & 255) / 255.0f, (a[1] >> 16 & 255) / 255.0f, (a[1] >> 24) / 255.0f};
                    int srgb = (a[0] & NxClear_Srgb) && ps.colorId && g.img[ps.colorId].srgbView;
                    int was = ps.srgbBound;
                    if (srgb != was) bind_targets(cb, &ps, srgb);   /* clear through the sRGB view, as Nine does */
                    dkCmdBufClearColor(cb, 0, DkColorMask_RGBA, c);
                    if (srgb != was) bind_targets(cb, &ps, was);
                }
                if ((a[0] & 6) && ps.haveDepth) {
                    float z; memcpy(&z, &a[2], 4);
                    dkCmdBufClearDepthStencil(cb, (a[0] & 2) != 0, z, (a[0] & 4) ? 0xFF : 0, (uint8_t)a[3]);
                }
                apply_scissor(cb, &ps);
            }
            break;
        case NxOp_Marker: break;   /* tags for captures and tools */
        default: break;   /* unknown op: skipped by its count */
        }
    }
    dkCmdBufReportCounter(cb, DkCounter_Timestamp, ts + 16);
    dkCmdBufSignalFence(cb, &g.fence[s->frame % NXGPU_FRAMES], false);
    dkQueueSubmitCommands(g.queue, dkCmdBufFinishList(cb));
    if (s->present) {
        if (slot < 0) slot = dkQueueAcquireImage(g.queue, g.swapchain);
        dkQueuePresentImage(g.queue, g.swapchain, slot);
        g.lastSlot = slot;
    } else if (slot >= 0) {
        dkQueuePresentImage(g.queue, g.swapchain, slot);   /* an acquired image must go back */
        g.lastSlot = slot;
    }
}

/* Waits for the frame in slot (if any) and publishes its stats. Called with g.lock held. */
static void retire_until(uint32_t frame) {
    while (g.retired <= frame && g.retired < g.recorded) {
        uint32_t f = g.retired, slot = f % NXGPU_FRAMES;
        mutexUnlock(&g.lock);
        dkFenceWait(&g.fence[slot], -1);
        mutexLock(&g.lock);
        if (g.retired != f) continue;
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
    if (!subAddr) return NXGPU_BAD_CALL;
    mutexLock(&g.lock);
    while (g.hasPending) condvarWait(&g.cvTaken, &g.lock);
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

static void drain(void) {   /* everything submitted so far has finished on the GPU */
    mutexLock(&g.lock);
    while (g.hasPending) condvarWait(&g.cvTaken, &g.lock);
    uint32_t last = g.pending.frame;
    while (g.recorded <= last && g.recorded < last + 1 && !g.quit) condvarWait(&g.cvDone, &g.lock);
    mutexUnlock(&g.lock);
    dkQueueWaitIdle(g.queue);
}

static float half_to_float(uint16_t h) {
    uint32_t sgn = (uint32_t)(h >> 15) << 31, e = h >> 10 & 31, m = h & 1023, u;
    if (!e) { if (!m) u = sgn; else { e = 113; while (!(m & 1024)) { m <<= 1; e--; } u = sgn | e << 23 | (m & 1023) << 13; } }
    else if (e == 31) u = sgn | 0x7F800000u | m << 13;
    else u = sgn | (e + 112) << 23 | m << 13;
    float f; memcpy(&f, &u, 4); return f;
}

static u64 api_readback(u64 a) {
    const NxReadback* r = (const NxReadback*)a;
    if (!r || !r->dst || r->format > NxRb_Stencil8) return NXGPU_BAD_CALL;
    DkImage* src;
    uint32_t fmt, iw, ih;
    if (r->image) {
        if (r->image > g.nImages) return NXGPU_BAD_CALL;
        src = &g.img[r->image].image; fmt = g.img[r->image].format; iw = g.img[r->image].w; ih = g.img[r->image].h;
    } else {
        src = &g.fb[g.lastSlot]; fmt = NxFmt_RGBA8; iw = g.width; ih = g.height;
    }
    uint32_t sb;   /* source bytes per pixel */
    switch (fmt) {
    case NxFmt_RGBA8: case NxFmt_BGRA8: case NxFmt_RGBX8: case NxFmt_BGRX8: case NxFmt_Z24S8: case NxFmt_Z24X8:
    case NxFmt_Z32F: sb = 4; break;
    case NxFmt_RGBA16F: case NxFmt_Z32FS8: sb = 8; break;
    case NxFmt_RGBA32F: sb = 16; break;
    case NxFmt_Z16: sb = 2; break;
    default: return NXGPU_BAD_CALL;
    }
    int depth = is_depth(fmt);
    if (depth != (r->format == NxRb_Depth32F || r->format == NxRb_Stencil8)) return NXGPU_BAD_CALL;
    if ((uint64_t)r->w * r->h * nx_readback_bpp(r->format) > r->dst_size || (uint64_t)r->w * r->h * sb > READBACK_SIZE ||
        r->x + r->w > iw || r->y + r->h > ih)
        return NXGPU_BAD_CALL;
    drain();
    DkImageView v; dkImageViewDefaults(&v, src);
    if (r->format == NxRb_Stencil8) v.dsSource = DkDsSource_Stencil;
    DkImageRect rect = {r->x, r->y, 0, r->w, r->h, 1};
    DkCopyBuf dst = {dkMemBlockGetGpuAddr(g.readbackMem), 0, 0};
    dkCmdBufCopyImageToBuffer(g.upload, &v, &rect, &dst, 0);
    submit_and_wait(g.upload);
    const uint8_t* s = (const uint8_t*)dkMemBlockGetCpuAddr(g.readbackMem);
    uint32_t n = r->w * r->h;
    if (r->format == NxRb_RGBA8 || r->format == NxRb_RGBA32F) {
        int bgr = fmt == NxFmt_BGRA8 || fmt == NxFmt_BGRX8, x = fmt == NxFmt_RGBX8 || fmt == NxFmt_BGRX8;
        for (uint32_t i = 0; i < n; i++) {
            float c[4];
            if (sb == 4) for (int k = 0; k < 4; k++) c[k] = s[4 * i + k] / 255.0f;
            else if (sb == 8) for (int k = 0; k < 4; k++) { uint16_t h; memcpy(&h, s + 8 * i + 2 * k, 2); c[k] = half_to_float(h); }
            else memcpy(c, s + 16 * i, 16);
            if (bgr) { float t = c[0]; c[0] = c[2]; c[2] = t; }
            if (x) c[3] = 1.0f;
            if (r->format == NxRb_RGBA32F) memcpy((uint8_t*)r->dst + 16 * i, c, 16);
            else for (int k = 0; k < 4; k++) {
                float f = c[k] < 0 ? 0 : c[k] > 1 ? 1 : c[k];
                ((uint8_t*)r->dst)[4 * i + k] = (uint8_t)(f * 255.0f + 0.5f);
            }
        }
    } else if (r->format == NxRb_Depth32F) {   /* layouts per deko3d's copy engine (trap: confirm on hardware) */
        for (uint32_t i = 0; i < n; i++) {
            float z;
            if (fmt == NxFmt_Z32F || fmt == NxFmt_Z32FS8) memcpy(&z, s + sb * i, 4);
            else if (fmt == NxFmt_Z16) { uint16_t u; memcpy(&u, s + 2 * i, 2); z = u / 65535.0f; }
            else { uint32_t u; memcpy(&u, s + 4 * i, 4); z = (u >> 8) / 16777215.0f; }   /* S8Z24: depth high 24 bits */
            memcpy((uint8_t*)r->dst + 4 * i, &z, 4);
        }
    } else {
        for (uint32_t i = 0; i < n; i++) ((uint8_t*)r->dst)[i] = s[i];   /* stencil view: one byte per pixel */
    }
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
    dkMemBlockMakerDefaults(&m, g.dev, (size + 0xFFF) & ~0xFFFu);
    m.flags = flags;
    return dkMemBlockCreate(&m);
}

const NxGpuApi* nxgpu_init(uint32_t width, uint32_t height) {
    memset(&g, 0, sizeof g);
    g.width = width; g.height = height;
    DkDeviceMaker dm;
    dkDeviceMakerDefaults(&dm);   /* DepthZeroToOne | OriginUpperLeft | YAxisPointsUp */
    dm.cbDebug = dk_debug;
    g.dev = dkDeviceCreate(&dm);
    if (!g.dev) return NULL;

    DkImageLayoutMaker lm;
    dkImageLayoutMakerDefaults(&lm, g.dev);
    lm.flags = DkImageFlags_UsageRender | DkImageFlags_UsagePresent | DkImageFlags_HwCompression;
    lm.format = DkImageFormat_RGBA8_Unorm;
    lm.dimensions[0] = width; lm.dimensions[1] = height;
    DkImageLayout fbl;
    dkImageLayoutInitialize(&fbl, &lm);
    uint32_t fbAlign = dkImageLayoutGetAlignment(&fbl);
    uint32_t fbSize = ((uint32_t)dkImageLayoutGetSize(&fbl) + fbAlign - 1) & ~(fbAlign - 1);
    g.fbMem = host_block(NXGPU_SWAP_IMAGES * fbSize, DkMemBlockFlags_GpuCached | DkMemBlockFlags_Image);
    if (!g.fbMem) return NULL;   /* every created object is checked: nxgpu_host.c logs "deko3d init FAILED" (NX1 check 6) */
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
    if (!g.queue) return NULL;

    DkSwapchainMaker sm;
    dkSwapchainMakerDefaults(&sm, g.dev, nwindowGetDefault(), fbs, NXGPU_SWAP_IMAGES);
    g.swapchain = dkSwapchainCreate(&sm);
    if (!g.swapchain) return NULL;

    g.cmdMem = host_block(NXGPU_FRAMES * CMDMEM_PER_FRAME, DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached);
    if (!g.cmdMem) return NULL;
    DkCmdBufMaker cm;
    dkCmdBufMakerDefaults(&cm, g.dev);
    for (int i = 0; i < NXGPU_FRAMES; i++) { g.cmd[i] = dkCmdBufCreate(&cm); if (!g.cmd[i]) return NULL; }
    g.upload = dkCmdBufCreate(&cm);
    g.capture = dkCmdBufCreate(&cm);
    if (!g.upload || !g.capture) return NULL;
    DkMemBlock upMem = host_block(64u << 10, DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached);
    if (!upMem) return NULL;
    dkCmdBufAddMemory(g.upload, upMem, 0, 64u << 10);

    uint32_t descSize = NX_MAX_IMAGES * sizeof(DkImageDescriptor) + NX_MAX_SAMPLERS * sizeof(DkSamplerDescriptor);
    g.descMem = host_block(descSize, DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached);
    if (!g.descMem) return NULL;
    g.imageDesc = (DkImageDescriptor*)dkMemBlockGetCpuAddr(g.descMem);
    g.samplerDesc = (DkSamplerDescriptor*)(g.imageDesc + NX_MAX_IMAGES);
    DkSampler s0; dkSamplerDefaults(&s0);
    dkSamplerDescriptorInitialize(&g.samplerDesc[0], &s0);   /* sampler 0: nearest, repeat */
    g.codeMem = host_block(CODE_POOL, DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached | DkMemBlockFlags_Code);
    g.stagingMem = host_block(STAGING_SIZE, DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached);
    g.imageMem = host_block(IMAGE_POOL, DkMemBlockFlags_GpuCached | DkMemBlockFlags_Image);
    g.readbackMem = host_block(READBACK_SIZE, DkMemBlockFlags_CpuCached | DkMemBlockFlags_GpuCached);
    g.tsMem = host_block(0x1000, DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached);
    if (!g.codeMem || !g.imageMem || !g.readbackMem || !g.stagingMem || !g.tsMem) return NULL;
    g.descDirty = true;

    mutexInit(&g.lock);
    condvarInit(&g.cvSubmit); condvarInit(&g.cvTaken); condvarInit(&g.cvDone);
    if (R_FAILED(threadCreate(&g.thread, render_thread, NULL, NULL, 0x10000, 0x2B, 1))) return NULL;
    threadStart(&g.thread);

    NxGpuApi* api = &g.api;
    api->info.magic = NXGPU_MAGIC; api->info.version = NXGPU_VERSION;
    api->info.frames_in_flight = NXGPU_FRAMES; api->info.width = width; api->info.height = height; api->info.backend = 1;
    api->mem_create = (u64)api_mem_create; api->mem_gpu_addr = (u64)api_mem_gpu_addr;
    api->image_create = (u64)api_image_create; api->sampler_create = (u64)api_sampler_create;
    api->shader_create = (u64)api_shader_create; api->submit = (u64)api_submit;
    api->wait_frame = (u64)api_wait_frame; api->readback = (u64)api_readback; api->shutdown = (u64)api_shutdown;
    return api;
}
