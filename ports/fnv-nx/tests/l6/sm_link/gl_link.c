/* gl_link.c - links and runs (translated VS, translated PS) pairs on Mesa llvmpipe (desktop GL 4.5 core, EGL surfaceless) through the path the
 * GL reference backend takes: render/native/gl/nxgl.c creates the context (glClipControl(LOWER_LEFT, ZERO_TO_ONE)) and nxgl_shim_glsl() rewrites the
 * GLSL (per-stage UBO bindings stage * 16 + n, per-stage block names), exactly as tests/l6/shcache/glcheck.c does.
 *   LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe EGL_PLATFORM=surfaceless gl_link JOBS
 * JOBS: whitespace separated words, '#' to end of line = comment. One job:
 *   job ID VS.glsl PS.glsl      starts a job (state is reset: all constants and driver words 0, vs posfix.z = 1)
 *   vc N x y z w                VS float constant c[N] (UBO 0 of the VS stage)
 *   pc N x y z w                PS float constant c[N]
 *   vd W v                      VS driver block word W (UBO 2, VS stage) = float v
 *   pd W v                      PS driver block word W (UBO 2, PS stage) = float v
 *   pdu W v                     PS driver block word W = uint v
 *   go                          link, draw one GL_POINTS vertex into a 1x1 RGBA32F + DEPTH32F target, print the result
 * Output, one line per job: "ID ok r g b a depth" (%.9g) | "ID compile_fail <log>" | "ID link_fail <log>". Exit 0 when the file was processed. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <EGL/egl.h>
#include <GL/glcorearb.h>
#include "gl/nxgl.h"

#define FN(ret, name, args) static ret (*name) args
FN(GLuint, glCreateShader, (GLenum)); FN(void, glShaderSource, (GLuint, GLsizei, const GLchar *const *, const GLint *)); FN(void, glCompileShader, (GLuint));
FN(void, glGetShaderiv, (GLuint, GLenum, GLint *)); FN(void, glGetShaderInfoLog, (GLuint, GLsizei, GLsizei *, GLchar *)); FN(void, glDeleteShader, (GLuint));
FN(GLuint, glCreateProgram, (void)); FN(void, glAttachShader, (GLuint, GLuint)); FN(void, glLinkProgram, (GLuint)); FN(void, glDeleteProgram, (GLuint));
FN(void, glGetProgramiv, (GLuint, GLenum, GLint *)); FN(void, glGetProgramInfoLog, (GLuint, GLsizei, GLsizei *, GLchar *)); FN(void, glUseProgram, (GLuint));
FN(void, glGenVertexArrays, (GLsizei, GLuint *)); FN(void, glBindVertexArray, (GLuint));
FN(void, glGenRenderbuffers, (GLsizei, GLuint *)); FN(void, glBindRenderbuffer, (GLenum, GLuint)); FN(void, glRenderbufferStorage, (GLenum, GLenum, GLsizei, GLsizei));
FN(void, glGenFramebuffers, (GLsizei, GLuint *)); FN(void, glBindFramebuffer, (GLenum, GLuint)); FN(void, glFramebufferRenderbuffer, (GLenum, GLenum, GLenum, GLuint));
FN(void, glViewport, (GLint, GLint, GLsizei, GLsizei)); FN(void, glEnable, (GLenum)); FN(void, glDisable, (GLenum)); FN(void, glDepthFunc, (GLenum)); FN(void, glDepthMask, (GLboolean));
FN(void, glClearColor, (GLfloat, GLfloat, GLfloat, GLfloat)); FN(void, glClearDepth, (GLdouble)); FN(void, glClear, (GLbitfield));
FN(void, glGenBuffers, (GLsizei, GLuint *)); FN(void, glBindBuffer, (GLenum, GLuint)); FN(void, glBufferData, (GLenum, GLsizeiptr, const void *, GLenum));
FN(void, glBindBufferBase, (GLenum, GLuint, GLuint)); FN(void, glDrawArrays, (GLenum, GLint, GLsizei)); FN(void, glReadPixels, (GLint, GLint, GLsizei, GLsizei, GLenum, GLenum, void *));
FN(void, glClampColor, (GLenum, GLenum)); FN(void, glDeleteBuffers, (GLsizei, const GLuint *));
#define LOAD(n) do { n = (void *)eglGetProcAddress(#n); if (!n) { fprintf(stderr, "gl_link: no %s\n", #n); return 2; } } while (0)

static char *slurp(const char *path)
{
    FILE *f = fopen(path, "rb"); char *b; long l;
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); l = ftell(f); rewind(f); b = (char *)malloc((size_t)l + 1);
    if (fread(b, 1, (size_t)l, f) != (size_t)l) { free(b); fclose(f); return NULL; }
    b[l] = 0; fclose(f); return b;
}

static GLuint compile(const char *path, int stage, char *log, size_t logn)
{
    char *src = slurp(path), *out; GLuint sh; GLint ok = 0; const char *s[1]; size_t n;
    if (!src) { snprintf(log, logn, "cannot read %s", path); return 0; }
    n = strlen(src); out = (char *)malloc(n * 2 + 512);
    if (!nxgl_shim_glsl(src, n, (uint32_t)stage, out, n * 2 + 512)) { snprintf(log, logn, "shim failed"); free(src); free(out); return 0; }
    sh = glCreateShader(stage ? GL_FRAGMENT_SHADER : GL_VERTEX_SHADER); s[0] = out;
    glShaderSource(sh, 1, s, NULL); glCompileShader(sh); glGetShaderiv(sh, GL_COMPILE_STATUS, &ok);
    if (!ok) { glGetShaderInfoLog(sh, (GLsizei)logn, NULL, log); glDeleteShader(sh); sh = 0; }
    free(src); free(out); return sh;
}
static void oneline(char *s) { for (; *s; s++) if (*s == '\n' || *s == '\t') *s = ' '; }

typedef struct { float cf[2][256 * 4]; float drv[2][128]; } State;

static GLuint ubo(GLuint binding, const void *data, size_t n)
{
    GLuint b; glGenBuffers(1, &b); glBindBuffer(GL_UNIFORM_BUFFER, b); glBufferData(GL_UNIFORM_BUFFER, (GLsizeiptr)n, data, GL_STATIC_DRAW);
    glBindBufferBase(GL_UNIFORM_BUFFER, binding, b); return b;
}

static void run(const char *id, const char *vsp, const char *psp, State *st, GLuint vao, GLuint fbo)
{
    char log[2048] = ""; GLuint vs, ps, prog, b[4]; GLint ok = 0; float px[5];
    vs = compile(vsp, 0, log, sizeof log); if (!vs) { oneline(log); printf("%s compile_fail VS %.300s\n", id, log); return; }
    ps = compile(psp, 1, log, sizeof log); if (!ps) { oneline(log); printf("%s compile_fail PS %.300s\n", id, log); glDeleteShader(vs); return; }
    prog = glCreateProgram(); glAttachShader(prog, vs); glAttachShader(prog, ps); glLinkProgram(prog); glGetProgramiv(prog, GL_LINK_STATUS, &ok);
    if (!ok) { glGetProgramInfoLog(prog, sizeof log, NULL, log); oneline(log); printf("%s link_fail %.300s\n", id, log); glDeleteProgram(prog); glDeleteShader(vs); glDeleteShader(ps); return; }
    glUseProgram(prog); glBindVertexArray(vao); glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    b[0] = ubo(0, st->cf[0], sizeof st->cf[0]); b[1] = ubo(2, st->drv[0], sizeof st->drv[0]);                     /* VS: stage 0, bindings 0 / 2 */
    b[2] = ubo(16, st->cf[1], sizeof st->cf[1]); b[3] = ubo(18, st->drv[1], sizeof st->drv[1]);                    /* PS: stage 1, bindings 16 / 18 */
    glClearColor(-7.f, -7.f, -7.f, -7.f); glClearDepth(0.9); glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
    glDrawArrays(GL_POINTS, 0, 1);
    glReadPixels(0, 0, 1, 1, GL_RGBA, GL_FLOAT, px);
    {   float d; glReadPixels(0, 0, 1, 1, GL_DEPTH_COMPONENT, GL_FLOAT, &d); px[4] = d; }
    printf("%s ok %.9g %.9g %.9g %.9g %.9g\n", id, px[0], px[1], px[2], px[3], px[4]);
    glDeleteBuffers(4, b); glDeleteProgram(prog); glDeleteShader(vs); glDeleteShader(ps);
}

int main(int argc, char **argv)
{
    char err[512], w[256], id[128] = "", vsp[1024] = "", psp[1024] = ""; FILE *f; State st; GLuint vao, fbo, rb, db; int active = 0;
    if (argc < 2) { fprintf(stderr, "usage: gl_link JOBS\n"); return 2; }
    if (!nxgl_create(0, 0, err, sizeof err)) { fprintf(stderr, "gl_link: no GL context: %s\n", err); return 2; }
    LOAD(glCreateShader); LOAD(glShaderSource); LOAD(glCompileShader); LOAD(glGetShaderiv); LOAD(glGetShaderInfoLog); LOAD(glDeleteShader); LOAD(glCreateProgram);
    LOAD(glAttachShader); LOAD(glLinkProgram); LOAD(glDeleteProgram); LOAD(glGetProgramiv); LOAD(glGetProgramInfoLog); LOAD(glUseProgram);
    LOAD(glGenVertexArrays); LOAD(glBindVertexArray); LOAD(glGenRenderbuffers); LOAD(glBindRenderbuffer); LOAD(glRenderbufferStorage); LOAD(glGenFramebuffers);
    LOAD(glBindFramebuffer); LOAD(glFramebufferRenderbuffer); LOAD(glViewport); LOAD(glEnable); LOAD(glDisable); LOAD(glDepthFunc); LOAD(glDepthMask); LOAD(glClearColor);
    LOAD(glClearDepth); LOAD(glClear); LOAD(glGenBuffers); LOAD(glBindBuffer); LOAD(glBufferData); LOAD(glBindBufferBase); LOAD(glDrawArrays); LOAD(glReadPixels);
    LOAD(glClampColor); LOAD(glDeleteBuffers);
    glGenVertexArrays(1, &vao); glBindVertexArray(vao);
    glGenRenderbuffers(1, &rb); glBindRenderbuffer(GL_RENDERBUFFER, rb); glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA32F, 1, 1);
    glGenRenderbuffers(1, &db); glBindRenderbuffer(GL_RENDERBUFFER, db); glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT32F, 1, 1);
    glGenFramebuffers(1, &fbo); glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, rb); glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER, db);
    glViewport(0, 0, 1, 1); glDisable(GL_CULL_FACE); glDisable(GL_BLEND); glEnable(GL_DEPTH_TEST); glDepthFunc(GL_ALWAYS); glDepthMask(GL_TRUE);
    glClampColor(0x891C /* GL_CLAMP_READ_COLOR */, 0 /* GL_FALSE */);
    f = fopen(argv[1], "r"); if (!f) { fprintf(stderr, "gl_link: cannot read %s\n", argv[1]); return 2; }
    while (fscanf(f, "%255s", w) == 1) {
        if (w[0] == '#') { int ch; while ((ch = fgetc(f)) != EOF && ch != '\n') ; continue; }
        if (!strcmp(w, "job")) {
            if (fscanf(f, "%127s %1023s %1023s", id, vsp, psp) != 3) return 2;
            memset(&st, 0, sizeof st); st.drv[0][2] = 1.0f; active = 1;                                          /* VS posfix.z = 1 (no flip) */
        } else if (!active) return 2;
        else if (!strcmp(w, "vc") || !strcmp(w, "pc")) {
            int n, s = w[0] == 'p'; if (fscanf(f, "%d", &n) != 1 || n < 0 || n > 255) return 2;
            for (int i = 0; i < 4; i++) if (fscanf(f, "%f", &st.cf[s][n * 4 + i]) != 1) return 2;
        } else if (!strcmp(w, "vd") || !strcmp(w, "pd")) { int n; float v; if (fscanf(f, "%d %f", &n, &v) != 2 || n < 0 || n > 127) return 2; st.drv[w[0] == 'p'][n] = v; }
        else if (!strcmp(w, "pdu")) { int n; unsigned v; if (fscanf(f, "%d %u", &n, &v) != 2 || n < 0 || n > 127) return 2; memcpy(&st.drv[1][n], &v, 4); }
        else if (!strcmp(w, "go")) { run(id, vsp, psp, &st, vao, fbo); active = 0; fflush(stdout); }
        else return 2;
    }
    fclose(f); nxgl_destroy(); return 0;
}
