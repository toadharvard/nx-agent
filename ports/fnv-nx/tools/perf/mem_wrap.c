/* mem_wrap.c - the C heap for the memory lane (platform/win32/memtrack.cpp, tools/perf/MEM.md): linked into a lane
 * guest by tools/threads/variant.sh MEM=1 with -Wl,--wrap= for each function below, so the game's and the CRT
 * stand-ins' own malloc calls are recorded (kind heap, by the caller's address). A call inside HeapAlloc & co. is
 * recorded there (fnvnx_mem_inside) and skipped here; a thread-local guard keeps the recorder's own allocations (the
 * first w32_self of a thread, a thread record) out. */
#include <stddef.h>

void *__real_malloc(size_t n);
void *__real_calloc(size_t a, size_t b);
void *__real_realloc(void *p, size_t n);
void __real_free(void *p);
void *__real_memalign(size_t al, size_t n);
void *__real_aligned_alloc(size_t al, size_t n);
int __real_posix_memalign(void **pp, size_t al, size_t n);

void fnvnx_mem_note(int kind, const void *p, size_t n, unsigned caller);
void fnvnx_mem_forget(int kind, const void *p);
void fnvnx_mem_fail(int kind, size_t n, unsigned caller);
int fnvnx_mem_inside(void);
extern volatile int w32_mem_on;

static __thread int t_busy;
#define RA ((unsigned)(unsigned long)__builtin_return_address(0))
#define ON() (w32_mem_on && !t_busy)
#define REC(p, n, c) do { t_busy = 1; if (!fnvnx_mem_inside()) { if (p) fnvnx_mem_note(0, (p), (n), (c)); \
                          else fnvnx_mem_fail(0, (n), (c)); } t_busy = 0; } while (0)

void *__wrap_malloc(size_t n) { void *p = __real_malloc(n); if (ON()) REC(p, n, RA); return p; }
void *__wrap_calloc(size_t a, size_t b) { void *p = __real_calloc(a, b); if (ON()) REC(p, a * b, RA); return p; }
void *__wrap_memalign(size_t al, size_t n) { void *p = __real_memalign(al, n); if (ON()) REC(p, n, RA); return p; }
void *__wrap_aligned_alloc(size_t al, size_t n) { void *p = __real_aligned_alloc(al, n); if (ON()) REC(p, n, RA); return p; }
int __wrap_posix_memalign(void **pp, size_t al, size_t n)
{
    int r = __real_posix_memalign(pp, al, n);
    if (ON()) REC(r ? (void *)0 : *pp, n, RA);
    return r;
}
void *__wrap_realloc(void *p, size_t n)
{
    unsigned c = RA;
    int on = ON() && (t_busy = 1, !fnvnx_mem_inside());
    if (on && p) fnvnx_mem_forget(0, p);
    t_busy = 0;
    void *q = __real_realloc(p, n);
    if (on) REC(q, n, c);
    return q;
}
void __wrap_free(void *p)
{
    if (p && ON()) { t_busy = 1; if (!fnvnx_mem_inside()) fnvnx_mem_forget(0, p); t_busy = 0; }
    __real_free(p);
}
