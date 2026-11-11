/* host_boundary.cpp - the host test process's implementation of platform/include/fnvnx_boundary.h.
 * "The game" is the native test code: an export's value is its native address, a game function is called natively, a
 * game thread runs natively on the OS thread the platform made, and there is no loader (images are registered by the
 * tests through fnvnx_win32_register_image; their sections are read from the PE headers in memory). The calls are
 * counted (host_boundary.h) so the tests can see what the platform sent through the boundary. */
#include "fnvnx_boundary.h"
#include "host_boundary.h"

#include <stdlib.h>
#include <string.h>

extern "C" {
int g_bnd_exports, g_bnd_invokes, g_bnd_threads_created, g_bnd_threads_run, g_bnd_threads_freed,
    g_bnd_threads_freed_unrun, g_bnd_process_detaches;
uint32_t (*g_bnd_loader)(const char *name, uint32_t *base);

fnvnx_gaddr fnvnx_bnd_export_value(const char *, const char *, uint32_t, const void *native)
{
    __atomic_fetch_add(&g_bnd_exports, 1, __ATOMIC_RELAXED);
    return (fnvnx_gaddr)native;
}

typedef uintptr_t W;
void fnvnx_bnd_guest_invoke(fnvnx_gaddr addr, uint32_t, uint32_t nargs, const uint32_t *args, uint32_t kind,
                            struct fnvnx_bnd_ret *out)
{
    __atomic_fetch_add(&g_bnd_invokes, 1, __ATOMIC_RELAXED);
    W a[16] = {0};
    for (uint32_t i = 0; i < nargs && i < 16; i++)
        a[i] = args[i];
    /* SysV: the caller pops its arguments, so a callee that declares fewer than the 16 passed ones is called correctly */
#define ARGS a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12], a[13], a[14], a[15]
    struct fnvnx_bnd_ret r = {0, 0, 0.0};
    if (kind == FNVNX_BND_RET_ST0) {
        r.st0 = ((double (*)(W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W))addr)(ARGS);
    } else {
        uint64_t v = ((uint64_t(*)(W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W))addr)(ARGS);
        r.eax = (uint32_t)v;
        r.edx = kind == FNVNX_BND_RET_I64 ? (uint32_t)(v >> 32) : 0;
    }
#undef ARGS
    if (out)
        *out = r;
}

struct fnvnx_bnd_thread {
    fnvnx_gaddr start;
};

fnvnx_bnd_thread *fnvnx_bnd_thread_create(fnvnx_gaddr start, size_t)
{
    fnvnx_bnd_thread *t = (fnvnx_bnd_thread *)calloc(1, sizeof *t);
    if (!t)
        return NULL;
    t->start = start;
    __atomic_fetch_add(&g_bnd_threads_created, 1, __ATOMIC_RELAXED);
    return t;
}

uint32_t fnvnx_bnd_thread_run(fnvnx_bnd_thread *t, fnvnx_gaddr param)
{
    __atomic_fetch_add(&g_bnd_threads_run, 1, __ATOMIC_RELAXED);
    /* the parameter is a native pointer here, wider than a stack word: the start routine is called directly */
    return (uint32_t)((unsigned long (*)(void *))t->start)((void *)param);
}

void fnvnx_bnd_thread_release(fnvnx_bnd_thread *t, int notify)
{
    __atomic_fetch_add(&g_bnd_threads_freed, 1, __ATOMIC_RELAXED);
    if (!notify)
        __atomic_fetch_add(&g_bnd_threads_freed_unrun, 1, __ATOMIC_RELAXED);
    free(t);
}

static uint32_t rd32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }
static uint16_t rd16(const uint8_t *p) { uint16_t v; memcpy(&v, p, 2); return v; }
static uint32_t pg(uint32_t v) { return (v + 0xfffu) & ~0xfffu; }

unsigned fnvnx_bnd_image_ranges(uint32_t base, struct fnvnx_bnd_range *out, unsigned max)
{
    const uint8_t *b = (const uint8_t *)(uintptr_t)base;
    const uint8_t *pe = b + rd32(b + 0x3c), *opt = pe + 24;
    unsigned nsec = rd16(pe + 6), n = 0;
    uint32_t soi = rd32(opt + 56), soh = rd32(opt + 60), cur = 0;   /* SizeOfImage, SizeOfHeaders */
    const uint8_t *sh = opt + rd16(pe + 20);
    auto put = [&](uint32_t va, uint32_t size, unsigned prot) {
        if (n < max)
            out[n] = {va, size, prot};
        n++;
    };
    put(base, pg(soh), 1);
    cur = pg(soh);
    for (unsigned i = 0; i < nsec; i++, sh += 40) {
        uint32_t vs = rd32(sh + 8), rva = rd32(sh + 12), raw = rd32(sh + 16), ch = rd32(sh + 36);
        uint32_t size = pg(vs > raw ? vs : raw);
        if (rva > cur)
            put(base + cur, rva - cur, 0);
        put(base + rva, size, (ch & 0x40000000u ? 1u : 0u) | (ch & 0x80000000u ? 2u : 0u) | (ch & 0x20000000u ? 4u : 0u));
        cur = rva + size;
    }
    if (pg(soi) > cur)
        put(base + cur, pg(soi) - cur, 0);
    return n;
}

uint32_t fnvnx_bnd_module_load(const char *name, uint32_t *base)
{
    if (g_bnd_loader)
        return g_bnd_loader(name, base);
    return 126;   /* ERROR_MOD_NOT_FOUND: the host process has no translated modules */
}

void fnvnx_bnd_process_detach(void) { __atomic_fetch_add(&g_bnd_process_detaches, 1, __ATOMIC_RELAXED); }
}
