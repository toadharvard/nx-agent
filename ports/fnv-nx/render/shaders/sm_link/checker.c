/* sm_link checker (item 4.6, rule text: render/shaders/total/SM_LINK_RULE.md).
 *   sm_link_check SHADER_DIR OUT_DIR
 * SHADER_DIR: the *.sdp packages of gamedata/shaders (a declared external; format: u32 magic 100, u32 count, u32 data size, then per entry a
 * 256-byte name, u32 size, the Direct3D bytecode). The stage of an entry is the version token (0xFFFE.... vertex, 0xFFFF.... pixel), never its name.
 * Universe: every unique bytecode (sha256) of every package, translated by the host translator in every flag set of the variant key: all subsets
 * of FNVNX_SHADER_KEY_FLAGS within the flag bits the enum of fnvnx_shaders.h defines (SM_ALL_FLAGS below), for both stages.
 * Per stage form it compiles the GLSL on the pinned Mesa (GL 4.5 core, llvmpipe, EGL surfaceless), reads the interface signature two ways (the
 * declarations of the translator's emission grammar; the Mesa program-interface query of a separable single-stage program) and writes one
 * certificate stage/<bytecode sha256>.f<flags>.json. It then links, for every (vertex class, PS signature) with equal FLAT_COLOR, the VS variant that fnvnx_dk_vs_needs_complete (fnvnx_dk_layout.h) selects
 * and writes one certificate link/<vs sig>.<ps sig>.f<flat>.json. OUT_DIR/summary.json = {"universe", "red", "details"}. Exit 0 iff red == 0.
 * Output is a function of the inputs: sorted directory order, no times, no paths.
 * Not covered yet (rule text section 3/5): link set L, FFP stages, the backend's own GLSL rewrite. */
#define _POSIX_C_SOURCE 200809L
#include <dirent.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fnvnx_shaders.h"
#include "fnvnx_sdp.h"
#include "fnvnx_dk_layout.h"   /* the product header: fnvnx_dk_vs_needs_complete is called, not copied */
#include "fnvnx_sha256.h"
/* The flag bits the enum of fnvnx_shaders.h defines: bits 0..log2(FNVNX_SHADER_FLAT_COLOR). Relies on FLAT_COLOR being the highest bit of the enum (reported: the header should
 * carry an all-flags macro next to KEY_FLAGS so that a new flag cannot be missed here). */
#define SM_ALL_FLAGS ((unsigned)((FNVNX_SHADER_FLAT_COLOR << 1) - 1u))

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

#define hex(b, n, o) fnvnx_shader_key_hex((b), (o))   /* every hashed name here is a 32-byte key (bytecode or signature) */
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
    unsigned char *b = malloc((size_t)sz + 1); if (!b || fread(b, 1, (size_t)sz, fp) != (size_t)sz) { fclose(fp); free(b); return -1; } fclose(fp);
    fnvnx_sdp p; char err[200];
    if (fnvnx_sdp_parse(b, (size_t)sz, &p, err, sizeof err)) { fprintf(stderr, "sm_link: %s: %s\n", path, err); free(b); return -1; }   /* the translator's strict package reader */
    for (uint32_t i = 0; i < p.count; i++) { if (add_form(F, p.entries[i].data, p.entries[i].size)) { fnvnx_sdp_free(&p); free(b); return -1; } (*entries)++; }
    fnvnx_sdp_free(&p); free(b); return 0;
}

typedef struct { uint8_t p[225]; int n; } Pairs;   /* FNVNX_DK_PAIR codes of the translator's reflection ("varyings_written" of a VS, "varyings_read" of a PS) */
typedef struct { uint8_t sig[32]; unsigned shader; int form; unsigned flags; Pairs pr; } Rep;
typedef struct { Rep *r; int n; } Reps;
typedef struct { uint8_t sb[32], sc[32]; Pairs w; } VFull;   /* a vertex class: the signatures of its base and complete variants, the pairs its reflection says it writes */
typedef struct { VFull *v; int n; } VFulls;
static int reflect_pairs(const char *json, const char *key, Pairs *o) {
    char pat[40]; snprintf(pat, sizeof pat, "\"%s\": [", key); o->n = 0;
    const char *q = json ? strstr(json, pat) : NULL; if (!q) return -1;
    q += strlen(pat);
    while (*q && *q != ']') { char *e; long v = strtol(q, &e, 10); if (e == q || v < 0 || v > 255 || o->n >= 224) return -1; o->p[o->n++] = (uint8_t)v; q = e; while (*q == ',' || *q == ' ') q++; }
    return *q == ']' ? 0 : -1;
}
static int add_rep(Reps *R, const uint8_t sig[32], unsigned shader, int form, unsigned flags, const Pairs *pr) {
    for (int i = 0; i < R->n; i++) if (!memcmp(R->r[i].sig, sig, 32)) return 0;
    R->r = realloc(R->r, sizeof(Rep) * (size_t)(R->n + 1)); if (!R->r) return -1;
    memcpy(R->r[R->n].sig, sig, 32); R->r[R->n].shader = shader; R->r[R->n].form = form; R->r[R->n].flags = flags; R->r[R->n].pr = *pr; R->n++; return 0;
}
static int add_vfull(VFulls *V, const uint8_t sb[32], const uint8_t sc[32], const Pairs *w) {
    for (int i = 0; i < V->n; i++) if (!memcmp(V->v[i].sb, sb, 32) && !memcmp(V->v[i].sc, sc, 32) && V->v[i].w.n == w->n && !memcmp(V->v[i].w.p, w->p, (size_t)w->n)) return 0;
    V->v = realloc(V->v, sizeof(VFull) * (size_t)(V->n + 1)); if (!V->v) return -1;
    memcpy(V->v[V->n].sb, sb, 32); memcpy(V->v[V->n].sc, sc, 32); V->v[V->n].w = *w; V->n++; return 0;
}
static const Rep *find_rep(const Reps *R, const uint8_t sig[32]) { for (int i = 0; i < R->n; i++) if (!memcmp(R->r[i].sig, sig, 32)) return &R->r[i]; return NULL; }

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
    VFulls vf[2] = { { 0, 0 }, { 0, 0 } };
    long red = 0, stage_certs = 0, link_certs = 0, nvs = 0, nps = 0, refused = 0; unsigned long long mism = 0;
    /* The flag sets of the variant key: every subset of the key bits (fnvnx_shaders.h: KEY_FLAGS = complement of NONKEY_FLAGS) among the bits of the enum. */
    unsigned fsets[SM_ALL_FLAGS + 1]; int nfs = 0;
    for (unsigned m = 0; m <= SM_ALL_FLAGS; m++) if ((m & FNVNX_SHADER_KEY_FLAGS) == m) fsets[nfs++] = m;
    for (int i = 0; i < F.n; i++) {
        Form *f = &F.forms[i]; char hs[65]; hex(f->sha, 32, hs);
        if (f->stage < 0) { red++; refused++; continue; }
        if (f->stage == 0) nvs++; else nps++;
        int nv = nfs; uint8_t bsig[SM_ALL_FLAGS + 1][32]; Pairs bw[SM_ALL_FLAGS + 1]; unsigned char bok[SM_ALL_FLAGS + 1]; memset(bok, 0, sizeof bok);
        for (int v = 0; v < nv; v++) {
            unsigned flags = fsets[v]; const int fl = (flags & FNVNX_SHADER_FLAT_COLOR) ? 1 : 0;
            fnvnx_shader_opts o; fnvnx_shader_result r; memset(&o, 0, sizeof o); memset(&r, 0, sizeof r);
            o.flags = FNVNX_SHADER_KEEP_GLSL | FNVNX_SHADER_NO_DKSH | flags;
            fnvnx_shader_translate_glsl(f->bc, f->n, &o, &r);
            snprintf(path, sizeof path, "%s/stage/%s.f%u.json", odir, hs, flags);
            FILE *c = fopen(path, "w"); if (!c) { fprintf(stderr, "sm_link: cannot write %s\n", path); return 2; }
            const char *status = "ok"; char why[400] = ""; uint8_t sig[32] = { 0 }; int active = 0, disagree = 0, ndecl = 0; unsigned sh = 0; Pairs pr = { { 0 }, 0 }; Decl decls[64];
            if (!r.ok || !r.glsl) { status = "translate_fail"; snprintf(why, sizeof why, "%.399s", r.error); }
            else {
                sh = glCreateShader(f->stage ? GL_FRAGMENT_SHADER : GL_VERTEX_SHADER); const char *src[1] = { r.glsl }; int ok = 0;
                glShaderSource(sh, 1, src, NULL); glCompileShader(sh); glGetShaderiv(sh, GL_COMPILE_STATUS, &ok);
                if (!ok) { status = "compile_fail"; glGetShaderInfoLog(sh, (int)sizeof why, NULL, why); }
                else {
                    if (grammar_read(r.glsl, sig, decls, 64, &ndecl)) { status = "grammar_fail"; }
                    else { if (reflect_pairs(r.reflection_json, f->stage ? "varyings_read" : "varyings_written", &pr)) { status = "reflection_fail"; } disagree = query_read(sh, decls, ndecl, &active); if (disagree) { status = "signature_disagree"; mism += (unsigned long long)disagree; } }
                }
            }
            { char sg[65]; hex(sig, 32, sg);
              fprintf(c, "{\"format\": \"fnvnx-sm-stage/1\", \"bytecode_sha256\": \"%s\", \"stage\": \"%s\", \"variant_flags\": %u, \"status\": \"%s\", \"signature_sha256\": \"%s\", \"declared_varyings\": %d, \"active_varyings\": %d, \"disagreements\": %d, \"log\": ",
                      hs, f->stage ? "ps" : "vs", flags, status, sg, ndecl, active, disagree);
              jstr(c, why); fprintf(c, "}\n"); }
            fclose(c); stage_certs++;
            if (strcmp(status, "ok")) { red++; if (sh) glDeleteShader(sh); }
            else {
                Reps *R = f->stage ? &rp[fl] : &rv[fl];
                if (add_rep(R, sig, sh, i, flags, &pr)) return 2;
                if (f->stage == 0) {
                    int cb = (flags & FNVNX_SHADER_VS_COMPLETE) ? 1 : 0;
                    const unsigned bk = flags & ~(unsigned)FNVNX_SHADER_VS_COMPLETE;   /* the complete variant pairs with the base variant of the same other bits (ascending: base first) */
                    if (!cb) { memcpy(bsig[bk], sig, 32); bw[bk] = pr; bok[bk] = 1; }
                    else if (bok[bk] && add_vfull(&vf[fl], bsig[bk], sig, &bw[bk])) return 2;
                }
                if (R->r[R->n - 1].shader != sh) glDeleteShader(sh);   /* not the class representative */
            }
            fnvnx_shader_result_free(&r);
        }
    }
    for (int fl = 0; fl < 2; fl++) {
        /* The program the draw path builds for (VS, PS): the VS variant is a pure function of the pair (fnvnx_dk_layout.h, fnvnx_dk_vs_needs_complete): the complete
         * variant iff the PS reads a canonical slot the base VS does not write (FOG0, slot 18, is always written). Only that pair is linked and certified. */
        for (int a = 0; a < vf[fl].n; a++) for (int b = 0; b < rp[fl].n; b++) {
            const VFull *vc = &vf[fl].v[a]; const Rep *pc = &rp[fl].r[b];
            int cb = fnvnx_dk_vs_needs_complete(vc->w.p, vc->w.n, pc->pr.p, pc->pr.n); const uint8_t *vsig = cb ? vc->sc : vc->sb;
            const Rep *vr = find_rep(&rv[fl], vsig); char sa[65], sb[65];
            if (!vr) { red++; continue; }
            hex(vsig, 32, sa); hex(pc->sig, 32, sb);
            snprintf(path, sizeof path, "%s/link/%s.%s.f%d.json", odir, sa, sb, fl);
            { FILE *ex = fopen(path, "r"); if (ex) { fclose(ex); continue; } }   /* the same program of another vertex class */
            unsigned pr = glCreateProgram(); int ls = 0; char log[400] = "";
            glAttachShader(pr, vr->shader); glAttachShader(pr, pc->shader); glLinkProgram(pr); glGetProgramiv(pr, GL_LINK_STATUS, &ls);
            if (!ls) glGetProgramInfoLog(pr, (int)sizeof log, NULL, log);
            glDeleteProgram(pr);
            FILE *c = fopen(path, "w"); if (!c) { fprintf(stderr, "sm_link: cannot write %s\n", path); return 2; }
            fprintf(c, "{\"format\": \"fnvnx-sm-link/1\", \"vs_signature_sha256\": \"%s\", \"ps_signature_sha256\": \"%s\", \"flat_color\": %d, \"vs_complete\": %d, \"status\": \"%s\", \"log\": ", sa, sb, fl, cb, ls ? "linked" : "link_fail");
            jstr(c, log); fprintf(c, "}\n"); fclose(c); link_certs++;
            if (!ls) red++;
        }
    }
    snprintf(path, sizeof path, "%s/summary.json", odir);
    FILE *s = fopen(path, "w"); if (!s) return 2;
    fprintf(s, "{\"format\": \"fnvnx-sm-link-summary/1\", \"universe\": %d, \"red\": %ld, \"details\": {\"entries\": %d, \"unique_bytecodes\": %d, \"vs\": %ld, \"ps\": %ld, \"refused\": %ld, "
               "\"stage_certificates\": %ld, \"link_certificates\": %ld, \"vs_classes\": [%d, %d], \"ps_classes\": [%d, %d], \"signature_disagreements\": %llu}}\n",
            F.n, red, entries, F.n, nvs, nps, refused, stage_certs, link_certs, vf[0].n, vf[1].n, rp[0].n, rp[1].n, mism);
    fclose(s);
    printf("unique %d (vs %ld ps %ld) stage certs %ld link certs %ld red %ld\n", F.n, nvs, nps, stage_certs, link_certs, red);
    return red ? 1 : 0;
}
