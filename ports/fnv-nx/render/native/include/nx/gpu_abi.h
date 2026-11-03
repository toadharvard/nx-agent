/* fnv-nx native renderer: the ILP32 front end <-> LP64 deko3d backend boundary (stream S6n, RENDER_NATIVE.md §3.1).
 *
 * Placement (S5 model, docs/TOOLCHAIN.md §2): libnx and deko3d stay LP64 in the host module. The ILP32 front end
 * (families, batcher, sorter, frame graph, ring allocators) runs in the guest and writes, per frame, one linear
 * command stream into guest memory. The backend (host) replays it into deko3d command buffers on its own render
 * thread. The only per-frame boundary crossing is nxgpu.submit(stream); resource creation (memory blocks,
 * textures, shaders) is a handful of calls at load time.
 *
 * Every struct here has fixed-width fields only and identical layout in ILP32 and LP64 (asserted below). Guest
 * addresses travel as uint64_t (zero-extended); the host dereferences them directly (one address space).
 */
#ifndef FNVNX_NX_GPU_ABI_H
#define FNVNX_NX_GPU_ABI_H

#include <stdint.h>
#include "fnvnx/hostapi.h"

#define FNVNX_ADDR_(p) ((uint64_t)(uintptr_t)(p))
#define NXGPU_MAGIC   0x5550474eu /* "NGPU" */
#define NXGPU_VERSION 1u

/* ---- memory blocks: caller-backed (guest memory, 4 KiB aligned, size multiple of 4 KiB) ------------------- */
enum NxMemKind { NxMem_Generic = 0, NxMem_Code = 1, NxMem_Image = 2 };  /* CPU-cached+GPU / shader code / GPU-only images */
typedef struct NxMemBlockDesc {
    uint64_t storage;      /* guest address, 0 = host allocates from its own heap (images, render targets) */
    uint32_t size;
    uint32_t kind;         /* NxMemKind */
} NxMemBlockDesc;
/* result of mem_create: block id (1..), 0 on failure; mem_gpu_addr(id) -> GPU virtual address of byte 0 */

/* ---- images and textures ---------------------------------------------------------------------------------- */
enum NxFormat {
    NxFmt_None = 0, NxFmt_RGBA8 = 1, NxFmt_BGRA8 = 2, NxFmt_BC1 = 3, NxFmt_BC2 = 4, NxFmt_BC3 = 5, NxFmt_R8 = 6,
    NxFmt_RG8 = 7, NxFmt_RGB565 = 8, NxFmt_RGBA16F = 9, NxFmt_R32F = 10, NxFmt_Z24S8 = 11, NxFmt_Z32F = 12,
};
enum NxImageFlags { NxImg_RenderTarget = 1, NxImg_Depth = 2, NxImg_Cube = 4 };
typedef struct NxImageDesc {
    uint16_t width, height;
    uint8_t format, mips, flags, swizzle; /* swizzle: 0 identity, 1 = L8->RRR1, 2 = A8->000R, 3 = L8A8->RRRG */
    uint32_t sampler;     /* NxSampler bits (filter/wrap) for the default handle */
    uint64_t data;        /* guest address of mip 0..mips-1 tightly packed (BCn: 4x4 blocks), 0 = no upload */
    uint32_t data_size;
    uint32_t pad;
} NxImageDesc;
enum NxSampler { NxSmp_Linear = 0, NxSmp_Point = 1, NxSmp_Clamp = 0, NxSmp_Wrap = 2, NxSmp_Mirror = 4 };
/* result of image_create: (image id << 32) | 32-bit texture handle (dkMakeTextureHandle(image slot, sampler slot));
 * the handle is what shaders receive (bindless-style, one global descriptor set). */

/* ---- shaders ---------------------------------------------------------------------------------------------- */
/* shader_create(dksh guest address, size) -> shader id (1..) */

/* ---- state ------------------------------------------------------------------------------------------------ */
/* Render-state word (64 bits). Fields grouped by deko3d state block so the backend can diff block by block. */
#define NXRS_BLEND_SHIFT  0   /* 16 bits: enable(1) srcRGB(4) dstRGB(4) op(3) alphaSame(1) ... */
#define NXRS_DEPTH_SHIFT  16  /* 8 bits: test(1) write(1) func(3) */
#define NXRS_RASTER_SHIFT 24  /* 8 bits: cull(2) frontCCW(1) depthBias class(3) */
#define NXRS_CWRITE_SHIFT 32  /* 4 bits: RGBA write mask */
#define NXRS_STENCIL_SHIFT 40 /* 24 bits: enable(1) func(3) pass(3) ref(8) mask(8) */
enum NxBlendFactor { NxBf_Zero, NxBf_One, NxBf_SrcAlpha, NxBf_InvSrcAlpha, NxBf_SrcColor, NxBf_InvSrcColor,
                     NxBf_DstColor, NxBf_DstAlpha, NxBf_InvDstAlpha };
enum NxCompare { NxCmp_Never, NxCmp_Less, NxCmp_Equal, NxCmp_LEqual, NxCmp_Greater, NxCmp_NotEqual, NxCmp_GEqual,
                 NxCmp_Always };

typedef struct NxVtxLayout {   /* up to 8 attributes from one interleaved buffer */
    uint8_t count, stride, pad[2];
    struct { uint8_t offset, type, components, pad; } attr[8];  /* type: 0 float32, 1 unorm8, 2 snorm16, 3 float16 */
} NxVtxLayout;

/* ---- command stream -------------------------------------------------------------------------------------- */
/* A stream is an array of 32-bit words: [opcode | (word count << 16)] followed by its arguments. */
enum NxOp {
    NxOp_End = 0,
    NxOp_DefineState,   /* id, vs, ps, layoutId, stateLo, stateHi           - once per new state id */
    NxOp_DefineLayout,  /* id, NxVtxLayout (9 words)                       - once per new layout */
    NxOp_BeginPass,     /* colorImage (0 = swapchain), depthImage, clearFlags, rgba8, depth f32, w, h */
    NxOp_EndPass,
    NxOp_Barrier,       /* kind: 0 = render target -> sampled */
    NxOp_BindState,     /* state id: backend binds only the blocks that differ from the current state */
    NxOp_BindUbo,       /* stage mask, slot, memBlock, offset, size */
    NxOp_BindVtx,       /* memBlock, offset, size */
    NxOp_BindIdx,       /* memBlock, offset, size, type (0 u16, 1 u32) */
    NxOp_BindTextures,  /* stage(1=frag), first slot, count, handles[count] */
    NxOp_Draw,          /* prim, vertexCount, instanceCount, firstVertex, firstInstance */
    NxOp_DrawIndexed,   /* prim, indexCount, instanceCount, firstIndex, baseVertex, firstInstance */
    NxOp_Viewport,      /* x, y, w, h (float bits) */
    NxOp_Count
};
enum NxPrim { NxPrim_Triangles = 0, NxPrim_TriStrip = 1, NxPrim_Lines = 2 };

typedef struct NxFrameStats {   /* written by the backend into the frame's stats slot after the GPU finished it */
    uint32_t frame;
    uint32_t cmd_words;
    uint32_t draws;
    uint32_t state_binds;       /* BindState ops */
    uint32_t block_binds;       /* deko3d state-block bind calls actually issued */
    uint32_t record_us;         /* render thread: time to record the command buffer */
    uint32_t gpu_us;            /* GPU time between the frame's begin and end timestamps (0 if unavailable) */
    uint32_t present_interval_us;
} NxFrameStats;

typedef struct NxSubmit {
    uint64_t stream;      /* guest address of the command words */
    uint32_t words;
    uint32_t frame;       /* monotonically increasing frame number */
    uint64_t stats;       /* guest address of an NxFrameStats to fill when the frame has completed, or 0 */
} NxSubmit;

/* ---- the host API extension: appended after FnvnxHostApi (the guest runtime accepts size >= base size) ---- */
typedef struct NxGpuApi {
    uint32_t magic, version, size, frames_in_flight;
    uint32_t width, height;              /* swapchain size */
    uint32_t pad[2];
    uint64_t mem_create;                 /* (NxMemBlockDesc*) -> id */
    uint64_t mem_gpu_addr;               /* (id) -> GPU address */
    uint64_t image_create;               /* (NxImageDesc*) -> (id << 32) | handle */
    uint64_t shader_create;              /* (dksh, size, stage 0 vert 1 frag) -> id */
    uint64_t submit;                     /* (NxSubmit*) -> 0; returns once the render thread took the stream */
    uint64_t wait_frame;                 /* (frame) -> 0 once the GPU finished that frame (ring reuse fence) */
    uint64_t shutdown;                   /* () -> 0; drains the render thread */
} NxGpuApi;

typedef struct NxHostApi {
    FnvnxHostApi base;
    NxGpuApi gpu;
} NxHostApi;

#if defined(__cplusplus)
#define NX_ASSERT_ static_assert
#else
#define NX_ASSERT_ _Static_assert
#endif
NX_ASSERT_(sizeof(NxMemBlockDesc) == 16, "NxMemBlockDesc");
NX_ASSERT_(sizeof(NxImageDesc) == 32, "NxImageDesc");
NX_ASSERT_(sizeof(NxVtxLayout) == 36, "NxVtxLayout");
NX_ASSERT_(sizeof(NxFrameStats) == 32, "NxFrameStats");
NX_ASSERT_(sizeof(NxSubmit) == 24, "NxSubmit");
NX_ASSERT_(sizeof(NxGpuApi) == 0x58, "NxGpuApi");
NX_ASSERT_(sizeof(NxHostApi) == 0x78 + 0x58, "NxHostApi");

#endif
