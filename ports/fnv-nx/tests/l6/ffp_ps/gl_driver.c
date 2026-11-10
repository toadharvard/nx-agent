/* gl_driver <cases.bin> <flattened ffp.frag>: runs ffp.frag.glsl on a 1x1 RGBA32F target (EGL surfaceless, GL 4.5+ core,
 * Mesa llvmpipe) for every Rec of the file and compares with nx_ffp_ps_eval (1/255). Textures are constant colours per
 * (stage, dimensionality): the GL gap is that sampling position-dependence (bump / projective) is covered by the
 * CPU-vs-oracle tests only. The state block is built by nx_ffp_ps_state_from_d3d9 (so the converter is exercised too). */
#define GL_GLEXT_PROTOTYPES 1
#include <stdio.h>
#include <stdlib.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/gl.h>
#include <GL/glext.h>
#include "ffp_test_common.h"

static float texcol(uint32_t s, uint32_t t, int k) { return (float)((s * 37u + t * 91u + (uint32_t)k * 53u + 17u) % 256u) / 255.0f; }
static void sample_cc(void *u, uint32_t st, uint32_t type, const float c[3], float o[4])
{ (void)u; (void)c; for (int k = 0; k < 4; k++) o[k] = texcol(st, type, k); }

static char *slurp(const char *p) { FILE *f = fopen(p, "rb"); if (!f) return NULL; fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    char *b = malloc(n + 1); if (fread(b, 1, n, f) != (size_t)n) return NULL; b[n] = 0; fclose(f); return b; }

static GLuint compile(GLenum t, const char *src)
{
    GLuint s = glCreateShader(t); glShaderSource(s, 1, &src, NULL); glCompileShader(s);
    GLint ok; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) { char log[2048]; glGetShaderInfoLog(s, sizeof log, NULL, log); fprintf(stderr, "shader compile failed:\n%s\n", log); exit(3); }
    return s;
}

static const char *VS =
    "#version 450\n"
    "uniform vec4 uD, uS, uT[8];\n"
    "layout(location=0) out vec4 oD; layout(location=1) out vec4 oS; layout(location=18) out vec4 oF;\n"
    "layout(location=2) out vec4 t0; layout(location=3) out vec4 t1; layout(location=4) out vec4 t2; layout(location=5) out vec4 t3;\n"
    "layout(location=6) out vec4 t4; layout(location=7) out vec4 t5; layout(location=8) out vec4 t6; layout(location=9) out vec4 t7;\n"
    "void main(){ vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);\n"
    "  oD = uD; oS = uS; oF = vec4(0.0); t0 = uT[0]; t1 = uT[1]; t2 = uT[2]; t3 = uT[3]; t4 = uT[4]; t5 = uT[5]; t6 = uT[6]; t7 = uT[7]; }\n";

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

    char *fs = slurp(argv[2]); if (!fs) return 2;
    if (!strncmp(fs, "#version 460", 12)) memcpy(fs, "#version 450", 12);   /* what nxgl_shim_glsl does */
    GLuint prog = glCreateProgram();
    glAttachShader(prog, compile(GL_VERTEX_SHADER, VS)); glAttachShader(prog, compile(GL_FRAGMENT_SHADER, fs));
    glLinkProgram(prog);
    GLint ok; glGetProgramiv(prog, GL_LINK_STATUS, &ok);
    if (!ok) { char log[2048]; glGetProgramInfoLog(prog, sizeof log, NULL, log); fprintf(stderr, "link failed:\n%s\n", log); return 3; }
    glUseProgram(prog);

    GLuint vao, fbo, rb, ubo[2];
    glGenVertexArrays(1, &vao); glBindVertexArray(vao);
    glGenRenderbuffers(1, &rb); glBindRenderbuffer(GL_RENDERBUFFER, rb); glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA32F, 1, 1);
    glGenFramebuffers(1, &fbo); glBindFramebuffer(GL_FRAMEBUFFER, fbo); glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, rb);
    glViewport(0, 0, 1, 1); glDisable(GL_CULL_FACE); glDisable(GL_BLEND);
    glClampColor(GL_CLAMP_READ_COLOR, GL_FALSE);
    for (int s = 0; s < 8; s++) for (int t = 1; t <= 3; t++) {          /* texture units: 2D n, CUBE 8+n, VOLUME 16+n */
        GLenum tgt = t == 1 ? GL_TEXTURE_2D : t == 2 ? GL_TEXTURE_CUBE_MAP : GL_TEXTURE_3D;
        GLuint tex; unsigned char px[4];
        for (int k = 0; k < 4; k++) px[k] = (unsigned char)(texcol(s, t, k) * 255.0f + 0.5f);
        glActiveTexture(GL_TEXTURE0 + (t - 1) * 8 + s); glGenTextures(1, &tex); glBindTexture(tgt, tex);
        if (t == 1) glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 1, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, px);
        else if (t == 2) for (int f = 0; f < 6; f++) glTexImage2D(GL_TEXTURE_CUBE_MAP_POSITIVE_X + f, 0, GL_RGBA8, 1, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, px);
        else glTexImage3D(GL_TEXTURE_3D, 0, GL_RGBA8, 1, 1, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, px);
        glTexParameteri(tgt, GL_TEXTURE_MIN_FILTER, GL_NEAREST); glTexParameteri(tgt, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    }
    glGenBuffers(2, ubo);
    float zeros[80] = {0};                                              /* d9_driver block: alpha test off, fog off */
    glBindBuffer(GL_UNIFORM_BUFFER, ubo[0]); glBufferData(GL_UNIFORM_BUFFER, sizeof zeros, zeros, GL_STATIC_DRAW); glBindBufferBase(GL_UNIFORM_BUFFER, 2, ubo[0]);
    glBindBuffer(GL_UNIFORM_BUFFER, ubo[1]); glBufferData(GL_UNIFORM_BUFFER, sizeof(NxFfpPsState), NULL, GL_DYNAMIC_DRAW); glBindBufferBase(GL_UNIFORM_BUFFER, NX_FFP_UBO_BINDING, ubo[1]);
    GLint lD = glGetUniformLocation(prog, "uD"), lS = glGetUniformLocation(prog, "uS"), lT = glGetUniformLocation(prog, "uT");

    FILE *f = fopen(argv[1], "rb"); Rec r; int n = 0, bad = 0; double maxerr = 0;
    while (fread(&r, sizeof r, 1, f) == 1) {
        NxFfpPsState st; NxFfpPsInputs in; float want[4], got[4];
        rec_to_state(&r, &st, &in); in.sample = sample_cc; in.user = 0;
        nx_ffp_ps_eval(&st, &in, want);
        glBindBuffer(GL_UNIFORM_BUFFER, ubo[1]); glBufferSubData(GL_UNIFORM_BUFFER, 0, sizeof st, &st);
        glUniform4fv(lD, 1, in.diffuse); glUniform4fv(lS, 1, in.specular); glUniform4fv(lT, 8, &in.texcoord[0][0]);
        glClearColor(-1, -1, -1, -1); glClear(GL_COLOR_BUFFER_BIT);
        glDrawArrays(GL_TRIANGLES, 0, 3);
        glReadPixels(0, 0, 1, 1, GL_RGBA, GL_FLOAT, got);
        double e = 0; for (int k = 0; k < 4; k++) e = fmax(e, fabs((double)got[k] - want[k]));
        if (e > maxerr) maxerr = e;
        if (e > 1.0 / 255.0) { if (bad < 5) printf("  GL mismatch case %d: gl %.4f %.4f %.4f %.4f ref %.4f %.4f %.4f %.4f\n", n, got[0], got[1], got[2], got[3], want[0], want[1], want[2], want[3]); bad++; }
        n++;
    }
    printf("(c) GL(%s): cases %d, mismatches > 1/255: %d, max abs error %.5f\n", (const char *)glGetString(GL_RENDERER), n, bad, maxerr);
    return bad != 0;
}
