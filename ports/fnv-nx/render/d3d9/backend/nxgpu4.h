/* fnv-nx L6: backend ABI v4 = nx/gpu_abi.h v3 plus what a total D3D9 device needs (rule RD8: one front, two
 * backends - deko3d on the console, GL 4.5 on llvmpipe through host module 10 - that differ only below this file).
 *
 * v4 is additive: every v3 structure, op and function keeps its number and meaning. A backend reports
 * NxGpuInfo.version = 4 only when it implements EVERY op and function below; the D3D9 device refuses to start on a
 * lower version (there is no per-feature probing and no skip path, RD2/RD3).
 *
 * Why each addition exists (D3D9 contract -> v3 gap):
 *   objects are released                         -> destroy functions; ids are reused only after destroy
 *   volume textures, cube/mip render targets     -> NxImageDesc4 (type, depth, samples), NxTarget (level, layer)
 *   4 simultaneous render targets                -> NxOp_BeginPass4
 *   Lock/Unlock, UpdateSurface, UpdateTexture    -> NxOp_ImageUpdate (any level, layer, box)
 *   StretchRect, ColorFill, Present rects        -> NxOp_Blit, NxOp_Clear (v3) on any target
 *   GetRenderTargetData, read locks              -> image_read (native bytes of any level, layer, rect)
 *   AUTOGENMIPMAP / GenerateMipSubLevels         -> NxOp_GenMips
 *   16 streams, instancing, every D3DDECLTYPE    -> NxOp_DefineLayout4, NxOp_BindVtxN
 *   queries                                      -> NxOp_QueryBegin/End, query_result
 *   multisampling, line/point states             -> NxRenderState w[3], NxImageDesc4.samples
 *   more sampler states than descriptor slots    -> sampler_update (the device evicts by rule, the id is redefined)
 *   presentation interval, gamma ramp            -> NxPresent
 * User clip planes, point size, alpha test and fog are shader epilogue work (driver UBO): no backend state beyond
 * the clip enable mask (w[2] bits 8-15) and the point bits already in v3.
 */
#ifndef FNVNX_NXGPU4_H
#define FNVNX_NXGPU4_H
#include "nx/gpu_abi.h"

#ifdef __cplusplus
extern "C" {
#endif

#define NXGPU_VERSION4 4u

/* ---- images ------------------------------------------------------------------------------------------------- */
enum NxImageType { NxImgType_2D = 0, NxImgType_Cube = 1, NxImgType_3D = 2 };
typedef struct NxImageDesc4 {
    uint16_t width, height, depth;   /* depth: 1 unless NxImgType_3D */
    uint8_t type;                    /* NxImageType */
    uint8_t format;                  /* NxFormat */
    uint8_t mips;                    /* >= 1 */
    uint8_t samples;                 /* 1, 2, 4, 8 (render targets and depth only) */
    uint8_t flags;                   /* NxImageFlags (render target, depth) */
    uint8_t pad0;
    uint16_t swizzle;                /* NXIMG_SWIZZLE(...), 0 = identity */
    uint16_t pad1;
} NxImageDesc4;
NX_ASSERT_(sizeof(NxImageDesc4) == 16, "NxImageDesc4");
/* Contents are undefined until written by NxOp_ImageUpdate, a pass or a blit; the device writes every level it
 * creates (D3D9 resources start zero-filled: the device uploads zeros, the backend does not guess). */

/* A subresource: mip level and layer (cube face 0-5 in D3DCUBEMAP_FACES order, or the z slice of a 3D image). */
typedef struct NxTarget { uint32_t image; uint16_t level, layer; } NxTarget;

/* ---- vertex layouts ----------------------------------------------------------------------------------------- */
/* One attribute type per D3DDECLTYPE; the shader sees D3D9's expansion (missing components 0, 0, 0, 1). */
enum NxAttrType4 {
    NxAttr4_Float1 = 0, NxAttr4_Float2, NxAttr4_Float3, NxAttr4_Float4,
    NxAttr4_D3DColor,        /* bytes B,G,R,A -> (R,G,B,A) / 255 */
    NxAttr4_UByte4,          /* 4 x u8, not normalised */
    NxAttr4_Short2,          /* (x, y, 0, 1) */
    NxAttr4_Short4,
    NxAttr4_UByte4N, NxAttr4_Short2N, NxAttr4_Short4N, NxAttr4_UShort2N, NxAttr4_UShort4N,
    NxAttr4_UDec3,           /* 10:10:10 unsigned -> (x, y, z, 1) */
    NxAttr4_Dec3N,           /* 10:10:10 signed normalised -> (x, y, z, 1) */
    NxAttr4_Float16_2, NxAttr4_Float16_4,
    NxAttr4_Count
};
enum { NX4_MAX_STREAMS = 16, NX4_MAX_ATTRS = 16 };
typedef struct NxVtxAttr4 { uint16_t offset; uint8_t stream, type, location, pad[3]; } NxVtxAttr4;
typedef struct NxVtxLayout4 {
    uint8_t attrCount, pad[3];
    uint32_t instanceStreams;        /* bit s: stream s advances per instance (D3DSTREAMSOURCE_INSTANCEDATA) */
    uint32_t divisor[NX4_MAX_STREAMS];   /* for instance streams: instances per element (>= 1) */
    NxVtxAttr4 attr[NX4_MAX_ATTRS];
} NxVtxLayout4;
/* A declaration element whose usage the bound vertex shader does not read is not an attribute: at most 16 remain
 * (SM1-3 shaders declare at most 16 inputs), so the bound of 16 loses nothing. */

/* ---- render state w[3] (v3: reserved 0) --------------------------------------------------------------------------- */
#define NXRS_M_MSAA        0   /* D3DRS_MULTISAMPLEANTIALIAS != 0 */
#define NXRS_M_AALINE      1   /* D3DRS_ANTIALIASEDLINEENABLE != 0 */
#define NXRS_M_LASTPIXEL   2   /* D3DRS_LASTPIXEL != 0 (lines) */
#define NXRS_M_NOCLIP      3   /* D3DRS_CLIPPING == 0 */
#define NXRS_M_SAMPLEMASK  32  /* bits 32-63: D3DRS_MULTISAMPLEMASK */

/* ---- command stream: ops 0..NxOp_Count-1 are v3 --------------------------------------------------------------- */
enum NxOp4 {
    NxOp_BeginPass4 = NxOp_Count,
                        /* colorCount (0-4), then per colour target 2 words (image, level | layer << 16), then the
                           depth target 2 words (image 0 = none). No clear, no state reset except: no state bound.
                           Targets must agree in size and sample count (the device checks; D3D9 requires it). */
    NxOp_DefineLayout4, /* id, then NxVtxLayout4 as words */
    NxOp_BindVtxN,      /* stream, memBlock, offset, size, stride */
    NxOp_ImageUpdate,   /* image, level | layer << 16, x, y, z, w, h, d, memBlock, offset, size, rowPitch:
                           native-format texels (BCn: 4x4 blocks) of that box; for a cube or 2D image z = 0, d = 1 */
    NxOp_Blit,          /* src image, src level | layer << 16, sx, sy, sw, sh, dst image (0 = swapchain), dst level
                           | layer << 16, dx, dy, dw, dh, filter (0 point, 1 linear): colour or depth-stencil copy
                           with scaling and format conversion; a multisampled source is resolved first.
                           Outside any pass. */
    NxOp_GenMips,       /* image, filter (0 point, 1 linear): levels 1.. from level 0, box filter. Outside any pass. */
    NxOp_QueryBegin,    /* query id, kind (NxQueryKind) */
    NxOp_QueryEnd,      /* query id */
    NxOp_Count4
};
enum NxQueryKind { NxQuery_Occlusion = 0, NxQuery_Event = 1, NxQuery_TimestampNs = 2 };

/* ---- functions (ext_call numbers continue after v3's NXGPU_FN_COUNT) ------------------------------------------ */
typedef struct NxImageRead {         /* waits for all submitted work */
    NxTarget src;
    uint32_t x, y, z, w, h, d;
    uint64_t dst;                    /* native-format bytes, rows top to bottom, rowPitch bytes apart */
    uint32_t dst_size, rowPitch;
} NxImageRead;

typedef struct NxPresent {
    uint32_t image;                  /* the back buffer image the device renders to */
    int32_t sx, sy, sw, sh;          /* source rect */
    int32_t dx, dy, dw, dh;          /* destination rect in the swapchain image */
    uint32_t interval;               /* vertical blanks to wait: 0 immediate, 1-4 */
    uint64_t gamma;                  /* 0 or the address of 3 x 256 u16 (D3DGAMMARAMP) applied by the present blit */
    uint32_t frame, pad;
} NxPresent;

typedef struct NxGpuApi4 {
    NxGpuApi v3;
    uint64_t image_create4;          /* (NxImageDesc4*) -> image id, 0 when the device asked for something the
                                        format table (render/d3d9/env) says this backend has: a backend defect */
    uint64_t image_destroy;          /* (image id) -> 0; waits until no submitted work uses it */
    uint64_t mem_destroy;            /* (mem id) -> 0 */
    uint64_t shader_destroy;         /* (shader id) -> 0 */
    uint64_t sampler_update;         /* (sampler id, NxSamplerState*) -> 0: redefines the id for later submits */
    uint64_t image_read;             /* (NxImageRead*) -> 0 */
    uint64_t query_result;           /* (query id, wait: 0/1, uint64_t* out) -> 0 ready, 1 not ready (wait = 0) */
    uint64_t present;                /* (NxPresent*) -> 0 */
} NxGpuApi4;

enum NxGpuFn4 {
    NXGPU_FN_IMAGE_CREATE4 = NXGPU_FN_COUNT, NXGPU_FN_IMAGE_DESTROY, NXGPU_FN_MEM_DESTROY, NXGPU_FN_SHADER_DESTROY,
    NXGPU_FN_SAMPLER_UPDATE, NXGPU_FN_IMAGE_READ, NXGPU_FN_QUERY_RESULT, NXGPU_FN_PRESENT,
    NXGPU_FN_COUNT4
};

#ifdef __cplusplus
}
#endif
#endif
