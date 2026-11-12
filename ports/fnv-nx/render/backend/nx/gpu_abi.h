/* fnv-nx native renderer: the front end <-> backend boundary (stream S6n, RENDER_NATIVE.md §3.1).
 *
 * Two backends implement it: host/nxgpu.c (deko3d, LP64 in the Switch host module next to libnx) and gl/nxgl.c
 * (desktop OpenGL 4.5 over EGL surfaceless, the host reference). The front end (nx/front.h) writes, per frame,
 * one linear command stream of 32-bit words; the backend replays it. Resource creation is a handful of calls.
 *
 * Calls: a backend fills an NxGpuApi (LP64 function addresses). The front reaches it
 *   - in the ILP32 guest through S5's ext_call slot: ext_call(NXGPU_EXT_MODULE, NXGPU_FN_x, args, n), handled on
 *     the host by the backend's own dispatch (render/d3d9/backend/gl4_core.c for the GL backend);
 *   - in LP64 host programs (GL tests) by calling the backend's dispatch directly.
 * Every struct here has fixed-width fields only and identical layout in ILP32 and LP64 (asserted below). Guest
 * addresses travel as uint64_t (zero-extended); the host dereferences them directly (one address space).
 */
#ifndef FNVNX_NX_GPU_ABI_H
#define FNVNX_NX_GPU_ABI_H

#include <stdint.h>
#include "state.h"

#ifdef __cplusplus
extern "C" {
#endif

#define FNVNX_ADDR_(p) ((uint64_t)(uintptr_t)(p))
#define NXGPU_MAGIC      0x5550474eu /* "NGPU" */
#define NXGPU_VERSION    3u
#define NXGPU_EXT_MODULE 10u         /* S5 ext_call module id (Nine is 9) */

/* ---- memory blocks: caller-backed (4 KiB aligned, size multiple of 4 KiB) ----------------------------------- */
enum NxMemKind { NxMem_Generic = 0, NxMem_Code = 1, NxMem_Image = 2 };
typedef struct NxMemBlockDesc {
    uint64_t storage;      /* caller address; the CPU writes there, the GPU reads it (GL: copied at use) */
    uint32_t size;
    uint32_t kind;         /* NxMemKind */
} NxMemBlockDesc;

/* ---- images ------------------------------------------------------------------------------------------------- */
enum NxImageFlags { NxImg_RenderTarget = 1, NxImg_Depth = 2, NxImg_Cube = 4 };
#define NXIMG_SWIZZLE(r, g, b, a) ((uint16_t)((r) | (g) << 4 | (b) << 8 | (a) << 12))   /* NxSwizzle nibbles */
#define NXIMG_SWIZZLE_IDENTITY NXIMG_SWIZZLE(NxSwz_R, NxSwz_G, NxSwz_B, NxSwz_A)
typedef struct NxImageDesc {
    uint16_t width, height;
    uint8_t format;        /* NxFormat */
    uint8_t mips;          /* 0 = 1 */
    uint8_t flags;         /* NxImageFlags */
    uint8_t pad0;
    uint16_t swizzle;      /* NXIMG_SWIZZLE(...) for sampling; 0 means identity */
    uint16_t pad1;
    uint32_t pad2;
    uint64_t data;         /* mips 0..n-1 tightly packed (BCn: 4x4 blocks; cube: face-major), 0 = no upload */
    uint32_t data_size;
    uint32_t pad3;
} NxImageDesc;
/* image_create -> image id (>= 1), 0 on failure. When the format has NXFMT_SRGB, id + 1 is the same image seen
 * through its sRGB view (the backend reserves it). A texture handle is NX_TEX_HANDLE(image id, sampler id)
 * (= dkMakeTextureHandle) on both backends. */
#define NX_TEX_HANDLE(image, sampler) ((uint32_t)(image) | (uint32_t)(sampler) << 20)
#define NX_MAX_IMAGES   4096
#define NX_MAX_SAMPLERS 256

/* ---- shaders ------------------------------------------------------------------------------------------------ */
enum NxStage { NxStage_Vertex = 0, NxStage_Fragment = 1 };
typedef struct NxShaderDesc {
    uint32_t stage;        /* NxStage */
    uint32_t dksh_size;
    uint64_t dksh;         /* DKSH image (deko3d); 0 is fine for the GL backend */
    uint64_t glsl;         /* GLSL 4.60 source in the deko3d flavour (the GL backend rewrites version + bindings) */
    uint32_t glsl_size;
    uint32_t pad;
} NxShaderDesc;
/* Resource bindings in the GLSL: UBO and sampler `binding = n` are deko3d per-stage slots; the GL backend maps
 * them to GL binding stage * 16 + n. */

/* ---- vertex layouts ----------------------------------------------------------------------------------------- */
enum NxAttrType { NxAttr_Float32 = 0, NxAttr_Unorm8 = 1, NxAttr_Snorm16 = 2, NxAttr_Float16 = 3 };
typedef struct NxVtxLayout {   /* up to 8 attributes from one interleaved buffer; attribute i = location i, or
                                  location - 1 when attr[i].location is set (1..16: translated programs read their
                                  inputs at the fnvnx_dk_layout.h locations, up to 15) */
    uint8_t count, stride, pad[2];
    struct { uint8_t offset, type, components, location; } attr[8];
} NxVtxLayout;

/* ---- command stream ------------------------------------------------------------------------------------------ */
/* An array of 32-bit words: [opcode | (argument word count << 16)] followed by its arguments. A backend skips
 * unknown opcodes by their count. Floats travel as their bit patterns. */
enum NxOp {
    NxOp_End = 0,
    NxOp_DefineState,   /* id, vs, ps, layoutId, then NxRenderState w[0..3] as lo/hi word pairs (12 words) */
    NxOp_DefineLayout,  /* id, NxVtxLayout (9 words) */
    NxOp_BeginPass,     /* colorImage, depthImage, clearFlags (1 colour, 2 depth, 4 stencil), clear RGBA8 (R in the
                           low byte), clear depth f32, clear stencil, width, height. Then viewport = (0.5, 0.5, w,
                           h, 0, 1) (D3D9's full viewport, see NxViewport), scissor = (0, 0, w, h), blend constant
                           (1,1,1,1), depth bias 0, stencil ref from the bound state; no state bound. */
    NxOp_EndPass,
    NxOp_Barrier,       /* kind: 0 = render targets written so far become sampleable */
    NxOp_BindState,     /* state id; also sets stencil ref/masks from the state's w[1] bits 32-55 */
    NxOp_BindUbo,       /* stage mask (1 vertex, 2 fragment), slot, memBlock, offset, size */
    NxOp_BindVtx,       /* memBlock, offset, size */
    NxOp_BindIdx,       /* memBlock, offset, size, type (0 u16, 1 u32) */
    NxOp_BindTextures,  /* stage (NxStage), first slot, count, handles[count] */
    NxOp_Draw,          /* prim, vertexCount, instanceCount, firstVertex, firstInstance */
    NxOp_DrawIndexed,   /* prim, indexCount, instanceCount, firstIndex, baseVertex, firstInstance */
    NxOp_Viewport,      /* NxViewport: x, y, w, h, minZ, maxZ (float bits) */
    NxOp_Scissor,       /* x, y, w, h (int32); effective while the bound state has the scissor bit */
    NxOp_StencilRef,    /* ref (both faces; until the next BindState) */
    NxOp_BlendConst,    /* r, g, b, a (float bits) */
    NxOp_DepthBias,     /* units, slope, clamp (float bits; units as NxDynamicState.depthBiasUnits) */
    NxOp_Clear,         /* flags (NxClear_*: 1 colour, 2 depth, 4 stencil, 8 sRGB), RGBA8 (R low), depth f32, stencil,
                           x, y, w, h (pixels, top-left origin): clears that rectangle of the pass's targets; ignores
                           the bound state's masks and scissor (v3). With NxClear_Srgb the colour goes through the
                           target's sRGB view when it has one (D3D9 Clear under SRGBWRITEENABLE, Nine
                           nine_context_clear_fb): 0x80 is stored as 188 */
    NxOp_Marker,        /* tag (fourcc), a, b: a no-op for the backends, kept by captures (v3, additive). Tag
                           NX_MARKER_PASS: a = the engine pass id of the draws that follow in this pass (0..758, the
                           numbering of docs/RENDER_NATIVE.md §1.3; NX_PASS_UNKNOWN = none), b = NxFamilyId
                           (nx/families.h). Emitted by the front before the first draw of each run of draws with the
                           same (pass id, family); a pass with no tagged draw has no marker */
    NxOp_Count
};
#define NX_FOURCC(a, b, c, d) ((uint32_t)(a) | (uint32_t)(b) << 8 | (uint32_t)(c) << 16 | (uint32_t)(d) << 24)
#define NX_MARKER_PASS  NX_FOURCC('P', 'A', 'S', 'S')
#define NX_PASS_UNKNOWN 0xFFFFFFFFu
enum NxClearFlags { NxClear_Color = 1, NxClear_Depth = 2, NxClear_Stencil = 4, NxClear_Srgb = 8 };
enum NxPrim { NxPrim_Triangles = 0, NxPrim_TriStrip = 1, NxPrim_Lines = 2, NxPrim_LineStrip = 3, NxPrim_Points = 4,
              NxPrim_TriFan = 5 };

typedef struct NxFrameStats {   /* written by the backend after the GPU finished the frame */
    uint32_t frame;
    uint32_t cmd_words;
    uint32_t draws;
    uint32_t state_binds;       /* BindState ops */
    uint32_t block_binds;       /* state-block bind calls actually issued */
    uint32_t record_us;         /* time to replay/record the stream */
    uint32_t gpu_us;            /* GPU time of the frame (0 if unavailable) */
    uint32_t present_interval_us;
} NxFrameStats;

typedef struct NxSubmit {
    uint64_t stream;      /* address of the command words */
    uint32_t words;
    uint32_t frame;       /* monotonically increasing frame number */
    uint64_t stats;       /* address of an NxFrameStats to fill when the frame has completed, or 0 */
    uint32_t present;     /* 1: present the swapchain image after the frame (deko3d; GL ignores it) */
    uint32_t pad;
} NxSubmit;

enum NxReadbackFormat {        /* what NxReadback writes per pixel (row 0 = top, tightly packed) */
    NxRb_RGBA8 = 0,             /* colour images: 4 bytes R, G, B, A (unorm formats; float formats clamped) */
    NxRb_RGBA32F = 1,           /* colour images: 4 floats (exact for float formats, unorm as c / max) */
    NxRb_Depth32F = 2,          /* depth images: one float, the stored depth in [0,1] */
    NxRb_Stencil8 = 3           /* depth-stencil images: one byte */
};
static inline uint32_t nx_readback_bpp(uint32_t f) { return f == NxRb_RGBA32F ? 16u : f == NxRb_Stencil8 ? 1u : 4u; }
typedef struct NxReadback {
    uint32_t image;             /* image id, 0 = the swapchain image of the last frame */
    uint32_t x, y, w, h;
    uint32_t format;            /* NxReadbackFormat (v3) */
    uint64_t dst;
    uint32_t dst_size;
    uint32_t pad;
} NxReadback;

typedef struct NxGpuInfo {
    uint32_t magic, version, frames_in_flight, flags;
    uint32_t width, height;     /* swapchain size (0 when there is none) */
    uint32_t backend;           /* 1 deko3d, 2 GL */
    uint32_t pad;
} NxGpuInfo;

/* ---- the backend table (LP64) --------------------------------------------------------------------------------- */
typedef struct NxGpuApi {
    NxGpuInfo info;
    uint64_t mem_create;                 /* (NxMemBlockDesc*) -> id (1..), 0 on failure */
    uint64_t mem_gpu_addr;               /* (id) -> GPU address of byte 0 (GL: 0) */
    uint64_t image_create;               /* (NxImageDesc*) -> image id */
    uint64_t sampler_create;             /* (NxSamplerState*) -> sampler id (1..NX_MAX_SAMPLERS-1), 0 on failure */
    uint64_t shader_create;              /* (NxShaderDesc*) -> shader id (1..), 0 on failure */
    uint64_t submit;                     /* (NxSubmit*) -> 0 once the backend took the stream */
    uint64_t wait_frame;                 /* (frame) -> 0 once the GPU finished that frame */
    uint64_t readback;                   /* (NxReadback*) -> 0 ok; waits for all submitted work */
    uint64_t shutdown;                   /* () -> 0 */
} NxGpuApi;

/* ext_call function numbers; the arguments are those of the NxGpuApi slot, in order. */
enum NxGpuFn {
    NXGPU_FN_INFO = 0,    /* (NxGpuInfo* out) -> 0 */
    NXGPU_FN_MEM_CREATE, NXGPU_FN_MEM_GPU_ADDR, NXGPU_FN_IMAGE_CREATE, NXGPU_FN_SAMPLER_CREATE,
    NXGPU_FN_SHADER_CREATE, NXGPU_FN_SUBMIT, NXGPU_FN_WAIT_FRAME, NXGPU_FN_READBACK, NXGPU_FN_SHUTDOWN,
    NXGPU_FN_COUNT
};
#define NXGPU_BAD_CALL 0xFFFFFFFFFFFFFF02ull

#ifdef __cplusplus
}
#define NX_ASSERT_ static_assert
#else
#define NX_ASSERT_ _Static_assert
#endif
NX_ASSERT_(sizeof(NxMemBlockDesc) == 16, "NxMemBlockDesc");
NX_ASSERT_(sizeof(NxImageDesc) == 32, "NxImageDesc");
NX_ASSERT_(sizeof(NxShaderDesc) == 32, "NxShaderDesc");
NX_ASSERT_(sizeof(NxVtxLayout) == 36, "NxVtxLayout");
NX_ASSERT_(sizeof(NxFrameStats) == 32, "NxFrameStats");
NX_ASSERT_(sizeof(NxSubmit) == 32, "NxSubmit");
NX_ASSERT_(sizeof(NxReadback) == 40, "NxReadback");
NX_ASSERT_(sizeof(NxGpuInfo) == 32, "NxGpuInfo");
NX_ASSERT_(sizeof(NxGpuApi) == 32 + 9 * 8, "NxGpuApi");

#endif
