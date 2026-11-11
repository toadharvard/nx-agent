/* fnv-nx L6 GL 4.5 backend v4: EGL surfaceless GL 4.5 core context, memory blocks, the NxGpuApi4 table and the
 * ext_call dispatch. Conventions mapped onto GL are those of nxgl v3 (render/native/gl/nxgl.c, nx/state.h):
 * glClipControl(LOWER_LEFT, ZERO_TO_ONE) + y negated in every vertex shader (clip +y = framebuffer row 0 = top
 * row), first-vertex provoking vertex, on-screen CW is front, binding = stage * 16 + slot, sRGB through texture
 * views (image id + 1). Memory blocks are caller-backed: the ranges a bind names are copied into the block's GL
 * buffer when the bind is replayed. */
#define _GNU_SOURCE
#include "gl4.h"
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <stdio.h>
#include <string.h>

/* ---- the tables are complete, or B2b is explicitly pending ----------------------------------------------------- */
#define CNT1(a) +1
#define CNT3(a, b, c) +1
enum { N_OPS = 0 NXGL4_OP_LIST(CNT1), N_FNS = 2 NXGL4_FN_LIST(CNT3) };
#ifdef NXGL4_B2B_PENDING
_Static_assert(N_OPS == NxOp_Count4 - 5 && N_FNS == NXGPU_FN_COUNT4 - 3, "B2a tables = v4 minus the B2b entries");
#define NXGL4_VERSION NXGPU_VERSION   /* 3: the device refuses to start on a backend without every v4 entry */
#else
_Static_assert(N_OPS == NxOp_Count4 && N_FNS == NXGPU_FN_COUNT4,
               "gl4: the op/function tables lack the B2b entries (BeginPass4, DefineLayout4, BindVtxN, QueryBegin/End, "
               "sampler_update, query_result, present): build with -DNXGL4_B2B_PENDING until B2b lands");
#define NXGL4_VERSION NXGPU_VERSION4
#endif

/* ---- memory blocks ----------------------------------------------------------------------------------------------- */
const Gl4Mem* gl4_mem(uint32_t id) { return id && id <= GL4_MAX_MEM && g4.mem[id].used ? &g4.mem[id] : NULL; }

uint64_t gl4_api_mem_create(uint64_t a) {
    const NxMemBlockDesc* d = (const NxMemBlockDesc*)nxgl4_guest_ptr(a, sizeof *d);
    if (!d || !d->size || !d->storage) return 0;
    for (uint32_t id = 1; id <= GL4_MAX_MEM; id++) {
        if (g4.mem[id].used) continue;
        Gl4Mem* m = &g4.mem[id];
        m->storage = d->storage; m->size = d->size; m->used = 1;
        p_glGenBuffers(1, &m->buf);
        p_glBindBuffer(GL_COPY_WRITE_BUFFER, m->buf);
        p_glBufferData(GL_COPY_WRITE_BUFFER, d->size, NULL, GL_DYNAMIC_DRAW);
        return id;
    }
    return 0;
}
uint64_t gl4_api_mem_gpu_addr(uint64_t id) { (void)id; return 0; }

uint64_t gl4_api_mem_destroy(uint64_t id) {
    if (id > GL4_MAX_MEM || !gl4_mem((uint32_t)id)) { gl4_err("mem_destroy: %llu is not a live block", (unsigned long long)id); return NXGPU_BAD_CALL; }
    p_glDeleteBuffers(1, &g4.mem[id].buf);
    memset(&g4.mem[id], 0, sizeof g4.mem[id]);
    if (g4.vtxBlock == id) g4.vtxBlock = 0;
    if (g4.idxBlock == id) g4.idxBlock = 0;
    return 0;
}

void gl4_upload_range(uint32_t block, uint32_t off, uint32_t size) {
    const Gl4Mem* m = gl4_mem(block);
    if (!m || (uint64_t)off + size > m->size) { gl4_err("bind: range %u+%u outside block %u", off, size, block); return; }
    p_glBindBuffer(GL_COPY_WRITE_BUFFER, m->buf);
    p_glBufferSubData(GL_COPY_WRITE_BUFFER, off, size, nxgl4_guest_ptr(m->storage + off, size));
}

/* ---- ext_call dispatch ---------------------------------------------------------------------------------------- */
static uint64_t api_info(uint64_t out) {
    void* p = nxgl4_guest_ptr(out, sizeof g4.api.v3.info);
    memcpy(p, &g4.api.v3.info, sizeof g4.api.v3.info);
    return 0;
}
static uint64_t api_shutdown(void) { p_glFinish(); return 0; }

typedef uint64_t (*Fn1)(uint64_t);
#define FNENT(id, field, impl) [NXGPU_FN_##id] = impl,
static const Fn1 k_fn[NXGPU_FN_COUNT4] = { NXGL4_FN_LIST(FNENT) };

uint64_t nxgl4_ext(uint32_t fn, const uint64_t* a, uint32_t n) {
    if (fn == NXGPU_FN_SHUTDOWN) return api_shutdown();
    if (fn >= NXGPU_FN_COUNT4 || (n && !a) || n < 1) return NXGPU_BAD_CALL;
    if (fn == NXGPU_FN_INFO) return api_info(a[0]);
    return k_fn[fn] ? k_fn[fn](a[0]) : NXGPU_BAD_CALL;   /* NULL: a B2b function while B2b is pending */
}

/* ---- init ----------------------------------------------------------------------------------------------------- */
static void* getproc(const char* n) { return (void*)eglGetProcAddress(n); }

const NxGpuApi4* nxgl4_create(uint32_t width, uint32_t height, char* err, size_t errlen) {
    memset(&g4, 0, sizeof g4);
#define FAIL(msg) do { if (err && errlen) snprintf(err, errlen, "%s (egl 0x%x)", msg, eglGetError()); return NULL; } while (0)
    PFNEGLGETPLATFORMDISPLAYEXTPROC getPlatformDisplay =
        (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (!getPlatformDisplay) FAIL("eglGetPlatformDisplayEXT missing");
    EGLDisplay dpy = getPlatformDisplay(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    if (dpy == EGL_NO_DISPLAY) FAIL("no surfaceless display");
    g4.dpy = dpy;
    EGLint maj, min;
    if (!eglInitialize(dpy, &maj, &min)) FAIL("eglInitialize");
    if (!eglBindAPI(EGL_OPENGL_API)) FAIL("eglBindAPI");
    const EGLint attrs[] = {EGL_CONTEXT_MAJOR_VERSION, 4, EGL_CONTEXT_MINOR_VERSION, 5,
                            EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL_NONE};
    EGLContext ctx = eglCreateContext(dpy, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, attrs);
    if (ctx == EGL_NO_CONTEXT) FAIL("eglCreateContext 4.5 core");
    g4.ctx = ctx;
    if (!eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, ctx)) FAIL("eglMakeCurrent");
#undef FAIL
    if (!gl4_load_gl(getproc, err, errlen)) return NULL;
    p_glClipControl(GL_LOWER_LEFT, GL_ZERO_TO_ONE);
    p_glProvokingVertex(GL_FIRST_VERTEX_CONVENTION);
    p_glFrontFace(GL_CCW);   /* on-screen CW = front: the image's y runs the other way (see nxgl v3) */
    p_glEnable(GL_PROGRAM_POINT_SIZE);
    p_glGenVertexArrays(1, &g4.lay[0].vao);
    p_glBindVertexArray(g4.lay[0].vao);
    if (width && height) {
        NxImageDesc4 d;
        memset(&d, 0, sizeof d);
        d.width = (uint16_t)width; d.height = (uint16_t)height; d.depth = 1; d.type = NxImgType_2D;
        d.format = NxFmt_RGBA8; d.mips = 1; d.samples = 1; d.flags = NxImg_RenderTarget;
        g4.swapImage = gl4_image_make(&d);
        if (!g4.swapImage) { if (err && errlen) snprintf(err, errlen, "swapchain image"); return NULL; }
    }
    NxGpuApi4* api = &g4.api;
    api->v3.info.magic = NXGPU_MAGIC; api->v3.info.version = NXGL4_VERSION; api->v3.info.frames_in_flight = 2;
    api->v3.info.width = width; api->v3.info.height = height; api->v3.info.backend = 2;
#define SLOT(id, field, impl) api->field = (uint64_t)(uintptr_t)impl;
    NXGL4_FN_LIST(SLOT)
#undef SLOT
    api->v3.shutdown = (uint64_t)(uintptr_t)api_shutdown;
    if (p_glGetError()) { if (err && errlen) snprintf(err, errlen, "GL error during init"); return NULL; }
    return api;
}

void nxgl4_destroy(void) {
    if (g4.dpy) {
        eglMakeCurrent(g4.dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (g4.ctx) eglDestroyContext(g4.dpy, g4.ctx);
        eglTerminate(g4.dpy);
    }
    memset(&g4, 0, sizeof g4);
}
