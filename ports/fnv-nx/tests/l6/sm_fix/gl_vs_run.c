/* gl_vs_run <vs.glsl> <consts.bin> [loc] [ps.glsl]: runs a translated D3D9 vertex shader (glsldk profile) on llvmpipe (EGL surfaceless,
 * GL 4.5 core) for one GL_POINTS vertex into a 1x1 RGBA32F target and prints the four floats of its varying at
 * location `FNVNX_LOC` (argv[3], default 2 = TEXCOORD0) as hex bits and values. Contract: UBO0 = vec4 c[256] (consts.bin,
 * 256*16 bytes), UBO2 = vs driver block (posfix = 0,0,1,0). The shader must write o0 = (0,0,0,1) so the point lands on the pixel. */
#define GL_GLEXT_PROTOTYPES 1
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/gl.h>
#include <GL/glext.h>
static char *slurp(const char *p, long *len) { FILE *f = fopen(p, "rb"); if (!f) return NULL; fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    char *b = malloc(n + 1); if (fread(b, 1, n, f) != (size_t)n) return NULL; b[n] = 0; fclose(f); if (len) *len = n; return b; }
static GLuint compile(GLenum t, const char *src)
{
    GLuint s = glCreateShader(t); glShaderSource(s, 1, &src, NULL); glCompileShader(s);
    GLint ok; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) { char log[4096]; glGetShaderInfoLog(s, sizeof log, NULL, log); fprintf(stderr, "shader compile failed:\n%s\n", log); exit(3); }
    return s;
}
int main(int argc, char **argv)
{
    if (argc < 3) return 2;
    int loc = argc > 3 ? atoi(argv[3]) : 2;
    PFNEGLGETPLATFORMDISPLAYEXTPROC getpd = (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    EGLDisplay dpy = getpd ? getpd(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL) : eglGetDisplay(EGL_DEFAULT_DISPLAY);
    EGLint maj, min;
    if (!eglInitialize(dpy, &maj, &min)) { fprintf(stderr, "eglInitialize failed\n"); return 4; }
    eglBindAPI(EGL_OPENGL_API);
    EGLint ca[] = { EGL_CONTEXT_MAJOR_VERSION, 4, EGL_CONTEXT_MINOR_VERSION, 5, EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL_NONE };
    EGLContext ctx = eglCreateContext(dpy, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, ca);
    if (ctx == EGL_NO_CONTEXT || !eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, ctx)) { fprintf(stderr, "no GL context\n"); return 4; }
    char *vs = slurp(argv[1], NULL), *cb = NULL; long cn = 0; cb = slurp(argv[2], &cn);
    if (!vs || !cb || cn != 4096) return 2;
    char fsb[256]; snprintf(fsb, sizeof fsb, "#version 450\nlayout(location=%d) in vec4 t; layout(location=0) out vec4 o; void main(){ o = t; }\n", loc);
    const char *fs = argc > 4 ? slurp(argv[4], NULL) : fsb;            /* argv[4]: a translated pixel shader instead */
    if (!fs) return 2;
    GLuint prog = glCreateProgram();
    glAttachShader(prog, compile(GL_VERTEX_SHADER, vs)); glAttachShader(prog, compile(GL_FRAGMENT_SHADER, fs));
    glLinkProgram(prog);
    GLint ok; glGetProgramiv(prog, GL_LINK_STATUS, &ok);
    if (!ok) { char log[2048]; glGetProgramInfoLog(prog, sizeof log, NULL, log); fprintf(stderr, "link failed:\n%s\n", log); return 3; }
    glUseProgram(prog);
    GLuint vao, fbo, rb, ubo[2];
    glGenVertexArrays(1, &vao); glBindVertexArray(vao);
    glGenRenderbuffers(1, &rb); glBindRenderbuffer(GL_RENDERBUFFER, rb); glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA32F, 1, 1);
    glGenFramebuffers(1, &fbo); glBindFramebuffer(GL_FRAMEBUFFER, fbo); glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, rb);
    glViewport(0, 0, 1, 1); glDisable(GL_CULL_FACE); glDisable(GL_BLEND); glClampColor(GL_CLAMP_READ_COLOR, GL_FALSE);
    glGenBuffers(2, ubo);
    float drv[512] = { 0, 0, 1, 0 };                                   /* VS: posfix; PS: its driver block reads zeros (alpha test off) */
    glBindBuffer(GL_UNIFORM_BUFFER, ubo[0]); glBufferData(GL_UNIFORM_BUFFER, 4096, cb, GL_STATIC_DRAW); glBindBufferBase(GL_UNIFORM_BUFFER, 0, ubo[0]);
    glBindBuffer(GL_UNIFORM_BUFFER, ubo[1]); glBufferData(GL_UNIFORM_BUFFER, sizeof drv, drv, GL_STATIC_DRAW); glBindBufferBase(GL_UNIFORM_BUFFER, 2, ubo[1]);
    glClearColor(-1e9f, -1e9f, -1e9f, -1e9f); glClear(GL_COLOR_BUFFER_BIT);
    glDrawArrays(GL_POINTS, 0, 1);
    float got[4]; glReadPixels(0, 0, 1, 1, GL_RGBA, GL_FLOAT, got);
    printf("%.9g %.9g %.9g %.9g\n", got[0], got[1], got[2], got[3]);
    return 0;
}
