/* Linux host of nxgpu4_selftest.c over libnxgl4.so (the role firstframe_main.c plays on the console).
 * usage: harness <w> <h> <vs.glsl> <fs.glsl> <present frames>      prints the selftest log and `RESULT PASS` / `RESULT FAIL <reason>`.
 * Every address the backend asks about must lie in one allocation of this host (nxgl4_guest_ptr checks it, loudly). */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "gl4.h"
#include "nxgpu4_selftest.h"
#include "selftest_expect.h"

typedef struct { uint64_t a, n; } Blk;
static Blk g_blk[64];
static int g_nblk, g_hook_calls, g_frames, g_bad_ptr;

void* nxgl4_guest_ptr(uint64_t addr, uint64_t size) {
    for (int i = 0; i < g_nblk; i++)
        if (addr >= g_blk[i].a && addr + size <= g_blk[i].a + g_blk[i].n) return (void*)(uintptr_t)addr;
    g_bad_ptr++;
    fprintf(stderr, "harness: backend used the address %#llx (+%llu) that no allocation holds\n", (unsigned long long)addr, (unsigned long long)size);
    return NULL;
}
void nxgl4_on_present(uint32_t frame) { (void)frame; g_hook_calls++; }

static void* h_alloc(void* ctx, uint32_t size, uint64_t* addr) {
    (void)ctx;
    size = (size + 4095u) & ~4095u;
    void* p = g_nblk < 64 ? aligned_alloc(4096, size) : NULL;
    if (!p) return NULL;
    memset(p, 0, size);
    g_blk[g_nblk++] = (Blk){(uint64_t)(uintptr_t)p, size};
    *addr = (uint64_t)(uintptr_t)p;
    return p;
}
static void h_log(void* ctx, const char* fmt, ...) {
    (void)ctx;
    va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap); putchar('\n');
}
static void h_frame(void* ctx, uint32_t w, uint32_t h, const uint8_t* rgba) { (void)ctx; (void)rgba; g_frames++; printf("FRAME %ux%u\n", w, h); }
static void* slurp(const char* path, uint32_t* n) {
    FILE* f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "harness: cannot open %s\n", path); exit(2); }
    fseek(f, 0, SEEK_END); long len = ftell(f); fseek(f, 0, SEEK_SET);
    char* b = malloc((size_t)len);
    if (fread(b, 1, (size_t)len, f) != (size_t)len) { fprintf(stderr, "harness: short read of %s\n", path); exit(2); }
    fclose(f); *n = (uint32_t)len;
    return b;
}

int main(int argc, char** argv) {
    if (argc != 6) { fprintf(stderr, "usage: harness w h vs.glsl fs.glsl frames\n"); return 2; }
    static const NxSelftestExpect ex[] = { NXST_EXPECT_ROWS };
    char err[256], reason[256];
    uint32_t w = (uint32_t)atoi(argv[1]), h = (uint32_t)atoi(argv[2]);
    if (!nxgl4_create(w, h, err, sizeof err)) { printf("RESULT FAIL nxgl4_create: %s\n", err); return 1; }
    NxSelftestHost host;
    memset(&host, 0, sizeof host);
    host.ext = nxgl4_ext; host.alloc = h_alloc; host.log = h_log; host.frame = h_frame;
    host.shader_form = NXST_SHADER_GLSL;
    host.shader[0].data = slurp(argv[3], &host.shader[0].size);
    host.shader[1].data = slurp(argv[4], &host.shader[1].size);
    host.expect = ex; host.nexpect = sizeof ex / sizeof ex[0];
    host.present_frames = (uint32_t)atoi(argv[5]);
    host.reason = reason; host.reason_cap = sizeof reason;
    int st = nxgpu4_selftest(&host);
    printf("PRESENT_HOOK calls=%d expected=%u\n", g_hook_calls, st ? 0u : host.present_frames);
    if (!st && ((uint32_t)g_hook_calls != host.present_frames || g_frames != 1 || g_bad_ptr)) { printf("RESULT FAIL hook calls %d, frames %d, bad pointers %d\n", g_hook_calls, g_frames, g_bad_ptr); return 1; }
    if (st) { printf("RESULT FAIL %s\n", reason); return 1; }
    printf("RESULT PASS\n");
    nxgl4_destroy();
    return 0;
}
