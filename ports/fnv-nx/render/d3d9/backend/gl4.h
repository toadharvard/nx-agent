/* fnv-nx L6: GL 4.5 backend v4 (B2a: base port of nxgl v3 + images). Host only (Linux, EGL surfaceless, Mesa
 * llvmpipe). Implements NxGpuApi4 (nxgpu4.h) over desktop GL 4.5 core. Every guest address is translated by
 * nxgl4_guest_ptr(); nothing here dereferences a guest address directly.
 *
 * B2b (BeginPass4, DefineLayout4, BindVtxN, QueryBegin/End, sampler_update, query_result, present) is not in the
 * op/function tables; the build of gl4_core.c refuses to compile without -DNXGL4_B2B_PENDING until it is. */
#ifndef FNVNX_GL4_H
#define FNVNX_GL4_H
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <GL/glcorearb.h>
#include "nxgpu4.h"
#include "gl4_funcs.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---- public ------------------------------------------------------------------------------------------------- */
/* The embedder maps a guest address range [addr, addr + size) to a host pointer (the test provides identity). */
void* nxgl4_guest_ptr(uint64_t addr, uint64_t size);
/* Creates the surfaceless GL 4.5 core context (current on the calling thread; every later call comes from that
 * thread) and the swapchain image (image 0 names it) of width x height (none when 0). NULL on failure (err). */
const NxGpuApi4* nxgl4_create(uint32_t width, uint32_t height, char* err, size_t errlen);
void nxgl4_destroy(void);
/* ext_call dispatch for fn 0..NXGPU_FN_COUNT4-1 over the table the backend created; arguments as in NxGpuApi4. */
uint64_t nxgl4_ext(uint32_t fn, const uint64_t* args, uint32_t nargs);
const char* nxgl4_last_error(void);
size_t nxgl4_shim_glsl(const char* src, size_t n, uint32_t stage, char* out, size_t cap);

/* ---- internal ----------------------------------------------------------------------------------------------- */
#define GL4_MAX_MEM      4096
#define GL4_MAX_SHADERS  4096
#define GL4_MAX_STATES   4096
#define GL4_MAX_LAYOUTS  256
#define GL4_MAX_PROGRAMS 1024
#define GL4_MAX_FBOS     64

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
typedef struct { GLuint sh; uint8_t stage, used; } Gl4Shader;
typedef struct { uint16_t vs, ps; GLuint prog; uint8_t used; } Gl4Program;
typedef struct { NxRenderState rs; uint16_t vs, ps, layout, prog; uint32_t epoch; uint8_t defined; } Gl4State;
typedef struct { NxVtxLayout l; GLuint vao; uint8_t defined; } Gl4Layout;
typedef struct { uint32_t color, depth; GLuint fbo; } Gl4Fbo;

typedef struct Gl4 {
    void* dpy; void* ctx;
    NxGpuApi4 api;
    Gl4Mem mem[GL4_MAX_MEM + 1];
    Gl4Image img[NX_MAX_IMAGES + 2];
    GLuint smp[NX_MAX_SAMPLERS]; uint32_t nSmp;
    Gl4Shader sh[GL4_MAX_SHADERS + 1];
    Gl4Program prog[GL4_MAX_PROGRAMS + 1]; uint32_t progEpoch;
    Gl4State st[GL4_MAX_STATES + 1];
    Gl4Layout lay[GL4_MAX_LAYOUTS + 1];
    Gl4Fbo fbo[GL4_MAX_FBOS]; uint32_t nFbo, fboNext;
    GLuint blitFbo[2];          /* scratch read/draw framebuffers of blits and reads */
    uint32_t swapImage, passColor, frameDraws;
    uint64_t drawsFrame;
    uint32_t errCount;          /* contract violations recorded (gl4_err) */
    char err[2048];
    uint32_t boundState, passW, passH, vtxBlock, vtxOffset, idxType, idxBlock, idxOffset, stencilRef;
    int haveApplied, vtxDirty;
    NxRenderState applied;
    GLint scissor[4];
} Gl4;
extern Gl4 g4;

void gl4_err(const char* fmt, ...);
/* number of texels of an edge at a mip level */
static inline uint32_t gl4_lvl(uint32_t v, uint32_t level) { v >>= level; return v ? v : 1u; }

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
void gl4_op_blit(const uint32_t* a, uint32_t argc);
void gl4_op_genmips(const uint32_t* a, uint32_t argc);
/* attaches one subresource of an image (colour or depth-stencil) to a framebuffer attachment point */
void gl4_attach(GLuint fbo, GLenum att, const Gl4Image* im, uint32_t level, uint32_t layer);
/* a new single-sample 2D texture holding the resolve of a multisampled image (the caller deletes it) */
GLuint gl4_resolve_tex(const Gl4Image* im);
/* gl4_state.c */
void gl4_apply_state(const NxRenderState* rs, int force);
void gl4_apply_stencil(uint64_t w1, uint32_t ref);   /* stencil funcs of both faces with the given reference */
void gl4_op_begin_pass(const uint32_t* a, uint32_t argc);
void gl4_op_clear(const uint32_t* a, uint32_t argc);
void gl4_bind_layout(uint32_t lay);
void gl4_fbo_forget(uint32_t image);
uint64_t gl4_api_sampler_create(uint64_t desc);
/* gl4_shader.c */
uint64_t gl4_api_shader_create(uint64_t desc);
uint64_t gl4_api_shader_destroy(uint64_t id);
uint16_t gl4_program_for(uint16_t vs, uint16_t ps);
/* gl4_core.c */
void gl4_upload_range(uint32_t block, uint32_t off, uint32_t size);
const Gl4Mem* gl4_mem(uint32_t id);
/* gl4_stream.c */
uint64_t gl4_api_submit(uint64_t desc);
uint64_t gl4_api_wait_frame(uint64_t frame);

/* The op and function tables (X-macro lists; the handlers are gl4_op_<name> / gl4_api_<name>). The B2b entries
 * are not here: gl4_core.c asserts that the lists cover NxOp_Count4 / NXGPU_FN_COUNT4. */
#define NXGL4_OP_LIST(X) \
    X(DefineState) X(DefineLayout) X(BeginPass) X(EndPass) X(Barrier) X(BindState) X(BindUbo) X(BindVtx) \
    X(BindIdx) X(BindTextures) X(Draw) X(DrawIndexed) X(Viewport) X(Scissor) X(StencilRef) X(BlendConst) \
    X(DepthBias) X(Clear) X(Marker) X(ImageUpdate) X(Blit) X(GenMips)
/* function list: X(FN id suffix, NxGpuApi4 field, implementation) ; INFO and SHUTDOWN are handled by the core */
#define NXGL4_FN_LIST(X) \
    X(MEM_CREATE, v3.mem_create, gl4_api_mem_create) X(MEM_GPU_ADDR, v3.mem_gpu_addr, gl4_api_mem_gpu_addr) \
    X(IMAGE_CREATE, v3.image_create, gl4_api_image_create) \
    X(SAMPLER_CREATE, v3.sampler_create, gl4_api_sampler_create) \
    X(SHADER_CREATE, v3.shader_create, gl4_api_shader_create) X(SUBMIT, v3.submit, gl4_api_submit) \
    X(WAIT_FRAME, v3.wait_frame, gl4_api_wait_frame) X(READBACK, v3.readback, gl4_api_readback) \
    X(IMAGE_CREATE4, image_create4, gl4_api_image_create4) X(IMAGE_DESTROY, image_destroy, gl4_api_image_destroy) \
    X(MEM_DESTROY, mem_destroy, gl4_api_mem_destroy) X(SHADER_DESTROY, shader_destroy, gl4_api_shader_destroy) \
    X(IMAGE_READ, image_read, gl4_api_image_read)
uint64_t gl4_api_mem_create(uint64_t desc);
uint64_t gl4_api_mem_gpu_addr(uint64_t id);
uint64_t gl4_api_mem_destroy(uint64_t id);

#ifdef __cplusplus
}
#endif
#endif
