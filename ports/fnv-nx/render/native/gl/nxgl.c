/* fnv-nx native renderer: desktop OpenGL 4.5 reference backend (stream S6n). See nxgl.h and nx/gpu_abi.h.
 *
 * Conventions mapped onto GL (they are the backend contract in nx/state.h):
 *   - glClipControl(GL_LOWER_LEFT, GL_ZERO_TO_ONE) and every vertex shader ends with gl_Position.y = -gl_Position.y
 *     (added by the shim): clip +y lands on framebuffer row 0, which readback returns as *     the top row; depth range [0,1].
 *   - first-vertex provoking vertex (glProvokingVertex(GL_FIRST_VERTEX_CONVENTION)).
 *   - "CW on screen" is front: NXGL_FRONT_FACE below (checked by the cull scene, tests/s6n/scenes).
 *   - bindings: GL binding = stage * 16 + deko3d slot (UBOs and texture units), done by nxgl_shim_glsl().
 *   - sRGB: sRGB-capable images get a texture view in the sRGB format (image id + 1). Render targets are attached
 *     through the sRGB view; GL_FRAMEBUFFER_SRGB follows the bound state's sRGB-write bit.
 *   - caller-backed memory blocks: each has a GL buffer; the ranges named by BindUbo/BindVtx/BindIdx are copied
 *     from the caller's memory into it when the bind is replayed.
 */
#define _GNU_SOURCE
#include "nxgl.h"
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/glcorearb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifndef GL_TEXTURE_MAX_ANISOTROPY
#define GL_TEXTURE_MAX_ANISOTROPY 0x84FE
#endif
#ifndef GL_COMPRESSED_RGBA_S3TC_DXT1_EXT
#define GL_COMPRESSED_RGBA_S3TC_DXT1_EXT 0x83F1
#define GL_COMPRESSED_RGBA_S3TC_DXT3_EXT 0x83F2
#define GL_COMPRESSED_RGBA_S3TC_DXT5_EXT 0x83F3
#endif
#ifndef GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT1_EXT
#define GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT1_EXT 0x8C4D
#define GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT3_EXT 0x8C4E
#define GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT5_EXT 0x8C4F
#endif

/* "CW on screen = front". GL decides facing in its y-up window space; the image's y runs the other way (framebuffer
 * row 0 = image top), so on-screen CW is GL_CCW. Fill rule: with GL_LOWER_LEFT and y negated in the vertex shader
 * llvmpipe's tie rule is D3D9's top-left rule in image space (V6n defect y_fill_bias: GL_UPPER_LEFT got ties on
 * slanted edges wrong, a viewport bias only fixed horizontal ones). Checked by the cull and textured scenes and by
 * V6n's exact raster reference (render/verify/rastercheck.py). */
#define NXGL_FRONT_FACE GL_CCW

/* ---- GL entry points (loaded with eglGetProcAddress) ------------------------------------------------------- */
#define NXGL_FUNCS(X) \
    X(PFNGLGETERRORPROC, glGetError) X(PFNGLGETINTEGERVPROC, glGetIntegerv) X(PFNGLGETSTRINGPROC, glGetString) \
    X(PFNGLENABLEPROC, glEnable) X(PFNGLDISABLEPROC, glDisable) X(PFNGLENABLEIPROC, glEnablei) \
    X(PFNGLDISABLEIPROC, glDisablei) X(PFNGLCLIPCONTROLPROC, glClipControl) \
    X(PFNGLPROVOKINGVERTEXPROC, glProvokingVertex) X(PFNGLGENBUFFERSPROC, glGenBuffers) \
    X(PFNGLBINDBUFFERPROC, glBindBuffer) X(PFNGLBUFFERDATAPROC, glBufferData) \
    X(PFNGLBUFFERSUBDATAPROC, glBufferSubData) X(PFNGLBINDBUFFERRANGEPROC, glBindBufferRange) \
    X(PFNGLGENVERTEXARRAYSPROC, glGenVertexArrays) X(PFNGLBINDVERTEXARRAYPROC, glBindVertexArray) \
    X(PFNGLVERTEXATTRIBFORMATPROC, glVertexAttribFormat) X(PFNGLVERTEXATTRIBBINDINGPROC, glVertexAttribBinding) \
    X(PFNGLENABLEVERTEXATTRIBARRAYPROC, glEnableVertexAttribArray) X(PFNGLBINDVERTEXBUFFERPROC, glBindVertexBuffer) \
    X(PFNGLGENTEXTURESPROC, glGenTextures) X(PFNGLBINDTEXTUREPROC, glBindTexture) \
    X(PFNGLACTIVETEXTUREPROC, glActiveTexture) X(PFNGLTEXSTORAGE2DPROC, glTexStorage2D) \
    X(PFNGLTEXSUBIMAGE2DPROC, glTexSubImage2D) X(PFNGLCOMPRESSEDTEXSUBIMAGE2DPROC, glCompressedTexSubImage2D) \
    X(PFNGLTEXPARAMETERIPROC, glTexParameteri) X(PFNGLTEXPARAMETERIVPROC, glTexParameteriv) \
    X(PFNGLTEXTUREVIEWPROC, glTextureView) X(PFNGLGENSAMPLERSPROC, glGenSamplers) \
    X(PFNGLSAMPLERPARAMETERIPROC, glSamplerParameteri) X(PFNGLSAMPLERPARAMETERFPROC, glSamplerParameterf) \
    X(PFNGLSAMPLERPARAMETERFVPROC, glSamplerParameterfv) X(PFNGLBINDSAMPLERPROC, glBindSampler) \
    X(PFNGLCREATESHADERPROC, glCreateShader) X(PFNGLSHADERSOURCEPROC, glShaderSource) \
    X(PFNGLCOMPILESHADERPROC, glCompileShader) X(PFNGLGETSHADERIVPROC, glGetShaderiv) \
    X(PFNGLGETSHADERINFOLOGPROC, glGetShaderInfoLog) X(PFNGLCREATEPROGRAMPROC, glCreateProgram) \
    X(PFNGLATTACHSHADERPROC, glAttachShader) X(PFNGLLINKPROGRAMPROC, glLinkProgram) \
    X(PFNGLGETPROGRAMIVPROC, glGetProgramiv) X(PFNGLGETPROGRAMINFOLOGPROC, glGetProgramInfoLog) \
    X(PFNGLUSEPROGRAMPROC, glUseProgram) X(PFNGLGENFRAMEBUFFERSPROC, glGenFramebuffers) \
    X(PFNGLBINDFRAMEBUFFERPROC, glBindFramebuffer) X(PFNGLFRAMEBUFFERTEXTURE2DPROC, glFramebufferTexture2D) \
    X(PFNGLCHECKFRAMEBUFFERSTATUSPROC, glCheckFramebufferStatus) X(PFNGLDRAWBUFFERSPROC, glDrawBuffers) \
    X(PFNGLVIEWPORTINDEXEDFPROC, glViewportIndexedf) X(PFNGLDEPTHRANGEFPROC, glDepthRangef) \
    X(PFNGLSCISSORPROC, glScissor) X(PFNGLCLEARBUFFERFVPROC, glClearBufferfv) \
    X(PFNGLCLEARBUFFERFIPROC, glClearBufferfi) X(PFNGLCLEARBUFFERIVPROC, glClearBufferiv) \
    X(PFNGLCOLORMASKIPROC, glColorMaski) X(PFNGLBLENDFUNCSEPARATEPROC, glBlendFuncSeparate) \
    X(PFNGLBLENDEQUATIONSEPARATEPROC, glBlendEquationSeparate) X(PFNGLBLENDCOLORPROC, glBlendColor) \
    X(PFNGLDEPTHFUNCPROC, glDepthFunc) X(PFNGLDEPTHMASKPROC, glDepthMask) \
    X(PFNGLSTENCILFUNCSEPARATEPROC, glStencilFuncSeparate) X(PFNGLSTENCILOPSEPARATEPROC, glStencilOpSeparate) \
    X(PFNGLSTENCILMASKSEPARATEPROC, glStencilMaskSeparate) X(PFNGLCULLFACEPROC, glCullFace) \
    X(PFNGLFRONTFACEPROC, glFrontFace) X(PFNGLPOLYGONMODEPROC, glPolygonMode) \
    X(PFNGLPOLYGONOFFSETPROC, glPolygonOffset) \
    X(PFNGLDRAWARRAYSINSTANCEDBASEINSTANCEPROC, glDrawArraysInstancedBaseInstance) \
    X(PFNGLDRAWELEMENTSINSTANCEDBASEVERTEXBASEINSTANCEPROC, glDrawElementsInstancedBaseVertexBaseInstance) \
    X(PFNGLREADPIXELSPROC, glReadPixels) X(PFNGLREADBUFFERPROC, glReadBuffer) X(PFNGLFINISHPROC, glFinish) \
    X(PFNGLPIXELSTOREIPROC, glPixelStorei) X(PFNGLCOLORMASKPROC, glColorMask) X(PFNGLSTENCILMASKPROC, glStencilMask)

#define X(type, name) static type p_##name;
NXGL_FUNCS(X)
#undef X
#define X(type, name) {#name, (void (**)(void))&p_##name},
static const struct { const char* name; void (**fn)(void); } g_funcs[] = { NXGL_FUNCS(X) };
#undef X

/* ---- tables ---------------------------------------------------------------------------------------------- */
#define MAX_MEM      256
#define MAX_SHADERS  1024
#define MAX_STATES   4096
#define MAX_LAYOUTS  256
#define MAX_PROGRAMS 1024
#define MAX_FBOS     64

typedef struct { uint8_t* cpu; uint32_t size; GLuint buf; } Mem;
typedef struct {
    GLuint tex;            /* texture name (the sRGB view for a view entry) */
    GLenum target;
    uint8_t format, isView, isDepth, hasStencil, used;
    uint16_t w, h;
    uint32_t base;         /* for a view: the image it views */
} Image;
typedef struct { GLuint sh; uint32_t stage; } Shader;
typedef struct { uint16_t vs, ps; GLuint prog; } Program;
typedef struct { NxRenderState rs; uint16_t layout, prog; uint8_t defined; } StateRec;
typedef struct { NxVtxLayout l; GLuint vao; uint8_t defined; } Layout;
typedef struct { uint32_t color, depth; GLuint fbo; } Fbo;

static struct {
    EGLDisplay dpy;
    EGLContext ctx;
    NxGpuApi api;
    Mem mem[MAX_MEM + 1]; uint32_t nMem;
    Image img[NX_MAX_IMAGES + 1]; uint32_t nImg;
    GLuint smp[NX_MAX_SAMPLERS]; uint32_t nSmp;
    Shader sh[MAX_SHADERS + 1]; uint32_t nSh;
    Program prog[MAX_PROGRAMS + 1]; uint32_t nProg;
    StateRec st[MAX_STATES + 1];
    Layout lay[MAX_LAYOUTS + 1];
    Fbo fbo[MAX_FBOS]; uint32_t nFbo;
    GLuint readFbo;
    uint32_t swapImage;    /* image id used for "colour 0" */
    uint32_t passColor;    /* colour image of the current pass (resolved, never 0) */
    uint32_t frameDraws;   /* draws replayed in the current submit */
    int skipDraws;         /* the draw hook asked to stop drawing */
    char err[2048];
    /* replay state */
    uint32_t boundState, passW, passH;
    int haveApplied;
    NxRenderState applied;
    uint32_t vtxBlock, vtxOffset, idxType;
    int vtxDirty;
    uint32_t stencilRef;
    GLint scissor[4];      /* the dynamic scissor rectangle (restored after NxOp_Clear) */
} g;

const char* nxgl_last_error(void) { return g.err; }
static void seterr(const char* fmt, const char* a, const char* b) {
    snprintf(g.err, sizeof g.err, fmt, a ? a : "", b ? b : "");
}

/* ---- the GLSL shim -------------------------------------------------------------------------------------------- */
size_t nxgl_shim_glsl(const char* src, size_t n, uint32_t stage, char* out, size_t cap) {
    size_t o = 0, i = 0;
#define PUT(c) do { if (o + 1 >= cap) return 0; out[o++] = (c); } while (0)
    while (i < n) {
        if ((i == 0 || src[i - 1] == '\n') && n - i >= 8 && !memcmp(src + i, "#version", 8)) {
            const char* v = "#version 450 core";
            for (const char* p = v; *p; p++) PUT(*p);
            while (i < n && src[i] != '\n') i++;
            continue;
        }
        if (n - i >= 7 && !memcmp(src + i, "binding", 7) && (i == 0 || !(src[i - 1] == '_' ||
            (src[i - 1] >= 'a' && src[i - 1] <= 'z') || (src[i - 1] >= 'A' && src[i - 1] <= 'Z') ||
            (src[i - 1] >= '0' && src[i - 1] <= '9')))) {
            size_t j = i + 7;
            while (j < n && (src[j] == ' ' || src[j] == '\t')) j++;
            if (j < n && src[j] == '=') {
                j++;
                while (j < n && (src[j] == ' ' || src[j] == '\t')) j++;
                if (j < n && src[j] >= '0' && src[j] <= '9') {
                    unsigned v = 0;
                    while (j < n && src[j] >= '0' && src[j] <= '9') v = v * 10 + (unsigned)(src[j++] - '0');
                    char num[32];
                    int k = snprintf(num, sizeof num, "binding = %u", v + stage * 16);
                    for (int q = 0; q < k; q++) PUT(num[q]);
                    i = j;
                    continue;
                }
            }
        }
        /* "uniform Name {": a block of that name in two stages is one GL block and must have one binding; the
         * stages bind their own buffers (binding = stage * 16 + n), so each stage gets its own block name */
        if (n - i >= 8 && !memcmp(src + i, "uniform", 7) && (src[i + 7] == ' ' || src[i + 7] == '\t') &&
            (i == 0 || src[i - 1] == ' ' || src[i - 1] == '\t' || src[i - 1] == ')' || src[i - 1] == '\n')) {
            size_t j = i + 7;
            while (j < n && (src[j] == ' ' || src[j] == '\t')) j++;
            size_t id0 = j;
            while (j < n && (src[j] == '_' || (src[j] >= 'a' && src[j] <= 'z') || (src[j] >= 'A' && src[j] <= 'Z') ||
                             (src[j] >= '0' && src[j] <= '9'))) j++;
            size_t id1 = j;
            while (j < n && (src[j] == ' ' || src[j] == '\t' || src[j] == '\n')) j++;
            if (id1 > id0 && j < n && src[j] == '{') {
                for (size_t q = i; q < id1; q++) PUT(src[q]);
                const char* sfx = stage ? "_nxfs" : "_nxvs";
                for (const char* q = sfx; *q; q++) PUT(*q);
                i = id1;
                continue;
            }
        }
        /* vertex stage: "void main(" -> "void nx_main_(", wrapper appended below */
        if (stage == 0 && n - i >= 9 && !memcmp(src + i, "void main", 9) && (i == 0 || src[i - 1] == '\n' ||
            src[i - 1] == ' ') && (i + 9 >= n || src[i + 9] == '(' || src[i + 9] == ' ')) {
            const char* r = "void nx_main_";
            for (const char* q = r; *q; q++) PUT(*q);
            i += 9;
            continue;
        }
        PUT(src[i]);
        i++;
    }
    if (stage == 0) {
        const char* w = "\nvoid main() { nx_main_(); gl_Position.y = -gl_Position.y; }\n";
        for (const char* q = w; *q; q++) PUT(*q);
    }
    out[o] = 0;
    return o;
#undef PUT
}

/* ---- formats ------------------------------------------------------------------------------------------------ */
typedef struct { GLenum internal, format, type, srgb; uint8_t compressed, bpp; } GlFmt;
static GlFmt glfmt(uint32_t f) {
    GlFmt r = {0, 0, 0, 0, 0, 0};
#define F(fmt, i, fo, ty, s, c, b) case fmt: { GlFmt t = {i, fo, ty, s, c, b}; return t; }
    switch (f) {
    F(NxFmt_RGBA8, GL_RGBA8, GL_RGBA, GL_UNSIGNED_BYTE, GL_SRGB8_ALPHA8, 0, 4)
    F(NxFmt_BGRA8, GL_RGBA8, GL_BGRA, GL_UNSIGNED_BYTE, GL_SRGB8_ALPHA8, 0, 4)
    F(NxFmt_BGRX8, GL_RGB8, GL_BGRA, GL_UNSIGNED_BYTE, GL_SRGB8, 0, 4)
    F(NxFmt_RGBX8, GL_RGB8, GL_RGBA, GL_UNSIGNED_BYTE, GL_SRGB8, 0, 4)
    F(NxFmt_R5G6B5, GL_RGB565, GL_RGB, GL_UNSIGNED_SHORT_5_6_5, 0, 0, 2)
    F(NxFmt_A1R5G5B5, GL_RGB5_A1, GL_BGRA, GL_UNSIGNED_SHORT_1_5_5_5_REV, 0, 0, 2)
    F(NxFmt_X1R5G5B5, GL_RGB5_A1, GL_BGRA, GL_UNSIGNED_SHORT_1_5_5_5_REV, 0, 0, 2)
    F(NxFmt_A4R4G4B4, GL_RGBA4, GL_BGRA, GL_UNSIGNED_SHORT_4_4_4_4_REV, 0, 0, 2)
    F(NxFmt_RGB10A2, GL_RGB10_A2, GL_RGBA, GL_UNSIGNED_INT_2_10_10_10_REV, 0, 0, 4)
    F(NxFmt_R8, GL_R8, GL_RED, GL_UNSIGNED_BYTE, 0, 0, 1)
    F(NxFmt_RG8, GL_RG8, GL_RG, GL_UNSIGNED_BYTE, 0, 0, 2)
    F(NxFmt_R16, GL_R16, GL_RED, GL_UNSIGNED_SHORT, 0, 0, 2)
    F(NxFmt_RG16, GL_RG16, GL_RG, GL_UNSIGNED_SHORT, 0, 0, 4)
    F(NxFmt_RGBA16, GL_RGBA16, GL_RGBA, GL_UNSIGNED_SHORT, 0, 0, 8)
    F(NxFmt_RG8S, GL_RG8_SNORM, GL_RG, GL_BYTE, 0, 0, 2)
    F(NxFmt_RGBA8S, GL_RGBA8_SNORM, GL_RGBA, GL_BYTE, 0, 0, 4)
    F(NxFmt_RG16S, GL_RG16_SNORM, GL_RG, GL_SHORT, 0, 0, 4)
    F(NxFmt_RGBA16S, GL_RGBA16_SNORM, GL_RGBA, GL_SHORT, 0, 0, 8)
    F(NxFmt_R16F, GL_R16F, GL_RED, GL_HALF_FLOAT, 0, 0, 2)
    F(NxFmt_RG16F, GL_RG16F, GL_RG, GL_HALF_FLOAT, 0, 0, 4)
    F(NxFmt_RGBA16F, GL_RGBA16F, GL_RGBA, GL_HALF_FLOAT, 0, 0, 8)
    F(NxFmt_R32F, GL_R32F, GL_RED, GL_FLOAT, 0, 0, 4)
    F(NxFmt_RG32F, GL_RG32F, GL_RG, GL_FLOAT, 0, 0, 8)
    F(NxFmt_RGBA32F, GL_RGBA32F, GL_RGBA, GL_FLOAT, 0, 0, 16)
    F(NxFmt_BC1, GL_COMPRESSED_RGBA_S3TC_DXT1_EXT, 0, 0, GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT1_EXT, 1, 8)
    F(NxFmt_BC2, GL_COMPRESSED_RGBA_S3TC_DXT3_EXT, 0, 0, GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT3_EXT, 1, 16)
    F(NxFmt_BC3, GL_COMPRESSED_RGBA_S3TC_DXT5_EXT, 0, 0, GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT5_EXT, 1, 16)
    F(NxFmt_BC4, GL_COMPRESSED_RED_RGTC1, 0, 0, 0, 1, 8)
    F(NxFmt_BC5, GL_COMPRESSED_RG_RGTC2, 0, 0, 0, 1, 16)
    F(NxFmt_Z16, GL_DEPTH_COMPONENT16, GL_DEPTH_COMPONENT, GL_UNSIGNED_SHORT, 0, 0, 2)
    F(NxFmt_Z24X8, GL_DEPTH_COMPONENT24, GL_DEPTH_COMPONENT, GL_UNSIGNED_INT, 0, 0, 4)
    F(NxFmt_Z24S8, GL_DEPTH24_STENCIL8, GL_DEPTH_STENCIL, GL_UNSIGNED_INT_24_8, 0, 0, 4)
    F(NxFmt_Z32F, GL_DEPTH_COMPONENT32F, GL_DEPTH_COMPONENT, GL_FLOAT, 0, 0, 4)
    F(NxFmt_Z32FS8, GL_DEPTH32F_STENCIL8, GL_DEPTH_STENCIL, GL_FLOAT_32_UNSIGNED_INT_24_8_REV, 0, 0, 8)
    default: return r;
    }
#undef F
}

static GLint glswz(uint32_t s) {
    static const GLint t[6] = {GL_RED, GL_GREEN, GL_BLUE, GL_ALPHA, GL_ZERO, GL_ONE};
    return s < 6 ? t[s] : GL_ZERO;
}

/* ---- API: resources ------------------------------------------------------------------------------------- */
static uint64_t api_mem_create(uint64_t a) {
    const NxMemBlockDesc* d = (const NxMemBlockDesc*)(uintptr_t)a;
    if (!d || g.nMem >= MAX_MEM || !d->size || !d->storage) return 0;
    uint32_t id = ++g.nMem;
    Mem* m = &g.mem[id];
    m->cpu = (uint8_t*)(uintptr_t)d->storage;
    m->size = d->size;
    p_glGenBuffers(1, &m->buf);
    p_glBindBuffer(GL_COPY_WRITE_BUFFER, m->buf);
    p_glBufferData(GL_COPY_WRITE_BUFFER, d->size, NULL, GL_DYNAMIC_DRAW);
    return id;
}
static uint64_t api_mem_gpu_addr(uint64_t id) { (void)id; return 0; }

static void upload_range(uint32_t block, uint32_t off, uint32_t size) {
    if (!block || block > g.nMem) return;
    Mem* m = &g.mem[block];
    if (off >= m->size) return;
    if (size > m->size - off) size = m->size - off;
    p_glBindBuffer(GL_COPY_WRITE_BUFFER, m->buf);
    p_glBufferSubData(GL_COPY_WRITE_BUFFER, off, size, m->cpu + off);
}

static uint64_t api_image_create(uint64_t a) {
    const NxImageDesc* d = (const NxImageDesc*)(uintptr_t)a;
    if (!d || !d->width || !d->height) return 0;
    GlFmt f = glfmt(d->format);
    if (!f.internal) return 0;
    int srgb = nx_format_has_srgb(d->format) && f.srgb;
    if (g.nImg + 1 + srgb > NX_MAX_IMAGES) return 0;
    uint32_t id = ++g.nImg;
    Image* im = &g.img[id];
    memset(im, 0, sizeof *im);
    int cube = (d->flags & NxImg_Cube) != 0;
    uint32_t mips = d->mips ? d->mips : 1;
    im->target = cube ? GL_TEXTURE_CUBE_MAP : GL_TEXTURE_2D;
    im->format = d->format; im->w = d->width; im->h = d->height; im->used = 1;
    im->isDepth = d->format == NxFmt_Z16 || d->format == NxFmt_Z24X8 || d->format == NxFmt_Z24S8 ||
                  d->format == NxFmt_Z32F || d->format == NxFmt_Z32FS8;
    im->hasStencil = d->format == NxFmt_Z24S8 || d->format == NxFmt_Z32FS8;
    p_glGenTextures(1, &im->tex);
    p_glActiveTexture(GL_TEXTURE0);
    p_glBindTexture(im->target, im->tex);
    p_glTexStorage2D(im->target, (GLsizei)mips, f.internal, d->width, d->height);
    p_glTexParameteri(im->target, GL_TEXTURE_MAX_LEVEL, (GLint)mips - 1);
    uint16_t sw = d->swizzle ? d->swizzle : NXIMG_SWIZZLE_IDENTITY;
    GLint swz[4] = {glswz(sw & 15), glswz(sw >> 4 & 15), glswz(sw >> 8 & 15), glswz(sw >> 12 & 15)};
    if (!im->isDepth) p_glTexParameteriv(im->target, GL_TEXTURE_SWIZZLE_RGBA, swz);
    if (d->data) {
        const uint8_t* src = (const uint8_t*)(uintptr_t)d->data;
        const uint8_t* end = src + d->data_size;
        p_glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        p_glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
        for (int face = 0; face < (cube ? 6 : 1); face++) {
            GLenum tgt = cube ? (GLenum)(GL_TEXTURE_CUBE_MAP_POSITIVE_X + face) : GL_TEXTURE_2D;
            uint32_t w = d->width, h = d->height;
            for (uint32_t m = 0; m < mips; m++) {
                uint32_t bytes = f.compressed ? ((w + 3) / 4) * ((h + 3) / 4) * f.bpp : w * h * f.bpp;
                if (src + bytes > end) break;
                if (f.compressed) p_glCompressedTexSubImage2D(tgt, (GLint)m, 0, 0, w, h, f.internal, bytes, src);
                else p_glTexSubImage2D(tgt, (GLint)m, 0, 0, w, h, f.format, f.type, src);
                src += bytes;
                w = w > 1 ? w / 2 : 1; h = h > 1 ? h / 2 : 1;
            }
        }
    }
    if (srgb) {
        uint32_t vid = ++g.nImg;
        Image* v = &g.img[vid];
        *v = *im;
        v->isView = 1; v->base = id;
        p_glGenTextures(1, &v->tex);
        p_glTextureView(v->tex, im->target, im->tex, f.srgb, 0, mips, 0, cube ? 6 : 1);
        p_glBindTexture(v->target, v->tex);
        p_glTexParameteriv(v->target, GL_TEXTURE_SWIZZLE_RGBA, swz);
    }
    GLenum e = p_glGetError();
    if (e) { char b[32]; snprintf(b, sizeof b, "0x%x", e); seterr("image_create: GL error %s%s", b, ""); }
    return id;
}

static GLint glwrap(uint32_t w) {
    switch (w) {
    case NxWrap_Repeat: return GL_REPEAT;
    case NxWrap_Mirror: return GL_MIRRORED_REPEAT;
    case NxWrap_Border: return GL_CLAMP_TO_BORDER;
    case NxWrap_MirrorOnce: return GL_MIRROR_CLAMP_TO_EDGE;
    default: return GL_CLAMP_TO_EDGE;
    }
}

static uint64_t api_sampler_create(uint64_t a) {
    const NxSamplerState* s = (const NxSamplerState*)(uintptr_t)a;
    if (!s || g.nSmp + 1 >= NX_MAX_SAMPLERS) return 0;
    uint32_t id = ++g.nSmp;
    GLuint o;
    p_glGenSamplers(1, &o);
    g.smp[id] = o;
    uint32_t mip = s->filter >> NXSMP_MIP_SHIFT & 3;
    int minL = (s->filter & NXSMP_MIN_LINEAR) != 0, magL = (s->filter & NXSMP_MAG_LINEAR) != 0;
    GLint minf = mip == NxMip_None ? (minL ? GL_LINEAR : GL_NEAREST)
               : mip == NxMip_Nearest ? (minL ? GL_LINEAR_MIPMAP_NEAREST : GL_NEAREST_MIPMAP_NEAREST)
               : (minL ? GL_LINEAR_MIPMAP_LINEAR : GL_NEAREST_MIPMAP_LINEAR);
    p_glSamplerParameteri(o, GL_TEXTURE_MIN_FILTER, minf);
    p_glSamplerParameteri(o, GL_TEXTURE_MAG_FILTER, magL ? GL_LINEAR : GL_NEAREST);
    p_glSamplerParameteri(o, GL_TEXTURE_WRAP_S, glwrap(s->wrap & 7));
    p_glSamplerParameteri(o, GL_TEXTURE_WRAP_T, glwrap(s->wrap >> 3 & 7));
    p_glSamplerParameteri(o, GL_TEXTURE_WRAP_R, glwrap(s->wrap >> 6 & 7));
    p_glSamplerParameterf(o, GL_TEXTURE_LOD_BIAS, s->lodBias);
    p_glSamplerParameterf(o, GL_TEXTURE_MIN_LOD, (float)s->minLod);
    p_glSamplerParameterf(o, GL_TEXTURE_MAX_LOD, (float)s->maxLod);
    if (s->maxAniso > 1) p_glSamplerParameterf(o, GL_TEXTURE_MAX_ANISOTROPY, (float)s->maxAniso);
    float bc[4] = {(s->border & 255) / 255.0f, (s->border >> 8 & 255) / 255.0f, (s->border >> 16 & 255) / 255.0f,
                   (s->border >> 24) / 255.0f};
    p_glSamplerParameterfv(o, GL_TEXTURE_BORDER_COLOR, bc);
    if (s->filter & NXSMP_SHADOW) {
        p_glSamplerParameteri(o, GL_TEXTURE_COMPARE_MODE, GL_COMPARE_REF_TO_TEXTURE);
        p_glSamplerParameteri(o, GL_TEXTURE_COMPARE_FUNC, GL_LEQUAL);
    }
    p_glGetError();   /* anisotropy may be unsupported: ignore */
    return id;
}

static uint64_t api_shader_create(uint64_t a) {
    const NxShaderDesc* d = (const NxShaderDesc*)(uintptr_t)a;
    if (!d || !d->glsl || !d->glsl_size || g.nSh >= MAX_SHADERS || d->stage > 1) return 0;
    size_t cap = d->glsl_size * 2 + 256;
    char* src = (char*)malloc(cap);
    if (!src) return 0;
    if (!nxgl_shim_glsl((const char*)(uintptr_t)d->glsl, d->glsl_size, d->stage, src, cap)) { free(src); return 0; }
    GLuint s = p_glCreateShader(d->stage == NxStage_Vertex ? GL_VERTEX_SHADER : GL_FRAGMENT_SHADER);
    const char* srcs[1] = {src};
    p_glShaderSource(s, 1, srcs, NULL);
    p_glCompileShader(s);
    free(src);
    GLint ok = 0;
    p_glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[1500];
        p_glGetShaderInfoLog(s, sizeof log, NULL, log);
        seterr("shader compile failed: %s%s", log, "");
        fprintf(stderr, "nxgl: %s\n", g.err);
        return 0;
    }
    uint32_t id = ++g.nSh;
    g.sh[id].sh = s; g.sh[id].stage = d->stage;
    return id;
}

static uint16_t program_for(uint16_t vs, uint16_t ps) {
    for (uint32_t i = 1; i <= g.nProg; i++)
        if (g.prog[i].vs == vs && g.prog[i].ps == ps) return (uint16_t)i;
    if (g.nProg >= MAX_PROGRAMS || !vs || vs > g.nSh || !ps || ps > g.nSh) return 0;
    GLuint p = p_glCreateProgram();
    p_glAttachShader(p, g.sh[vs].sh);
    p_glAttachShader(p, g.sh[ps].sh);
    p_glLinkProgram(p);
    GLint ok = 0;
    p_glGetProgramiv(p, GL_LINK_STATUS, &ok);
    if (!ok) {
        char log[1500];
        p_glGetProgramInfoLog(p, sizeof log, NULL, log);
        seterr("program link failed: %s%s", log, "");
        fprintf(stderr, "nxgl: %s\n", g.err);
        return 0;
    }
    uint32_t id = ++g.nProg;
    g.prog[id].vs = vs; g.prog[id].ps = ps; g.prog[id].prog = p;
    return (uint16_t)id;
}

/* ---- state application --------------------------------------------------------------------------------- */
static GLenum glcmp(uint32_t c) {
    static const GLenum t[8] = {GL_NEVER, GL_LESS, GL_EQUAL, GL_LEQUAL, GL_GREATER, GL_NOTEQUAL, GL_GEQUAL, GL_ALWAYS};
    return t[c & 7];
}
static GLenum glsop(uint32_t o) {
    static const GLenum t[8] = {GL_KEEP, GL_ZERO, GL_REPLACE, GL_INCR, GL_DECR, GL_INVERT, GL_INCR_WRAP, GL_DECR_WRAP};
    return t[o & 7];
}
static GLenum glbf(uint32_t f) {
    static const GLenum t[NxBf_Count] = {GL_ZERO, GL_ONE, GL_SRC_COLOR, GL_ONE_MINUS_SRC_COLOR, GL_SRC_ALPHA,
        GL_ONE_MINUS_SRC_ALPHA, GL_DST_ALPHA, GL_ONE_MINUS_DST_ALPHA, GL_DST_COLOR, GL_ONE_MINUS_DST_COLOR,
        GL_SRC_ALPHA_SATURATE, GL_CONSTANT_COLOR, GL_ONE_MINUS_CONSTANT_COLOR, GL_SRC1_COLOR, GL_ONE_MINUS_SRC1_COLOR};
    return f < NxBf_Count ? t[f] : GL_ZERO;
}
static GLenum glbop(uint32_t o) {
    static const GLenum t[5] = {GL_FUNC_ADD, GL_FUNC_SUBTRACT, GL_FUNC_REVERSE_SUBTRACT, GL_MIN, GL_MAX};
    return o < 5 ? t[o] : GL_FUNC_ADD;
}

static void apply_stencil_funcs(uint64_t w1, uint32_t ref) {
    uint32_t rm = NXRS_GET(w1, NXRS_S_READMASK, 8);
    p_glStencilFuncSeparate(GL_FRONT, glcmp(NXRS_GET(w1, NXRS_S_FUNC, 3)), (GLint)ref, rm);
    p_glStencilFuncSeparate(GL_BACK, glcmp(NXRS_GET(w1, NXRS_S_BFUNC, 3)), (GLint)ref, rm);
}

static void apply_state(const NxRenderState* rs, int force) {
    uint64_t w0 = rs->w[0], w1 = rs->w[1], w2 = rs->w[2];
    if (force || w0 != g.applied.w[0]) {
        for (GLuint rt = 0; rt < 4; rt++) {
            uint32_t m = NXRS_GET(w0, NXRS_B_MASK(rt), 4);
            p_glColorMaski(rt, m & 1, (m >> 1) & 1, (m >> 2) & 1, (m >> 3) & 1);
            if (w0 & 1) p_glEnablei(GL_BLEND, rt); else p_glDisablei(GL_BLEND, rt);
        }
        if (w0 & 1) {
            p_glBlendFuncSeparate(glbf(NXRS_GET(w0, NXRS_B_SRC, 5)), glbf(NXRS_GET(w0, NXRS_B_DST, 5)),
                                  glbf(NXRS_GET(w0, NXRS_B_SRCA, 5)), glbf(NXRS_GET(w0, NXRS_B_DSTA, 5)));
            p_glBlendEquationSeparate(glbop(NXRS_GET(w0, NXRS_B_OP, 3)), glbop(NXRS_GET(w0, NXRS_B_OPA, 3)));
        }
    }
    if (force || w1 != g.applied.w[1]) {
        if (w1 & 1) {
            p_glEnable(GL_DEPTH_TEST);
            p_glDepthFunc(glcmp(NXRS_GET(w1, NXRS_D_FUNC, 3)));
        } else {
            p_glDisable(GL_DEPTH_TEST);
        }
        p_glDepthMask(NXRS_GET(w1, NXRS_D_WRITE, 1) ? GL_TRUE : GL_FALSE);
        if (NXRS_GET(w1, NXRS_S_ENABLE, 1)) {
            p_glEnable(GL_STENCIL_TEST);
            p_glStencilOpSeparate(GL_FRONT, glsop(NXRS_GET(w1, NXRS_S_FAIL, 3)), glsop(NXRS_GET(w1, NXRS_S_ZFAIL, 3)),
                                  glsop(NXRS_GET(w1, NXRS_S_PASS, 3)));
            p_glStencilOpSeparate(GL_BACK, glsop(NXRS_GET(w1, NXRS_S_BFAIL, 3)), glsop(NXRS_GET(w1, NXRS_S_BZFAIL, 3)),
                                  glsop(NXRS_GET(w1, NXRS_S_BPASS, 3)));
            p_glStencilMaskSeparate(GL_FRONT_AND_BACK, NXRS_GET(w1, NXRS_S_WRITEMASK, 8));
        } else {
            p_glDisable(GL_STENCIL_TEST);
        }
    }
    /* ref and masks are re-applied at every BindState (dynamic) */
    g.stencilRef = NXRS_GET(w1, NXRS_S_REF, 8);
    if (NXRS_GET(w1, NXRS_S_ENABLE, 1)) apply_stencil_funcs(w1, g.stencilRef);
    if (force || w2 != g.applied.w[2]) {
        uint32_t cull = NXRS_GET(w2, NXRS_R_CULL, 2);
        if (cull == NxCull_None) p_glDisable(GL_CULL_FACE);
        else { p_glEnable(GL_CULL_FACE); p_glCullFace(cull == NxCull_CW ? GL_FRONT : GL_BACK); }
        uint32_t fill = NXRS_GET(w2, NXRS_R_FILL, 2);
        p_glPolygonMode(GL_FRONT_AND_BACK, fill == NxFill_Wireframe ? GL_LINE : fill == NxFill_Point ? GL_POINT : GL_FILL);
        if (NXRS_GET(w2, NXRS_R_BIAS, 1)) {
            p_glEnable(GL_POLYGON_OFFSET_FILL); p_glEnable(GL_POLYGON_OFFSET_LINE); p_glEnable(GL_POLYGON_OFFSET_POINT);
        } else {
            p_glDisable(GL_POLYGON_OFFSET_FILL); p_glDisable(GL_POLYGON_OFFSET_LINE); p_glDisable(GL_POLYGON_OFFSET_POINT);
        }
        if (NXRS_GET(w2, NXRS_R_SCISSOR, 1)) p_glEnable(GL_SCISSOR_TEST); else p_glDisable(GL_SCISSOR_TEST);
        if (NXRS_GET(w2, NXRS_R_SRGB, 1)) p_glEnable(GL_FRAMEBUFFER_SRGB); else p_glDisable(GL_FRAMEBUFFER_SRGB);
        uint32_t clip = NXRS_GET(w2, NXRS_R_CLIP, 8);
        for (int i = 0; i < 8; i++) {
            if (clip >> i & 1) p_glEnable(GL_CLIP_DISTANCE0 + i); else p_glDisable(GL_CLIP_DISTANCE0 + i);
        }
    }
    g.applied = *rs;
    g.haveApplied = 1;
}

/* ---- render targets ---------------------------------------------------------------------------------------- */
static uint32_t rt_view(uint32_t id) {   /* the sRGB view entry of an image, if it has one */
    if (id && id < g.nImg && g.img[id + 1].isView && g.img[id + 1].base == id) return id + 1;
    return id;
}

static GLuint fbo_for(uint32_t color, uint32_t depth) {
    for (uint32_t i = 0; i < g.nFbo; i++)
        if (g.fbo[i].color == color && g.fbo[i].depth == depth) return g.fbo[i].fbo;
    GLuint f;
    p_glGenFramebuffers(1, &f);
    p_glBindFramebuffer(GL_FRAMEBUFFER, f);
    if (color && color <= g.nImg) {
        p_glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, g.img[rt_view(color)].tex, 0);
        GLenum db = GL_COLOR_ATTACHMENT0;
        p_glDrawBuffers(1, &db);
    } else {
        GLenum db = GL_NONE;
        p_glDrawBuffers(1, &db);
    }
    if (depth && depth <= g.nImg)
        p_glFramebufferTexture2D(GL_FRAMEBUFFER, g.img[depth].hasStencil ? GL_DEPTH_STENCIL_ATTACHMENT : GL_DEPTH_ATTACHMENT,
                                 GL_TEXTURE_2D, g.img[depth].tex, 0);
    GLenum st = p_glCheckFramebufferStatus(GL_FRAMEBUFFER);
    if (st != GL_FRAMEBUFFER_COMPLETE) {
        char b[32]; snprintf(b, sizeof b, "0x%x", st);
        seterr("framebuffer incomplete: %s%s", b, "");
        fprintf(stderr, "nxgl: %s\n", g.err);
    }
    if (g.nFbo < MAX_FBOS) { g.fbo[g.nFbo].color = color; g.fbo[g.nFbo].depth = depth; g.fbo[g.nFbo].fbo = f; g.nFbo++; }
    return f;
}

static float asf(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

static void begin_pass(const uint32_t* a) {
    uint32_t color = a[0] ? a[0] : g.swapImage, depth = a[1];
    g.passColor = color;
    p_glBindFramebuffer(GL_FRAMEBUFFER, fbo_for(color, depth));
    g.passW = a[6]; g.passH = a[7];
    /* clears: full target, all channels, no sRGB encoding of the clear colour */
    p_glDisable(GL_SCISSOR_TEST);
    p_glDisable(GL_FRAMEBUFFER_SRGB);
    if (a[2] & 1) {
        p_glColorMaski(0, 1, 1, 1, 1);
        float c[4] = {(a[3] & 255) / 255.0f, (a[3] >> 8 & 255) / 255.0f, (a[3] >> 16 & 255) / 255.0f, (a[3] >> 24) / 255.0f};
        p_glClearBufferfv(GL_COLOR, 0, c);
    }
    if ((a[2] & 6) && depth) {
        p_glDepthMask(GL_TRUE);
        p_glStencilMaskSeparate(GL_FRONT_AND_BACK, 0xFF);
        float z = asf(a[4]);
        GLint s = (GLint)(a[5] & 0xFF);
        if ((a[2] & 6) == 6 && g.img[depth].hasStencil) p_glClearBufferfi(GL_DEPTH_STENCIL, 0, z, s);
        else if (a[2] & 2) p_glClearBufferfv(GL_DEPTH, 0, &z);
        else if (g.img[depth].hasStencil) p_glClearBufferiv(GL_STENCIL, 0, &s);
    }
    g.haveApplied = 0;
    g.boundState = 0;
    p_glViewportIndexedf(0, 0.5f, 0.5f, (float)g.passW, (float)g.passH);
    p_glDepthRangef(0.0f, 1.0f);
    p_glScissor(0, 0, (GLsizei)g.passW, (GLsizei)g.passH);
    g.scissor[0] = 0; g.scissor[1] = 0; g.scissor[2] = (GLint)g.passW; g.scissor[3] = (GLint)g.passH;
    p_glBlendColor(1, 1, 1, 1);
    p_glPolygonOffset(0, 0);
}

/* NxOp_Clear: a rectangle of the pass's targets, outside the bound state (scissor test does the rectangle) */
static void clear_rect(const uint32_t* a) {
    p_glEnable(GL_SCISSOR_TEST);
    p_glScissor((GLint)a[4], (GLint)a[5], (GLsizei)a[6], (GLsizei)a[7]);
    /* targets are attached through their sRGB view when they have one: GL_FRAMEBUFFER_SRGB encodes the clear */
    if (a[0] & NxClear_Srgb) p_glEnable(GL_FRAMEBUFFER_SRGB); else p_glDisable(GL_FRAMEBUFFER_SRGB);
    if (a[0] & 1) {
        p_glColorMaski(0, 1, 1, 1, 1);
        float c[4] = {(a[1] & 255) / 255.0f, (a[1] >> 8 & 255) / 255.0f, (a[1] >> 16 & 255) / 255.0f, (a[1] >> 24) / 255.0f};
        p_glClearBufferfv(GL_COLOR, 0, c);
    }
    float z = asf(a[2]);
    GLint st = (GLint)(a[3] & 0xFF);
    if (a[0] & 6) { p_glDepthMask(GL_TRUE); p_glStencilMaskSeparate(GL_FRONT_AND_BACK, 0xFF); }
    if ((a[0] & 6) == 6) p_glClearBufferfi(GL_DEPTH_STENCIL, 0, z, st);
    else if (a[0] & 2) p_glClearBufferfv(GL_DEPTH, 0, &z);
    else if (a[0] & 4) p_glClearBufferiv(GL_STENCIL, 0, &st);
    p_glScissor(g.scissor[0], g.scissor[1], g.scissor[2], g.scissor[3]);
    if (g.boundState) apply_state(&g.st[g.boundState].rs, 1);   /* masks, scissor enable, sRGB back */
    else { p_glDisable(GL_SCISSOR_TEST); g.haveApplied = 0; }
}

/* ---- draw hook (tools: per-draw images) ---------------------------------------------------------------------- */
static int (*g_drawHook)(void*, uint32_t, uint32_t);
static void* g_drawUser;
void nxgl_set_draw_hook(int (*hook)(void* user, uint32_t draw, uint32_t colorImage), void* user) {
    g_drawHook = hook; g_drawUser = user; g.skipDraws = 0;
}
int nxgl_read_rgba8(uint32_t image, uint32_t* w, uint32_t* h, uint8_t* dst, size_t cap) {
    uint32_t id = image ? image : g.swapImage;
    if (!id || id > g.nImg) return -1;
    if (g.img[id].isView) id = g.img[id].base;
    Image* im = &g.img[id];
    if (im->isDepth) return -1;
    *w = im->w; *h = im->h;
    if ((size_t)im->w * im->h * 4 > cap) return -1;
    GLint prev = 0;
    p_glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &prev);
    if (!g.readFbo) p_glGenFramebuffers(1, &g.readFbo);
    p_glBindFramebuffer(GL_READ_FRAMEBUFFER, g.readFbo);
    p_glFramebufferTexture2D(GL_READ_FRAMEBUFFER, GL_DEPTH_STENCIL_ATTACHMENT, GL_TEXTURE_2D, 0, 0);
    p_glFramebufferTexture2D(GL_READ_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_TEXTURE_2D, 0, 0);
    p_glFramebufferTexture2D(GL_READ_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, im->tex, 0);
    p_glReadBuffer(GL_COLOR_ATTACHMENT0);
    p_glPixelStorei(GL_PACK_ALIGNMENT, 1);
    p_glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    p_glReadPixels(0, 0, (GLsizei)im->w, (GLsizei)im->h, GL_RGBA, GL_UNSIGNED_BYTE, dst);
    p_glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)prev);
    return 0;
}

int nxgl_read_rgba32f(uint32_t image, uint32_t* w, uint32_t* h, float* dst, size_t cap) {
    uint32_t id = image ? image : g.swapImage;
    if (!id || id > g.nImg) return -1;
    if (g.img[id].isView) id = g.img[id].base;
    Image* im = &g.img[id];
    if (im->isDepth) return -1;
    *w = im->w; *h = im->h;
    if ((size_t)im->w * im->h * 4 * sizeof(float) > cap) return -1;
    GLint prev = 0;
    p_glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &prev);
    if (!g.readFbo) p_glGenFramebuffers(1, &g.readFbo);
    p_glBindFramebuffer(GL_READ_FRAMEBUFFER, g.readFbo);
    p_glFramebufferTexture2D(GL_READ_FRAMEBUFFER, GL_DEPTH_STENCIL_ATTACHMENT, GL_TEXTURE_2D, 0, 0);
    p_glFramebufferTexture2D(GL_READ_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_TEXTURE_2D, 0, 0);
    p_glFramebufferTexture2D(GL_READ_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, im->tex, 0);
    p_glReadBuffer(GL_COLOR_ATTACHMENT0);
    p_glPixelStorei(GL_PACK_ALIGNMENT, 1);
    p_glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    p_glReadPixels(0, 0, (GLsizei)im->w, (GLsizei)im->h, GL_RGBA, GL_FLOAT, dst);
    p_glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)prev);
    return 0;
}

/* ---- replay ------------------------------------------------------------------------------------------------- */
static GLenum glprim(uint32_t p) {
    static const GLenum t[6] = {GL_TRIANGLES, GL_TRIANGLE_STRIP, GL_LINES, GL_LINE_STRIP, GL_POINTS, GL_TRIANGLE_FAN};
    return p < 6 ? t[p] : GL_TRIANGLES;
}

static void bind_layout(uint32_t lay) {
    if (!lay || lay > MAX_LAYOUTS || !g.lay[lay].defined) { p_glBindVertexArray(g.lay[0].vao); return; }
    Layout* l = &g.lay[lay];
    if (!l->vao) {
        p_glGenVertexArrays(1, &l->vao);
        p_glBindVertexArray(l->vao);
        for (uint32_t i = 0; i < l->l.count && i < 8; i++) {
            GLint c = l->l.attr[i].components ? l->l.attr[i].components : 1;
            GLenum type; GLboolean norm;
            switch (l->l.attr[i].type) {
            case NxAttr_Unorm8: type = GL_UNSIGNED_BYTE; norm = GL_TRUE; break;
            case NxAttr_Snorm16: type = GL_SHORT; norm = GL_TRUE; break;
            case NxAttr_Float16: type = GL_HALF_FLOAT; norm = GL_FALSE; break;
            default: type = GL_FLOAT; norm = GL_FALSE; break;
            }
            GLuint loc = l->l.attr[i].location ? (GLuint)(l->l.attr[i].location - 1) : i;
            if (loc > 15) continue;
            p_glVertexAttribFormat(loc, c, type, norm, l->l.attr[i].offset);
            p_glVertexAttribBinding(loc, 0);
            p_glEnableVertexAttribArray(loc);
        }
    }
    p_glBindVertexArray(l->vao);
}

static void bind_vertex_buffer(void) {
    const StateRec* s = g.boundState ? &g.st[g.boundState] : NULL;
    uint32_t stride = s && s->layout && s->layout <= MAX_LAYOUTS ? g.lay[s->layout].l.stride : 0;
    if (g.vtxBlock && g.vtxBlock <= g.nMem)
        p_glBindVertexBuffer(0, g.mem[g.vtxBlock].buf, g.vtxOffset, (GLsizei)stride);
}

static void replay(const NxSubmit* sub, NxFrameStats* stats) {
    const uint32_t* w = (const uint32_t*)(uintptr_t)sub->stream;
    const uint32_t* end = w + sub->words;
    uint32_t idxBlock = 0, idxOffset = 0;
    while (w < end) {
        uint32_t op = w[0] & 0xFFFF, argc = w[0] >> 16;
        const uint32_t* a = w + 1;
        if (a + argc > end) break;
        w += 1 + argc;
        switch (op) {
        case NxOp_End: w = end; break;
        case NxOp_DefineLayout:
            if (argc >= 10 && a[0] && a[0] <= MAX_LAYOUTS) {
                Layout* l = &g.lay[a[0]];
                memcpy(&l->l, a + 1, sizeof(NxVtxLayout));
                l->defined = 1;
                l->vao = 0;   /* (re)built at first use */
            }
            break;
        case NxOp_DefineState:
            if (argc >= 12 && a[0] && a[0] <= MAX_STATES) {
                StateRec* s = &g.st[a[0]];
                for (int i = 0; i < 4; i++) s->rs.w[i] = a[4 + 2 * i] | (uint64_t)a[5 + 2 * i] << 32;
                s->prog = program_for((uint16_t)a[1], (uint16_t)a[2]);
                s->layout = (uint16_t)a[3];
                s->defined = 1;
            }
            break;
        case NxOp_BeginPass: if (argc >= 8) begin_pass(a); break;
        case NxOp_EndPass: break;
        case NxOp_Barrier: break;   /* GL orders render-to-texture -> sampling itself */
        case NxOp_BindState: {
            if (argc < 1 || !a[0] || a[0] > MAX_STATES || !g.st[a[0]].defined) break;
            StateRec* s = &g.st[a[0]];
            stats->state_binds++;
            if (g.boundState != a[0] || !g.haveApplied) {
                p_glUseProgram(s->prog ? g.prog[s->prog].prog : 0);
                bind_layout(s->layout);
                if (idxBlock) p_glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g.mem[idxBlock].buf);
                g.vtxDirty = 1;
            }
            apply_state(&s->rs, !g.haveApplied);
            stats->block_binds++;
            g.boundState = a[0];
            break;
        }
        case NxOp_BindUbo:
            if (argc >= 5 && a[2] && a[2] <= g.nMem) {
                upload_range(a[2], a[3], a[4]);
                if (a[0] & 1) p_glBindBufferRange(GL_UNIFORM_BUFFER, 0 * 16 + a[1], g.mem[a[2]].buf, a[3], a[4]);
                if (a[0] & 2) p_glBindBufferRange(GL_UNIFORM_BUFFER, 1 * 16 + a[1], g.mem[a[2]].buf, a[3], a[4]);
            }
            break;
        case NxOp_BindVtx:
            if (argc >= 3) { upload_range(a[0], a[1], a[2]); g.vtxBlock = a[0]; g.vtxOffset = a[1]; g.vtxDirty = 1; }
            break;
        case NxOp_BindIdx:
            if (argc >= 4 && a[0] && a[0] <= g.nMem) {
                upload_range(a[0], a[1], a[2]);
                idxBlock = a[0]; idxOffset = a[1]; g.idxType = a[3];
                p_glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g.mem[a[0]].buf);
            }
            break;
        case NxOp_BindTextures:
            if (argc >= 3) {
                uint32_t stage = a[0] > 1 ? 1 : a[0];
                for (uint32_t i = 0; i < a[2] && 3 + i < argc; i++) {
                    uint32_t h = a[3 + i], img = h & 0xFFFFF, smp = h >> 20;
                    GLuint unit = stage * 16 + a[1] + i;
                    p_glActiveTexture(GL_TEXTURE0 + unit);
                    if (img && img <= g.nImg) p_glBindTexture(g.img[img].target, g.img[img].tex);
                    p_glBindSampler(unit, smp && smp <= g.nSmp ? g.smp[smp] : 0);
                }
            }
            break;
        case NxOp_Draw:
        case NxOp_DrawIndexed: {
            if (!g.boundState || g.skipDraws) break;
            if (g.vtxDirty) { bind_vertex_buffer(); g.vtxDirty = 0; }
            if (op == NxOp_Draw && argc >= 5) {
                p_glDrawArraysInstancedBaseInstance(glprim(a[0]), (GLint)a[3], (GLsizei)a[1], (GLsizei)a[2], a[4]);
            } else if (op == NxOp_DrawIndexed && argc >= 6) {
                uint32_t isz = g.idxType ? 4 : 2;
                p_glDrawElementsInstancedBaseVertexBaseInstance(glprim(a[0]), (GLsizei)a[1],
                    g.idxType ? GL_UNSIGNED_INT : GL_UNSIGNED_SHORT, (const void*)(uintptr_t)(idxOffset + a[3] * isz),
                    (GLsizei)a[2], (GLint)a[4], a[5]);
            }
            stats->draws++;
            if (g_drawHook && g_drawHook(g_drawUser, g.frameDraws, g.passColor)) g.skipDraws = 1;
            g.frameDraws++;
            break;
        }
        case NxOp_Viewport:
            if (argc >= 6) {
                p_glViewportIndexedf(0, asf(a[0]), asf(a[1]), asf(a[2]), asf(a[3]));
                p_glDepthRangef(asf(a[4]), asf(a[5]));
            }
            break;
        case NxOp_Scissor:
            if (argc >= 4) {
                for (int q = 0; q < 4; q++) g.scissor[q] = (GLint)a[q];
                p_glScissor(g.scissor[0], g.scissor[1], g.scissor[2], g.scissor[3]);
            }
            break;
        case NxOp_StencilRef:
            if (argc >= 1 && g.boundState) { g.stencilRef = a[0] & 0xFF; apply_stencil_funcs(g.st[g.boundState].rs.w[1], g.stencilRef); }
            break;
        case NxOp_BlendConst:
            if (argc >= 4) p_glBlendColor(asf(a[0]), asf(a[1]), asf(a[2]), asf(a[3]));
            break;
        case NxOp_DepthBias:
            if (argc >= 2) p_glPolygonOffset(asf(a[1]), asf(a[0]));
            break;
        case NxOp_Clear:
            if (argc >= 8) clear_rect(a);
            break;
        case NxOp_Marker: break;   /* tags for captures and tools */
        default: break;
        }
    }
}

static uint64_t api_submit(uint64_t a) {
    const NxSubmit* s = (const NxSubmit*)(uintptr_t)a;
    if (!s) return NXGPU_BAD_CALL;
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    NxFrameStats st;
    memset(&st, 0, sizeof st);
    st.frame = s->frame; st.cmd_words = s->words;
    g.frameDraws = 0;
    replay(s, &st);
    p_glFinish();
    clock_gettime(CLOCK_MONOTONIC, &t1);
    st.record_us = (uint32_t)((t1.tv_sec - t0.tv_sec) * 1000000 + (t1.tv_nsec - t0.tv_nsec) / 1000);
    GLenum e = p_glGetError();
    if (e) { char b[32]; snprintf(b, sizeof b, "0x%x", e); seterr("submit: GL error %s%s", b, ""); }
    if (s->stats) memcpy((void*)(uintptr_t)s->stats, &st, sizeof st);
    return 0;
}

static uint64_t api_wait_frame(uint64_t f) { (void)f; return 0; }   /* submit is synchronous */

static uint64_t api_readback(uint64_t a) {
    const NxReadback* r = (const NxReadback*)(uintptr_t)a;
    if (!r || !r->dst || r->format > NxRb_Stencil8) return NXGPU_BAD_CALL;
    uint32_t id = r->image ? r->image : g.swapImage;
    if (!id || id > g.nImg) return NXGPU_BAD_CALL;
    if (g.img[id].isView) id = g.img[id].base;
    Image* im = &g.img[id];
    int depthFmt = r->format == NxRb_Depth32F || r->format == NxRb_Stencil8;
    if (depthFmt != im->isDepth || (r->format == NxRb_Stencil8 && !im->hasStencil)) return NXGPU_BAD_CALL;
    if ((uint64_t)r->w * r->h * nx_readback_bpp(r->format) > r->dst_size || (uint64_t)r->x + r->w > im->w || (uint64_t)r->y + r->h > im->h)
        return NXGPU_BAD_CALL;
    if (!g.readFbo) p_glGenFramebuffers(1, &g.readFbo);
    p_glBindFramebuffer(GL_READ_FRAMEBUFFER, g.readFbo);
    p_glFramebufferTexture2D(GL_READ_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, im->isDepth ? 0 : im->tex, 0);
    p_glFramebufferTexture2D(GL_READ_FRAMEBUFFER, GL_DEPTH_STENCIL_ATTACHMENT, GL_TEXTURE_2D, 0, 0);
    if (im->isDepth)
        p_glFramebufferTexture2D(GL_READ_FRAMEBUFFER, im->hasStencil ? GL_DEPTH_STENCIL_ATTACHMENT : GL_DEPTH_ATTACHMENT,
                                 GL_TEXTURE_2D, im->tex, 0);
    p_glReadBuffer(im->isDepth ? GL_NONE : GL_COLOR_ATTACHMENT0);
    p_glPixelStorei(GL_PACK_ALIGNMENT, 1);
    p_glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    p_glDisable(GL_FRAMEBUFFER_SRGB);
    g.haveApplied = 0;
    GLenum fmt = GL_RGBA, type = GL_UNSIGNED_BYTE;
    if (r->format == NxRb_RGBA32F) type = GL_FLOAT;
    else if (r->format == NxRb_Depth32F) { fmt = GL_DEPTH_COMPONENT; type = GL_FLOAT; }
    else if (r->format == NxRb_Stencil8) { fmt = GL_STENCIL_INDEX; type = GL_UNSIGNED_BYTE; }
    p_glReadPixels((GLint)r->x, (GLint)r->y, (GLsizei)r->w, (GLsizei)r->h, fmt, type, (void*)(uintptr_t)r->dst);
    p_glFramebufferTexture2D(GL_READ_FRAMEBUFFER, GL_DEPTH_STENCIL_ATTACHMENT, GL_TEXTURE_2D, 0, 0);
    p_glFramebufferTexture2D(GL_READ_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_TEXTURE_2D, 0, 0);
    p_glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);
    return p_glGetError() ? NXGPU_BAD_CALL : 0;
}

static uint64_t api_shutdown(void) { p_glFinish(); return 0; }

/* ---- init ----------------------------------------------------------------------------------------------------- */
const NxGpuApi* nxgl_create(uint32_t width, uint32_t height, char* err, size_t errlen) {
    memset(&g, 0, sizeof g);
#define FAIL(msg) do { if (err && errlen) snprintf(err, errlen, "%s (egl 0x%x)", msg, eglGetError()); return NULL; } while (0)
    PFNEGLGETPLATFORMDISPLAYEXTPROC getPlatformDisplay =
        (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (!getPlatformDisplay) FAIL("eglGetPlatformDisplayEXT missing");
    g.dpy = getPlatformDisplay(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    if (g.dpy == EGL_NO_DISPLAY) FAIL("no surfaceless display");
    EGLint maj, min;
    if (!eglInitialize(g.dpy, &maj, &min)) FAIL("eglInitialize");
    if (!eglBindAPI(EGL_OPENGL_API)) FAIL("eglBindAPI");
    const EGLint attrs[] = {EGL_CONTEXT_MAJOR_VERSION, 4, EGL_CONTEXT_MINOR_VERSION, 5,
                            EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL_NONE};
    g.ctx = eglCreateContext(g.dpy, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, attrs);
    if (g.ctx == EGL_NO_CONTEXT) FAIL("eglCreateContext 4.5 core");
    if (!eglMakeCurrent(g.dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, g.ctx)) FAIL("eglMakeCurrent");
    for (size_t i = 0; i < sizeof g_funcs / sizeof g_funcs[0]; i++) {
        *g_funcs[i].fn = eglGetProcAddress(g_funcs[i].name);
        if (!*g_funcs[i].fn) {
            if (err && errlen) snprintf(err, errlen, "GL function %s missing", g_funcs[i].name);
            return NULL;
        }
    }
#undef FAIL
    p_glClipControl(GL_LOWER_LEFT, GL_ZERO_TO_ONE);
    p_glProvokingVertex(GL_FIRST_VERTEX_CONVENTION);
    p_glFrontFace(NXGL_FRONT_FACE);
    p_glEnable(GL_PROGRAM_POINT_SIZE);
    p_glGenVertexArrays(1, &g.lay[0].vao);
    p_glBindVertexArray(g.lay[0].vao);
    if (width && height) {
        NxImageDesc d;
        memset(&d, 0, sizeof d);
        d.width = (uint16_t)width; d.height = (uint16_t)height; d.format = NxFmt_RGBA8; d.flags = NxImg_RenderTarget;
        g.swapImage = (uint32_t)api_image_create((uint64_t)(uintptr_t)&d);
    }
    NxGpuApi* api = &g.api;
    api->info.magic = NXGPU_MAGIC; api->info.version = NXGPU_VERSION; api->info.frames_in_flight = 2;
    api->info.width = width; api->info.height = height; api->info.backend = 2;
    api->mem_create = (uint64_t)(uintptr_t)api_mem_create;
    api->mem_gpu_addr = (uint64_t)(uintptr_t)api_mem_gpu_addr;
    api->image_create = (uint64_t)(uintptr_t)api_image_create;
    api->sampler_create = (uint64_t)(uintptr_t)api_sampler_create;
    api->shader_create = (uint64_t)(uintptr_t)api_shader_create;
    api->submit = (uint64_t)(uintptr_t)api_submit;
    api->wait_frame = (uint64_t)(uintptr_t)api_wait_frame;
    api->readback = (uint64_t)(uintptr_t)api_readback;
    api->shutdown = (uint64_t)(uintptr_t)api_shutdown;
    if (p_glGetError()) { if (err && errlen) snprintf(err, errlen, "GL error during init"); return NULL; }
    return api;
}

void nxgl_destroy(void) {
    if (g.dpy) {
        eglMakeCurrent(g.dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (g.ctx) eglDestroyContext(g.dpy, g.ctx);
        eglTerminate(g.dpy);
    }
    memset(&g, 0, sizeof g);
}
