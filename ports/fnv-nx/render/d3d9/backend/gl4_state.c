/* fnv-nx L6 GL 4.5 backend v4: render state words -> GL (nx/state.h conventions), passes, clears, vertex
 * layouts and samplers. Ported from nxgl v3 with total enum switches. */
#include "gl4.h"
#include <string.h>

#ifndef GL_TEXTURE_MAX_ANISOTROPY
#define GL_TEXTURE_MAX_ANISOTROPY 0x84FE
#endif

static GLenum glcmp(uint32_t c) {   /* 3-bit field, all 8 values are NxCompare */
    static const GLenum t[8] = {GL_NEVER, GL_LESS, GL_EQUAL, GL_LEQUAL, GL_GREATER, GL_NOTEQUAL, GL_GEQUAL, GL_ALWAYS};
    return t[c & 7];
}
static GLenum glsop(uint32_t o) {   /* 3-bit field, all 8 values are NxStencilOp */
    static const GLenum t[8] = {GL_KEEP, GL_ZERO, GL_REPLACE, GL_INCR, GL_DECR, GL_INVERT, GL_INCR_WRAP, GL_DECR_WRAP};
    return t[o & 7];
}
static GLenum glbf(uint32_t f) {
    switch ((enum NxBlendFactor)f) {
    case NxBf_Zero: return GL_ZERO;
    case NxBf_One: return GL_ONE;
    case NxBf_SrcColor: return GL_SRC_COLOR;
    case NxBf_InvSrcColor: return GL_ONE_MINUS_SRC_COLOR;
    case NxBf_SrcAlpha: return GL_SRC_ALPHA;
    case NxBf_InvSrcAlpha: return GL_ONE_MINUS_SRC_ALPHA;
    case NxBf_DstAlpha: return GL_DST_ALPHA;
    case NxBf_InvDstAlpha: return GL_ONE_MINUS_DST_ALPHA;
    case NxBf_DstColor: return GL_DST_COLOR;
    case NxBf_InvDstColor: return GL_ONE_MINUS_DST_COLOR;
    case NxBf_SrcAlphaSat: return GL_SRC_ALPHA_SATURATE;
    case NxBf_ConstColor: return GL_CONSTANT_COLOR;
    case NxBf_InvConstColor: return GL_ONE_MINUS_CONSTANT_COLOR;
    case NxBf_Src1Color: return GL_SRC1_COLOR;
    case NxBf_InvSrc1Color: return GL_ONE_MINUS_SRC1_COLOR;
    case NxBf_Count: break;
    }
    gl4_err("blend factor %u", f);
    return GL_ZERO;
}
static GLenum glbop(uint32_t o) {
    switch ((enum NxBlendOp)o) {
    case NxBop_Add: return GL_FUNC_ADD;
    case NxBop_Subtract: return GL_FUNC_SUBTRACT;
    case NxBop_RevSubtract: return GL_FUNC_REVERSE_SUBTRACT;
    case NxBop_Min: return GL_MIN;
    case NxBop_Max: return GL_MAX;
    }
    gl4_err("blend op %u", o);
    return GL_FUNC_ADD;
}

void gl4_apply_stencil(uint64_t w1, uint32_t ref) {
    uint32_t rm = NXRS_GET(w1, NXRS_S_READMASK, 8);
    p_glStencilFuncSeparate(GL_FRONT, glcmp(NXRS_GET(w1, NXRS_S_FUNC, 3)), (GLint)ref, rm);
    p_glStencilFuncSeparate(GL_BACK, glcmp(NXRS_GET(w1, NXRS_S_BFUNC, 3)), (GLint)ref, rm);
}

static void apply_cull(uint32_t cull) {
    switch ((enum NxCull)cull) {
    case NxCull_None: p_glDisable(GL_CULL_FACE); return;
    case NxCull_CW: p_glEnable(GL_CULL_FACE); p_glCullFace(GL_FRONT); return;
    case NxCull_CCW: p_glEnable(GL_CULL_FACE); p_glCullFace(GL_BACK); return;
    }
    gl4_err("cull %u", cull);
}
static GLenum glfill(uint32_t fill) {
    switch ((enum NxFill)fill) {
    case NxFill_Solid: return GL_FILL;
    case NxFill_Wireframe: return GL_LINE;
    case NxFill_Point: return GL_POINT;
    }
    gl4_err("fill %u", fill);
    return GL_FILL;
}

void gl4_apply_state(const NxRenderState* rs, int force) {
    uint64_t w0 = rs->w[0], w1 = rs->w[1], w2 = rs->w[2];
    if (force || w0 != g4.applied.w[0]) {
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
    if (force || w1 != g4.applied.w[1]) {
        if (w1 & 1) { p_glEnable(GL_DEPTH_TEST); p_glDepthFunc(glcmp(NXRS_GET(w1, NXRS_D_FUNC, 3))); }
        else p_glDisable(GL_DEPTH_TEST);
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
    g4.stencilRef = NXRS_GET(w1, NXRS_S_REF, 8);   /* ref and masks are re-applied at every BindState */
    if (NXRS_GET(w1, NXRS_S_ENABLE, 1)) gl4_apply_stencil(w1, g4.stencilRef);
    if (force || w2 != g4.applied.w[2]) {
        apply_cull(NXRS_GET(w2, NXRS_R_CULL, 2));
        p_glPolygonMode(GL_FRONT_AND_BACK, glfill(NXRS_GET(w2, NXRS_R_FILL, 2)));
        const GLenum po[3] = {GL_POLYGON_OFFSET_FILL, GL_POLYGON_OFFSET_LINE, GL_POLYGON_OFFSET_POINT};
        for (int i = 0; i < 3; i++) { if (NXRS_GET(w2, NXRS_R_BIAS, 1)) p_glEnable(po[i]); else p_glDisable(po[i]); }
        if (NXRS_GET(w2, NXRS_R_SCISSOR, 1)) p_glEnable(GL_SCISSOR_TEST); else p_glDisable(GL_SCISSOR_TEST);
        if (NXRS_GET(w2, NXRS_R_SRGB, 1)) p_glEnable(GL_FRAMEBUFFER_SRGB); else p_glDisable(GL_FRAMEBUFFER_SRGB);
        uint32_t clip = NXRS_GET(w2, NXRS_R_CLIP, 8);
        for (int i = 0; i < 8; i++) { if (clip >> i & 1) p_glEnable(GL_CLIP_DISTANCE0 + i); else p_glDisable(GL_CLIP_DISTANCE0 + i); }
    }
    g4.applied = *rs;
    g4.haveApplied = 1;
}

/* ---- render targets ---------------------------------------------------------------------------------------- */
void gl4_fbo_forget(uint32_t image) {
    for (uint32_t i = 0; i < GL4_MAX_FBOS; i++) {
        if (!g4.fbo[i].fbo || (g4.fbo[i].color != image && g4.fbo[i].depth != image)) continue;
        p_glDeleteFramebuffers(1, &g4.fbo[i].fbo);
        memset(&g4.fbo[i], 0, sizeof g4.fbo[i]);
    }
}

static GLuint fbo_for(uint32_t color, uint32_t depth) {
    uint32_t slot = GL4_MAX_FBOS;
    for (uint32_t i = 0; i < GL4_MAX_FBOS; i++) {
        if (g4.fbo[i].fbo && g4.fbo[i].color == color && g4.fbo[i].depth == depth) return g4.fbo[i].fbo;
        if (!g4.fbo[i].fbo && slot == GL4_MAX_FBOS) slot = i;
    }
    if (slot == GL4_MAX_FBOS) {   /* cache full: evict round-robin */
        slot = g4.fboNext++ % GL4_MAX_FBOS;
        p_glDeleteFramebuffers(1, &g4.fbo[slot].fbo);
    }
    GLuint f;
    p_glCreateFramebuffers(1, &f);
    const Gl4Image* c = gl4_img(color ? gl4_view_of(color) : 0);
    const Gl4Image* z = gl4_img(depth);
    if (c) { gl4_attach(f, GL_COLOR_ATTACHMENT0, c, 0, 0); p_glNamedFramebufferDrawBuffer(f, GL_COLOR_ATTACHMENT0); }
    else p_glNamedFramebufferDrawBuffer(f, GL_NONE);
    if (z) gl4_attach(f, gl4_fmt(z->fmt).glds ? GL_DEPTH_STENCIL_ATTACHMENT : GL_DEPTH_ATTACHMENT, z, 0, 0);
    GLenum st = p_glCheckNamedFramebufferStatus(f, GL_DRAW_FRAMEBUFFER);
    if (st != GL_FRAMEBUFFER_COMPLETE) gl4_err("framebuffer incomplete: 0x%x", st);
    g4.fbo[slot].color = color; g4.fbo[slot].depth = depth; g4.fbo[slot].fbo = f;
    return f;
}

static float asf(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

static void clear_depth_stencil(uint32_t flags, const Gl4Image* z, uint32_t zbits, uint32_t stencil) {
    float depth = asf(zbits);
    GLint s = (GLint)(stencil & 0xFF);
    if (flags & (NxClear_Depth | NxClear_Stencil)) { p_glDepthMask(GL_TRUE); p_glStencilMaskSeparate(GL_FRONT_AND_BACK, 0xFF); }
    if ((flags & 6) == 6 && z->hasStencil) p_glClearBufferfi(GL_DEPTH_STENCIL, 0, depth, s);
    else if (flags & NxClear_Depth) p_glClearBufferfv(GL_DEPTH, 0, &depth);
    else if (flags & NxClear_Stencil && z->hasStencil) p_glClearBufferiv(GL_STENCIL, 0, &s);
}

/* NxOp_BeginPass: colorImage, depthImage, clearFlags, clear RGBA8, clear depth, clear stencil, width, height */
void gl4_op_begin_pass(const uint32_t* a, uint32_t argc) {
    if (argc < 8) { gl4_err("BeginPass: %u words", argc); return; }
    uint32_t color = a[0] ? a[0] : g4.swapImage, depth = a[1];
    if ((a[0] && !gl4_img(a[0])) || (color && !gl4_img(color)) || (depth && !gl4_img(depth))) { gl4_err("BeginPass: bad image"); return; }
    g4.passColor = color;
    p_glBindFramebuffer(GL_FRAMEBUFFER, fbo_for(color, depth));
    g4.passW = a[6]; g4.passH = a[7];
    p_glDisable(GL_SCISSOR_TEST);   /* clears: full target, all channels, no sRGB encoding of the clear colour */
    p_glDisable(GL_FRAMEBUFFER_SRGB);
    if (a[2] & NxClear_Color) {
        p_glColorMaski(0, 1, 1, 1, 1);
        float c[4] = {(a[3] & 255) / 255.0f, (a[3] >> 8 & 255) / 255.0f, (a[3] >> 16 & 255) / 255.0f, (a[3] >> 24) / 255.0f};
        p_glClearBufferfv(GL_COLOR, 0, c);
    }
    if ((a[2] & 6) && depth) clear_depth_stencil(a[2], gl4_img(depth), a[4], a[5]);
    g4.haveApplied = 0;
    g4.boundState = 0;
    p_glViewportIndexedf(0, 0.5f, 0.5f, (float)g4.passW, (float)g4.passH);
    p_glDepthRangef(0.0f, 1.0f);
    p_glScissor(0, 0, (GLsizei)g4.passW, (GLsizei)g4.passH);
    g4.scissor[0] = 0; g4.scissor[1] = 0; g4.scissor[2] = (GLint)g4.passW; g4.scissor[3] = (GLint)g4.passH;
    p_glBlendColor(1, 1, 1, 1);
    p_glPolygonOffset(0, 0);
}

/* NxOp_Clear: flags, RGBA8, depth, stencil, x, y, w, h: a rectangle of the pass's targets, outside the bound state */
void gl4_op_clear(const uint32_t* a, uint32_t argc) {
    if (argc < 8) { gl4_err("Clear: %u words", argc); return; }
    p_glEnable(GL_SCISSOR_TEST);
    p_glScissor((GLint)a[4], (GLint)a[5], (GLsizei)a[6], (GLsizei)a[7]);
    if (a[0] & NxClear_Srgb) p_glEnable(GL_FRAMEBUFFER_SRGB); else p_glDisable(GL_FRAMEBUFFER_SRGB);
    if (a[0] & NxClear_Color) {
        p_glColorMaski(0, 1, 1, 1, 1);
        float c[4] = {(a[1] & 255) / 255.0f, (a[1] >> 8 & 255) / 255.0f, (a[1] >> 16 & 255) / 255.0f, (a[1] >> 24) / 255.0f};
        p_glClearBufferfv(GL_COLOR, 0, c);
    }
    if (a[0] & 6) {
        float depth = asf(a[2]);
        GLint s = (GLint)(a[3] & 0xFF);
        p_glDepthMask(GL_TRUE); p_glStencilMaskSeparate(GL_FRONT_AND_BACK, 0xFF);
        if ((a[0] & 6) == 6) p_glClearBufferfi(GL_DEPTH_STENCIL, 0, depth, s);
        else if (a[0] & NxClear_Depth) p_glClearBufferfv(GL_DEPTH, 0, &depth);
        else p_glClearBufferiv(GL_STENCIL, 0, &s);
    }
    p_glScissor(g4.scissor[0], g4.scissor[1], g4.scissor[2], g4.scissor[3]);
    if (g4.boundState) gl4_apply_state(&g4.st[g4.boundState].rs, 1);   /* masks, scissor enable, sRGB back */
    else { p_glDisable(GL_SCISSOR_TEST); g4.haveApplied = 0; }
}

/* ---- vertex layouts ---------------------------------------------------------------------------------------- */
void gl4_bind_layout(uint32_t lay) {
    if (!lay || lay > GL4_MAX_LAYOUTS || !g4.lay[lay].defined) { p_glBindVertexArray(g4.lay[0].vao); return; }
    Gl4Layout* l = &g4.lay[lay];
    if (!l->vao) {
        p_glGenVertexArrays(1, &l->vao);
        p_glBindVertexArray(l->vao);
        if (l->l.count > 8) gl4_err("layout %u: %u attributes", lay, l->l.count);
        for (uint32_t i = 0; i < l->l.count && i < 8; i++) {
            GLint c = l->l.attr[i].components ? l->l.attr[i].components : 1;
            GLenum type = GL_FLOAT; GLboolean norm = GL_FALSE;
            switch ((enum NxAttrType)l->l.attr[i].type) {
            case NxAttr_Float32: break;
            case NxAttr_Unorm8: type = GL_UNSIGNED_BYTE; norm = GL_TRUE; break;
            case NxAttr_Snorm16: type = GL_SHORT; norm = GL_TRUE; break;
            case NxAttr_Float16: type = GL_HALF_FLOAT; break;
            }
            GLuint loc = l->l.attr[i].location ? (GLuint)(l->l.attr[i].location - 1) : i;
            if (loc > 15) { gl4_err("layout %u: location %u", lay, loc); continue; }
            p_glVertexAttribFormat(loc, c, type, norm, l->l.attr[i].offset);
            p_glVertexAttribBinding(loc, 0);
            p_glEnableVertexAttribArray(loc);
        }
    }
    p_glBindVertexArray(l->vao);
}

/* ---- samplers ---------------------------------------------------------------------------------------------- */
static GLint glwrap(uint32_t w) {
    switch ((enum NxWrap)w) {
    case NxWrap_Repeat: return GL_REPEAT;
    case NxWrap_Mirror: return GL_MIRRORED_REPEAT;
    case NxWrap_Clamp: return GL_CLAMP_TO_EDGE;
    case NxWrap_Border: return GL_CLAMP_TO_BORDER;
    case NxWrap_MirrorOnce: return GL_MIRROR_CLAMP_TO_EDGE;
    }
    gl4_err("wrap %u", w);
    return GL_CLAMP_TO_EDGE;
}

static GLint glminfilter(uint32_t mip, int minL) {
    switch ((enum NxMipFilter)mip) {
    case NxMip_None: return minL ? GL_LINEAR : GL_NEAREST;
    case NxMip_Nearest: return minL ? GL_LINEAR_MIPMAP_NEAREST : GL_NEAREST_MIPMAP_NEAREST;
    case NxMip_Linear: return minL ? GL_LINEAR_MIPMAP_LINEAR : GL_NEAREST_MIPMAP_LINEAR;
    }
    gl4_err("mip filter %u", mip);
    return GL_NEAREST;
}

uint64_t gl4_api_sampler_create(uint64_t a) {
    const NxSamplerState* s = (const NxSamplerState*)nxgl4_guest_ptr(a, sizeof *s);
    if (!s || g4.nSmp + 1 >= NX_MAX_SAMPLERS) return 0;
    uint32_t id = ++g4.nSmp;
    GLuint o;
    p_glGenSamplers(1, &o);
    g4.smp[id] = o;
    p_glSamplerParameteri(o, GL_TEXTURE_MIN_FILTER, glminfilter(s->filter >> NXSMP_MIP_SHIFT & 3, (s->filter & NXSMP_MIN_LINEAR) != 0));
    p_glSamplerParameteri(o, GL_TEXTURE_MAG_FILTER, (s->filter & NXSMP_MAG_LINEAR) ? GL_LINEAR : GL_NEAREST);
    p_glSamplerParameteri(o, GL_TEXTURE_WRAP_S, glwrap(s->wrap & 7));
    p_glSamplerParameteri(o, GL_TEXTURE_WRAP_T, glwrap(s->wrap >> 3 & 7));
    p_glSamplerParameteri(o, GL_TEXTURE_WRAP_R, glwrap(s->wrap >> 6 & 7));
    p_glSamplerParameterf(o, GL_TEXTURE_LOD_BIAS, s->lodBias);
    p_glSamplerParameterf(o, GL_TEXTURE_MIN_LOD, (float)s->minLod);
    p_glSamplerParameterf(o, GL_TEXTURE_MAX_LOD, (float)s->maxLod);
    if (s->maxAniso > 1) p_glSamplerParameterf(o, GL_TEXTURE_MAX_ANISOTROPY, (float)s->maxAniso);
    float bc[4] = {(s->border & 255) / 255.0f, (s->border >> 8 & 255) / 255.0f, (s->border >> 16 & 255) / 255.0f, (s->border >> 24) / 255.0f};
    p_glSamplerParameterfv(o, GL_TEXTURE_BORDER_COLOR, bc);
    if (s->filter & NXSMP_SHADOW) {
        p_glSamplerParameteri(o, GL_TEXTURE_COMPARE_MODE, GL_COMPARE_REF_TO_TEXTURE);
        p_glSamplerParameteri(o, GL_TEXTURE_COMPARE_FUNC, GL_LEQUAL);
    }
    return id;
}
