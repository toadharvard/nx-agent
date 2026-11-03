/* Mutation fuzzing of the loader on untrusted executables: every stage must fail cleanly, never crash or touch
 * memory outside what it mapped (the window is a PROT_NONE reservation, so a stray access faults; ASan/UBSan catch
 * the rest).   usage: fuzz_loader SEED.exe ADDRMAP.bin ITERATIONS [SEED] */
#define _DEFAULT_SOURCE
#include "fnvnx_loader.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#define WLO 0x00400000u
#define WHI 0x02100000u

static uint8_t *file;
static uint32_t file_size;
static uint8_t *res;
static uintptr_t bias;

static int f_read(void *ctx, uint32_t off, void *dst, uint32_t size) {
    (void)ctx;
    if ((uint64_t)off + size > file_size) return -1;
    memcpy(dst, file + off, size);
    return 0;
}
static int f_map(void *ctx, uintptr_t addr, uint32_t size, unsigned prot) {
    (void)ctx; (void)prot;
    if (addr < (uintptr_t)res || addr + size > (uintptr_t)res + (WHI - WLO)) return -1;
    return mmap((void *)addr, size, PROT_READ | PROT_WRITE, MAP_FIXED | MAP_PRIVATE | MAP_ANONYMOUS, -1, 0) ==
                   MAP_FAILED ? -1 : 0;
}
static void *f_alloc(void *ctx, uint32_t size) { (void)ctx; return malloc(size ? size : 1); }
static void f_free(void *ctx, void *p) { (void)ctx; free(p); }
static uint32_t resolver(void *ctx, const char *dll, const char *name, uint32_t ord, unsigned hint) {
    (void)ctx; (void)dll; (void)ord; (void)hint;
    return name && name[0] == 'S' ? 0x30000000u : 0;
}

static uint64_t rng = 88172645463325252ull;
static uint32_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)rng; }

static void *slurp(const char *path, uint32_t *size) {
    FILE *f = fopen(path, "rb");
    long n;
    void *p;
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); n = ftell(f); fseek(f, 0, SEEK_SET);
    p = malloc((size_t)n + 1);
    if (fread(p, 1, (size_t)n, f) != (size_t)n) { free(p); p = NULL; }
    fclose(f);
    *size = (uint32_t)n;
    return p;
}

int main(int argc, char **argv) {
    uint8_t *seed;
    uint32_t seed_size, map_size, it, iters, k;
    void *map_blob;
    fnvnx_codemap map;
    fnvnx_os os;
    unsigned outcome[16] = {0};
    if (argc < 4) { fprintf(stderr, "usage: fuzz_loader SEED.exe ADDRMAP.bin ITERATIONS [SEED]\n"); return 2; }
    seed = (uint8_t *)slurp(argv[1], &seed_size);
    map_blob = slurp(argv[2], &map_size);
    iters = (uint32_t)strtoul(argv[3], NULL, 0);
    if (argc > 4) rng ^= strtoull(argv[4], NULL, 0) * 0x9E3779B97F4A7C15ull;
    if (!seed || !map_blob || fnvnx_codemap_from_blob(&map, map_blob, map_size)) return 2;
    file = (uint8_t *)malloc(seed_size);
    res = (uint8_t *)mmap(NULL, WHI - WLO, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (res == MAP_FAILED) return 2;
    bias = (uintptr_t)res - WLO;
    memset(&os, 0, sizeof os);
    os.read = f_read; os.map_fixed = f_map; os.alloc = f_alloc; os.free = f_free;

    for (it = 0; it < iters; it++) {
        static fnvnx_pe pe;
        static fnvnx_image img;
        static fnvnx_fixup fx[4096];
        fnvnx_scan_stats st;
        uint32_t n = 0, flips = 1 + rnd() % 8, applied;
        int r, stage = 0;
        memcpy(file, seed, seed_size);
        file_size = seed_size;
        for (k = 0; k < flips; k++) {
            uint32_t pos = (rnd() & 1) ? rnd() % 0x400 : rnd() % seed_size, mode = rnd() % 4;
            if (mode == 0 && pos + 4 <= seed_size) {   /* an interesting 32-bit value: a pointer into the image */
                static const uint32_t lo[] = {0, 0xffffffffu, 0x00fdf000u, 0x01180000u, 0x00400000u, 0x01270000u};
                uint32_t v = lo[rnd() % 6] + (rnd() % 3 ? rnd() % 0x4000 : 0);
                pos &= ~3u;
                memcpy(file + pos, &v, 4);
            } else {
                file[pos] = mode == 1 ? (uint8_t)(file[pos] ^ (1u << (rnd() % 8))) : (uint8_t)rnd();
            }
        }
        if (rnd() % 16 == 0) file_size = rnd() % seed_size;
        /* reset the window */
        mmap(res, WHI - WLO, PROT_NONE, MAP_FIXED | MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
        r = fnvnx_pe_open(&pe, &os, file_size);
        if (!r) { stage = 1; r = fnvnx_image_map(&img, &pe, &os, pe.image_base, bias, WLO, WHI, rnd() & 1); }
        if (!r) { stage = 2; r = fnvnx_image_load_relocs(&img, &os); }
        if (!r) { stage = 3; r = fnvnx_image_relocate(&img, &os, &applied); if (r == FNVNX_E_UNSUPPORTED) r = 0; }
        if (!r) { stage = 4; r = fnvnx_imports_bind(&img, resolver, NULL, 0xdead0000u, NULL); }
        if (stage == 4 && (!r || r == FNVNX_E_FORMAT || r == FNVNX_E_UNSUPPORTED)) {   /* imports may be bad */
            stage = 5;
            r = fnvnx_scan_codeptrs(&img, &os, &map, NULL, fx, 4096, &n, &st);
            if (!r || r == FNVNX_E_NOMEM) {
                stage = 6;
                fnvnx_fixups_apply(&img, &map, fx, n < 4096 ? n : 4096, 1, NULL, NULL);
                r = 0;
            }
        }
        if (stage >= 1) fnvnx_image_release(&img, &os);
        outcome[stage + (r ? 8 : 0) > 15 ? 15 : stage + (r ? 8 : 0)]++;
    }
    printf("fuzz iterations=%u", iters);
    for (k = 0; k < 16; k++) if (outcome[k]) printf(" %s%u=%u", k >= 8 ? "fail@" : "ok@", k & 7, outcome[k]);
    printf("\n");
    munmap(res, WHI - WLO);
    free(file); free(seed); free(map_blob);
    return 0;
}
