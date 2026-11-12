/*
 * sdp2dksh - thin CLI over libfnvnx_shaders.
 *
 *   sdp2dksh [-o DIR] [--glsl] [--no-dksh] [--strict] [--variants] [--vs-complete] [--link U:I,...] PACKAGE.sdp...   translate every entry
 *   sdp2dksh [-o DIR] [--glsl] --blob FILE...                         translate raw bytecode files
 *   sdp2dksh --list PACKAGE.sdp                                       list entries
 *   sdp2dksh --ctab [--blob] FILE...                                  dump constant tables (TSV)
 *   sdp2dksh --pack OUT.sdp NAME=FILE...                              build a package (tests)
 *
 * Output per unique bytecode (deduplicated by hash): DIR/<hash>.dksh, DIR/<hash>.json
 * (reflection), DIR/<hash>.glsl with --glsl; DIR/index.tsv lists every entry with its stats.
 * --variants: also the static epilogue variants of every pixel shader that has an epilogue,
 * DIR/<hash>__e0.dksh / __ea.dksh / __ef.dksh (FNVNX_SHADER_EPI_*), listed in DIR/variants.tsv.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fnvnx_sdp.h"
#include "fnvnx_shaders.h"
#include "fnvnx_dk_layout.h"
#include "fnvnx_sha256.h"

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

typedef struct { const char *dir; uint32_t flags; int strict; FILE *index, *variants; int total, ok, fail, dup;
                 char (*seen)[FNVNX_SHADER_KEY_HEX]; size_t nseen, capseen; } ctx_t;   /* hex SHA-256 keys of the bytecodes seen */

static int seen(ctx_t *c, const char *h)
{
    for (size_t i = 0; i < c->nseen; i++) if (!strcmp(c->seen[i], h)) return 1;
    if (c->nseen == c->capseen) {
        c->capseen = c->capseen ? c->capseen * 2 : 256;
        c->seen = (char (*)[FNVNX_SHADER_KEY_HEX])realloc(c->seen, c->capseen * sizeof *c->seen);
    }
    memcpy(c->seen[c->nseen++], h, FNVNX_SHADER_KEY_HEX);
    return 0;
}

/* the static epilogue variants of one pixel shader; none when its GLSL has no epilogue guard */
static void variants(ctx_t *c, const char *h, const void *bc, size_t n)
{
    static const struct { const char *tag; uint32_t f; } v[] = {
        { "__e0", 0 }, { "__ea", FNVNX_SHADER_EPI_ALPHA }, { "__ef", FNVNX_SHADER_EPI_FOG } };
    fnvnx_shader_opts g = { FNVNX_SHADER_KEEP_GLSL | FNVNX_SHADER_NO_DKSH, NULL };
    fnvnx_shader_result r;
    fnvnx_shader_translate(bc, n, &g, &r);
    int has = r.ok && r.glsl && strstr(r.glsl, "DK_EPI_ALPHA");
    fnvnx_shader_result_free(&r);
    if (!has) return;
    for (size_t i = 0; i < sizeof v / sizeof v[0]; i++) {
        fnvnx_shader_opts o = { (c->flags & ~FNVNX_SHADER_NO_DKSH) | FNVNX_SHADER_EPI_STATIC | v[i].f, NULL };
        fnvnx_shader_translate(bc, n, &o, &r);
        char path[4096];
        if (r.dksh) { snprintf(path, sizeof path, "%s/%s%s.dksh", c->dir, h, v[i].tag); spit(path, r.dksh, r.dksh_size); }
        if (r.glsl) { snprintf(path, sizeof path, "%s/%s%s.glsl", c->dir, h, v[i].tag); spit(path, r.glsl, strlen(r.glsl)); }
        fprintf(c->variants, "%s\t%s\t%s\t%u\t%u\t%u\t%s\n", h, v[i].tag, r.ok ? "ok" : "fail",
                r.stats.dk_instructions, r.stats.dk_gprs, r.stats.dk_code_size, r.ok ? "" : r.error);
        fnvnx_shader_result_free(&r);
    }
}

static unsigned char g_link[224]; static uint32_t g_nlink;   /* --link USAGE:INDEX,... (the draw's varying link set) */

static void one(ctx_t *c, const char *pkg, const char *name, const void *bc, size_t n)
{
    fnvnx_shader_opts o = { c->flags, NULL, g_nlink ? g_link : NULL, g_nlink };
    fnvnx_shader_result r;
    c->total++;
    uint8_t key[32]; char h[FNVNX_SHADER_KEY_HEX]; fnvnx_sha256(bc, n, key); fnvnx_shader_key_hex(key, h);
    int dup = seen(c, h);
    fnvnx_shader_translate(bc, n, &o, &r);
    char path[4096];
    if (!dup) {
        if (r.dksh) { snprintf(path, sizeof path, "%s/%s.dksh", c->dir, h); spit(path, r.dksh, r.dksh_size); }
        if (r.reflection_json) { snprintf(path, sizeof path, "%s/%s.json", c->dir, h); spit(path, r.reflection_json, strlen(r.reflection_json)); }
        if (r.glsl) { snprintf(path, sizeof path, "%s/%s.glsl", c->dir, h); spit(path, r.glsl, strlen(r.glsl)); }
    } else c->dup++;
    if (!dup && c->variants && r.ok && r.stage == FNVNX_STAGE_PIXEL) variants(c, h, bc, n);
    if (r.ok) c->ok++; else { c->fail++; fprintf(stderr, "FAIL %s:%s: %s\n", pkg, name, r.error); }
    fprintf(c->index, "%s\t%s\t%s\t%s_%d_%d\t%s\t%u\t%u\t%u\t%u\t%u\t%s\n", h, pkg, name,
            r.stage == FNVNX_STAGE_VERTEX ? "vs" : r.stage == FNVNX_STAGE_PIXEL ? "ps" : "??", r.major, r.minor,
            r.ok ? "ok" : "fail", r.stats.d3d_instructions, r.stats.glsl_statements, r.stats.dk_instructions,
            r.stats.dk_gprs, r.stats.dk_code_size, r.ok ? "" : r.error);
    fnvnx_shader_result_free(&r);
}

static void ctab_one(const char *pkg, const char *name, const void *bc, size_t n)
{
    fnvnx_ctab_entry *e; char err[512];
    int k = fnvnx_shader_ctab(bc, n, &e, err, sizeof err);
    uint8_t key[32]; char h[FNVNX_SHADER_KEY_HEX]; fnvnx_sha256(bc, n, key); fnvnx_shader_key_hex(key, h);
    if (k < 0) { printf("%s\t%s\t%s\terror\t%s\n", pkg, name, h, err); return; }
    if (k == 0) printf("%s\t%s\t%s\t-\n", pkg, name, h);
    for (int i = 0; i < k; i++)
        printf("%s\t%s\t%s\t%s\t%s\t%u\t%u\t%s\t%s\t%u\t%u\t%u\n", pkg, name, h, e[i].name, e[i].set,
               e[i].reg, e[i].count, e[i].cls, e[i].type, e[i].rows, e[i].columns, e[i].elements);
    free(e);
}

static int usage(void)
{
    fprintf(stderr, "usage: sdp2dksh [-o DIR] [--glsl] [--no-dksh] [--strict] [--variants] [--numerics wined3d|nine] PACKAGE.sdp...\n"
                    "       sdp2dksh [-o DIR] [--glsl] --blob FILE...\n"
                    "       sdp2dksh --list PACKAGE.sdp\n"
                    "       sdp2dksh --ctab [--blob] FILE...\n"
                    "       sdp2dksh --pack OUT.sdp NAME=FILE...\n"
                    "--numerics: D3D9's implementation-defined rcp/rsq/log/nrm at 0 as WineD3D (default) or Gallium Nine\n"
                    "            (V6n's oracle); FNVNX_NUMERICS=nine in the environment selects nine too\n");
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
    ctx_t c = { ".", 0, 0, NULL, NULL, 0, 0, 0, 0, NULL, 0, 0 };
    int blob = 0, list = 0, ctab = 0, vars = 0, numerics_set = 0, i = 1;
    for (; i < argc && argv[i][0] == '-'; i++) {
        if (!strcmp(argv[i], "-o") && i + 1 < argc) c.dir = argv[++i];
        else if (!strcmp(argv[i], "--glsl")) c.flags |= FNVNX_SHADER_KEEP_GLSL;
        else if (!strcmp(argv[i], "--no-dksh")) c.flags |= FNVNX_SHADER_NO_DKSH;
        else if (!strcmp(argv[i], "--strict")) c.strict = 1;
        else if (!strcmp(argv[i], "--vs-complete")) c.flags |= FNVNX_SHADER_VS_COMPLETE;   /* vertex shaders: the complete variant (fnvnx_dk_layout.h) */
        else if (!strcmp(argv[i], "--variants")) vars = 1;
        else if (!strcmp(argv[i], "--blob")) blob = 1;
        else if (!strcmp(argv[i], "--list")) list = 1;
        else if (!strcmp(argv[i], "--ctab")) list = ctab = 1;
        else if (!strcmp(argv[i], "--pack")) return do_pack(argc - i - 1, argv + i + 1);
        else if (!strcmp(argv[i], "--link") && i + 1 < argc) {
            for (const char *q = argv[++i]; *q && g_nlink < 224; ) {
                int u, x, k;
                if (sscanf(q, "%d:%d%n", &u, &x, &k) != 2 || u < 0 || u > 13 || x < 0 || x > 15) { fprintf(stderr, "bad --link\n"); return 2; }
                g_link[g_nlink++] = FNVNX_DK_PAIR(u, x); q += k; if (*q == ',') q++;
            }
        }
        else if (!strcmp(argv[i], "--numerics") && i + 1 < argc) {
            const char *m = argv[++i];
            if (!strcmp(m, "nine")) c.flags |= FNVNX_SHADER_NUMERICS_NINE;
            else if (!strcmp(m, "wined3d")) c.flags &= ~FNVNX_SHADER_NUMERICS_NINE;
            else return usage();
            numerics_set = 1;
        }
        else return usage();
    }
    if (!numerics_set && getenv("FNVNX_NUMERICS") && !strcmp(getenv("FNVNX_NUMERICS"), "nine"))
        c.flags |= FNVNX_SHADER_NUMERICS_NINE;
    if (i >= argc) return usage();
    if (ctab) printf("package\tname\thash\tconstant\tset\treg\tcount\tclass\ttype\trows\tcolumns\telements\n");
    if (!list) {
        char p[4096]; snprintf(p, sizeof p, "%s/index.tsv", c.dir);
        c.index = fopen(p, "w");
        if (!c.index) { fprintf(stderr, "cannot write %s\n", p); return 1; }
        fprintf(c.index, "hash\tpackage\tname\tmodel\tstatus\td3d_instr\tglsl_stmts\tdk_instr\tdk_gprs\tdk_code\terror\n");
        if (vars) {
            snprintf(p, sizeof p, "%s/variants.tsv", c.dir);
            c.variants = fopen(p, "w");
            if (!c.variants) { fprintf(stderr, "cannot write %s\n", p); return 1; }
            fprintf(c.variants, "hash\tvariant\tstatus\tdk_instr\tdk_gprs\tdk_code\terror\n");
        }
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
    if (c.variants) fclose(c.variants);
    if (c.index) {
        fclose(c.index);
        printf("sdp2dksh: %d shaders, %d ok, %d failed, %d duplicates\n", c.total, c.ok, c.fail, c.dup);
    }
    free(c.seen);
    return bad ? 1 : (c.strict && c.fail) ? 1 : 0;
}
