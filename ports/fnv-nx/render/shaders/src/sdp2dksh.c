/*
 * sdp2dksh - thin CLI over libfnvnx_shaders.
 *
 *   sdp2dksh [-o DIR] [--glsl] [--no-dksh] [--no-variants] [--strict] PACKAGE.sdp...   translate every entry
 *   sdp2dksh [-o DIR] [--glsl] --blob FILE...                         translate raw bytecode files
 *   sdp2dksh --list PACKAGE.sdp                                       list entries
 *   sdp2dksh --ctab [--blob] FILE...                                  dump constant tables (TSV)
 *   sdp2dksh --pack OUT.sdp NAME=FILE...                              build a package (tests)
 *
 * Output per unique bytecode (deduplicated by hash): DIR/<hash>.dksh, DIR/<hash>.json
 * (reflection), DIR/<hash>.glsl with --glsl; pixel programs with a D3D9 epilogue also
 * DIR/<hash>__e0.dksh, __ea, __ef (compile-time epilogue variants; --no-variants skips them).
 * DIR/index.tsv lists every entry with its stats (dk_instr_e0/_ea/_ef: the variants, "-" when none).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fnvnx_sdp.h"
#include "fnvnx_shaders.h"

static uint8_t *slurp(const char *path, size_t *len)
{
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    uint8_t *b = (uint8_t *)malloc(n > 0 ? (size_t)n : 1);
    if (b && fread(b, 1, (size_t)n, f) != (size_t)n) { free(b); b = NULL; }
    fclose(f);
    *len = (size_t)n;
    return b;
}
static int spit(const char *path, const void *p, size_t n)
{
    FILE *f = fopen(path, "wb");
    if (!f) return -1;
    int ok = fwrite(p, 1, n, f) == n;
    return (fclose(f) == 0 && ok) ? 0 : -1;
}

typedef struct { uint64_t h; char *row; int ok; char *err; } seen_t;   /* row: the index columns after the name */
typedef struct { const char *dir; uint32_t flags; int strict; FILE *index; int total, ok, fail, dup;
                 seen_t *seen; size_t nseen, capseen; } ctx_t;

static seen_t *seen(ctx_t *c, uint64_t h)
{
    for (size_t i = 0; i < c->nseen; i++) if (c->seen[i].h == h) return &c->seen[i];
    return NULL;
}

static void remember(ctx_t *c, uint64_t h, const char *row, int ok, const char *err)
{
    if (c->nseen == c->capseen) {
        c->capseen = c->capseen ? c->capseen * 2 : 256;
        c->seen = (seen_t *)realloc(c->seen, c->capseen * sizeof(seen_t));
    }
    seen_t *s = &c->seen[c->nseen++];
    s->h = h; s->row = strdup(row); s->ok = ok; s->err = strdup(err);
}

static void one(ctx_t *c, const char *pkg, const char *name, const void *bc, size_t n)
{
    fnvnx_shader_opts o = { c->flags, NULL };
    fnvnx_shader_result r;
    c->total++;
    uint64_t h = fnvnx_shader_hash(bc, n);
    const seen_t *s = seen(c, h);
    if (s) {   /* identical bytecode: the first translation's row */
        c->dup++;
        if (s->ok) c->ok++; else { c->fail++; fprintf(stderr, "FAIL %s:%s: %s\n", pkg, name, s->err); }
        fprintf(c->index, "%016llx\t%s\t%s\t%s\n", (unsigned long long)h, pkg, name, s->row);
        return;
    }
    fnvnx_shader_translate(bc, n, &o, &r);
    char path[4096];
    if (r.dksh) { snprintf(path, sizeof path, "%s/%016llx.dksh", c->dir, (unsigned long long)h); spit(path, r.dksh, r.dksh_size); }
    for (int k = 0; k < r.num_variants; k++) {
        snprintf(path, sizeof path, "%s/%016llx%s.dksh", c->dir, (unsigned long long)h, fnvnx_epi_suffix[k]);
        spit(path, r.variants[k].dksh, r.variants[k].dksh_size);
    }
    if (r.reflection_json) { snprintf(path, sizeof path, "%s/%016llx.json", c->dir, (unsigned long long)h); spit(path, r.reflection_json, strlen(r.reflection_json)); }
    if (r.glsl) { snprintf(path, sizeof path, "%s/%016llx.glsl", c->dir, (unsigned long long)h); spit(path, r.glsl, strlen(r.glsl)); }
    if (r.ok) c->ok++; else { c->fail++; fprintf(stderr, "FAIL %s:%s: %s\n", pkg, name, r.error); }
    char row[2048], v[3][16];
    for (int k = 0; k < FNVNX_EPI_COUNT; k++)
        if (k < r.num_variants && r.ok) snprintf(v[k], sizeof v[k], "%u", r.variants[k].stats.dk_instructions);
        else snprintf(v[k], sizeof v[k], "-");
    snprintf(row, sizeof row, "%s_%d_%d\t%s\t%u\t%u\t%u\t%u\t%u\t%s\t%s\t%s\t%s",
             r.stage == FNVNX_STAGE_VERTEX ? "vs" : r.stage == FNVNX_STAGE_PIXEL ? "ps" : "??", r.major, r.minor,
             r.ok ? "ok" : "fail", r.stats.d3d_instructions, r.stats.glsl_statements, r.stats.dk_instructions,
             r.stats.dk_gprs, r.stats.dk_code_size, r.ok ? "" : r.error, v[0], v[1], v[2]);
    fprintf(c->index, "%016llx\t%s\t%s\t%s\n", (unsigned long long)h, pkg, name, row);
    remember(c, h, row, r.ok, r.error);
    fnvnx_shader_result_free(&r);
}

static void ctab_one(const char *pkg, const char *name, const void *bc, size_t n)
{
    fnvnx_ctab_entry *e; char err[512];
    int k = fnvnx_shader_ctab(bc, n, &e, err, sizeof err);
    unsigned long long h = (unsigned long long)fnvnx_shader_hash(bc, n);
    if (k < 0) { printf("%s\t%s\t%016llx\terror\t%s\n", pkg, name, h, err); return; }
    if (k == 0) printf("%s\t%s\t%016llx\t-\n", pkg, name, h);
    for (int i = 0; i < k; i++)
        printf("%s\t%s\t%016llx\t%s\t%s\t%u\t%u\t%s\t%s\t%u\t%u\t%u\n", pkg, name, h, e[i].name, e[i].set,
               e[i].reg, e[i].count, e[i].cls, e[i].type, e[i].rows, e[i].columns, e[i].elements);
    free(e);
}

static int usage(void)
{
    fprintf(stderr, "usage: sdp2dksh [-o DIR] [--glsl] [--no-dksh] [--no-variants] [--strict] PACKAGE.sdp...\n"
                    "       sdp2dksh [-o DIR] [--glsl] --blob FILE...\n"
                    "       sdp2dksh --list PACKAGE.sdp\n"
                    "       sdp2dksh --ctab [--blob] FILE...\n"
                    "       sdp2dksh --pack OUT.sdp NAME=FILE...\n");
    return 2;
}

static int do_pack(int argc, char **argv)
{
    if (argc < 1) return usage();
    int n = argc - 1;
    fnvnx_sdp_entry *e = (fnvnx_sdp_entry *)calloc(n ? n : 1, sizeof *e);
    for (int i = 0; i < n; i++) {
        char *eq = strchr(argv[i + 1], '=');
        if (!eq) return usage();
        size_t nl = (size_t)(eq - argv[i + 1]);
        if (nl > FNVNX_SDP_NAME_SIZE) nl = FNVNX_SDP_NAME_SIZE;
        memcpy(e[i].name, argv[i + 1], nl);
        size_t len;
        e[i].data = slurp(eq + 1, &len);
        if (!e[i].data) { fprintf(stderr, "cannot read %s\n", eq + 1); return 1; }
        e[i].size = (uint32_t)len;
    }
    uint8_t *out; size_t outlen;
    if (fnvnx_sdp_write(e, (uint32_t)n, &out, &outlen) || spit(argv[0], out, outlen)) { fprintf(stderr, "cannot write %s\n", argv[0]); return 1; }
    return 0;
}

int main(int argc, char **argv)
{
    ctx_t c = { ".", 0, 0, NULL, 0, 0, 0, 0, NULL, 0, 0 };
    int blob = 0, list = 0, ctab = 0, i = 1;
    for (; i < argc && argv[i][0] == '-'; i++) {
        if (!strcmp(argv[i], "-o") && i + 1 < argc) c.dir = argv[++i];
        else if (!strcmp(argv[i], "--glsl")) c.flags |= FNVNX_SHADER_KEEP_GLSL;
        else if (!strcmp(argv[i], "--no-dksh")) c.flags |= FNVNX_SHADER_NO_DKSH;
        else if (!strcmp(argv[i], "--no-variants")) c.flags |= FNVNX_SHADER_NO_VARIANTS;
        else if (!strcmp(argv[i], "--strict")) c.strict = 1;
        else if (!strcmp(argv[i], "--blob")) blob = 1;
        else if (!strcmp(argv[i], "--list")) list = 1;
        else if (!strcmp(argv[i], "--ctab")) list = ctab = 1;
        else if (!strcmp(argv[i], "--pack")) return do_pack(argc - i - 1, argv + i + 1);
        else return usage();
    }
    if (i >= argc) return usage();
    if (ctab) printf("package\tname\thash\tconstant\tset\treg\tcount\tclass\ttype\trows\tcolumns\telements\n");
    if (!list) {
        char p[4096]; snprintf(p, sizeof p, "%s/index.tsv", c.dir);
        c.index = fopen(p, "w");
        if (!c.index) { fprintf(stderr, "cannot write %s\n", p); return 1; }
        fprintf(c.index, "hash\tpackage\tname\tmodel\tstatus\td3d_instr\tglsl_stmts\tdk_instr\tdk_gprs\tdk_code\terror"
                         "\tdk_instr_e0\tdk_instr_ea\tdk_instr_ef\n");
    }
    int bad = 0;
    for (; i < argc; i++) {
        size_t len;
        uint8_t *buf = slurp(argv[i], &len);
        if (!buf) { fprintf(stderr, "cannot read %s\n", argv[i]); bad = 1; continue; }
        if (blob && ctab) { const char *b = strrchr(argv[i], '/'); ctab_one("-", b ? b + 1 : argv[i], buf, len); free(buf); continue; }
        if (blob) { const char *b = strrchr(argv[i], '/'); one(&c, "-", b ? b + 1 : argv[i], buf, len); free(buf); continue; }
        fnvnx_sdp sdp; char err[256];
        if (fnvnx_sdp_parse(buf, len, &sdp, err, sizeof err)) { fprintf(stderr, "%s: %s\n", argv[i], err); free(buf); bad = 1; continue; }
        for (uint32_t k = 0; k < sdp.count; k++) {
            if (ctab) ctab_one(argv[i], sdp.entries[k].name, sdp.entries[k].data, sdp.entries[k].size);
            else if (list) printf("%s\t%u\t%u\n", sdp.entries[k].name, sdp.entries[k].offset, sdp.entries[k].size);
            else one(&c, argv[i], sdp.entries[k].name, sdp.entries[k].data, sdp.entries[k].size);
        }
        fnvnx_sdp_free(&sdp);
        free(buf);
    }
    if (c.index) {
        fclose(c.index);
        printf("sdp2dksh: %d shaders, %d ok, %d failed, %d duplicates\n", c.total, c.ok, c.fail, c.dup);
    }
    for (size_t k = 0; k < c.nseen; k++) { free(c.seen[k].row); free(c.seen[k].err); }
    free(c.seen);
    return bad ? 1 : (c.strict && c.fail) ? 1 : 0;
}
