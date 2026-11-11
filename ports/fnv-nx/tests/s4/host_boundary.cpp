/* host_boundary.cpp - the host test process's implementation of platform/include/fnvnx_boundary.h.
 * "The game" is the native test code: an export's value is its native address, a game function is called natively, a
 * game thread runs natively on the OS thread the platform made, and there is no loader (images are registered by the
 * tests through fnvnx_win32_register_image). The calls are
 * counted (host_boundary.h) so the tests can see what the platform sent through the boundary. */
#include "fnvnx_boundary.h"
#include "host_boundary.h"

#include <stdlib.h>
#include <string.h>

extern "C" {
int g_bnd_exports, g_bnd_invokes, g_bnd_threads_created, g_bnd_threads_run, g_bnd_threads_freed,
    g_bnd_threads_freed_unrun, g_bnd_process_detaches;
uint32_t (*g_bnd_loader)(const char *name, uint32_t *base);
int (*g_bnd_invoke_hook)(fnvnx_gaddr addr, uint32_t ecx, uint32_t nargs, const fnvnx_gword *args, uint32_t kind,
                         struct fnvnx_bnd_ret *out);

fnvnx_gaddr fnvnx_bnd_export_value(const char *, const char *, uint32_t, const void *native)
{
    __atomic_fetch_add(&g_bnd_exports, 1, __ATOMIC_RELAXED);
    return (fnvnx_gaddr)native;
}

typedef uintptr_t W;
void fnvnx_bnd_guest_invoke(fnvnx_gaddr addr, uint32_t ecx, uint32_t nargs, const fnvnx_gword *args, uint32_t kind,
                            struct fnvnx_bnd_ret *out)
{
    __atomic_fetch_add(&g_bnd_invokes, 1, __ATOMIC_RELAXED);
    if (g_bnd_invoke_hook && g_bnd_invoke_hook(addr, ecx, nargs, args, kind, out))
        return;
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
        /* a pointer / LONG_PTR result is pointer-wide here; edx:eax of an int64 result are its two halves */
        r.eax = kind == FNVNX_BND_RET_I64 ? (uint32_t)v : (fnvnx_gword)v;
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

uint32_t fnvnx_bnd_module_load(const char *name, uint32_t *base)
{
    if (g_bnd_loader)
        return g_bnd_loader(name, base);
    return 126;   /* ERROR_MOD_NOT_FOUND: the host process has no translated modules */
}

void fnvnx_bnd_process_detach(void) { __atomic_fetch_add(&g_bnd_process_detaches, 1, __ATOMIC_RELAXED); }
}
