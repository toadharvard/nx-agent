/* sm_link checker (item 4.6, rule text: render/shaders/total/SM_LINK_RULE.md). Three modes, each a hermetic run stage of the recipe:
 * [--deb DEB_ROOT] before the mode names the mount of the declared debs.
 *   sm_link_check stage   SHADER_DIR SHARD N OUT.jsonl            one certificate line per (bytecode, flag set) of the forms with index % N == SHARD
 *   sm_link_check link    SHADER_DIR SHARD N OUT.jsonl STAGE...   one certificate line per program (VS signature, PS signature, flat) with index % N == SHARD
 *   sm_link_check summary SHADER_DIR OUT.json STAGE... -- LINK... the totals and the completeness check of both certificate sets against the universe
 * SHADER_DIR: the *.sdp packages of gamedata/shaders (a declared external). The stage of an entry is the version token (0xFFFE.... vertex, 0xFFFF.... pixel).
 * Universe: every unique bytecode (sha256) of every package, in every flag set of the variant key (all submasks of FNVNX_SHADER_KEY_FLAGS, enumerated directly;
 * the flag list of fnvnx_shaders.h), both stages. A stage certificate is the compile of the translator's GLSL on the pinned Mesa (GL 4.5 core, llvmpipe, EGL surfaceless)
 * and the interface signature read two ways (the emission grammar; the Mesa program-interface query of a separable single-stage program).
 * A link certificate is the glLinkProgram of the program the draw path builds for a (vertex class, PS class): the VS variant fnvnx_dk_vs_needs_complete selects.
 * Output is a function of the inputs: sorted order, no times, no paths. Not covered yet (rule text section 3/5): link set L, FFP stages. The text Mesa compiles is the output of nxgl4_shim_glsl (render/gl4shim), the GL backend's own rewrite. */
#define _POSIX_C_SOURCE 200809L
#include <dirent.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fnvnx_shaders.h"
#include "fnvnx_sdp.h"
#include "nx_gl4shim.h"    /* the GL backend's own GLSL rewrite: the text Mesa compiles is the shim's output */
#include "nx_smscan.h"    /* the one scanner of the draw path: nx_sm_check, nx_sm_pairs */
#include "nx_pair.h"      /* the one selection function of the draw path: nx_pair_variant */
#include "fnvnx_dk_layout.h"   /* the product header: fnvnx_dk_vs_needs_complete is called, not copied */
#include "fnvnx_sha256.h"
/* The flag bits the enum of fnvnx_shaders.h defines: bits 0..log2(FNVNX_SHADER_FLAT_COLOR). Relies on FLAT_COLOR being the highest bit of the enum (reported: the header should
 * carry an all-flags macro next to KEY_FLAGS so that a new flag cannot be missed here). */
_Static_assert(__builtin_popcount(FNVNX_SHADER_KEY_FLAGS) <= 12, "the key space is enumerated: 2^popcount(KEY) flag sets");

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

typedef struct { uint8_t p[225]; int n; } Pairs;   /* the declared FNVNX_DK_PAIR codes of a shader (fnvnx_shader_declared_pairs) */

/* The flag sets of the variant key: every submask of FNVNX_SHADER_KEY_FLAGS (generated from the one flag list of fnvnx_shaders.h), enumerated directly, ascending. */
static unsigned fsets[1u << 12]; static int nfs;
static void make_fsets(void) {
    const unsigned K = FNVNX_SHADER_KEY_FLAGS; nfs = 0;
    for (unsigned m = K;; m = (m - 1) & K) { fsets[nfs++] = m; if (!m) break; }
    for (int a = 0, b = nfs - 1; a < b; a++, b--) { unsigned t = fsets[a]; fsets[a] = fsets[b]; fsets[b] = t; }
}

static const char *deb_root;   /* --deb ROOT: where the declared debs' files are mounted (the run stage's /deb); unset on a machine whose libraries are installed */
static int gl_init(void) {
    if (deb_root) {   /* the loader's own search paths of the GL vendor libraries, inside the mounted union */
        char v[1100]; snprintf(v, sizeof v, "%s/usr/share/glvnd/egl_vendor.d", deb_root); setenv("__EGL_VENDOR_LIBRARY_DIRS", v, 1);
        snprintf(v, sizeof v, "%s/usr/lib/x86_64-linux-gnu/dri", deb_root); setenv("LIBGL_DRIVERS_PATH", v, 1);
    }
    setenv("LIBGL_ALWAYS_SOFTWARE", "1", 1); setenv("GALLIUM_DRIVER", "llvmpipe", 1); setenv("EGL_PLATFORM", "surfaceless", 1);
    EGLDisplay d = eglGetPlatformDisplay(0x31DD, NULL, NULL); EGLint ma, mi;
    if (!d || !eglInitialize(d, &ma, &mi)) { fprintf(stderr, "sm_link: EGL init failed\n"); return -1; }
    eglBindAPI(0x30A2);
    const EGLint ca[] = { 0x3098, 4, 0x30FB, 5, 0x30FD, 1, 0x3038 };
    EGLContext cx = eglCreateContext(d, NULL, NULL, ca);
    if (!cx || !eglMakeCurrent(d, NULL, NULL, cx)) { fprintf(stderr, "sm_link: GL context failed\n"); return -1; }
    LOAD(glCreateShader); LOAD(glShaderSource); LOAD(glCompileShader); LOAD(glGetShaderiv); LOAD(glGetShaderInfoLog); LOAD(glDeleteShader);
    LOAD(glCreateProgram); LOAD(glAttachShader); LOAD(glLinkProgram); LOAD(glGetProgramiv); LOAD(glGetProgramInfoLog); LOAD(glDeleteProgram);
    LOAD(glProgramParameteri); LOAD(glGetProgramInterfaceiv); LOAD(glGetProgramResourceName); LOAD(glGetProgramResourceiv);
    return 0;
}

static int load_forms(const char *sdir, Forms *F, int *entries) {
    char **names = NULL; int nn = 0; DIR *D = opendir(sdir); *entries = 0; F->n = F->cap = 0; F->forms = NULL;
    if (!D) { fprintf(stderr, "sm_link: cannot open %s\n", sdir); return -1; }
    for (struct dirent *e; (e = readdir(D));) { size_t l = strlen(e->d_name); if (l > 4 && !strcmp(e->d_name + l - 4, ".sdp")) { names = realloc(names, sizeof(char *) * (size_t)(nn + 1)); names[nn++] = strdup(e->d_name); } }
    closedir(D); qsort(names, (size_t)nn, sizeof(char *), cmp_str);
    for (int i = 0; i < nn; i++) { char p[1024]; snprintf(p, sizeof p, "%s/%s", sdir, names[i]); if (read_sdp(F, p, entries)) { fprintf(stderr, "sm_link: bad package %s\n", names[i]); return -1; } }
    qsort(F->forms, (size_t)F->n, sizeof(Form), cmp_form);
    return 0;
}

/* One stage certificate: translate, compile, read the signature two ways. status: ok | translate_fail | compile_fail | grammar_fail | reflection_fail | signature_disagree.
 * Returns the compiled shader (0 if none) and fills sig / pairs. */
typedef struct { const char *status; char why[400]; uint8_t sig[32]; Pairs pr; unsigned major; int active, disagree, ndecl; unsigned shader; } StageRes;
static void do_stage(const Form *f, unsigned flags, StageRes *o) {
    fnvnx_shader_opts opt; fnvnx_shader_result r; Decl decls[64];
    memset(&opt, 0, sizeof opt); memset(&r, 0, sizeof r); memset(o, 0, sizeof *o); o->status = "ok";
    opt.flags = FNVNX_SHADER_KEEP_GLSL | FNVNX_SHADER_NO_DKSH | flags;
    fnvnx_shader_translate_glsl(f->bc, f->n, &opt, &r);
    if (!r.ok || !r.glsl) { o->status = "translate_fail"; snprintf(o->why, sizeof o->why, "%.399s", r.error); }
    else {
        unsigned sh = glCreateShader(f->stage ? GL_FRAGMENT_SHADER : GL_VERTEX_SHADER); int ok = 0;
        size_t gl = strlen(r.glsl), cap = 2 * gl + 4096; char *shim = malloc(cap);
        if (!shim || !nxgl4_shim_glsl(r.glsl, gl, (uint32_t)f->stage, shim, cap)) { free(shim); o->status = "shim_fail"; fnvnx_shader_result_free(&r); o->shader = sh; return; }
        const char *src[1] = { shim };
        glShaderSource(sh, 1, src, NULL); glCompileShader(sh); glGetShaderiv(sh, GL_COMPILE_STATUS, &ok); free(shim);
        o->shader = sh;
        if (!ok) { o->status = "compile_fail"; glGetShaderInfoLog(sh, (int)sizeof o->why, NULL, o->why); }
        else if (grammar_read(r.glsl, o->sig, decls, 64, &o->ndecl)) o->status = "grammar_fail";
        else {
            NxSmIface ifc; uint32_t *tk = malloc(f->n + 4), nb;   /* the declared pairs: nx_sm_pairs of the scan the draw path makes (the bytecode bytes are 32-bit tokens) */
            if (tk) memcpy(tk, f->bc, f->n);
            int rc = 0; nb = 0;
            if (!tk || !(nb = nx_sm_bytes(tk)) || (rc = nx_sm_check(tk, nb, f->stage, &ifc))) { o->status = "reflection_fail"; snprintf(o->why, sizeof o->why, "nx_sm_bytes %u nx_sm_check %d", nb, rc); }
            else { o->pr.n = (int)nx_sm_pairs(&ifc, o->pr.p); o->major = ifc.major; }
            free(tk);
            o->disagree = query_read(sh, decls, o->ndecl, &o->active);
            if (o->disagree) o->status = "signature_disagree";
        }
    }
    fnvnx_shader_result_free(&r);
}

/* ---- the stage certificate lines ---- */
typedef struct { uint8_t sha[32]; int stage; unsigned flags; int ok; uint8_t sig[32]; Pairs pr; unsigned major; int disagree; int active, ndecl; } SLine;
static const char *jkey(const char *ln, const char *key) { char pat[48]; snprintf(pat, sizeof pat, "\"%s\": ", key); const char *q = strstr(ln, pat); return q ? q + strlen(pat) : NULL; }
static int unhex(const char *h, uint8_t *o, int n) { for (int i = 0; i < n; i++) { unsigned v; if (sscanf(h + 2 * i, "%2x", &v) != 1) return -1; o[i] = (uint8_t)v; } return 0; }
static int parse_sline(const char *ln, SLine *s) {
    const char *q; memset(s, 0, sizeof *s);
    if (!(q = jkey(ln, "bytecode_sha256")) || unhex(q + 1, s->sha, 32)) return -1;
    if (!(q = jkey(ln, "stage"))) return -1;
    s->stage = atoi(q);
    if (!(q = jkey(ln, "variant_flags"))) return -1;
    s->flags = (unsigned)strtoul(q, NULL, 10);
    if (!(q = jkey(ln, "status"))) return -1;
    s->ok = !strncmp(q, "\"ok\"", 4);
    if (!(q = jkey(ln, "disagreements"))) return -1;
    s->disagree = atoi(q);
    if (!(q = jkey(ln, "active_varyings"))) return -1;
    s->active = atoi(q);
    if (!(q = jkey(ln, "declared_varyings"))) return -1;
    s->ndecl = atoi(q);
    if (s->ok) {
        if (!(q = jkey(ln, "signature_sha256")) || unhex(q + 1, s->sig, 32)) return -1;
        if (!(q = jkey(ln, "major"))) return -1;
        s->major = (unsigned)strtoul(q, NULL, 10);
        if (!(q = jkey(ln, "pairs")) || *q != '[') return -1;
        for (q++; *q && *q != ']';) { char *e; long v = strtol(q, &e, 10); if (e == q || s->pr.n >= 224) return -1; s->pr.p[s->pr.n++] = (uint8_t)v; q = e; while (*q == ',' || *q == ' ') q++; }
    }
    return 0;
}
static int cmp_sline(const void *a, const void *b) { const SLine *x = a, *y = b; int c = memcmp(x->sha, y->sha, 32); return c ? c : (x->flags > y->flags) - (x->flags < y->flags); }
typedef struct { SLine *l; long n; } SLines;
static int read_stage_files(int nf, char **fv, SLines *S) {
    S->l = NULL; S->n = 0; long cap = 0;
    for (int i = 0; i < nf; i++) {
        FILE *fp = fopen(fv[i], "r"); if (!fp) { fprintf(stderr, "sm_link: cannot read %s\n", fv[i]); return -1; }
        char *ln = NULL; size_t lc = 0;
        while (getline(&ln, &lc, fp) > 0) {
            if (S->n == cap) { cap = cap ? 2 * cap : 4096; S->l = realloc(S->l, sizeof(SLine) * (size_t)cap); if (!S->l) return -1; }
            if (parse_sline(ln, &S->l[S->n])) { fprintf(stderr, "sm_link: bad stage line in %s\n", fv[i]); return -1; }
            S->n++;
        }
        free(ln); fclose(fp);
    }
    qsort(S->l, (size_t)S->n, sizeof(SLine), cmp_sline);
    return 0;
}

/* ---- the program set: every (VS signature, PS signature, flat) the draw path builds for a (vertex class, PS class) ---- */
typedef struct { uint8_t vsig[32], psig[32]; int fl, cb; } Prog;
static int cmp_prog(const void *a, const void *b) { const Prog *x = a, *y = b; if (x->fl != y->fl) return x->fl - y->fl; int c = memcmp(x->vsig, y->vsig, 32); return c ? c : memcmp(x->psig, y->psig, 32); }
typedef struct { uint8_t sb[32], sc[32]; Pairs w; int fl; } VCls;
typedef struct { uint8_t sig[32]; Pairs pr; unsigned major; int fl; } PCls;
static int cmp_pairs(const Pairs *a, const Pairs *b) { if (a->n != b->n) return a->n - b->n; return memcmp(a->p, b->p, (size_t)a->n); }
static int cmp_vcls(const void *a, const void *b) { const VCls *x = a, *y = b; int c; if (x->fl != y->fl) return x->fl - y->fl; if ((c = memcmp(x->sb, y->sb, 32))) return c; if ((c = memcmp(x->sc, y->sc, 32))) return c; return cmp_pairs(&x->w, &y->w); }
static int cmp_pcls(const void *a, const void *b) { const PCls *x = a, *y = b; int c; if (x->fl != y->fl) return x->fl - y->fl; if ((c = memcmp(x->sig, y->sig, 32))) return c; if (x->major != y->major) return x->major < y->major ? -1 : 1; return cmp_pairs(&x->pr, &y->pr); }
static long rejected_pairs;   /* nx_pair_variant refused the pair (outside the link slots): the draw path cannot build it */
static long uncovered_L;   /* programs whose link set L is not empty: the draw path translates both stages per pair (link_pairs), which this certificate does not cover */
static Prog *programs(const SLines *S, long *np) {
    uncovered_L = 0; rejected_pairs = 0;
    VCls *v = NULL; long nv = 0; PCls *p = NULL; long npc = 0;
    for (long i = 0; i < S->n; i++) {
        const SLine *a = &S->l[i]; if (!a->ok) continue; int fl = (a->flags & FNVNX_SHADER_FLAT_COLOR) ? 1 : 0;
        if (a->stage == 1) { p = realloc(p, sizeof(PCls) * (size_t)(npc + 1)); memcpy(p[npc].sig, a->sig, 32); p[npc].pr = a->pr; p[npc].major = a->major; p[npc].fl = fl; npc++; }
        else if (!(a->flags & FNVNX_SHADER_VS_COMPLETE)) {   /* the complete variant of the same bytecode and the same other bits */
            SLine key; memcpy(key.sha, a->sha, 32); key.flags = a->flags | FNVNX_SHADER_VS_COMPLETE;
            const SLine *c = bsearch(&key, S->l, (size_t)S->n, sizeof(SLine), cmp_sline);
            if (!c || !c->ok) continue;
            v = realloc(v, sizeof(VCls) * (size_t)(nv + 1)); memcpy(v[nv].sb, a->sig, 32); memcpy(v[nv].sc, c->sig, 32); v[nv].w = a->pr; v[nv].fl = fl; nv++;
        }
    }
    qsort(v, (size_t)nv, sizeof(VCls), cmp_vcls); qsort(p, (size_t)npc, sizeof(PCls), cmp_pcls);
    Prog *pg = NULL; long n = 0;
    for (long a = 0; a < nv; a++) {
        if (a && !cmp_vcls(&v[a - 1], &v[a])) continue;
        for (long b = 0; b < npc; b++) {
            if (b && !cmp_pcls(&p[b - 1], &p[b])) continue;
            if (v[a].fl != p[b].fl) continue;
            uint32_t vv, pv, nl = 0; uint8_t L[2 * 224];   /* the draw path's selection function, not a copy of its rule */
            if (nx_pair_variant(v[a].w.p, (uint32_t)v[a].w.n, (uint8_t)p[b].major, p[b].pr.p, (uint32_t)p[b].pr.n, (uint32_t)v[a].fl, &vv, &pv, L, &nl) < 0) { rejected_pairs++; continue; }
            if (nl) { uncovered_L++; continue; }
            int cb = (vv & FNVNX_SHADER_VS_COMPLETE) != 0;
            pg = realloc(pg, sizeof(Prog) * (size_t)(n + 1)); memcpy(pg[n].vsig, cb ? v[a].sc : v[a].sb, 32); memcpy(pg[n].psig, p[b].sig, 32); pg[n].fl = v[a].fl; pg[n].cb = cb; n++;
        }
    }
    free(v); free(p);
    qsort(pg, (size_t)n, sizeof(Prog), cmp_prog);
    long m = 0; for (long i = 0; i < n; i++) if (!m || cmp_prog(&pg[m - 1], &pg[i])) pg[m++] = pg[i];
    *np = m; return pg;
}

/* ---- mode stage ---- */
static int mode_stage(int argc, char **argv) {
    if (argc != 6) { fprintf(stderr, "usage: sm_link_check stage SHADER_DIR SHARD N OUT.jsonl\n"); return 2; }
    int sh = atoi(argv[3]), N = atoi(argv[4]); if (N < 1 || sh < 0 || sh >= N) { fprintf(stderr, "sm_link: bad shard\n"); return 2; }
    Forms F; int entries; if (gl_init() || load_forms(argv[2], &F, &entries)) return 2; make_fsets();
    FILE *o = fopen(argv[5], "w"); if (!o) { fprintf(stderr, "sm_link: cannot write %s\n", argv[5]); return 2; }
    for (int i = sh; i < F.n; i += N) {
        const Form *f = &F.forms[i]; char hs[FNVNX_SHADER_KEY_HEX]; hex(f->sha, 32, hs);
        if (f->stage < 0) { fprintf(o, "{\"format\": \"fnvnx-sm-stage/1\", \"bytecode_sha256\": \"%s\", \"stage\": -1, \"variant_flags\": 0, \"status\": \"refused\", \"disagreements\": 0, \"active_varyings\": 0, \"declared_varyings\": 0}\n", hs); continue; }
        for (int v = 0; v < nfs; v++) {
            StageRes r; do_stage(f, fsets[v], &r); char sg[FNVNX_SHADER_KEY_HEX]; hex(r.sig, 32, sg);
            fprintf(o, "{\"format\": \"fnvnx-sm-stage/1\", \"bytecode_sha256\": \"%s\", \"stage\": %d, \"variant_flags\": %u, \"status\": \"%s\", \"signature_sha256\": \"%s\", \"pairs\": [", hs, f->stage, fsets[v], r.status, sg);
            for (int k = 0; k < r.pr.n; k++) fprintf(o, "%s%d", k ? ", " : "", r.pr.p[k]);
            fprintf(o, "], \"major\": %u, \"declared_varyings\": %d, \"active_varyings\": %d, \"disagreements\": %d, \"log\": ", r.major, r.ndecl, r.active, r.disagree); jstr(o, r.why); fprintf(o, "}\n");
            if (r.shader) glDeleteShader(r.shader);
        }
    }
    return fclose(o) ? 2 : 0;
}

/* ---- mode link ---- */
typedef struct { uint8_t sig[32]; unsigned shader; } Held;
typedef struct { Held *h; int n; } Helds;
static unsigned held_shader(Helds *H, const uint8_t sig[32], const SLines *S, const Forms *F, int stage) {
    for (int i = 0; i < H->n; i++) if (!memcmp(H->h[i].sig, sig, 32)) return H->h[i].shader;
    for (long i = 0; i < S->n; i++) {   /* the representative: the first certificate of this signature in (bytecode, flags) order */
        const SLine *a = &S->l[i]; if (!a->ok || a->stage != stage || memcmp(a->sig, sig, 32)) continue;
        Form key; memcpy(key.sha, a->sha, 32); const Form *f = bsearch(&key, F->forms, (size_t)F->n, sizeof(Form), cmp_form); if (!f) return 0;
        StageRes r; do_stage(f, a->flags, &r); if (strcmp(r.status, "ok") || memcmp(r.sig, sig, 32)) return 0;   /* a certificate that does not reproduce is no representative */
        H->h = realloc(H->h, sizeof(Held) * (size_t)(H->n + 1)); memcpy(H->h[H->n].sig, sig, 32); H->h[H->n].shader = r.shader; return H->h[H->n++].shader;
    }
    return 0;
}
static int mode_link(int argc, char **argv) {
    if (argc < 7) { fprintf(stderr, "usage: sm_link_check link SHADER_DIR SHARD N OUT.jsonl STAGE...\n"); return 2; }
    int sh = atoi(argv[3]), N = atoi(argv[4]); if (N < 1 || sh < 0 || sh >= N) { fprintf(stderr, "sm_link: bad shard\n"); return 2; }
    Forms F; int entries; SLines S; long np;
    if (gl_init() || load_forms(argv[2], &F, &entries) || read_stage_files(argc - 6, argv + 6, &S)) return 2;
    Prog *pg = programs(&S, &np); Helds H = { NULL, 0 };
    FILE *o = fopen(argv[5], "w"); if (!o) { fprintf(stderr, "sm_link: cannot write %s\n", argv[5]); return 2; }
    for (long i = sh; i < np; i += N) {
        char sa[FNVNX_SHADER_KEY_HEX], sb[FNVNX_SHADER_KEY_HEX]; hex(pg[i].vsig, 32, sa); hex(pg[i].psig, 32, sb);
        unsigned vs = held_shader(&H, pg[i].vsig, &S, &F, 0), ps = held_shader(&H, pg[i].psig, &S, &F, 1);
        int ls = 0; char log[400] = "no representative";
        if (vs && ps) {
            unsigned pr = glCreateProgram(); glAttachShader(pr, vs); glAttachShader(pr, ps); glLinkProgram(pr); glGetProgramiv(pr, GL_LINK_STATUS, &ls);
            log[0] = 0; if (!ls) glGetProgramInfoLog(pr, (int)sizeof log, NULL, log);
            glDeleteProgram(pr);
        }
        fprintf(o, "{\"format\": \"fnvnx-sm-link/1\", \"vs_signature_sha256\": \"%s\", \"ps_signature_sha256\": \"%s\", \"flat_color\": %d, \"vs_complete\": %d, \"status\": \"%s\", \"log\": ", sa, sb, pg[i].fl, pg[i].cb, ls ? "linked" : "link_fail");
        jstr(o, log); fprintf(o, "}\n");
    }
    return fclose(o) ? 2 : 0;
}

/* ---- mode summary: totals, and the completeness of both certificate sets against the universe (the stage set is exactly forms x flag sets; the link set is exactly the program set) ---- */
static int mode_summary(int argc, char **argv) {
    if (argc < 5) { fprintf(stderr, "usage: sm_link_check summary SHADER_DIR OUT.json STAGE... -- LINK...\n"); return 2; }
    int sep = -1; for (int i = 4; i < argc; i++) if (!strcmp(argv[i], "--")) { sep = i; break; }
    if (sep < 0) { fprintf(stderr, "sm_link: no -- between the stage and link files\n"); return 2; }
    Forms F; int entries; SLines S; long np;
    if (load_forms(argv[2], &F, &entries) || read_stage_files(sep - 4, argv + 4, &S)) return 2;
    make_fsets();
    long red = 0, refused = 0, nvs = 0, nps = 0, missing = 0, extra = 0, bad_stage = 0, disagree = 0;
    /* the stage set: expected = every non-refused form x every flag set (and one refused line per refused form); got = the lines read */
    long exp = 0; for (int i = 0; i < F.n; i++) { if (F.forms[i].stage < 0) { refused++; exp++; } else { exp += nfs; if (F.forms[i].stage) nps++; else nvs++; } }
    if (S.n != exp) { missing = exp > S.n ? exp - S.n : 0; extra = S.n > exp ? S.n - exp : 0; }
    for (long i = 0; i < S.n; i++) {
        if (i && !cmp_sline(&S.l[i - 1], &S.l[i])) extra++;   /* a duplicate line */
        Form key; memcpy(key.sha, S.l[i].sha, 32); const Form *f = bsearch(&key, F.forms, (size_t)F.n, sizeof(Form), cmp_form);
        if (!f) extra++;
        if (!S.l[i].ok) bad_stage++;
        disagree += S.l[i].disagree;
    }
    /* every (form, flag set) is present: walk both sorted sequences */
    { long k = 0; for (int i = 0; i < F.n; i++) { int want = F.forms[i].stage < 0 ? 1 : nfs;
          for (int v = 0; v < want; v++, k++) { unsigned fs = F.forms[i].stage < 0 ? 0 : fsets[v];
              if (k >= S.n || memcmp(S.l[k].sha, F.forms[i].sha, 32) || S.l[k].flags != fs) { missing++; k--; break; } } } }
    Prog *pg = programs(&S, &np);
    /* the link set */
    char **lf = argv + sep + 1; int nl = argc - sep - 1; long nlines = 0, linked = 0, lfail = 0, lbad = 0;
    for (int i = 0; i < nl; i++) {
        FILE *fp = fopen(lf[i], "r"); if (!fp) { fprintf(stderr, "sm_link: cannot read %s\n", lf[i]); return 2; }
        char *ln = NULL; size_t lc = 0;
        while (getline(&ln, &lc, fp) > 0) {
            const char *q; Prog k; memset(&k, 0, sizeof k); nlines++;
            if (!(q = jkey(ln, "vs_signature_sha256")) || unhex(q + 1, k.vsig, 32) || !(q = jkey(ln, "ps_signature_sha256")) || unhex(q + 1, k.psig, 32) || !(q = jkey(ln, "flat_color"))) { lbad++; continue; }
            k.fl = atoi(q);
            if (!bsearch(&k, pg, (size_t)np, sizeof(Prog), cmp_prog)) extra++;
            if ((q = jkey(ln, "status")) && !strncmp(q, "\"linked\"", 8)) linked++; else lfail++;
        }
        free(ln); fclose(fp);
    }
    if (nlines != np) { if (nlines < np) missing += np - nlines; else extra += nlines - np; }
    red = refused + bad_stage + disagree + lfail + lbad + missing + extra + rejected_pairs;
    FILE *s = fopen(argv[3], "w"); if (!s) return 2;
    fprintf(s, "{\"format\": \"fnvnx-sm-link-summary/1\", \"universe\": %d, \"red\": %ld, \"details\": {\"entries\": %d, \"unique_bytecodes\": %d, \"vs\": %ld, \"ps\": %ld, \"flag_sets\": %d, \"refused\": %ld, "
               "\"stage_certificates\": %ld, \"stage_not_ok\": %ld, \"signature_disagreements\": %ld, \"programs\": %ld, \"link_certificates\": %ld, \"linked\": %ld, \"link_fail\": %ld, "
               "\"bad_link_lines\": %ld, \"missing_certificates\": %ld, \"unexpected_certificates\": %ld, \"link_set_programs_uncovered\": %ld, \"pairs_rejected\": %ld}}\n",
            F.n, red, entries, F.n, nvs, nps, nfs, refused, S.n, bad_stage, disagree, np, nlines, linked, lfail, lbad, missing, extra, uncovered_L, rejected_pairs);
    return fclose(s) ? 2 : 0;
}

int main(int argc, char **argv) {
    if (argc >= 3 && !strcmp(argv[1], "--deb")) { deb_root = argv[2]; argv += 2; argc -= 2; argv[0] = "sm_link_check"; }
    if (argc >= 2 && !strcmp(argv[1], "stage")) return mode_stage(argc, argv);
    if (argc >= 2 && !strcmp(argv[1], "link")) return mode_link(argc, argv);
    if (argc >= 2 && !strcmp(argv[1], "summary")) return mode_summary(argc, argv);
    fprintf(stderr, "usage: sm_link_check stage|link|summary ...\n"); return 2;
}
