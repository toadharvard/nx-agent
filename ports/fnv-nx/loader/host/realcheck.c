/* realcheck: the module loader on the player's real files (private; only aggregate numbers are printed).
 *   realcheck FalloutNV.exe MODULE_DIR      linked with the generated modules.c (contract/modules.py) and its exe hash file
 * Hooks over an mmap window with a bias; every platform import gets a key (the real table is generated elsewhere); every
 * DllMain returns 1 and is recorded; the exe's entry returns 0. */
#define _DEFAULT_SOURCE
#include "fnvnx_start.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#define WINDOW 0x40000000u
static uint8_t *g_res; static uint32_t g_next = 0x30000000u;
static FILE *g_fp[1 + FNVNX_MAX_MODULES]; static const char *g_dir;
static unsigned g_calls; static uint32_t g_order[64]; static fnvnx_proc *g_proc;
fnvnx_proc *fnvnx_start_proc(void) { return g_proc; }
uint32_t fnvnx_lift_enter(uint32_t eip, uint32_t esp, uint32_t teb) {
    (void)esp; (void)teb;
    if (g_calls < 64) g_order[g_calls] = eip;
    g_calls++;
    return eip == g_proc->exe.entry_va ? 0u : 1u;
}
static int h_read(void *c, uint32_t file, uint32_t off, void *dst, uint32_t size) {
    (void)c;
    if (file > fnvnx_module_count) return -1;
    if (!g_fp[file]) {
        char path[1024];
        snprintf(path, sizeof path, "%s/%s", g_dir, file ? fnvnx_modules[file - 1].name : "FalloutNV.exe");
        if (!(g_fp[file] = fopen(path, "rb"))) return -1;
    }
    return fseek(g_fp[file], (long)off, SEEK_SET) || fread(dst, 1, size, g_fp[file]) != size ? -1 : 0;
}
static int h_map(void *c, uintptr_t a, uint32_t s, unsigned p) { (void)c; (void)p; return mmap((void *)a, s, PROT_READ | PROT_WRITE, MAP_FIXED | MAP_PRIVATE | MAP_ANONYMOUS, -1, 0) == MAP_FAILED; }
static int h_unmap(void *c, uintptr_t a, uint32_t s) { (void)c; return munmap((void *)a, s); }
static int h_prot(void *c, uintptr_t a, uint32_t s, unsigned p) { (void)c; return mprotect((void *)a, s, (int)((p & 1 ? PROT_READ : 0) | (p & 2 ? PROT_WRITE : 0))); }
static void *h_alloc(void *c, uint32_t s) { (void)c; return malloc(s); }
static void h_free(void *c, void *p) { (void)c; free(p); }
static unsigned g_nolog;
static void h_log(void *c, const char *m) { (void)c; if (strstr(m, "no provider")) g_nolog++; else if (strstr(m, "module ") || strstr(m, "init ")) printf("  %s\n", m); }
static uint32_t h_va(void *c, uint32_t s, unsigned p) { uint32_t v = g_next; (void)p; if (h_map(c, (uintptr_t)v + (uintptr_t)g_res, s, 3)) return 0; g_next += s + 0x1000; return v; }
static int h_fva(void *c, uint32_t v, uint32_t s) { (void)c; (void)v; (void)s; return 0; }
static uint32_t resolve(void *c, const char *d, const char *n, uint32_t o) { (void)c; (void)d; (void)n; return 0x100000u + 16u * (o & 0xff); }

static unsigned g_nreg; static uint32_t g_regbase[32], g_regsize[32]; static char g_regname[32][64];
static void h_reg(void *c, const char *n, uint32_t b, uint32_t s) { (void)c; if (g_nreg < 32) { snprintf(g_regname[g_nreg], 64, "%s", n); g_regbase[g_nreg] = b; g_regsize[g_nreg] = s; } g_nreg++; }
int main(int argc, char **argv) {
    static fnvnx_proc p; fnvnx_os os = {NULL, h_read, h_map, h_prot, h_alloc, h_free, h_log, h_va, h_fva, h_reg, h_unmap}; fnvnx_start_cfg cfg; unsigned i; int r;
    FILE *f; uint8_t *buf; uint32_t len; unsigned nrel = 0;
    if (argc < 3) return 2;
    g_dir = argv[2];
    g_res = (uint8_t *)mmap(NULL, WINDOW, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    f = fopen(argv[1], "rb"); fseek(f, 0, SEEK_END); len = (uint32_t)ftell(f); fseek(f, 0, SEEK_SET);
    buf = (uint8_t *)malloc(len); if (fread(buf, 1, len, f) != len) return 2; fclose(f);
    memset(&cfg, 0, sizeof cfg);
    cfg.file_size = len; fnvnx_sha256(buf, len, cfg.sha256); cfg.resolve = resolve; cfg.bias = (uintptr_t)g_res;
    cfg.modules = fnvnx_modules; cfg.nmodules = fnvnx_module_count; cfg.exe_name = "FalloutNV.exe"; g_proc = &p;
    /* the window is below 4 GiB in guest VAs: the real bases (0x01410000..) lie inside it */
    r = fnvnx_start_prepare(&p, &os, &cfg);
    printf("prepare rc %d; platform slots without provider %u\n", r, g_nolog);
    if (r) return 1;
    for (i = 0; i < p.nmod; i++) {
        if (p.mod[i].state == FNVNX_MOD_UNLOADED) continue;
        nrel += p.mod[i].relocs_applied;
        printf("  mapped %s base %08x relocations %u imports bound %u\n", p.mod[i].name, (unsigned)p.mod[i].img.load_base, (unsigned)p.mod[i].relocs_applied, (unsigned)p.mod[i].imports_bound);
    }
    printf("exe imports bound %u; images mapped %u; total relocations %u\n", (unsigned)p.exe.imports_bound, 1u + (unsigned)p.nmod, nrel);
    r = fnvnx_start_init_modules(&p);
    printf("init rc %d; guest calls %u; DllMain order:", r, g_calls);
    for (i = 0; i < g_calls && i < 64; i++) printf(" %08x", (unsigned)g_order[i]);
    printf("\n");
    return r != 0;
}
