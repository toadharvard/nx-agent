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
 *   presentation interval, gamma ramp, software
 *   cursor, GetFrontBufferData, WaitForVBlank    -> NxPresent (rules P1-P5), image_read of image 0, wait_vblank
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
 * creates (D3D9 resources start zero-filled: the device uploads zeros, the backend does not guess).
 *
 * Conventions of ImageUpdate and image_read (fixed by the GL 4.5 backend, gl4_fmt.c / gl4_image.c; deko3d follows):
 *   - Boxes are in the texel space of one level (row 0 = top). A BCn box is in texels too: x, y are multiples of 4
 *     and w, h multiples of 4 unless the box ends at the level's edge; the bytes are 4x4 blocks, rowPitch counts
 *     bytes of a row of blocks.
 *   - A cube image is addressed by the layer (the face, D3DCUBEMAP_FACES order) with z = 0, d = 1. A 3D image is
 *     addressed by z and d (a range of slices) with layer 0; for 3D the data of slice k of the box starts
 *     k * rows * rowPitch after the first (slice pitch = rows * rowPitch, rows = h, or ceil(h / 4) for BCn).
 *   - Depth byte layouts (little endian): Z16 u16; Z32F f32; Z24S8 and Z24X8 one u32 = depth24 << 8 | stencil (the
 *     x byte of Z24X8 is stored and read back as written); Z32FS8 eight bytes: f32 depth, then a u32 whose low byte
 *     is the stencil. Colour formats are those of nx/state.h NxFormat; BGRX8 / RGBX8 read the x byte back as 0xFF.
 *   - Depth images are 2D only; BC4 / BC5 have no 3D form on this backend (image_create4 returns 0). */

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
 * (SM1-3 shaders declare at most 16 inputs), so the bound of 16 loses nothing.
 * `location` is the vertex shader input location (0-15, unique in a layout); `offset` is relative to the element.
 * A layout that violates a bound (type, stream, location, duplicate location, an instance stream with divisor 0,
 * attribute count, an offset beyond the GL limit) makes its submit BAD_CALL before anything runs.
 *
 * What the shader sees, per type (D3D9's expansion; components not listed are 0, 0, 0, 1 for x, y, z, w):
 *   Float1-4        the floats                  D3DColor   bytes B, G, R, A -> (R, G, B, A) / 255
 *   UByte4          4 bytes as 0..255           Short2/4   signed 16-bit values as floats
 *   UByte4N         bytes / 255                 UShort2N/4N  unsigned 16-bit / 65535
 *   Short2N/4N      signed 16-bit / 32767, -32768 -> -1.0 (GL's snorm rule; the D3D9 formula c / 32767 would give
 *                   -1.00003 for that one code, D3D9 drivers on D3D10-class hardware clamp as GL does)
 *   Float16_2/_4    half floats converted exactly
 *   UDec3           x, y, z = the three 10-bit fields as floats 0..1023 (bits 0-9, 10-19, 20-29), w = 1
 *   Dec3N           x, y, z = the signed 10-bit fields / 511, -512 -> -1.0, w = 1
 * Bits 30-31 of UDec3 / Dec3N are ignored by D3D9 (w = 1) but are GL's w, so the backend converts exactly: in the
 * copy of the stream it stages for a draw it sets those two bits of every such word to 01, only for the streams
 * whose layout has such attributes; the bound bytes themselves are never changed (gl4_vtx.c). Out-of-range
 * vertex reads are undefined, as in D3D9. */

/* ---- render state w[3] (v3: reserved 0) --------------------------------------------------------------------------- */
#define NXRS_M_MSAA        0   /* D3DRS_MULTISAMPLEANTIALIAS != 0: clear = a multisampled target is written as if
                                   single-sample (every sample of a covered pixel gets the pixel-centre result) */
#define NXRS_M_AALINE      1   /* D3DRS_ANTIALIASEDLINEENABLE != 0: lines have fractional coverage (the coverage
                                   function is implementation defined, in D3D9 and in GL) */
#define NXRS_M_LASTPIXEL   2   /* D3DRS_LASTPIXEL != 0. No effect: lines are GL's diamond-exit lines whatever the bit is, as in
                                   Wine 9.0 wined3d (WINED3D_RS_LASTPIXEL has no GL state); the bit only keeps the state
                                   identity equal to the D3D9 state */
#define NXRS_M_NOCLIP      3   /* D3DRS_CLIPPING == 0: no near / far plane clipping, depth clamped to the depth range
                                   (user clip planes, w[2] bits 8-15, are independent) */
#define NXRS_M_SAMPLEMASK  32  /* bits 32-63: D3DRS_MULTISAMPLEMASK (all ones = every sample); bit i = sample i of a
                                   multisampled target; no effect on a single-sample target. Other bits of w[3]
                                   (4-31) are reserved: a DefineState that sets them makes the submit BAD_CALL */

/* ---- command stream: ops 0..NxOp_Count-1 are v3 --------------------------------------------------------------- */
enum NxOp4 {
    NxOp_BeginPass4 = NxOp_Count,
                        /* colorCount (0-4), then per colour target 2 words (image, level | layer << 16; image 0 =
                           the swapchain image), then the depth target 2 words (image 0 = none). Colour targets: a
                           2D image, a cube face (layer), a 3D slice (layer), any mip level, multisampled images;
                           colorCount 0 with a depth target = a depth-only pass. Draw buffer i = target i; sRGB
                           writes go through the target's sRGB view (NXRS_R_SRGB). All targets must have the same
                           size at their level and the same sample count, layer and level must exist, colour
                           targets must be colour images and the depth target a depth image, and there must be
                           at least one target: else the submit is BAD_CALL before anything runs (the device
                           guarantees it; D3D9 requires it). No clear; the pass starts as NxOp_BeginPass does
                           (viewport (0.5, 0.5, w, h), depth range 0..1, scissor (0, 0, w, h), blend constant
                           (1,1,1,1), depth bias 0, no state bound). NxOp_Clear clears every colour target of
                           the pass (D3D9 Clear). */
    NxOp_DefineLayout4, /* id (shared id space with NxOp_DefineLayout), then NxVtxLayout4 as words (50). A redefinition
                           takes effect at the next BindState. */
    NxOp_BindVtxN,      /* stream, memBlock, offset, size, stride: the bytes [offset, offset + size) of the block at
                           this moment are the stream's data (a snapshot, like every bind) with `stride` bytes per
                           element (0 = one element for every vertex / instance; at most the GL limit). memBlock 0
                           with size 0 unbinds the stream. A draw whose layout reads an unbound stream is BAD_CALL.
                           NxOp_BindVtx is BindVtxN(stream 0, ..., the stride of the v3 layout). */
    NxOp_ImageUpdate,   /* image, level | layer << 16, x, y, z, w, h, d, memBlock, offset, size, rowPitch:
                           native-format texels (BCn: 4x4 blocks) of that box; for a cube or 2D image z = 0, d = 1;
                           layouts of the box: see the conventions at NxImageDesc4 (3D: z/d = slices, layer 0,
                           slice pitch = rows * rowPitch) */
    NxOp_Blit,          /* src image, src level | layer << 16, sx, sy, sw, sh, dst image (0 = swapchain), dst level
                           | layer << 16, dx, dy, dw, dh, filter (0 point, 1 linear): colour or depth-stencil copy
                           with scaling and format conversion; a multisampled source is resolved first.
                           Outside any pass. Same format and size = an exact copy of any format. POINT: destination
                           texel d of a dw-wide destination from an sw-wide source takes the source texel
                           floor((d + 0.5) * sw / dw) = ((2 d + 1) * sw) / (2 dw), the same in y (an exact 2:1
                           takes texel 2 d + 1); done by integer arithmetic in the backend, so the result does
                           not depend on the GL implementation's nearest rule. Depth-stencil: identical formats
                           only, always point. Colour: raw values (no sRGB conversion), converted to the
                           destination format. LINEAR: bilinear at the destination texel centre (<= 1 LSB of the
                           ideal). A compressed format only copies. */
    NxOp_GenMips,       /* image, filter (0 point, 1 linear): levels 1.. from level 0, box filter. Outside any pass.
                           Level l is Blit(level l - 1 -> level l, whole, filter) per layer; point follows the rule
                           above (3D: slice z of level l takes slice floor((2 z + 1) * sd / (2 dd)) of level l - 1,
                           sd, dd = the depths); linear 3D uses GL's own mipmap filter (open point). */
    NxOp_QueryBegin,    /* query id (1..4096), kind (NxQueryKind): (re)defines the id; occlusion queries start
                           counting, event and timestamp queries only declare the id. Begin again = restart. */
    NxOp_QueryEnd,      /* query id: occlusion = stop counting; event = insert a fence; timestamp = record the GPU
                           time. An id that was never begun, or an occlusion query that is not active = BAD_CALL. */
    NxOp_Count4
};
enum NxQueryKind { NxQuery_Occlusion = 0, NxQuery_Event = 1, NxQuery_TimestampNs = 2 };
/* Results (query_result): occlusion = the number of samples that passed (= pixels on a single-sample target) of
 * the draws issued between Begin and End (overlapping queries are exact; the backend's own blits do not count);
 * event = 1 once all work before QueryEnd has completed; timestamp = GPU time in nanoseconds at QueryEnd. */

/* ---- functions (ext_call numbers continue after v3's NXGPU_FN_COUNT) ------------------------------------------ */
typedef struct NxImageRead {         /* waits for all submitted work */
    NxTarget src;
    uint32_t x, y, z, w, h, d;
    uint64_t dst;                    /* native-format bytes, rows top to bottom, rowPitch bytes apart */
    uint32_t dst_size, rowPitch;
} NxImageRead;

/* Present: the source rect of `image` (colour, level 0, any sample count) is written into the destination rect of
 * the swapchain image (the rest is untouched). Different sizes: POINT sampling by the Blit rule above (D3D9 leaves
 * the presentation filter to the driver). Source values are converted to the 8-bit RGBA swapchain as for a Blit.
 * Gamma ramp (3 x 256 u16: red, green, blue): per colour channel v = round(clamp(channel, 0, 1) * 255) (the 8-bit
 * value of an 8-bit source), out = ramp[channel][v] >> 8; alpha unchanged. interval is 0-4 (else BAD_CALL) and has
 * no observable effect on a surfaceless GL context: pacing belongs to the embedder (on the console the display's
 * vertical blank paces the swapchain: the call returns after `interval` blanks). After the image is complete
 * the backend calls the embedder's `void nxgl4_on_present(uint32_t frame)` (gl4.h) and, when the environment
 * variable NXGL4_FRAMES names a directory, first writes <dir>/frame_%06u.rgba (the swapchain image, RGBA8, top row
 * first) and frame_%06u.txt ("width height"). Rects outside either image, empty rects, a bad image = BAD_CALL (no
 * hook runs).
 *
 * Ordering and memory (v4 rule P1): PRESENT is ordered after every stream submitted before it (the device submits
 * its pending stream first; GL: submits are synchronous, deko3d: one queue). Every byte PRESENT reads (this
 * struct, the ramp, the cursor pixels) is consumed before the call returns: the caller may reuse or free that
 * memory immediately. The device never uses NxSubmit.present and never calls WAIT_FRAME for a present: frames in
 * flight are bounded by the stream ring of the glue (glue_stream.h R4).
 *
 * The software cursor (rule P2). D3D9 draws the software cursor over the PRESENTED image, never into the back
 * buffer (Wine 9.0 wined3d/swapchain.c swapchain_gl_present, cursor_texture: drawn onto the drawable after the back
 * buffer was blitted into it, recalled, the Wine source is not in this tree; the back buffer is not touched, so a later GetBackBuffer / lock reads what the application
 * drew). NxOp_Blit cannot express it (a Blit copies raw values, no blending, and is a stream op that runs before the
 * present), so the cursor is part of the present: when cursor != 0, after the destination rect has been written
 * (gamma included) and before the frame file / hook / display, the cursor image is alpha blended onto the swapchain
 * image. `cursor` = address of cw x ch pixels, D3DFMT_A8R8G8B8 (bytes B, G, R, A in memory), rows cw * 4 bytes
 * apart, top row first; (cx, cy) = the top-left corner in swapchain pixels (may be negative or outside: the part
 * outside the swapchain image is dropped; entirely outside = no change). 1 <= cw, ch <= 4096, else BAD_CALL (no hook
 * runs). Blend, exactly: a = pixel alpha (0-255); for each colour channel c with cursor value v (the gamma ramp,
 * when the present has one, is applied to v first: v = ramp[c][v] >> 8, as the display's ramp acts on the whole
 * output) and swapchain value d: out = (v * a + d * (255 - a) + 127) / 255 (integer division); the swapchain
 * alpha is unchanged. cursor = 0: no cursor.
 *
 * Swapchain image contents (rule P3): RGBA8, row 0 = top; the backend creates it all zero (RGBA 0, 0, 0, 0);
 * afterwards it holds what was last written to it (presents; v3 passes / blits that target image 0).
 *
 * Reading the front buffer (rule P4): NxImageRead with src.image == 0 reads the swapchain image: level 0, layer 0,
 * z = 0, d = 1, any box inside the image, bytes RGBA8 (R, G, B, A), top row first, rowPitch apart. It waits for
 * all submitted work like every image_read. D3D9 GetFrontBufferData before any Present returns the desktop under
 * the window; this environment has no desktop, its swapchain image is the all-zero image of rule P3 and the read
 * returns exactly that. No swapchain (NxGpuInfo.width == 0) = BAD_CALL.
 *
 * Vertical blank (rule P5): NXGPU_FN_WAIT_VBLANK(output): blocks until the next vertical blank of output 0 (the
 * only output, the one the swapchain presents to; any other value = BAD_CALL). Console: waits for the display's
 * vsync event (libnx viGetDisplayVsyncEvent). GL host reference (no display): a modelled 60 Hz panel, blanks at
 * k * 10^9 / 60 ns of CLOCK_MONOTONIC, the call returns at the first blank strictly after the call. It does not
 * touch the swapchain image. */
typedef struct NxPresent {
    uint32_t image;                  /* the back buffer image the device renders to */
    int32_t sx, sy, sw, sh;          /* source rect */
    int32_t dx, dy, dw, dh;          /* destination rect in the swapchain image */
    uint32_t interval;               /* vertical blanks to wait: 0 immediate, 1-4 */
    uint64_t gamma;                  /* 0 or the address of 3 x 256 u16 (D3DGAMMARAMP) applied by the present blit */
    uint32_t frame, pad;
    uint64_t cursor;                 /* 0 or the address of cw x ch BGRA pixels (rule P2) */
    int32_t cx, cy;                  /* top-left corner of the cursor in swapchain pixels */
    uint32_t cw, ch;
} NxPresent;
NX_ASSERT_(sizeof(NxPresent) == 80, "NxPresent");

typedef struct NxGpuApi4 {
    NxGpuApi v3;
    uint64_t image_create4;          /* (NxImageDesc4*) -> image id, 0 when the device asked for something the
                                        format table (render/d3d9/env) says this backend has: a backend defect */
    uint64_t image_destroy;          /* (image id) -> 0; waits until no submitted work uses it */
    uint64_t mem_destroy;            /* (mem id) -> 0 */
    uint64_t shader_destroy;         /* (shader id) -> 0 */
    uint64_t sampler_update;         /* (sampler id, NxSamplerState*) -> 0: redefines the id in place (every field is
                                        rewritten, nothing of the old definition survives) for later draws; an id
                                        that is not a sampler = BAD_CALL */
    uint64_t image_read;             /* (NxImageRead*) -> 0 */
    uint64_t query_result;           /* (query id, wait: 0/1, uint64_t* out) -> 0 ready (*out written), 1 not ready
                                        (wait = 0: not completed, or QueryEnd not issued yet); wait = 1 blocks until
                                        ready; wait = 1 on a query whose QueryEnd was never issued, an unknown id or
                                        wait > 1 = BAD_CALL */
    uint64_t present;                /* (NxPresent*) -> 0: see NxPresent */
    uint64_t wait_vblank;            /* (output) -> 0 after the next vertical blank: rule P5 at NxPresent */
} NxGpuApi4;

enum NxGpuFn4 {
    NXGPU_FN_IMAGE_CREATE4 = NXGPU_FN_COUNT, NXGPU_FN_IMAGE_DESTROY, NXGPU_FN_MEM_DESTROY, NXGPU_FN_SHADER_DESTROY,
    NXGPU_FN_SAMPLER_UPDATE, NXGPU_FN_IMAGE_READ, NXGPU_FN_QUERY_RESULT, NXGPU_FN_PRESENT,
    NXGPU_FN_WAIT_VBLANK,
    NXGPU_FN_COUNT4
};

#ifdef __cplusplus
}
#endif
#endif
