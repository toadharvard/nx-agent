#define _DEFAULT_SOURCE
#include "fnvnx_host.h"
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

static int h_read(void *ctx, uint32_t off, void *dst, uint32_t size) {
    fnvnx_host *h = (fnvnx_host *)ctx;
    if ((uint64_t)off + size > h->file_size) return -1;
    if (fseek(h->f, (long)off, SEEK_SET)) return -1;
    return fread(dst, 1, size, h->f) == size ? 0 : -1;
}

static int to_prot(unsigned p) {
    return ((p & FNVNX_PROT_R) ? PROT_READ : 0) | ((p & FNVNX_PROT_W) ? PROT_WRITE : 0) |
           ((p & FNVNX_PROT_X) ? PROT_EXEC : 0);
}

static int h_map_fixed(void *ctx, uintptr_t addr, uint32_t size, unsigned prot) {
    fnvnx_host *h = (fnvnx_host *)ctx;
    uintptr_t lo = (uintptr_t)h->res, hi = lo + h->res_size;
    unsigned i;
    if ((addr & 0xfff) || (size & 0xfff) || addr < lo || addr > hi || size > hi - addr) return -1;
    for (i = 0; i < h->nmaps; i++)
        if (addr < h->maps[i].hi && h->maps[i].lo < addr + size) return -1;   /* already in use */
    if (h->nmaps == sizeof h->maps / sizeof h->maps[0]) return -1;
    if (mmap((void *)addr, size, PROT_READ | PROT_WRITE, MAP_FIXED | MAP_PRIVATE | MAP_ANONYMOUS, -1, 0) ==
        MAP_FAILED)
        return -1;
    h->maps[h->nmaps].lo = addr;
    h->maps[h->nmaps].hi = addr + size;
    h->maps[h->nmaps].prot = prot;
    h->nmaps++;
    return 0;
}

static int h_protect(void *ctx, uintptr_t addr, uint32_t size, unsigned prot) {
    fnvnx_host *h = (fnvnx_host *)ctx;
    h->protect_calls++;
    return mprotect((void *)addr, size, to_prot(prot));
}

static void *h_alloc(void *ctx, uint32_t size) { (void)ctx; return malloc(size ? size : 1); }
static void h_free(void *ctx, void *p) { (void)ctx; free(p); }
static void h_log(void *ctx, const char *msg) {
    fnvnx_host *h = (fnvnx_host *)ctx;
    if (h->verbose) fprintf(stderr, "fnvnx: %s\n", msg);
}

int fnvnx_host_open(fnvnx_host *h, const char *path, uint32_t window_lo, uint32_t window_hi, fnvnx_os *os) {
    long size;
    memset(h, 0, sizeof *h);
    memset(os, 0, sizeof *os);
    if (window_hi <= window_lo || (window_lo & 0xfff) || (window_hi & 0xfff)) return FNVNX_E_ARG;
    h->f = fopen(path, "rb");
    if (!h->f) return FNVNX_E_IO;
    if (fseek(h->f, 0, SEEK_END) || (size = ftell(h->f)) < 0 || size > 0x7fffffffL) {
        fclose(h->f);
        return FNVNX_E_IO;
    }
    h->file_size = (uint32_t)size;
    h->res_size = window_hi - window_lo;
    h->res = (uint8_t *)mmap(NULL, h->res_size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (h->res == (uint8_t *)MAP_FAILED) { fclose(h->f); return FNVNX_E_MAP; }
    h->window_lo = window_lo;
    h->window_hi = window_hi;
    h->bias = (uintptr_t)h->res - window_lo;
    os->ctx = h;
    os->read = h_read;
    os->map_fixed = h_map_fixed;
    os->protect = h_protect;
    os->alloc = h_alloc;
    os->free = h_free;
    os->log = h_log;
    return FNVNX_OK;
}

void fnvnx_host_close(fnvnx_host *h) {
    if (h->res && h->res != (uint8_t *)MAP_FAILED) munmap(h->res, h->res_size);
    if (h->f) fclose(h->f);
    memset(h, 0, sizeof *h);
}
