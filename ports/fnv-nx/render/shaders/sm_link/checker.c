/* sm_link checker (item 4.6, rule text: render/shaders/total/SM_LINK_RULE.md).
 *   sm_link_check SHADER_DIR OUT_DIR
 * SHADER_DIR: the *.sdp packages of gamedata/shaders (a declared external; format: u32 magic 100, u32 count, u32 data size, then per entry a
 * 256-byte name, u32 size, the Direct3D bytecode). The stage of an entry is the version token (0xFFFE.... vertex, 0xFFFF.... pixel), never its name.
 * Universe: every unique bytecode (sha256) of every package, translated by the host translator in every variant the front end can key:
 *   VS: VS_COMPLETE x FLAT_COLOR (4);  PS: FLAT_COLOR x {runtime, EPI static none/alpha/fog/both} x NUMERICS_NINE (20).
 * Per stage form it compiles the GLSL on the pinned Mesa (GL 4.5 core, llvmpipe, EGL surfaceless), reads the interface signature two ways (the
 * declarations of the translator's emission grammar; the Mesa program-interface query of a separable single-stage program) and writes one
 * certificate stage/<bytecode sha256>.f<flags>.json. It then links one representative of every (VS signature, PS signature) class with equal FLAT_COLOR
 * and writes one certificate link/<vs sig>.<ps sig>.f<flat>.json. OUT_DIR/summary.json = {"universe", "red", "details"}. Exit 0 iff red == 0.
 * Output is a function of the inputs: sorted directory order, no times, no paths.
 * Not covered yet (rule text section 3/5): link set L and the draw path's complete bit per pair, FFP stages, the backend's own GLSL rewrite. */
#define _POSIX_C_SOURCE 200809L
#include <dirent.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fnvnx_shaders.h"
#include "fnvnx_sha256.h"

typedef void *EGLDisplay; typedef void *EGLContext; typedef void *EGLConfig; typedef void *EGLSurface; typedef int32_t EGLint; typedef unsigned EGLBoolean;
EGLDisplay eglGetPlatformDisplay(unsigned platform, void *native, const intptr_t *attribs);
EGLBoolean eglInitialize(EGLDisplay d, EGLint *major, EGLint *minor);
EGLBoolean eglBindAPI(unsigned api);
EGLContext eglCreateContext(EGLDisplay d, EGLConfig c, EGLContext share, const EGLint *attribs);
EGLBoolean eglMakeCurrent(EGLDisplay d, EGLSurface draw, EGLSurface read, EGLContext c);
void (*eglGetProcAddress(const char *name))(void);

enum { GL_FRAGMENT_SHADER = 0x8B30, GL_VERTEX_SHADER = 0x8B31, GL_COMPILE_STATUS = 0x8B81, GL_LINK_STATUS = 0x8B82, GL_PROGRAM_SEPARABLE = 0x8258,
       GL_PROGRAM_INPUT = 0x92E3, GL_PROGRAM_OUTPUT = 0x92E4, GL_ACTIVE_RESOURCES = 0x92F5, GL_LOCATION = 0x930E };
#define FN(ret, name, args) static ret(*name) args
FN(unsigned, glCreateShader, (unsigned)); FN(void, glShaderSource, (unsigned, int, const char *const *, const int *)); FN(void, glCompileShader, (unsigned));
FN(void, glGetShaderiv, (unsigned, unsigned, int *)); FN(void, glGetShaderInfoLog, (unsigned, int, int *, char *)); FN(void, glDeleteShader, (unsigned));
FN(unsigned, glCreateProgram, (void)); FN(void, glAttachShader, (unsigned, unsigned)); FN(void, glLinkProgram, (unsigned));
FN(void, glGetProgramiv, (unsigned, unsigned, int *)); FN(void, glGetProgramInfoLog, (unsigned, int, int *, char *)); FN(void, glDeleteProgram, (unsigned));
FN(void, glProgramParameteri, (unsigned, unsigned, int));
FN(void, glGetProgramInterfaceiv, (unsigned, unsigned, unsigned, int *));
FN(void, glGetProgramResourceName, (unsigned, unsigned, unsigned, int, int *, char *));
FN(void, glGetProgramResourceiv, (unsigned, unsigned, unsigned, int, const unsigned *, int, int *, int *));
#define LOAD(n) do { n = (void *)eglGetProcAddress(#n); if (!n) { fprintf(stderr, "sm_link: no %s\n", #n); return 2; } } while (0)

typedef struct { uint8_t sha[32]; int stage; size_t n; unsigned char *bc; } Form;   /* a unique bytecode */
typedef struct { char dir; int loc; char name[96]; } Decl;
typedef struct { uint8_t sig[32]; unsigned shader; int ok; } Sig;                  /* a signature class representative */

static void hex(const uint8_t *b, size_t n, char *o) { static const char d[] = "0123456789abcdef"; for (size_t i = 0; i < n; i++) { o[2 * i] = d[b[i] >> 4]; o[2 * i + 1] = d[b[i] & 15]; } o[2 * n] = 0; }
static int cmp_form(const void *a, const void *b) { return memcmp(((const Form *)a)->sha, ((const Form *)b)->sha, 32); }
static int cmp_str(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }
static uint32_t rd32(const unsigned char *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static void jstr(FILE *f, const char *s) { fputc('"', f); for (; *s; s++) { unsigned char c = (unsigned char)*s; if (c == '"' || c == '\\') { fputc('\\', f); fputc(c, f); } else if (c < 32 || c > 126) fputc(' ', f); else fputc(c, f); } fputc('"', f); }

/* The emission grammar: one declaration per line of the translator's GLSL (profile output_line sites). The signature is the sorted list of the
 * interface lines (layout/flat/in/out/uniform declarations); the declared interface is the (direction, location, name) of the varying lines. */
static int line_is_interface(const char *s) {
    return !strncmp(s, "layout(", 7) || !strncmp(s, "flat ", 5) || !strncmp(s, "in ", 3) || !strncmp(s, "out ", 4) || !strncmp(s, "uniform ", 8);
}
static int cmp_line(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }
static int grammar_read(const char *glsl, uint8_t sig[32], Decl *decls, int maxd, int *nd) {
    size_t cap = strlen(glsl) + 1; char *copy = malloc(cap); char **lines = malloc(sizeof(char *) * 4096); int n = 0; *nd = 0;
    if (!copy || !lines) return -1;
    memcpy(copy, glsl, cap);
    for (char *p = copy; *p;) {
        char *e = strchr(p, '\n'); if (e) *e = 0;
        while (*p == ' ' || *p == '\t') p++;
        if (line_is_interface(p)) { if (n == 4096) return -1; lines[n++] = p; }
        if (!e) break;
        p = e + 1;
    }
    qsort(lines, (size_t)n, sizeof(char *), cmp_line);
    size_t tot = 0; for (int i = 0; i < n; i++) tot += strlen(lines[i]) + 1;
    char *cat = malloc(tot + 1); size_t o = 0; if (!cat) return -1;
    for (int i = 0; i < n; i++) { size_t l = strlen(lines[i]); memcpy(cat + o, lines[i], l); o += l; cat[o++] = '\n'; }
    fnvnx_sha256(cat, o, sig);
    for (int i = 0; i < n; i++) {
        int loc; char name[96]; const char *q = lines[i]; char dir = 0;
        if (sscanf(q, "layout(location = %d)", &loc) != 1) continue;
        q = strchr(q, ')') + 1; while (*q == ' ') q++;
        if (!strncmp(q, "flat ", 5)) q += 5;
        if (!strncmp(q, "in ", 3)) { dir = 'i'; q += 3; } else if (!strncmp(q, "out ", 4)) { dir = 'o'; q += 4; } else continue;
        { const char *t = strrchr(q, ' '); if (!t) continue; t++; size_t l = strcspn(t, "; ["); if (l >= sizeof name) continue; memcpy(name, t, l); name[l] = 0; }
        if (*nd < maxd) { decls[*nd].dir = dir; decls[*nd].loc = loc; strcpy(decls[*nd].name, name); (*nd)++; }
    }
    free(cat); free(lines); free(copy);
    return 0;
}
/* The Mesa read: the active program inputs/outputs of the separable single-stage program of `sh`; each (direction, location, name) must be a declaration
 * of the grammar read. Returns the number of disagreements (0 = the two reads agree on every active resource), and the active count. */
static int query_read(unsigned sh, const Decl *decls, int nd, int *active) {
    unsigned prog = glCreateProgram(); int bad = 0; *active = 0;
    glProgramParameteri(prog, GL_PROGRAM_SEPARABLE, 1); glAttachShader(prog, sh); glLinkProgram(prog);
    int ls = 0; glGetProgramiv(prog, GL_LINK_STATUS, &ls);
    if (!ls) { glDeleteProgram(prog); return 1000000; }
    for (int pass = 0; pass < 2; pass++) {
        unsigned iface = pass ? GL_PROGRAM_OUTPUT : GL_PROGRAM_INPUT; int nres = 0; glGetProgramInterfaceiv(prog, iface, GL_ACTIVE_RESOURCES, &nres);
        for (int r = 0; r < nres; r++) {
            char name[128]; int len = 0, loc = -1; const unsigned prop = GL_LOCATION;
            glGetProgramResourceName(prog, iface, (unsigned)r, (int)sizeof name, &len, name);
            glGetProgramResourceiv(prog, iface, (unsigned)r, 1, &prop, 1, NULL, &loc);
            if (!strncmp(name, "gl_", 3)) continue;
            (*active)++;
            { char *b = strchr(name, '['); if (b) *b = 0; }
            int found = 0; for (int d = 0; d < nd; d++) if (decls[d].dir == (pass ? 'o' : 'i') && decls[d].loc == loc && !strcmp(decls[d].name, name)) found = 1;
            if (!found) bad++;
        }
    }
    glDeleteProgram(prog);
    return bad;
}

typedef struct { Form *forms; int n, cap; } Forms;
static int add_form(Forms *F, const unsigned char *bc, size_t n) {
    uint8_t sha[32]; fnvnx_sha256(bc, n, sha);
    for (int i = 0; i < F->n; i++) if (!memcmp(F->forms[i].sha, sha, 32)) return 0;
    if (F->n == F->cap) { F->cap = F->cap ? 2 * F->cap : 256; F->forms = realloc(F->forms, sizeof(Form) * (size_t)F->cap); if (!F->forms) return -1; }
    Form *f = &F->forms[F->n++]; memcpy(f->sha, sha, 32); f->n = n; f->bc = malloc(n); if (!f->bc) return -1; memcpy(f->bc, bc, n);
    { uint32_t v = rd32(bc); f->stage = (v >> 16) == 0xFFFEu ? 0 : (v >> 16) == 0xFFFFu ? 1 : -1; }
    return 0;
}
static int read_sdp(Forms *F, const char *path, int *entries) {
    FILE *fp = fopen(path, "rb"); if (!fp) return -1;
    fseek(fp, 0, SEEK_END); long sz = ftell(fp); rewind(fp);
    unsigned char *b = malloc((size_t)sz); if (!b || fread(b, 1, (size_t)sz, fp) != (size_t)sz) { fclose(fp); return -1; } fclose(fp);
    if (sz < 12 || rd32(b) != 100u || (long)rd32(b + 8) != sz - 12) { free(b); return -1; }
    uint32_t cnt = rd32(b + 4); long off = 12;
    for (uint32_t i = 0; i < cnt; i++) {
        if (off + 260 > sz) { free(b); return -1; }
        uint32_t s = rd32(b + off + 256); off += 260;
        if (off + (long)s > sz) { free(b); return -1; }
        if (add_form(F, b + off, s)) { free(b); return -1; }
        off += (long)s; (*entries)++;
    }
    free(b); return 0;
}

typedef struct { uint8_t sig[32]; unsigned shader; int form; unsigned flags; } Rep;
typedef struct { Rep *r; int n; } Reps;
static int add_rep(Reps *R, const uint8_t sig[32], unsigned shader, int form, unsigned flags) {
    for (int i = 0; i < R->n; i++) if (!memcmp(R->r[i].sig, sig, 32)) return 0;
    R->r = realloc(R->r, sizeof(Rep) * (size_t)(R->n + 1)); if (!R->r) return -1;
    memcpy(R->r[R->n].sig, sig, 32); R->r[R->n].shader = shader; R->r[R->n].form = form; R->r[R->n].flags = flags; R->n++; return 0;
}
static int cmp_rep(const void *a, const void *b) { return memcmp(((const Rep *)a)->sig, ((const Rep *)b)->sig, 32); }

int main(int argc, char **argv) {
    if (argc != 3) { fprintf(stderr, "usage: sm_link_check SHADER_DIR OUT_DIR\n"); return 2; }
    const char *sdir = argv[1], *odir = argv[2];
    setenv("LIBGL_ALWAYS_SOFTWARE", "1", 1); setenv("GALLIUM_DRIVER", "llvmpipe", 1); setenv("EGL_PLATFORM", "surfaceless", 1);
    EGLDisplay d = eglGetPlatformDisplay(0x31DD, NULL, NULL); EGLint ma, mi;
    if (!d || !eglInitialize(d, &ma, &mi)) { fprintf(stderr, "sm_link: EGL init failed\n"); return 2; }
    eglBindAPI(0x30A2);
    const EGLint ca[] = { 0x3098, 4, 0x30FB, 5, 0x30FD, 1, 0x3038 };
    EGLContext cx = eglCreateContext(d, NULL, NULL, ca);
    if (!cx || !eglMakeCurrent(d, NULL, NULL, cx)) { fprintf(stderr, "sm_link: GL context failed\n"); return 2; }
    LOAD(glCreateShader); LOAD(glShaderSource); LOAD(glCompileShader); LOAD(glGetShaderiv); LOAD(glGetShaderInfoLog); LOAD(glDeleteShader);
    LOAD(glCreateProgram); LOAD(glAttachShader); LOAD(glLinkProgram); LOAD(glGetProgramiv); LOAD(glGetProgramInfoLog); LOAD(glDeleteProgram);
    LOAD(glProgramParameteri); LOAD(glGetProgramInterfaceiv); LOAD(glGetProgramResourceName); LOAD(glGetProgramResourceiv);

    char **names = NULL; int nn = 0; DIR *D = opendir(sdir);
    if (!D) { fprintf(stderr, "sm_link: cannot open %s\n", sdir); return 2; }
    for (struct dirent *e; (e = readdir(D));) { size_t l = strlen(e->d_name); if (l > 4 && !strcmp(e->d_name + l - 4, ".sdp")) { names = realloc(names, sizeof(char *) * (size_t)(nn + 1)); names[nn++] = strdup(e->d_name); } }
    closedir(D); qsort(names, (size_t)nn, sizeof(char *), cmp_str);
    Forms F = { 0, 0, 0 }; int entries = 0;
    for (int i = 0; i < nn; i++) { char p[1024]; snprintf(p, sizeof p, "%s/%s", sdir, names[i]); if (read_sdp(&F, p, &entries)) { fprintf(stderr, "sm_link: bad package %s\n", names[i]); return 2; } }
    qsort(F.forms, (size_t)F.n, sizeof(Form), cmp_form);
    char path[1200]; snprintf(path, sizeof path, "%s/stage", odir); /* the output directories are made by the caller (declared outputs) */

    Reps rv[2] = { { 0, 0 }, { 0, 0 } }, rp[2] = { { 0, 0 }, { 0, 0 } };   /* by FLAT_COLOR */
    long red = 0, stage_certs = 0, link_certs = 0, nvs = 0, nps = 0, refused = 0; unsigned long long mism = 0;
    static const unsigned pv[4] = { 0, FNVNX_SHADER_VS_COMPLETE, FNVNX_SHADER_FLAT_COLOR, FNVNX_SHADER_VS_COMPLETE | FNVNX_SHADER_FLAT_COLOR };
    unsigned pp[20]; int npp = 0;
    { static const unsigned epi[5] = { 0, FNVNX_SHADER_EPI_STATIC, FNVNX_SHADER_EPI_STATIC | FNVNX_SHADER_EPI_ALPHA, FNVNX_SHADER_EPI_STATIC | FNVNX_SHADER_EPI_FOG,
                                       FNVNX_SHADER_EPI_STATIC | FNVNX_SHADER_EPI_ALPHA | FNVNX_SHADER_EPI_FOG };
      for (int fl = 0; fl < 2; fl++) for (int e = 0; e < 5; e++) for (int nm = 0; nm < 2; nm++)
          pp[npp++] = (fl ? FNVNX_SHADER_FLAT_COLOR : 0u) | epi[e] | (nm ? FNVNX_SHADER_NUMERICS_NINE : 0u); }
    for (int i = 0; i < F.n; i++) {
        Form *f = &F.forms[i]; char hs[65]; hex(f->sha, 32, hs);
        if (f->stage < 0) { red++; refused++; continue; }
        if (f->stage == 0) nvs++; else nps++;
        int nv = f->stage == 0 ? 4 : npp;
        for (int v = 0; v < nv; v++) {
            unsigned flags = f->stage == 0 ? pv[v] : pp[v];
            fnvnx_shader_opts o; fnvnx_shader_result r; memset(&o, 0, sizeof o); memset(&r, 0, sizeof r);
            o.flags = FNVNX_SHADER_KEEP_GLSL | FNVNX_SHADER_NO_DKSH | flags;
            fnvnx_shader_translate_glsl(f->bc, f->n, &o, &r);
            snprintf(path, sizeof path, "%s/stage/%s.f%u.json", odir, hs, flags);
            FILE *c = fopen(path, "w"); if (!c) { fprintf(stderr, "sm_link: cannot write %s\n", path); return 2; }
            const char *status = "ok"; char why[400] = ""; uint8_t sig[32] = { 0 }; int active = 0, disagree = 0, ndecl = 0; unsigned sh = 0;
            if (!r.ok || !r.glsl) { status = "translate_fail"; snprintf(why, sizeof why, "%.399s", r.error); }
            else {
                sh = glCreateShader(f->stage ? GL_FRAGMENT_SHADER : GL_VERTEX_SHADER); const char *src[1] = { r.glsl }; int ok = 0;
                glShaderSource(sh, 1, src, NULL); glCompileShader(sh); glGetShaderiv(sh, GL_COMPILE_STATUS, &ok);
                if (!ok) { status = "compile_fail"; glGetShaderInfoLog(sh, (int)sizeof why, NULL, why); }
                else {
                    Decl decls[64];
                    if (grammar_read(r.glsl, sig, decls, 64, &ndecl)) { status = "grammar_fail"; }
                    else { disagree = query_read(sh, decls, ndecl, &active); if (disagree) { status = "signature_disagree"; mism += (unsigned long long)disagree; } }
                }
            }
            { char sg[65]; hex(sig, 32, sg);
              fprintf(c, "{\"format\": \"fnvnx-sm-stage/1\", \"bytecode_sha256\": \"%s\", \"stage\": \"%s\", \"variant_flags\": %u, \"status\": \"%s\", \"signature_sha256\": \"%s\", \"declared_varyings\": %d, \"active_varyings\": %d, \"disagreements\": %d, \"log\": ",
                      hs, f->stage ? "ps" : "vs", flags, status, sg, ndecl, active, disagree);
              jstr(c, why); fprintf(c, "}\n"); }
            fclose(c); stage_certs++;
            if (strcmp(status, "ok")) { red++; if (sh) glDeleteShader(sh); }
            else {
                int fl = (flags & FNVNX_SHADER_FLAT_COLOR) ? 1 : 0; Reps *R = f->stage ? &rp[fl] : &rv[fl];
                if (add_rep(R, sig, sh, i, flags)) return 2;
                if (R->r[R->n - 1].shader != sh) glDeleteShader(sh);   /* not the class representative */
            }
            fnvnx_shader_result_free(&r);
        }
    }
    for (int fl = 0; fl < 2; fl++) {
        qsort(rv[fl].r, (size_t)rv[fl].n, sizeof(Rep), cmp_rep); qsort(rp[fl].r, (size_t)rp[fl].n, sizeof(Rep), cmp_rep);
        for (int a = 0; a < rv[fl].n; a++) for (int b = 0; b < rp[fl].n; b++) {
            unsigned pr = glCreateProgram(); int ls = 0; char log[400] = "", sa[65], sb[65];
            glAttachShader(pr, rv[fl].r[a].shader); glAttachShader(pr, rp[fl].r[b].shader); glLinkProgram(pr); glGetProgramiv(pr, GL_LINK_STATUS, &ls);
            if (!ls) glGetProgramInfoLog(pr, (int)sizeof log, NULL, log);
            glDeleteProgram(pr);
            hex(rv[fl].r[a].sig, 32, sa); hex(rp[fl].r[b].sig, 32, sb);
            snprintf(path, sizeof path, "%s/link/%s.%s.f%d.json", odir, sa, sb, fl);
            FILE *c = fopen(path, "w"); if (!c) { fprintf(stderr, "sm_link: cannot write %s\n", path); return 2; }
            fprintf(c, "{\"format\": \"fnvnx-sm-link/1\", \"vs_signature_sha256\": \"%s\", \"ps_signature_sha256\": \"%s\", \"flat_color\": %d, \"status\": \"%s\", \"log\": ", sa, sb, fl, ls ? "linked" : "link_fail");
            jstr(c, log); fprintf(c, "}\n"); fclose(c); link_certs++;
            if (!ls) red++;
        }
    }
    snprintf(path, sizeof path, "%s/summary.json", odir);
    FILE *s = fopen(path, "w"); if (!s) return 2;
    fprintf(s, "{\"format\": \"fnvnx-sm-link-summary/1\", \"universe\": %d, \"red\": %ld, \"details\": {\"entries\": %d, \"unique_bytecodes\": %d, \"vs\": %ld, \"ps\": %ld, \"refused\": %ld, "
               "\"stage_certificates\": %ld, \"link_certificates\": %ld, \"vs_classes\": [%d, %d], \"ps_classes\": [%d, %d], \"signature_disagreements\": %llu}}\n",
            F.n, red, entries, F.n, nvs, nps, refused, stage_certs, link_certs, rv[0].n, rv[1].n, rp[0].n, rp[1].n, mism);
    fclose(s);
    printf("unique %d (vs %ld ps %ld) stage certs %ld link certs %ld red %ld\n", F.n, nvs, nps, stage_certs, link_certs, red);
    return red ? 1 : 0;
}
