/* fnv-nx L6 GL 4.5 backend v4: render state words -> GL (nx/state.h conventions). Ported from nxgl v3 with total
 * enum switches. w[3] (nxgpu4.h NXRS_M_*):
 *   MSAA        GL_MULTISAMPLE: disabled = every sample of a covered pixel is written from the pixel centre (D3D9's
 *               MULTISAMPLEANTIALIAS = FALSE); no effect on a single-sample target
 *   AALINE      GL_LINE_SMOOTH: lines get fractional coverage (the exact coverage function is implementation
 *               defined in GL and in D3D9)
 *   LASTPIXEL   no GL state (Wine 9.0 wined3d: WINED3D_RS_LASTPIXEL is stored and never applied); lines are GL's
 *               diamond-exit lines for both values
 *   NOCLIP      GL_DEPTH_CLAMP: primitives are not clipped by the near / far planes, their depth is clamped to the
 *               depth range (x / y guard-band clipping has no visible effect and w <= 0 is clipped as always);
 *               user clip planes (w[2] bits 8-15) are independent of it
 *   SAMPLEMASK  GL_SAMPLE_MASK + glSampleMaski(0, mask) on a multisampled target, disabled for 0xFFFFFFFF; bit i =
 *               sample i. D3D9 defines the mask for multisample render targets only: on a single-sample target
 *               it has no effect (a rule of this backend, independent of what a GL implementation does there) */
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
    if (force || rs->w[3] != g4.applied.w[3]) {
        uint64_t w3 = rs->w[3];
        uint32_t mask = (uint32_t)(w3 >> NXRS_M_SAMPLEMASK);
        if (w3 >> NXRS_M_MSAA & 1) p_glEnable(GL_MULTISAMPLE); else p_glDisable(GL_MULTISAMPLE);
        if (w3 >> NXRS_M_AALINE & 1) p_glEnable(GL_LINE_SMOOTH); else p_glDisable(GL_LINE_SMOOTH);
        if (w3 >> NXRS_M_NOCLIP & 1) p_glEnable(GL_DEPTH_CLAMP); else p_glDisable(GL_DEPTH_CLAMP);
        if (mask != 0xFFFFFFFFu && g4.passSamples > 1) { p_glEnable(GL_SAMPLE_MASK); p_glSampleMaski(0, mask); } else p_glDisable(GL_SAMPLE_MASK);
    }
    g4.applied = *rs;
    g4.haveApplied = 1;
}

/* A known state for draws and blits the backend issues itself (scaling, present): everything gl4_apply_state sets
 * goes back to its neutral value, and the next BindState re-applies its own. */
void gl4_neutral_state(void) {
    for (GLuint rt = 0; rt < 4; rt++) { p_glColorMaski(rt, 1, 1, 1, 1); p_glDisablei(GL_BLEND, rt); }
    p_glDisable(GL_DEPTH_TEST); p_glDepthMask(GL_TRUE);
    p_glDisable(GL_STENCIL_TEST); p_glStencilMaskSeparate(GL_FRONT_AND_BACK, 0xFF);
    p_glDisable(GL_CULL_FACE); p_glPolygonMode(GL_FRONT_AND_BACK, GL_FILL);
    p_glDisable(GL_POLYGON_OFFSET_FILL); p_glDisable(GL_POLYGON_OFFSET_LINE); p_glDisable(GL_POLYGON_OFFSET_POINT);
    p_glDisable(GL_SCISSOR_TEST); p_glDisable(GL_FRAMEBUFFER_SRGB);
    for (int i = 0; i < 8; i++) p_glDisable(GL_CLIP_DISTANCE0 + i);
    p_glEnable(GL_MULTISAMPLE); p_glDisable(GL_LINE_SMOOTH); p_glDisable(GL_DEPTH_CLAMP); p_glDisable(GL_SAMPLE_MASK);
    g4.haveApplied = 0;
    g4.boundState = 0;
}

