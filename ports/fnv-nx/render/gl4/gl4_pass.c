/* fnv-nx L6 GL 4.5 backend v4: render passes and clears.
 *
 * A pass is a set of up to 4 colour subresources (image, level, layer: a cube face, a 3D slice, a mip level) and an
 * optional depth-stencil subresource, all of one size and one sample count (nxchk_pass_check; BeginPass4 that
 * violates it makes the submit BAD_CALL before anything runs). Colour image id 0 names the swapchain image. Colour
 * targets are attached through their sRGB view when the format has one (NXRS_R_SRGB then decides whether GL encodes
 * the write), draw buffer i = colour target i. A pass has no GL object: framebuffers are cached by the target
 * tuple. NxOp_Clear clears every colour target of the pass (D3D9 Clear clears all render targets). */
#include "gl4.h"
#include <string.h>

void gl4_fbo_forget(uint32_t image) {
    for (uint32_t i = 0; i < GL4_MAX_FBOS; i++) {
        const Gl4Pass* k = &g4.fbo[i].key;
        int uses = k->d.image == image;
        for (uint32_t c = 0; c < k->nColor; c++) uses |= k->c[c].image == image;
        if (!g4.fbo[i].fbo || !uses) continue;
        p_glDeleteFramebuffers(1, &g4.fbo[i].fbo);
        memset(&g4.fbo[i], 0, sizeof g4.fbo[i]);
    }
}

static GLuint fbo_for(const Gl4Pass* p) {
    uint32_t slot = GL4_MAX_FBOS;
    for (uint32_t i = 0; i < GL4_MAX_FBOS; i++) {
        if (g4.fbo[i].fbo && !memcmp(&g4.fbo[i].key, p, sizeof *p)) return g4.fbo[i].fbo;
        if (!g4.fbo[i].fbo && slot == GL4_MAX_FBOS) slot = i;
    }
    if (slot == GL4_MAX_FBOS) {   /* cache full: evict round-robin */
        slot = g4.fboNext++ % GL4_MAX_FBOS;
        p_glDeleteFramebuffers(1, &g4.fbo[slot].fbo);
    }
    GLuint f;
    p_glCreateFramebuffers(1, &f);
    GLenum bufs[4];
    for (uint32_t i = 0; i < p->nColor; i++) {
        const Gl4Image* c = gl4_img(gl4_view_of(p->c[i].image));
        bufs[i] = GL_COLOR_ATTACHMENT0 + i;
        gl4_attach(f, bufs[i], c, p->c[i].sub & 0xFFFF, p->c[i].sub >> 16);
    }
    if (p->nColor) p_glNamedFramebufferDrawBuffers(f, (GLsizei)p->nColor, bufs);
    else { p_glNamedFramebufferDrawBuffer(f, GL_NONE); p_glNamedFramebufferReadBuffer(f, GL_NONE); }
    const Gl4Image* z = gl4_img(p->d.image);
    if (z) gl4_attach(f, gl4_fmt(z->fmt).glds ? GL_DEPTH_STENCIL_ATTACHMENT : GL_DEPTH_ATTACHMENT, z, p->d.sub & 0xFFFF, p->d.sub >> 16);
    GLenum st = p_glCheckNamedFramebufferStatus(f, GL_DRAW_FRAMEBUFFER);
    if (st != GL_FRAMEBUFFER_COMPLETE) gl4_err("framebuffer incomplete: 0x%x", st);
    g4.fbo[slot].key = *p; g4.fbo[slot].fbo = f;
    return f;
}

static float asf(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

static void clear_color(uint32_t rgba8) {
    float c[4] = {(rgba8 & 255) / 255.0f, (rgba8 >> 8 & 255) / 255.0f, (rgba8 >> 16 & 255) / 255.0f, (rgba8 >> 24) / 255.0f};
    for (uint32_t i = 0; i < g4.passN; i++) { p_glColorMaski(i, 1, 1, 1, 1); p_glClearBufferfv(GL_COLOR, (GLint)i, c); }
}

static void clear_depth_stencil(uint32_t flags, const Gl4Image* z, uint32_t zbits, uint32_t stencil) {
    float depth = asf(zbits);
    GLint s = (GLint)(stencil & 0xFF);
    if (flags & (NxClear_Depth | NxClear_Stencil)) { p_glDepthMask(GL_TRUE); p_glStencilMaskSeparate(GL_FRONT_AND_BACK, 0xFF); }
    if ((flags & 6) == 6 && z->hasStencil) p_glClearBufferfi(GL_DEPTH_STENCIL, 0, depth, s);
    else if (flags & NxClear_Depth) p_glClearBufferfv(GL_DEPTH, 0, &depth);
    else if (flags & NxClear_Stencil && z->hasStencil) p_glClearBufferiv(GL_STENCIL, 0, &s);
}

/* binds the targets and sets what a pass starts with: viewport (0.5, 0.5, w, h) = D3D9's full viewport, depth range
 * [0,1], scissor (0, 0, w, h), blend constant (1,1,1,1), depth bias 0, no state bound */
static void begin(const Gl4Pass* p, uint32_t w, uint32_t h, uint32_t samples) {
    g4.passN = p->nColor; g4.passSamples = samples;
    p_glBindFramebuffer(GL_FRAMEBUFFER, fbo_for(p));
    g4.passW = w; g4.passH = h;
    p_glDisable(GL_SCISSOR_TEST);   /* clears: full target, all channels, no sRGB encoding of the clear colour */
    p_glDisable(GL_FRAMEBUFFER_SRGB);
}
static void begin_end(void) {
    g4.haveApplied = 0;
    g4.boundState = 0;
    p_glViewportIndexedf(0, 0.5f, 0.5f, (float)g4.passW, (float)g4.passH);
    p_glDepthRangef(0.0f, 1.0f);
    p_glScissor(0, 0, (GLsizei)g4.passW, (GLsizei)g4.passH);
    g4.scissor[0] = 0; g4.scissor[1] = 0; g4.scissor[2] = (GLint)g4.passW; g4.scissor[3] = (GLint)g4.passH;
    p_glBlendColor(1, 1, 1, 1);
    p_glPolygonOffset(0, 0);
}

/* NxOp_BeginPass (v3): colorImage, depthImage, clearFlags, clear RGBA8, clear depth, clear stencil, width, height */
void gl4_op_begin_pass(const uint32_t* a, uint32_t argc) {
    if (argc < 8) { gl4_err("BeginPass: %u words", argc); return; }
    Gl4Pass p;
    uint32_t w, h, ns;
    memset(&p, 0, sizeof p);
    p.nColor = 1; p.c[0].image = a[0] ? a[0] : g4.swapImage; p.d.image = a[1];
    if (nxchk_pass_check(gl4_chk_env(), &p, &w, &h, &ns)) return;
    begin(&p, a[6], a[7], ns);
    if (a[2] & NxClear_Color) clear_color(a[3]);
    if ((a[2] & 6) && p.d.image) clear_depth_stencil(a[2], gl4_img(p.d.image), a[4], a[5]);
    begin_end();
}

/* NxOp_BeginPass4: colorCount (0-4), (image, level | layer << 16) per colour target, depth (image, level | layer << 16) */
void gl4_op_begin_pass4(const uint32_t* a, uint32_t argc) {
    Gl4Pass p;
    uint32_t w, h, ns;
    if (nxchk_pass_parse4(gl4_chk_env(), a, argc, &p) || nxchk_pass_check(gl4_chk_env(), &p, &w, &h, &ns)) return;
    begin(&p, w, h, ns);
    begin_end();
}

/* NxOp_Clear: flags, RGBA8, depth, stencil, x, y, w, h: a rectangle of the pass's targets, outside the bound state */
void gl4_op_clear(const uint32_t* a, uint32_t argc) {
    if (argc < 8) { gl4_err("Clear: %u words", argc); return; }
    p_glEnable(GL_SCISSOR_TEST);
    p_glScissor((GLint)a[4], (GLint)a[5], (GLsizei)a[6], (GLsizei)a[7]);
    if (a[0] & NxClear_Srgb) p_glEnable(GL_FRAMEBUFFER_SRGB); else p_glDisable(GL_FRAMEBUFFER_SRGB);
    if (a[0] & NxClear_Color) clear_color(a[1]);
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
