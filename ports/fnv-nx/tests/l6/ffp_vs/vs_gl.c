/* vs_gl <records.bin> <flattened ffp.vert> : runs ffp.vert.glsl on EGL surfaceless (GL 4.5 core, Mesa llvmpipe) for every
 * VsRec - one point, rasterizer discard, outputs read back through transform feedback (gl_Position, the varyings,
 * gl_PointSize, gl_ClipDistance[0..5]) - and compares with nx_ffp_vs_eval. State/palette/posfix go through the real UBO
 * layouts (the struct IS the block). Absent inputs are bound to the record's junk values on purpose (presence comes from
 * the block). Tolerance |gl - cpu| <= 1e-4 * max(1, |cpu|). Exit 1 on any mismatch. */
#define GL_GLEXT_PROTOTYPES 1
#include <math.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/gl.h>
#include <GL/glext.h>
#include "vs_common.h"

static char *slurp(const char *p) { FILE *f = fopen(p, "rb"); if (!f) return NULL; fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    char *b = malloc(n + 1); if (fread(b, 1, n, f) != (size_t)n) return NULL; b[n] = 0; fclose(f); return b; }

int main(int argc, char **argv)
{
    if (argc < 3) return 2;
    PFNEGLGETPLATFORMDISPLAYEXTPROC getpd = (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    EGLDisplay dpy = getpd ? getpd(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL) : eglGetDisplay(EGL_DEFAULT_DISPLAY);
    EGLint maj, min;
    if (!eglInitialize(dpy, &maj, &min)) { fprintf(stderr, "eglInitialize failed 0x%x\n", eglGetError()); return 4; }
    eglBindAPI(EGL_OPENGL_API);
    EGLint ca[] = { EGL_CONTEXT_MAJOR_VERSION, 4, EGL_CONTEXT_MINOR_VERSION, 5, EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL_NONE };
    EGLContext ctx = eglCreateContext(dpy, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, ca);
    if (ctx == EGL_NO_CONTEXT || !eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, ctx)) { fprintf(stderr, "no GL context 0x%x\n", eglGetError()); return 4; }
    fprintf(stderr, "GL: %s | %s\n", glGetString(GL_RENDERER), glGetString(GL_VERSION));

    char *vs = slurp(argv[2]); if (!vs) return 2;
    if (!strncmp(vs, "#version 460", 12)) memcpy(vs, "#version 450", 12);
    GLuint sh = glCreateShader(GL_VERTEX_SHADER); glShaderSource(sh, 1, (const char **)&vs, NULL); glCompileShader(sh);
    GLint ok; glGetShaderiv(sh, GL_COMPILE_STATUS, &ok);
    if (!ok) { char log[4096]; glGetShaderInfoLog(sh, sizeof log, NULL, log); fprintf(stderr, "compile failed:\n%s\n", log); return 3; }
    GLuint prog = glCreateProgram(); glAttachShader(prog, sh);
    const char *names[] = { "gl_Position", "oColor0", "oColor1", "oTex0", "oTex1", "oTex2", "oTex3", "oTex4", "oTex5", "oTex6", "oTex7",
                            "oFog", "gl_PointSize", "gl_ClipDistance[0]", "gl_ClipDistance[1]", "gl_ClipDistance[2]",
                            "gl_ClipDistance[3]", "gl_ClipDistance[4]", "gl_ClipDistance[5]" };
    glTransformFeedbackVaryings(prog, 19, names, GL_INTERLEAVED_ATTRIBS);
    glLinkProgram(prog); glGetProgramiv(prog, GL_LINK_STATUS, &ok);
    if (!ok) { char log[4096]; glGetProgramInfoLog(prog, sizeof log, NULL, log); fprintf(stderr, "link failed:\n%s\n", log); return 3; }
    glUseProgram(prog);

    GLuint vao, ubo[3], xfb; glGenVertexArrays(1, &vao); glBindVertexArray(vao);
    glGenBuffers(3, ubo); glGenBuffers(1, &xfb);
    glBindBuffer(GL_UNIFORM_BUFFER, ubo[0]); glBufferData(GL_UNIFORM_BUFFER, 16, NULL, GL_DYNAMIC_DRAW); glBindBufferBase(GL_UNIFORM_BUFFER, 2, ubo[0]);
    glBindBuffer(GL_UNIFORM_BUFFER, ubo[1]); glBufferData(GL_UNIFORM_BUFFER, sizeof(NxFfpVsState), NULL, GL_DYNAMIC_DRAW); glBindBufferBase(GL_UNIFORM_BUFFER, NX_FFP_VS_UBO_BINDING, ubo[1]);
    glBindBuffer(GL_UNIFORM_BUFFER, ubo[2]); glBufferData(GL_UNIFORM_BUFFER, sizeof g_palette, NULL, GL_DYNAMIC_DRAW); glBindBufferBase(GL_UNIFORM_BUFFER, NX_FFP_VS_PALETTE_BINDING, ubo[2]);
    glBindBuffer(GL_TRANSFORM_FEEDBACK_BUFFER, xfb); glBufferData(GL_TRANSFORM_FEEDBACK_BUFFER, 64 * 4, NULL, GL_DYNAMIC_READ);
    glBindBufferBase(GL_TRANSFORM_FEEDBACK_BUFFER, 0, xfb);
    GLuint fbo, rb;                                   /* surfaceless: a complete 1x1 framebuffer is still required */
    glGenRenderbuffers(1, &rb); glBindRenderbuffer(GL_RENDERBUFFER, rb); glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA8, 1, 1);
    glGenFramebuffers(1, &fbo); glBindFramebuffer(GL_FRAMEBUFFER, fbo); glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, rb);
    glEnable(GL_RASTERIZER_DISCARD);

    FILE *f = fopen(argv[1], "rb"); VsRec r; int n = 0, bad = 0; double maxrel = 0;
    while (f && fread(&r, sizeof r, 1, f) == 1) {
        NxFfpVsState st; NxFfpVsIn in; NxFfpVsOut o; float want[OUT_WORDS], got[64];
        rec_expand(&r, &st, &in);
        nx_ffp_vs_eval(&st, (const float (*)[16])g_palette, r.posfix, &in, &o); out_flat(&o, want);
        glBindBuffer(GL_UNIFORM_BUFFER, ubo[0]); glBufferSubData(GL_UNIFORM_BUFFER, 0, 16, r.posfix);
        glBindBuffer(GL_UNIFORM_BUFFER, ubo[1]); glBufferSubData(GL_UNIFORM_BUFFER, 0, sizeof st, &st);
        glBindBuffer(GL_UNIFORM_BUFFER, ubo[2]); glBufferSubData(GL_UNIFORM_BUFFER, 0, sizeof g_palette, g_palette);
        const int tw = st.rsBlend[0] == 255;                           /* tweening aliases locations 1, 2 */
        glVertexAttrib4fv(0, in.pos);
        glVertexAttrib4fv(1, tw ? in.pos1 : in.blendweight); glVertexAttrib4fv(2, tw ? in.normal1 : in.blendindex);
        glVertexAttrib4fv(3, in.normal); glVertexAttrib4fv(4, in.color0); glVertexAttrib4fv(5, in.color1);
        for (int s = 0; s < 8; s++) glVertexAttrib4fv(6 + s, in.tex[s]);
        float ps[4] = { in.psize, 0, 0, 1 }; glVertexAttrib4fv(14, ps);
        GLuint q; glGenQueries(1, &q); glBeginQuery(GL_TRANSFORM_FEEDBACK_PRIMITIVES_WRITTEN, q);
        glBeginTransformFeedback(GL_POINTS); glDrawArrays(GL_POINTS, 0, 1); glEndTransformFeedback();
        glEndQuery(GL_TRANSFORM_FEEDBACK_PRIMITIVES_WRITTEN); GLuint pw = 9; glGetQueryObjectuiv(q, GL_QUERY_RESULT, &pw); glDeleteQueries(1, &q);
        if (pw != 1) { printf("  case %d: transform feedback wrote %u primitives (GL error 0x%x)\n", n, pw, glGetError()); bad++; n++; continue; }
        glBindBuffer(GL_TRANSFORM_FEEDBACK_BUFFER, xfb); glGetBufferSubData(GL_TRANSFORM_FEEDBACK_BUFFER, 0, 55 * 4, got);
        /* xfb layout: position4 color0 4 color1 4 tex 32 fog4 psize clip6 -> flat order of out_flat */
        float g[OUT_WORDS]; memcpy(g, got, 44 * 4); g[44] = got[44]; g[45] = got[48]; memcpy(g + 46, got + 49, 24);
        int badc = -1;
        for (int k = 0; k < OUT_WORDS; k++) {
            double rel = fabs((double)g[k] - want[k]) / fmax(1.0, fabs((double)want[k]));
            if (!(rel <= 1e-4) && !(isnan(g[k]) && isnan(want[k])) && !(g[k] == want[k])) { badc = k; break; }
            if (rel > maxrel && rel < 1e30) maxrel = rel;
        }
        if (badc >= 0) { if (bad < 5) printf("  GL mismatch case %d word %d: gl %.7g cpu %.7g\n", n, badc, g[badc], want[badc]); bad++; }
        n++;
    }
    printf("(c) GL(%s): cases %d, mismatches (> 1e-4 rel): %d, max rel error of matching words %.3g\n", (const char *)glGetString(GL_RENDERER), n, bad, maxrel);
    return bad != 0;
}
