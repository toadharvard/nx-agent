/* shxlate.c - translates every distinct bytecode stage-alone, and both stages of every recorded (VS, PS) pair whose link set L is non-canonical
 * (shcache_link_set), with libfnvnx_shaders: the entries of the shader cache.
 *   shxlate [--reverse] OUTDIR D9S_DIR...      each D9S_DIR written by d9s_shaders.py (<hash>.bin, pairs.tsv)
 * OUTDIR: <id>.dksh <id>.json <id>.glsl per entry, entries.tsv (id code_hash code_len stage flags l_hex dksh json glsl d3d_instr dk_instr), links.tsv
 * (vs ps l_hex: the L both stages of the pair were translated with, '-' = none), timing.tsv (id microseconds; not part of any comparison), fail.tsv.
 * id = <hash>-<v|p>-<flags hex>-<l hex or n>.  Every translation is run twice, with and without FNVNX_SHADER_KEEP_GLSL, and the DKSH and reflection must be
 * equal (the cache holds what the run-time call without KEEP_GLSL returns); every DKSH passes fnvnx_dksh_validate.  Output depends on the input
 * set only, not on the order of work (--reverse processes the work list backwards: the determinism check runs both).
 * Build: see tests/l6/shcache/run_real.sh. Exit 0 if everything translated, 1 otherwise (failures listed in fail.tsv). */
#define _POSIX_C_SOURCE 200809L
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "fnvnx_shaders.h"
#include "shcache.h"

typedef struct { uint64_t h; uint8_t *bc; size_t n; char *json; int stage; } Bin;
typedef struct { int v, p; } Pair;
typedef struct { uint64_t h; int bin, stage; uint8_t l[SHCACHE_MAX_L]; uint32_t nl; char id[80]; } Work;

static Bin *bins; static int nbins; static Pair *pairs; static int npairs; static Work *work; static int nwork;
static FILE *fent, *ftim, *ffail; static const char *outdir; static int failures;

static uint8_t *slurp(const char *path, size_t *len)
{
    FILE *f = fopen(path, "rb"); uint8_t *b; long n;
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); n = ftell(f); rewind(f);
    b = (uint8_t *)malloc(n > 0 ? (size_t)n : 1);
    if (b && fread(b, 1, (size_t)n, f) != (size_t)n) { free(b); b = NULL; }
    fclose(f); *len = (size_t)n; return b;
}
static int spit(const char *name, const char *ext, const void *p, size_t n)
{
    char path[1024]; FILE *f; int ok;
    snprintf(path, sizeof path, "%s/%s.%s", outdir, name, ext);
    f = fopen(path, "wb"); if (!f) return -1;
    ok = fwrite(p, 1, n, f) == n; return (fclose(f) == 0 && ok) ? 0 : -1;
}
static int cmp_str(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }
static int find_bin(uint64_t h) { int i; for (i = 0; i < nbins; i++) if (bins[i].h == h) return i; return -1; }
static void failed(const char *id, const char *why) { fprintf(ffail, "%s\t%s\n", id, why); failures++; }
static double now_us(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e6 + t.tv_nsec / 1e3; }

static int load_dir(const char *dir)
{
    DIR *d = opendir(dir); struct dirent *e; char **names = NULL; int nn = 0, i; char path[1024], line[256];
    FILE *f;
    if (!d) { fprintf(stderr, "shxlate: cannot open %s\n", dir); return -1; }
    while ((e = readdir(d))) { size_t l = strlen(e->d_name); if (l == 20 && !strcmp(e->d_name + 16, ".bin")) { names = (char **)realloc(names, (nn + 1) * sizeof *names); names[nn++] = strdup(e->d_name); } }
    closedir(d); qsort(names, nn, sizeof *names, cmp_str);
    for (i = 0; i < nn; i++) {
        size_t n; uint64_t h = strtoull(names[i], NULL, 16); int k; uint8_t *bc;
        snprintf(path, sizeof path, "%s/%s", dir, names[i]); bc = slurp(path, &n);
        if (!bc || shcache_hash64(bc, n) != h) { fprintf(stderr, "shxlate: %s is not its own hash\n", path); return -1; }
        if ((k = find_bin(h)) >= 0) { if (bins[k].n != n || memcmp(bins[k].bc, bc, n)) { fprintf(stderr, "shxlate: hash collision at %s\n", names[i]); return -1; } free(bc); }
        else { bins = (Bin *)realloc(bins, (nbins + 1) * sizeof *bins); bins[nbins].h = h; bins[nbins].bc = bc; bins[nbins].n = n; bins[nbins].json = NULL; bins[nbins++].stage = -1; }
        free(names[i]);
    }
    free(names);
    snprintf(path, sizeof path, "%s/pairs.tsv", dir); f = fopen(path, "r");
    if (!f) { fprintf(stderr, "shxlate: no %s\n", path); return -1; }
    if (!fgets(line, sizeof line, f)) return -1;
    while (fgets(line, sizeof line, f)) {
        unsigned long long a, b; if (sscanf(line, "%llx %llx", &a, &b) != 2) return -1;
        pairs = (Pair *)realloc(pairs, (npairs + 1) * sizeof *pairs);
        { Pair *q = &pairs[npairs]; int vi = find_bin(a), pi = find_bin(b); if (vi < 0 || pi < 0) { fprintf(stderr, "shxlate: pair names an unknown shader\n"); return -1; } q->v = vi; q->p = pi; }
        npairs++;
    }
    fclose(f); return 0;
}

static int cmp_pair(const void *a, const void *b) { const Pair *x = a, *y = b; return x->v != y->v ? x->v - y->v : x->p - y->p; }
static int cmp_work(const void *a, const void *b) { return strcmp(((const Work *)a)->id, ((const Work *)b)->id); }

static void add_work(int bin, uint32_t nl, const uint8_t *l)
{
    int i; Work w; memset(&w, 0, sizeof w);
    w.h = bins[bin].h; w.bin = bin; w.stage = bins[bin].stage; w.nl = nl; memcpy(w.l, l, nl);
    snprintf(w.id, sizeof w.id, "%016llx-%c-0-", (unsigned long long)w.h, w.stage ? 'p' : 'v');
    if (!nl) strcat(w.id, "n"); else for (i = 0; i < (int)nl; i++) sprintf(w.id + strlen(w.id), "%02x", l[i]);
    for (i = 0; i < nwork; i++) if (!strcmp(work[i].id, w.id)) return;
    work = (Work *)realloc(work, (nwork + 1) * sizeof *work); work[nwork++] = w;
}

static void do_work(const Work *w)
{
    fnvnx_shader_opts o; fnvnx_shader_result a, b; char err[256]; double t0 = now_us(); const Bin *bn = &bins[w->bin];
    memset(&o, 0, sizeof o); o.flags = FNVNX_SHADER_KEEP_GLSL; o.link_pairs = w->nl ? w->l : NULL; o.n_link_pairs = w->nl;
    if (!fnvnx_shader_translate(bn->bc, bn->n, &o, &a)) { failed(w->id, a.error); fnvnx_shader_result_free(&a); return; }
    o.flags = 0;
    if (!fnvnx_shader_translate(bn->bc, bn->n, &o, &b)) { failed(w->id, b.error); fnvnx_shader_result_free(&a); fnvnx_shader_result_free(&b); return; }
    fprintf(ftim, "%s\t%.0f\n", w->id, now_us() - t0);
    if (a.dksh_size != b.dksh_size || memcmp(a.dksh, b.dksh, a.dksh_size) || strcmp(a.reflection_json, b.reflection_json)) failed(w->id, "KEEP_GLSL changes the DKSH or the reflection");
    else if (fnvnx_dksh_validate(a.dksh, a.dksh_size, a.stage, err, sizeof err)) failed(w->id, err);
    else {
        char l[2 * SHCACHE_MAX_L + 2] = "";
        uint32_t i;
        for (i = 0; i < w->nl; i++) sprintf(l + 2 * i, "%02x", w->l[i]);
        if (spit(w->id, "dksh", a.dksh, a.dksh_size) || spit(w->id, "json", a.reflection_json, strlen(a.reflection_json)) || spit(w->id, "glsl", a.glsl, strlen(a.glsl))) failed(w->id, "write error");
        else fprintf(fent, "%s\t%016llx\t%zu\t%c\t0\t%s\t%zu\t%zu\t%zu\t%u\t%u\n", w->id, (unsigned long long)w->h, bn->n, w->stage ? 'p' : 'v', w->nl ? l : "-",
                     a.dksh_size, strlen(a.reflection_json), strlen(a.glsl), a.stats.d3d_instructions, a.stats.dk_instructions);
    }
    fnvnx_shader_result_free(&a); fnvnx_shader_result_free(&b);
}

int main(int argc, char **argv)
{
    int i, reverse = 0, ai = 1; char path[1024]; FILE *flk;
    if (argc > 1 && !strcmp(argv[1], "--reverse")) { reverse = 1; ai++; }
    if (argc - ai < 2) { fprintf(stderr, "usage: shxlate [--reverse] OUTDIR D9S_DIR...\n"); return 2; }
    outdir = argv[ai++];
    for (; ai < argc; ai++) if (load_dir(argv[ai])) return 2;
    snprintf(path, sizeof path, "mkdir -p '%s'", outdir); if (system(path)) return 2;
    snprintf(path, sizeof path, "%s/fail.tsv", outdir); ffail = fopen(path, "w");
    snprintf(path, sizeof path, "%s/timing.tsv", outdir); ftim = fopen(path, "w");
    snprintf(path, sizeof path, "%s/entries.tmp", outdir); fent = fopen(path, "w");
    snprintf(path, sizeof path, "%s/links.tsv", outdir); flk = fopen(path, "w");
    if (!ffail || !ftim || !fent || !flk) return 2;
    /* pass 1: front end of every bytecode (stage, reflection); no uam */
    for (i = 0; i < nbins; i++) {
        fnvnx_shader_opts o; fnvnx_shader_result r; memset(&o, 0, sizeof o); o.flags = FNVNX_SHADER_NO_DKSH;
        if (!fnvnx_shader_translate(bins[i].bc, bins[i].n, &o, &r)) { char id[40]; snprintf(id, sizeof id, "%016llx", (unsigned long long)bins[i].h); failed(id, r.error); }
        else { bins[i].stage = r.stage == FNVNX_STAGE_PIXEL; bins[i].json = strdup(r.reflection_json); }
        fnvnx_shader_result_free(&r);
    }
    for (i = 0; i < nbins; i++) if (bins[i].json) add_work(i, 0, NULL);
    qsort(pairs, npairs, sizeof *pairs, cmp_pair);
    for (i = 0; i < npairs; i++) {
        uint8_t l[SHCACHE_MAX_L]; uint32_t nl, k; const Bin *v = &bins[pairs[i].v], *p = &bins[pairs[i].p];
        if (i && !cmp_pair(&pairs[i - 1], &pairs[i])) continue;                  /* the same pair in another recording */
        if (!v->json || !p->json || v->stage != 0 || p->stage != 1) { failed("pair", "a stage of the pair did not translate or is of the wrong stage"); continue; }
        if (shcache_link_key(v->json, p->json, l, &nl)) { failed("pair", "illegal link set"); continue; }
        fprintf(flk, "%016llx\t%016llx\t", (unsigned long long)v->h, (unsigned long long)p->h);
        if (!nl) fputs("-", flk); else for (k = 0; k < nl; k++) fprintf(flk, "%02x", l[k]);
        fputc('\n', flk);
        if (nl) { add_work(pairs[i].v, nl, l); add_work(pairs[i].p, nl, l); }
    }
    qsort(work, nwork, sizeof *work, cmp_work);
    if (reverse) for (i = nwork - 1; i >= 0; i--) do_work(&work[i]); else for (i = 0; i < nwork; i++) do_work(&work[i]);
    fclose(fent); fclose(ftim); fclose(ffail); fclose(flk);
    { char cmd[2200]; snprintf(cmd, sizeof cmd, "(printf 'id\\thash\\tcode_len\\tstage\\tflags\\tl\\tdksh\\tjson\\tglsl\\td3d_instr\\tdk_instr\\n'; LC_ALL=C sort '%s/entries.tmp') > '%s/entries.tsv' && rm '%s/entries.tmp'", outdir, outdir, outdir);
      if (system(cmd)) return 2; }
    printf("shxlate: %d bytecodes, %d pairs, %d translations, %d failures\n", nbins, npairs, nwork, failures);
    return failures != 0;
}
