/* fuzz_soi.c - V1 (D14) against J-0127 (8b24dc6): fnvnx_pe_open rounds an unaligned SizeOfImage up.
 *
 * Structured mutations of exactly the fields that change meaning with the rounding (SizeOfImage around the last
 * section's end, around 0 and 0xfffff000, ImageBase near the top of the address space, the last section's VirtualSize
 * and RVA, the data directories at the image's end, NumberOfRvaAndSizes, file truncation), then the whole pipeline
 * (map, relocations, imports) under ASan/UBSan in a PROT_NONE window. After every accepted header it checks:
 *   I1 size_of_image is page aligned, I2 it is the declared value rounded up (never less, < declared + 0x1000),
 *   I3 image_base + size_of_image <= 2^32, I4 every section ends inside it, I5 every non-SECURITY directory too.
 * Mutations only touch the headers, so one in-memory buffer is restored header-only per iteration and nothing but
 * OUT.rec is written: for the first K cases a record (u32 index, i32 rc, u32 size_of_image, u32 file_size, then the
 * first HDR = 0x2000 bytes of the mutated file), so tests/v1/test_loader_soi.py can replay the headers through
 * loader/tools/fnvnx_pe.py (which reads nothing but the headers and the file size). At most K * 8 KiB on disk.
 *
 *   fuzz_soi SEED.exe ITERATIONS RNGSEED OUT.rec K
 */
#define _DEFAULT_SOURCE
#include "fnvnx_loader.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#define WLO 0x00400000u
#define WHI 0x02100000u
#define HDR 0x2000u

static uint8_t *file;
static uint32_t file_size;
static uint8_t *res;

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

static uint64_t rng = 0x2545F4914F6CDD1Dull;
static uint32_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)rng; }
static uint32_t rd32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static void wr32(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24); }

static uint32_t near(uint32_t v) {   /* a value at or near v: page edges and off-by-ones */
    static const int32_t d[] = {0, 1, -1, 0xfff, -0xfff, 0x1000, -0x1000, 0x1001, -0x1001, 0x1c, -0x1c, 2, 0x800};
    return v + (uint32_t)d[rnd() % (sizeof d / sizeof d[0])];
}

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
    uint32_t seed_size, it, iters, keep, lfanew, opt, nsec, optsize, sh_last, last_end, inv_bad = 0;
    unsigned accepted = 0, loaded = 0, outcome[8] = {0};
    fnvnx_os os;
    FILE *tsv;
    if (argc < 6) { fprintf(stderr, "usage: fuzz_soi SEED.exe ITERATIONS RNGSEED OUT.rec K\n"); return 2; }
    seed = (uint8_t *)slurp(argv[1], &seed_size);
    iters = (uint32_t)strtoul(argv[2], NULL, 0);
    rng ^= strtoull(argv[3], NULL, 0) * 0x9E3779B97F4A7C15ull;
    tsv = fopen(argv[4], "wb");
    keep = (uint32_t)strtoul(argv[5], NULL, 0);
    if (!seed || !tsv || seed_size < HDR) return 2;
    lfanew = rd32(seed + 0x3c); opt = lfanew + 24; nsec = rd16(seed + lfanew + 6); optsize = rd16(seed + lfanew + 20);
    sh_last = opt + optsize + 40 * (nsec - 1);
    if (sh_last + 40 > HDR) return 2;
    last_end = rd32(seed + sh_last + 12) + rd32(seed + sh_last + 8);
    file = (uint8_t *)malloc(seed_size);
    res = (uint8_t *)mmap(NULL, WHI - WLO, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (res == MAP_FAILED) return 2;
    memset(&os, 0, sizeof os);
    os.read = f_read; os.map_fixed = f_map; os.alloc = f_alloc; os.free = f_free;

    for (it = 0; it < iters; it++) {
        static fnvnx_pe pe;
        static fnvnx_image img;
        uint32_t k, n = 1 + rnd() % 3, declared, applied, i;
        int r, stage = 0;
        if (it == 0) memcpy(file, seed, seed_size); else memcpy(file, seed, HDR);   /* mutations stay below HDR */
        file_size = seed_size;
        for (k = 0; k < (it ? n : 0); k++) {   /* iteration 0: the seed as it is */
            uint32_t v, what = rnd() % 10;
            switch (what) {
            case 0: case 1: case 2:   /* SizeOfImage around the last section's end */
                wr32(file + opt + 56, near(last_end)); break;
            case 3:                   /* SizeOfImage at the extremes */
                { static const uint32_t e[] = {0, 1, 0xfff, 0x1000, 0xfffff000u, 0xfffff001u, 0xffffffffu, 0xfffffffeu,
                                               0x7fffffffu, 0x80000000u, 0x80000001u};
                  wr32(file + opt + 56, e[rnd() % (sizeof e / sizeof e[0])]); }
                break;
            case 4:                   /* ImageBase near the top, so base + rounded size crosses 2^32 */
                { static const uint32_t b[] = {0, 0x00400000u, 0xfeff0000u, 0xff000000u, 0xfffe0000u, 0xffff0000u,
                                               0x80000000u};
                  wr32(file + opt + 28, b[rnd() % (sizeof b / sizeof b[0])]); }
                break;
            case 5:                   /* the last section's VirtualSize past the declared size, inside the rounding */
                v = rd32(file + opt + 56) - rd32(file + sh_last + 12);
                wr32(file + sh_last + 8, near(v)); break;
            case 6:                   /* the last section's RVA */
                wr32(file + sh_last + 12, near(rd32(file + sh_last + 12))); break;
            case 7:                   /* a data directory reaching the declared or the rounded end */
                i = rnd() % 16;
                if (96 + 8 * i + 8 <= optsize) {
                    uint32_t soi = rd32(file + opt + 56), rva = near(soi & ~0xfffu);
                    wr32(file + opt + 96 + 8 * i, rva);
                    wr32(file + opt + 100 + 8 * i, near((((soi + 0xfffu) & ~0xfffu) - rva)));
                }
                break;
            case 8:                   /* NumberOfRvaAndSizes */
                wr32(file + opt + 92, rnd() % 3 ? rnd() % 20 : rnd()); break;
            default:                  /* the file ends early */
                file_size = rnd() % 2 ? rnd() % seed_size : seed_size - rnd() % 0x2000;
            }
        }
        declared = rd32(file + opt + 56);
        mmap(res, WHI - WLO, PROT_NONE, MAP_FIXED | MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
        r = fnvnx_pe_open(&pe, &os, file_size);
        if (it < keep) {
            uint32_t h[4];
            h[0] = it; h[1] = (uint32_t)r; h[2] = r ? 0 : pe.size_of_image; h[3] = file_size;
            fwrite(h, 4, 4, tsv);
            fwrite(file, 1, HDR, tsv);
        }
        if (!r) {
            accepted++;
            if (pe.size_of_image & 0xfffu) { inv_bad++; fprintf(stderr, "I1 #%u soi=%#x\n", it, pe.size_of_image); }
            if (pe.size_of_image < declared || pe.size_of_image - declared >= 0x1000u) {
                inv_bad++; fprintf(stderr, "I2 #%u declared=%#x soi=%#x\n", it, declared, pe.size_of_image); }
            if ((uint64_t)pe.image_base + pe.size_of_image > 0x100000000ull) {
                inv_bad++; fprintf(stderr, "I3 #%u base=%#x soi=%#x\n", it, pe.image_base, pe.size_of_image); }
            for (i = 0; i < pe.nsections; i++) {
                uint32_t vs = pe.sec[i].vsize ? pe.sec[i].vsize : pe.sec[i].raw_size;
                if ((uint64_t)pe.sec[i].rva + vs > pe.size_of_image) { inv_bad++; fprintf(stderr, "I4 #%u sec %u\n", it, i); }
            }
            for (i = 0; i < pe.ndirs; i++)
                if (i != FNVNX_DIR_SECURITY && pe.dir[i].size &&
                    (uint64_t)pe.dir[i].rva + pe.dir[i].size > pe.size_of_image) {
                    inv_bad++; fprintf(stderr, "I5 #%u dir %u\n", it, i); }
            stage = 1;
            r = fnvnx_image_map(&img, &pe, &os, pe.image_base, (uintptr_t)res - WLO, WLO, WHI, rnd() & 1);
            if (!r) { stage = 2; r = fnvnx_image_load_relocs(&img, &os); }
            if (!r) { stage = 3; r = fnvnx_image_relocate(&img, &os, &applied); if (r == FNVNX_E_UNSUPPORTED) r = 0; }
            if (!r) { stage = 4; r = fnvnx_imports_bind(&img, resolver, NULL, 0xdead0000u, NULL); loaded += !r; }
            fnvnx_image_release(&img, &os);
        }
        if (it == 0) printf("seed: pe_open %s, last stage %d rc %d (%s)\n", stage ? "ok" : "FAILED", stage, r,
                            fnvnx_strerror(r));
        outcome[stage]++;
    }
    fclose(tsv);
    printf("fuzz_soi iterations=%u accepted=%u loaded=%u invariant_failures=%u stages=%u/%u/%u/%u/%u\n", iters,
           accepted, loaded, inv_bad, outcome[0], outcome[1], outcome[2], outcome[3], outcome[4]);
    munmap(res, WHI - WLO);
    free(file); free(seed);
    return inv_bad ? 1 : 0;
}
