/* gl_run <ps.glsl> <spec> [vs.glsl]: runs a translated D3D9 pixel shader (glsldk profile) on llvmpipe (EGL surfaceless, GL 4.5 core) for one
 * GL_POINTS vertex into a 1x1 RGBA32F + DEPTH32F target and prints "r g b a depth" (%.9g).
 * Vertex stage: argv[3] (a translated vertex shader) or the built-in one: position (0,0,0,1) (window z = 0.5), varyings at location
 * 2+i = c[240+i] (i = 0..7, TEXCOORDi), location 0/1 = c[248]/c[249] (COLOR0/1), location 18 = c[250] (fog).
 * Spec (whitespace separated words, '#' to end of line = comment):
 *   c N x y z w                    float constant c[N] (UBO 0, shared by both stages)
 *   drvf W v                       32-bit word W of the driver block (UBO 2, shared by both stages) = float v
 *   drvu W v                       ... = uint v
 *   t2 U w h  rgba*(w*h)           2D texture on unit U (RGBA32F, NEAREST, clamp)
 *   tc U s    rgba*(6*s*s)         cube map on unit U, faces +X -X +Y -Y +Z -Z
 *   t3 U w h d rgba*(w*h*d)        3D texture on unit U
 *   depth v                        clear value of the depth buffer (default 0.9)
 * Contract: sampler binding = unit (fnvnx_dk_layout.h; the ps_1_x CUBE/VOLUME numbering 8+n / 16+n is the caller's spec). */
#define GL_GLEXT_PROTOTYPES 1
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/gl.h>
#include <GL/glext.h>
static char *slurp(const char *p)
{
    FILE *f = fopen(p, "rb"); if (!f) return NULL; fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    char *b = malloc(n + 1); if (fread(b, 1, n, f) != (size_t)n) return NULL; b[n] = 0; fclose(f); return b;
}
static GLuint compile(GLenum t, const char *src)
{
    GLuint s = glCreateShader(t); glShaderSource(s, 1, &src, NULL); glCompileShader(s);
    GLint ok; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) { char log[4096]; glGetShaderInfoLog(s, sizeof log, NULL, log); fprintf(stderr, "shader compile failed:\n%s\n", log); exit(3); }
    return s;
}
static const char *BUILTIN_VS =
    "#version 450 core\n"
    "layout(std140, binding = 0) uniform bvs_cbuf_f { vec4 c[256]; };\n"
    "layout(location = 0) out vec4 o0; layout(location = 1) out vec4 o1; layout(location = 18) out vec4 o18;\n"
    "layout(location = 2) out vec4 t0; layout(location = 3) out vec4 t1; layout(location = 4) out vec4 t2; layout(location = 5) out vec4 t3;\n"
    "layout(location = 6) out vec4 t4; layout(location = 7) out vec4 t5; layout(location = 8) out vec4 t6; layout(location = 9) out vec4 t7;\n"
    "void main() { gl_Position = vec4(0.0, 0.0, 0.0, 1.0); o0 = c[248]; o1 = c[249]; o18 = c[250];\n"
    "  t0 = c[240]; t1 = c[241]; t2 = c[242]; t3 = c[243]; t4 = c[244]; t5 = c[245]; t6 = c[246]; t7 = c[247]; }\n";
static float *readf(FILE *f, int n) { float *p = malloc(n * 4 * sizeof(float)); for (int i = 0; i < n * 4; i++) if (fscanf(f, "%f", &p[i]) != 1) exit(2); return p; }
int main(int argc, char **argv)
{
    if (argc < 3) return 2;
    PFNEGLGETPLATFORMDISPLAYEXTPROC getpd = (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    EGLDisplay dpy = getpd ? getpd(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL) : eglGetDisplay(EGL_DEFAULT_DISPLAY);
    EGLint maj, min;
    if (!eglInitialize(dpy, &maj, &min)) { fprintf(stderr, "eglInitialize failed\n"); return 4; }
    eglBindAPI(EGL_OPENGL_API);
    EGLint ca[] = { EGL_CONTEXT_MAJOR_VERSION, 4, EGL_CONTEXT_MINOR_VERSION, 5, EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL_NONE };
    EGLContext ctx = eglCreateContext(dpy, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, ca);
    if (ctx == EGL_NO_CONTEXT || !eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, ctx)) { fprintf(stderr, "no GL context\n"); return 4; }
    char *ps = slurp(argv[1]); const char *vs = argc > 3 ? slurp(argv[3]) : BUILTIN_VS;
    if (!ps || !vs) return 2;
    GLuint prog = glCreateProgram();
    glAttachShader(prog, compile(GL_VERTEX_SHADER, vs)); glAttachShader(prog, compile(GL_FRAGMENT_SHADER, ps));
    glLinkProgram(prog);
    GLint ok; glGetProgramiv(prog, GL_LINK_STATUS, &ok);
    if (!ok) { char log[2048]; glGetProgramInfoLog(prog, sizeof log, NULL, log); fprintf(stderr, "link failed:\n%s\n", log); return 3; }
    glUseProgram(prog);
    GLuint vao, fbo, rb, db, ubo[2];
    glGenVertexArrays(1, &vao); glBindVertexArray(vao);
    glGenRenderbuffers(1, &rb); glBindRenderbuffer(GL_RENDERBUFFER, rb); glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA32F, 1, 1);
    glGenRenderbuffers(1, &db); glBindRenderbuffer(GL_RENDERBUFFER, db); glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT32F, 1, 1);
    glGenFramebuffers(1, &fbo); glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, rb);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER, db);
    glViewport(0, 0, 1, 1); glDisable(GL_CULL_FACE); glDisable(GL_BLEND);
    glEnable(GL_DEPTH_TEST); glDepthFunc(GL_ALWAYS); glDepthMask(GL_TRUE); glClampColor(GL_CLAMP_READ_COLOR, GL_FALSE);
    float cst[1024]; unsigned drv[512]; memset(cst, 0, sizeof cst); memset(drv, 0, sizeof drv);
    ((float *)drv)[2] = 1.0f;                                          /* VS posfix.z = 1 (shared block; the PS reads it as fogcolor.z, fog off) */
    float clear_depth = 0.9f;
    FILE *f = fopen(argv[2], "r"); if (!f) return 2;
    char w[64];
    while (fscanf(f, "%63s", w) == 1) {
        if (w[0] == '#') { int ch; while ((ch = fgetc(f)) != EOF && ch != '\n') ; continue; }
        if (!strcmp(w, "c")) { int n; if (fscanf(f, "%d", &n) != 1 || n < 0 || n > 255) return 2; for (int i = 0; i < 4; i++) if (fscanf(f, "%f", &cst[n * 4 + i]) != 1) return 2; }
        else if (!strcmp(w, "drvf")) { int n; float v; if (fscanf(f, "%d %f", &n, &v) != 2) return 2; memcpy(&drv[n], &v, 4); }
        else if (!strcmp(w, "drvu")) { int n; unsigned v; if (fscanf(f, "%d %u", &n, &v) != 2) return 2; drv[n] = v; }
        else if (!strcmp(w, "depth")) { if (fscanf(f, "%f", &clear_depth) != 1) return 2; }
        else if (!strcmp(w, "t2") || !strcmp(w, "tc") || !strcmp(w, "t3")) {
            int u, a, b = 0, d = 0; GLuint t; glGenTextures(1, &t); glActiveTexture(GL_TEXTURE0 + 0);
            if (fscanf(f, "%d", &u) != 1 || fscanf(f, "%d", &a) != 1) return 2;
            glActiveTexture(GL_TEXTURE0 + u);
            if (!strcmp(w, "t2")) { if (fscanf(f, "%d", &b) != 1) return 2; float *p = readf(f, a * b);
                glBindTexture(GL_TEXTURE_2D, t); glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA32F, a, b, 0, GL_RGBA, GL_FLOAT, p);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE); }
            else if (!strcmp(w, "tc")) { glBindTexture(GL_TEXTURE_CUBE_MAP, t);
                for (int face = 0; face < 6; face++) { float *p = readf(f, a * a); glTexImage2D(GL_TEXTURE_CUBE_MAP_POSITIVE_X + face, 0, GL_RGBA32F, a, a, 0, GL_RGBA, GL_FLOAT, p); }
                glTexParameteri(GL_TEXTURE_CUBE_MAP, GL_TEXTURE_MIN_FILTER, GL_NEAREST); glTexParameteri(GL_TEXTURE_CUBE_MAP, GL_TEXTURE_MAG_FILTER, GL_NEAREST); }
            else { if (fscanf(f, "%d %d", &b, &d) != 2) return 2; float *p = readf(f, a * b * d);
                glBindTexture(GL_TEXTURE_3D, t); glTexImage3D(GL_TEXTURE_3D, 0, GL_RGBA32F, a, b, d, 0, GL_RGBA, GL_FLOAT, p);
                glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MIN_FILTER, GL_NEAREST); glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
                glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_R, GL_CLAMP_TO_EDGE); }
        } else { fprintf(stderr, "bad spec word %s\n", w); return 2; }
    }
    glGenBuffers(2, ubo);
    glBindBuffer(GL_UNIFORM_BUFFER, ubo[0]); glBufferData(GL_UNIFORM_BUFFER, 4096, cst, GL_STATIC_DRAW); glBindBufferBase(GL_UNIFORM_BUFFER, 0, ubo[0]);
    glBindBuffer(GL_UNIFORM_BUFFER, ubo[1]); glBufferData(GL_UNIFORM_BUFFER, sizeof drv, drv, GL_STATIC_DRAW); glBindBufferBase(GL_UNIFORM_BUFFER, 2, ubo[1]);
    glClearColor(-1e9f, -1e9f, -1e9f, -1e9f); glClearDepth(clear_depth); glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
    glDrawArrays(GL_POINTS, 0, 1);
    float got[4], dep; glReadPixels(0, 0, 1, 1, GL_RGBA, GL_FLOAT, got); glReadPixels(0, 0, 1, 1, GL_DEPTH_COMPONENT, GL_FLOAT, &dep);
    printf("%.9g %.9g %.9g %.9g %.9g\n", got[0], got[1], got[2], got[3], dep);
    return 0;
}
