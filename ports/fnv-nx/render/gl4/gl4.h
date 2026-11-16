/* fnv-nx L6: GL 4.5 backend v4 (B2a + B2b: the whole of ABI v4, nxgpu4.h). Host only (Linux, EGL surfaceless, Mesa
 * llvmpipe). Implements NxGpuApi4 over desktop GL 4.5 core. Every guest address is translated by
 * nxgl4_guest_ptr(); nothing here dereferences a guest address directly. The op and function tables are total over
 * NxOp_Count4 / NXGPU_FN_COUNT4 (static assert in gl4_core.c); the backend reports version 4. */
#ifndef FNVNX_GL4_H
#define FNVNX_GL4_H
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <GL/glcorearb.h>
#include "nxgpu4.h"
#include "nxgpu4_check.h"
#include "nxgpu4_shader.h"
#include "gl4_funcs.h"
#include "nx_gl4shim.h"
#include "fnvnx_dk_layout.h"

#ifdef __cplusplus
extern "C" {
#endif

/* The marker of the library's exports (gen_exports.py reads it: the linker version script is generated from the declarations that carry it). */
#define NXGL4_EXPORT __attribute__((visibility("default")))

/* ---- public ------------------------------------------------------------------------------------------------- */
/* The embedder maps a guest address range [addr, addr + size) to a host pointer (the test provides identity). */
/* Contract of the embedder: a host pointer to [addr, addr + size) when the whole range lies in ONE guest mapping the guest may read and write,
 * else NULL (the call then fails with NXGPU_BAD_CALL). size == 0: a non-NULL pointer when addr lies inside a mapping or exactly at its end (the
 * library does not dereference it), NULL when addr is outside every mapping. The device passes only memory of nxd3d_gpu_alloc (glue_stream.h). */
void* nxgl4_guest_ptr(uint64_t addr, uint64_t size);
/* Creates the surfaceless GL 4.5 core context (current on the calling thread; every later call comes from that
 * thread) and the swapchain image (image 0 names it) of width x height (none when 0). NULL on failure (err). */
NXGL4_EXPORT const NxGpuApi4* nxgl4_create(uint32_t width, uint32_t height, char* err, size_t errlen);
NXGL4_EXPORT void nxgl4_destroy(void);
/* ext_call dispatch for fn 0..NXGPU_FN_SHADER_CREATE_D3D (= NXGPU_FN_COUNT4) over the table the backend created; arguments as in NxGpuApi4. */
NXGL4_EXPORT uint64_t nxgl4_ext(uint32_t fn, const uint64_t* args, uint32_t nargs);
NXGL4_EXPORT const char* nxgl4_last_error(void);
/* The embedder's present hook, called by the present call (gl4_present.c) after the frame file (below) is written.
 * The embedder paces the frame there (the surfaceless context has no vblank: NxPresent.interval has no effect on
 * the image). When the environment variable NXGL4_FRAMES names a directory the present call writes the presented
 * swapchain image as <dir>/frame_%06u.rgba (RGBA8, top row first) and <dir>/frame_%06u.txt ("width height\n"). */
void nxgl4_on_present(uint32_t frame);

/* ---- internal ----------------------------------------------------------------------------------------------- */
#define GL4_MAX_STATES   NXCHK_MAX_STATES
#define GL4_MAX_LAYOUTS  NXCHK_MAX_LAYOUTS
#define GL4_MAX_FBOS     64
#define GL4_MAX_QUERIES  NX4_MAX_QUERIES

#define X(type, name) extern type p_##name;
NXGL4_FUNCS(X)
#undef X
int gl4_load_gl(void* (*getproc)(const char*), char* err, size_t errlen);

typedef struct { uint64_t storage; uint32_t size; GLuint buf; uint8_t used; } Gl4Mem;
typedef struct {
    GLuint tex;
    GLenum target;
    uint32_t base;              /* a view entry: the image it views */
    uint16_t w, h, d;
    uint8_t fmt, type, samples, mips, isView, used, isDepth, hasStencil;
} Gl4Image;
/* A shader made by NXGPU_FN_SHADER_CREATE_D3D: its memo key (the bytecode itself, so two codes never share an entry whatever their hash) and the number of
 * creates that returned this id (each SHADER_DESTROY takes one back; the last one releases the GL shader). */
typedef struct Gl4D3d { uint8_t* code; uint32_t size, refs; uint8_t stage, variant, nl, l[FNVNX_DK_VARYING_SLOTS]; } Gl4D3d;
typedef struct { GLuint sh; uint8_t stage, used; Gl4D3d* d3d; } Gl4Shader;
typedef struct { uint32_t vs, ps; GLuint prog; uint8_t used; } Gl4Program;
typedef struct { NxRenderState rs; uint32_t vs, ps, prog; uint16_t layout; uint32_t epoch; uint8_t defined; } Gl4State;
typedef struct { NxVtxLayout l; NxVtxLayout4 l4; GLuint vao; uint32_t gen; uint8_t defined, is4, stale; } Gl4Layout;
typedef NxChkTgt Gl4Tgt;               /* sub = level | layer << 16; image 0 = none */
typedef NxChkPass Gl4Pass;             /* unused entries are zero (memcmp key) */
typedef struct { Gl4Pass key; GLuint fbo; } Gl4Fbo;
typedef struct {   /* one vertex stream: a snapshot of the bound range, staged into buf when a draw needs it */
    uint8_t* data; uint32_t size, cap, stride, bindSeq, stagedSeq; uint64_t stagedSig;
    GLuint buf; uint8_t bound, isV3;
} Gl4Vtx;
typedef struct { uint64_t acc; GLuint q; GLsync sync; uint8_t kind, used, active, issued; } Gl4Query;
typedef struct { GLuint q; uint32_t n; uint32_t* ids; } Gl4Slice;   /* a GL occlusion query and the queries it counts for */

typedef struct Gl4 {
    void* dpy; void* ctx;
    NxGpuApi4 api;
    Gl4Mem* mem; uint32_t nMem;        /* slot = id; slot 0 unused; grown on demand (gl4_table_grow) */
    Gl4Image img[NX_MAX_IMAGES + 2];
    GLuint smp[NX_MAX_SAMPLERS]; uint32_t nSmp;
    Gl4Shader* sh; uint32_t nSh;
    Gl4Program* prog; uint32_t nProg, progEpoch;
    Gl4State st[GL4_MAX_STATES + 1];
    Gl4Layout lay[GL4_MAX_LAYOUTS + 1];
    Gl4Fbo fbo[GL4_MAX_FBOS]; uint32_t nFbo, fboNext;
    GLuint blitFbo[2];          /* scratch read/draw framebuffers of blits and reads */
    uint32_t swapImage, passN, passSamples, frameDraws;
    uint64_t drawsFrame;
    uint32_t errCount;          /* contract violations recorded (gl4_err) */
    char err[2048];
    uint32_t boundState, passW, passH, idxType, idxBlock, idxOffset, stencilRef;
    int haveApplied, vtxDirty;
    Gl4Vtx vtx[NX4_MAX_STREAMS];
    Gl4Query qry[GL4_MAX_QUERIES + 1];
    Gl4Slice* slices; uint32_t nSlices, capSlices; GLuint occCur;
    GLuint nearestSmp, rampTex, scaleProg[NX_GL4SHIM_PAIR_COUNT];
    GLint maxVtxStride, maxAttrOffset;
    NxRenderState applied;
    GLint scissor[4];
} Gl4;
extern Gl4 g4;

#define gl4_err nxchk_err   /* the backend-neutral checks (nxgpu4_check.c) report through the same sink */
const NxChkEnv* gl4_chk_env(void);   /* gl4_check.c: the neutral view of this backend (images, limits) */
/* number of texels of an edge at a mip level */
static inline uint32_t gl4_lvl(uint32_t v, uint32_t level) { return nxchk_lvl(v, level); }

/* gl4_fmt.c: one entry per NxFormat (zero internal = not an NxFormat) */
typedef struct {
    GLenum internal, format, type, srgb;   /* srgb: the sRGB view's internal format, 0 = none */
    uint8_t bpp;                           /* bytes per texel, per 4x4 block when compressed */
    uint8_t compressed, depth, stencil;    /* stencil: the NxFormat has stencil bits */
    uint8_t glds;                          /* the GL internal format is depth-stencil */
} Gl4Fmt;
Gl4Fmt gl4_fmt(uint32_t nxfmt);
GLint gl4_swz(uint32_t nibble);
size_t gl4_row_bytes(const Gl4Fmt* f, uint32_t w);    /* bytes of one row of w texels (block row when compressed) */
size_t gl4_rows(const Gl4Fmt* f, uint32_t h);         /* rows of h texels (block rows when compressed) */

/* gl4_image.c */
Gl4Image* gl4_img(uint32_t id);                       /* a live image or sRGB view entry, else NULL */
uint32_t gl4_view_of(uint32_t id);                    /* the sRGB view entry of a base image, else id itself */
uint32_t gl4_image_make(const NxImageDesc4* d);    /* host-side create (no guest address involved) */
uint64_t gl4_api_image_create(uint64_t desc);
uint64_t gl4_api_image_create4(uint64_t desc);
uint64_t gl4_api_image_destroy(uint64_t id);
void gl4_op_image_update(const uint32_t* a, uint32_t argc);
/* gl4_read.c */
uint64_t gl4_api_image_read(uint64_t desc);
uint64_t gl4_api_readback(uint64_t desc);
/* gl4_blit.c */
typedef struct { const Gl4Image* im; uint32_t level, layer, x, y, w, h; } Gl4Sub;   /* a rect of one subresource */
void gl4_op_blit(const uint32_t* a, uint32_t argc);
void gl4_op_genmips(const uint32_t* a, uint32_t argc);
/* attaches one subresource of an image (colour or depth-stencil) to a framebuffer attachment point */
void gl4_detach_all(GLuint fbo);   /* no attachment left on fbo */
void gl4_attach(GLuint fbo, GLenum att, const Gl4Image* im, uint32_t level, uint32_t layer);
/* a new single-sample 2D texture holding the resolve of a multisampled image (the caller deletes it) */
GLuint gl4_resolve_tex(const Gl4Image* im);
/* gl4_state.c */
void gl4_apply_state(const NxRenderState* rs, int force);
void gl4_apply_stencil(uint64_t w1, uint32_t ref);   /* stencil funcs of both faces with the given reference */
void gl4_neutral_state(void);                        /* known state for internal draws and blits; forces a re-apply */
/* gl4_pass.c: render passes (NxOp_BeginPass v3 and BeginPass4) and clears */
void gl4_op_begin_pass(const uint32_t* a, uint32_t argc);
void gl4_op_begin_pass4(const uint32_t* a, uint32_t argc);
void gl4_op_clear(const uint32_t* a, uint32_t argc);
void gl4_fbo_forget(uint32_t image);
/* gl4_vtx.c: vertex layouts (v3 and v4) and vertex streams */
void gl4_op_define_layout(const uint32_t* a, uint32_t argc);
void gl4_op_define_layout4(const uint32_t* a, uint32_t argc);
void gl4_op_bind_vtx(const uint32_t* a, uint32_t argc);      /* v3: stream 0, stride from the v3 layout */
void gl4_op_bind_vtxn(const uint32_t* a, uint32_t argc);
void gl4_bind_layout(uint32_t lay);
int gl4_vtx_flush(void);   /* stages and binds the streams the bound state reads; 0 ok, else a recorded error */
/* gl4_sampler.c */
uint64_t gl4_api_sampler_create(uint64_t desc);
uint64_t gl4_api_sampler_update(uint64_t id, uint64_t desc);
/* gl4_query.c: occlusion queries are counted in slices (one GL query per interval in which the set of active
 * queries is constant), so overlapping D3D queries are exact; slices are collected at the end of every submit */
void gl4_op_query_begin(const uint32_t* a, uint32_t argc);
void gl4_op_query_end(const uint32_t* a, uint32_t argc);
uint64_t gl4_api_query_result(uint64_t id, uint64_t wait, uint64_t out);
void gl4_query_submit_begin(void);   /* reopens the occlusion slice of the queries still active */
void gl4_query_submit_end(void);     /* closes the slice of the active queries */
void gl4_query_collect(void);        /* after glFinish: adds the finished slices to their queries */
void gl4_query_pause(void);          /* internal draws (scale blits) must not count: close / reopen the slice */
void gl4_query_resume(void);
/* gl4_scale.c: point-filtered scaling by rule (source texel floor((d + 0.5) * sw / dw)), texelFetch shaders */
void gl4_scale_point(const Gl4Sub* s, const Gl4Sub* d, const uint16_t* ramp);   /* ramp: 3 x 256 u16 or NULL */
int gl4_cursor_stamp(const Gl4Image* im, int32_t cx, int32_t cy, uint32_t cw, uint32_t ch, const uint8_t* bgra);   /* nxgpu4.h P2 (gl4_scale.c) */
/* gl4_present.c */
uint64_t gl4_api_present(uint64_t desc);
uint64_t gl4_api_wait_vblank(uint64_t output);
int gl4_swap_zero(void);   /* the swapchain image starts as RGBA 0; 1 = failed */
/* gl4_check.c: whole-stream checks done before anything executes */
int gl4_stream_check(const uint32_t* w, const uint32_t* end);
/* gl4_shader.c */
uint64_t gl4_api_shader_destroy(uint64_t id);
uint32_t gl4_shader_from_glsl(const char* glsl, size_t n, uint32_t stage);   /* shim + compile + register: the id, or 0 with nothing registered and the log in gl4_err */
/* gl4_d3dshader.c: NXGPU_FN_SHADER_CREATE_D3D */
uint64_t gl4_api_shader_create_d3d(uint64_t desc);
void gl4_d3d_free(Gl4D3d* d);
uint32_t gl4_program_for(uint32_t vs, uint32_t ps);
/* Id tables: the id is the slot index (ids 1.., no bound in the ABI). Doubles *cap (at least 64 slots) and zeroes the new slots; 0 only when the host allocator fails (the table is unchanged). */
int gl4_table_grow(void** tab, uint32_t* cap, size_t elem);
GLuint gl4_compile_pair(const struct nx_gl4shim_pair* row);   /* an internal program of the gl4shim table: 0 and an error when it fails */
uint32_t gl4_compile_stage(uint32_t id, const struct nx_gl4shim_stage* row);   /* a stage row under its reserved shader id: the id, or 0 and an error */
/* gl4_core.c */
void gl4_upload_range(uint32_t block, uint32_t off, uint32_t size);
const Gl4Mem* gl4_mem(uint32_t id);
/* gl4_stream.c */
uint64_t gl4_api_submit(uint64_t desc);
uint64_t gl4_api_wait_frame(uint64_t frame);

/* The op and function tables are expanded from the ABI lists (nxgpu: NXGPU_OP_LIST, NXGPU_FN_LIST_V3 / _V4 / _EXT, nxgpu4_shader.h);
 * the handlers are gl4_op_<name> / gl4_api_<member>. INFO and SHUTDOWN are handled by the core. */
uint64_t gl4_api_mem_create(uint64_t desc);
uint64_t gl4_api_mem_gpu_addr(uint64_t id);
uint64_t gl4_api_mem_destroy(uint64_t id);

#ifdef __cplusplus
}
#endif
#endif
