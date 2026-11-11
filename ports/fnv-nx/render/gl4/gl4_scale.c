/* fnv-nx L6 GL 4.5 backend v4: point-filtered scaling by rule, for Blit / GenMips with filter 0 (and the present
 * blit). Destination texel d of a dw-wide destination from an sw-wide source takes the source texel
 *     floor((d + 0.5) * sw / dw) = ((2 d + 1) * sw) / (2 dw)   (integer division, same in y; the 3D slice rule of
 *     GenMips uses it on z),
 * computed in the fragment shader in integers and fetched with texelFetch, so the result does not depend on the GL
 * implementation's nearest rule. Sources and destinations are the raw texels of the base textures (no sRGB
 * conversion, no view swizzle: the source is read through a view with identity swizzle).
 *   colour        one fragment per destination texel writes the fetched vec4 (the destination format converts);
 *   depth         gl_FragDepth = the fetched depth (depth test ALWAYS, writes on);
 *   stencil       (every format whose GL storage has stencil bits, so the x byte of Z24X8 is copied too) GL 4.5 has no stencil export: the destination stencil of the rect is cleared to 0 and bit b
 *                 (b = 0..7) is written by a pass that discards fragments whose source stencil bit b is 0 and
 *                 REPLACEs with ref 0xFF under write mask 1 << b.
 * The optional ramp (3 x 256 u16: red, green, blue) is the present gamma: out = ramp[c][round(clamp(c, 0, 1) * 255)]
 * >> 8, / 255 (the destination must be an 8-bit unorm colour image). The backend's own draws are not counted by
 * occlusion queries (the slice is paused). */
#include "gl4.h"
#include <stdio.h>
#include <string.h>

#ifndef GL_DEPTH_STENCIL_TEXTURE_MODE
#define GL_DEPTH_STENCIL_TEXTURE_MODE 0x90EA
#endif

#define SCALE_UNIT 62   /* texture units 62 / 63: above every unit a guest stage * 16 + slot can name */
enum { P_C2D, P_CARR, P_C3D, P_DEPTH, P_STENCIL };

static const char k_vs[] = "#version 450 core\nvoid main() { vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); "
                           "gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0); }\n";
static const char k_head[] =
    "#version 450 core\nlayout(location = 0) uniform ivec4 src;\nlayout(location = 1) uniform ivec4 dst;\n"
    "layout(location = 2) uniform ivec2 lz;\nlayout(location = 3) uniform int mode;\n"
    "layout(binding = 63) uniform usampler2D ramp;\n";
static const char k_tex[] = "ivec2 t = src.xy + ((2 * (ivec2(gl_FragCoord.xy) - dst.xy) + 1) * src.zw) / (2 * dst.zw);\n";
static const char k_gamma[] =
    "if (mode == 1) { ivec3 i = ivec3(round(clamp(c.rgb, 0.0, 1.0) * 255.0));\n"
    "  c.r = float(texelFetch(ramp, ivec2(i.r, 0), 0).r >> 8) / 255.0; c.g = float(texelFetch(ramp, ivec2(i.g, 1), 0).r >> 8) / 255.0;\n"
    "  c.b = float(texelFetch(ramp, ivec2(i.b, 2), 0).r >> 8) / 255.0; }\n";

static GLuint program(int kind) {
    static const char* const smp[] = {"sampler2D", "sampler2DArray", "sampler3D", "sampler2D", "usampler2D"};
    static const char* const fetch[] = {"texelFetch(s, t, 0)", "texelFetch(s, ivec3(t, lz.y), 0)", "texelFetch(s, ivec3(t, lz.y), 0)"};
    if (g4.scaleProg[kind]) return g4.scaleProg[kind];
    char fs[1800];
    int n = snprintf(fs, sizeof fs, "%slayout(binding = 62) uniform %s s;\n", k_head, smp[kind]);
    if (kind <= P_C3D)
        snprintf(fs + n, sizeof fs - (size_t)n, "layout(location = 0) out vec4 o;\nvoid main() {\n%s vec4 c = %s;\n%s o = c; }\n", k_tex, fetch[kind], k_gamma);
    else if (kind == P_DEPTH)
        snprintf(fs + n, sizeof fs - (size_t)n, "void main() {\n%s gl_FragDepth = texelFetch(s, t, 0).r; }\n", k_tex);
    else
        snprintf(fs + n, sizeof fs - (size_t)n, "void main() {\n%s if (((texelFetch(s, t, 0).r >> uint(mode)) & 1u) == 0u) discard; }\n", k_tex);
    g4.scaleProg[kind] = gl4_compile_program(k_vs, fs);
    return g4.scaleProg[kind];
}

static void ensure_objects(void) {
    if (!g4.nearestSmp) {
        p_glGenSamplers(1, &g4.nearestSmp);
        p_glSamplerParameteri(g4.nearestSmp, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        p_glSamplerParameteri(g4.nearestSmp, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        p_glSamplerParameteri(g4.nearestSmp, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        p_glSamplerParameteri(g4.nearestSmp, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        p_glSamplerParameteri(g4.nearestSmp, GL_TEXTURE_WRAP_R, GL_CLAMP_TO_EDGE);
    }
    if (!g4.rampTex) {
        p_glCreateTextures(GL_TEXTURE_2D, 1, &g4.rampTex);
        p_glTextureStorage2D(g4.rampTex, 1, GL_R16UI, 256, 3);
    }
    if (!g4.blitFbo[0]) p_glCreateFramebuffers(2, g4.blitFbo);
}

/* an identity-swizzle view of the source subresource: 2D -> 2D, cube -> 2D array (all faces), 3D -> 3D */
static GLuint source_view(const Gl4Sub* s, int stencil) {
    const Gl4Fmt f = gl4_fmt(s->im->fmt);
    GLenum target = s->im->target == GL_TEXTURE_CUBE_MAP ? GL_TEXTURE_2D_ARRAY : s->im->target;
    GLuint v;
    p_glGenTextures(1, &v);
    p_glTextureView(v, target, s->im->tex, f.internal, s->level, 1, 0, s->im->target == GL_TEXTURE_CUBE_MAP ? 6u : 1u);
    const GLint swz[4] = {GL_RED, GL_GREEN, GL_BLUE, GL_ALPHA};
    p_glTextureParameteriv(v, GL_TEXTURE_SWIZZLE_RGBA, swz);
    if (stencil) p_glTextureParameteri(v, GL_DEPTH_STENCIL_TEXTURE_MODE, GL_STENCIL_INDEX);
    return v;
}

static void draw(GLuint prog, GLuint view, const Gl4Sub* s, const Gl4Sub* d, uint32_t zlayer, int mode) {
    p_glUseProgram(prog);
    p_glBindTextureUnit(SCALE_UNIT, view); p_glBindSampler(SCALE_UNIT, g4.nearestSmp);
    p_glBindTextureUnit(SCALE_UNIT + 1, g4.rampTex); p_glBindSampler(SCALE_UNIT + 1, g4.nearestSmp);
    p_glProgramUniform4i(prog, 0, (GLint)s->x, (GLint)s->y, (GLint)s->w, (GLint)s->h);
    p_glProgramUniform4i(prog, 1, (GLint)d->x, (GLint)d->y, (GLint)d->w, (GLint)d->h);
    p_glProgramUniform2i(prog, 2, 0, (GLint)zlayer);
    p_glProgramUniform1i(prog, 3, mode);
    p_glDrawArraysInstancedBaseInstance(GL_TRIANGLES, 0, 3, 1, 0);
}

void gl4_scale_point(const Gl4Sub* s, const Gl4Sub* d, const uint16_t* ramp) {
    const Gl4Fmt f = gl4_fmt(s->im->fmt);
    uint32_t kind = f.depth ? P_DEPTH : s->im->target == GL_TEXTURE_CUBE_MAP ? P_CARR : s->im->target == GL_TEXTURE_3D ? P_C3D : P_C2D;
    gl4_query_pause();
    ensure_objects();
    gl4_neutral_state();
    gl4_detach_all(g4.blitFbo[1]);
    GLenum att = f.depth ? (f.glds ? GL_DEPTH_STENCIL_ATTACHMENT : GL_DEPTH_ATTACHMENT) : GL_COLOR_ATTACHMENT0;
    gl4_attach(g4.blitFbo[1], att, d->im, d->level, d->layer);
    p_glNamedFramebufferDrawBuffer(g4.blitFbo[1], f.depth ? GL_NONE : GL_COLOR_ATTACHMENT0);
    p_glBindFramebuffer(GL_FRAMEBUFFER, g4.blitFbo[1]);
    p_glBindVertexArray(g4.lay[0].vao);
    p_glViewportIndexedf(0, (float)d->x, (float)d->y, (float)d->w, (float)d->h);
    p_glEnable(GL_SCISSOR_TEST);
    p_glScissor((GLint)d->x, (GLint)d->y, (GLsizei)d->w, (GLsizei)d->h);
    p_glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    if (ramp) p_glTextureSubImage2D(g4.rampTex, 0, 0, 0, 256, 3, GL_RED_INTEGER, GL_UNSIGNED_SHORT, ramp);
    GLuint view = source_view(s, 0), prog = program((int)kind);
    uint32_t zl = s->im->target == GL_TEXTURE_CUBE_MAP || s->im->target == GL_TEXTURE_3D ? s->layer : 0;
    if (!f.depth) {
        draw(prog, view, s, d, zl, ramp != NULL);
    } else {
        p_glEnable(GL_DEPTH_TEST); p_glDepthFunc(GL_ALWAYS); p_glDepthMask(GL_TRUE);
        draw(prog, view, s, d, 0, 0);
        p_glDisable(GL_DEPTH_TEST);
        if (f.glds) {
            GLuint sview = source_view(s, 1);
            const GLint zero = 0;
            p_glStencilMaskSeparate(GL_FRONT_AND_BACK, 0xFF);
            p_glClearBufferiv(GL_STENCIL, 0, &zero);
            p_glEnable(GL_STENCIL_TEST);
            p_glStencilFuncSeparate(GL_FRONT_AND_BACK, GL_ALWAYS, 0xFF, 0xFF);
            p_glStencilOpSeparate(GL_FRONT_AND_BACK, GL_REPLACE, GL_REPLACE, GL_REPLACE);
            for (int b = 0; b < 8; b++) {
                p_glStencilMaskSeparate(GL_FRONT_AND_BACK, 1u << b);
                draw(program(P_STENCIL), sview, s, d, 0, b);
            }
            p_glDeleteTextures(1, &sview);
        }
    }
    p_glDeleteTextures(1, &view);
    gl4_detach_all(g4.blitFbo[1]);
    p_glBindFramebuffer(GL_FRAMEBUFFER, 0);
    gl4_neutral_state();
    gl4_query_resume();
}
