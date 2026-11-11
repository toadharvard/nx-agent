/* glcheck.c - compiles every translated GLSL and links every recorded (VS, PS) pair on Mesa llvmpipe (desktop GL 4.5 core, EGL surfaceless).
 * Uses render/native/gl/nxgl.c for the context (nxgl_create) and for the GLSL shim (nxgl_shim_glsl): the path the GL reference backend takes for a
 * glsldk shader, so "compiles" here means what nxgl's api_shader_create accepts.
 *   LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe EGL_PLATFORM=surfaceless glcheck SHXLATE_OUTDIR [failures.tsv]
 * Reads OUTDIR/entries.tsv (id ... stage) + <id>.glsl, OUTDIR/links.tsv (vs ps l|-); prints "compile ok/total", "link ok/total"; exit 0 iff all ok.
 * Pair (vs, ps) with L '-' uses the stage-alone entries <h>-<v|p>-0-n, else both L variants <h>-<v|p>-0-<l hex>. */
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
FN(void, glGetProgramiv, (GLuint, GLenum, GLint *)); FN(void, glGetProgramInfoLog, (GLuint, GLsizei, GLsizei *, GLchar *)); FN(const GLubyte *, glGetString, (GLenum));
#define LOAD(n) do { n = (void *)eglGetProcAddress(#n); if (!n) { fprintf(stderr, "glcheck: no %s\n", #n); return 2; } } while (0)

typedef struct { char id[96]; GLuint sh; int ok, tried; } Ent;
static Ent *ents; static int nents; static FILE *ffail; static const char *dir;

static char *slurp(const char *path, size_t *n)
{
    FILE *f = fopen(path, "rb"); char *b; long l;
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); l = ftell(f); rewind(f); b = (char *)malloc((size_t)l + 1);
    if (fread(b, 1, (size_t)l, f) != (size_t)l) { free(b); fclose(f); return NULL; }
    b[l] = 0; fclose(f); *n = (size_t)l; return b;
}
static Ent *find(const char *id) { int i; for (i = 0; i < nents; i++) if (!strcmp(ents[i].id, id)) return &ents[i]; return NULL; }

static int compile(Ent *e, int stage)
{
    char path[1100], *src, *out, log[2000]; size_t n; GLint ok = 0; const char *s[1];
    snprintf(path, sizeof path, "%s/%s.glsl", dir, e->id);
    if (!(src = slurp(path, &n))) { fprintf(ffail, "%s\tno glsl file\n", e->id); return 0; }
    out = (char *)malloc(n * 2 + 512);
    if (!nxgl_shim_glsl(src, n, (uint32_t)stage, out, n * 2 + 512)) { fprintf(ffail, "%s\tshim failed\n", e->id); free(src); free(out); return 0; }
    e->sh = glCreateShader(stage ? GL_FRAGMENT_SHADER : GL_VERTEX_SHADER); s[0] = out;
    glShaderSource(e->sh, 1, s, NULL); glCompileShader(e->sh); glGetShaderiv(e->sh, GL_COMPILE_STATUS, &ok);
    if (!ok) { glGetShaderInfoLog(e->sh, sizeof log, NULL, log); fprintf(ffail, "%s\tcompile: %.300s\n", e->id, log); for (char *p = strchr(log, '\n'); p; p = strchr(p + 1, '\n')) *p = ' '; }
    free(src); free(out); return ok;
}

int main(int argc, char **argv)
{
    char path[1100], line[4096], err[512]; FILE *f; int total = 0, okc = 0, lt = 0, lok = 0, i;
    if (argc < 2) { fprintf(stderr, "usage: glcheck OUTDIR [failures.tsv]\n"); return 2; }
    dir = argv[1]; ffail = argc > 2 ? fopen(argv[2], "w") : stderr;
    if (!nxgl_create(0, 0, err, sizeof err)) { fprintf(stderr, "glcheck: no GL context: %s\n", err); return 2; }
    LOAD(glCreateShader); LOAD(glShaderSource); LOAD(glCompileShader); LOAD(glGetShaderiv); LOAD(glGetShaderInfoLog); LOAD(glDeleteShader); LOAD(glCreateProgram);
    LOAD(glAttachShader); LOAD(glLinkProgram); LOAD(glDeleteProgram); LOAD(glGetProgramiv); LOAD(glGetProgramInfoLog); LOAD(glGetString);
    printf("GL_RENDERER %s | GL_VERSION %s\n", (const char *)glGetString(GL_RENDERER), (const char *)glGetString(GL_VERSION));
    snprintf(path, sizeof path, "%s/entries.tsv", dir); f = fopen(path, "r");
    if (!f || !fgets(line, sizeof line, f)) { fprintf(stderr, "glcheck: no entries.tsv\n"); return 2; }
    while (fgets(line, sizeof line, f)) {
        char id[96], h[24], st[4]; unsigned len;
        if (sscanf(line, "%95s %23s %u %3s", id, h, &len, st) != 4) return 2;
        ents = (Ent *)realloc(ents, (nents + 1) * sizeof *ents); memset(&ents[nents], 0, sizeof *ents); strcpy(ents[nents].id, id);
        ents[nents].tried = 1; ents[nents].ok = compile(&ents[nents], st[0] == 'p'); total++; okc += ents[nents].ok; nents++;
    }
    fclose(f);
    snprintf(path, sizeof path, "%s/links.tsv", dir); f = fopen(path, "r");
    if (!f) { fprintf(stderr, "glcheck: no links.tsv\n"); return 2; }
    while (fgets(line, sizeof line, f)) {
        char vh[24], ph[24], l[100], vid[96], pid[96]; Ent *v, *p; GLuint prog; GLint ok = 0;
        if (sscanf(line, "%23s %23s %99s", vh, ph, l) != 3) return 2;
        snprintf(vid, sizeof vid, "%s-v-0-%s", vh, strcmp(l, "-") ? l : "n"); snprintf(pid, sizeof pid, "%s-p-0-%s", ph, strcmp(l, "-") ? l : "n");
        lt++; v = find(vid); p = find(pid);
        if (!v || !p || !v->ok || !p->ok) { fprintf(ffail, "%s+%s\tlink: stage missing or did not compile\n", vid, pid); continue; }
        prog = glCreateProgram(); glAttachShader(prog, v->sh); glAttachShader(prog, p->sh); glLinkProgram(prog); glGetProgramiv(prog, GL_LINK_STATUS, &ok);
        if (!ok) { char log[2000]; glGetProgramInfoLog(prog, sizeof log, NULL, log); for (char *q = strchr(log, '\n'); q; q = strchr(q + 1, '\n')) *q = ' '; fprintf(ffail, "%s+%s\tlink: %.300s\n", vid, pid, log); }
        lok += ok; glDeleteProgram(prog);
    }
    fclose(f); for (i = 0; i < nents; i++) if (ents[i].ok) glDeleteShader(ents[i].sh);
    printf("compile %d/%d\nlink %d/%d\n", okc, total, lok, lt); nxgl_destroy();
    return !(okc == total && lok == lt);
}
