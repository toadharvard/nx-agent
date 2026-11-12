/* heap.c - MSVC heap extensions (_msize, _aligned_*) and small runtime entry points (_errno, _purecall,
 * clock). */
#include "fnvnx_msvcrt.h"

#include <errno.h>
#include <malloc.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "../os/os.h"

size_t _msize(void *p) { return p ? malloc_usable_size(p) : (size_t)-1; }

void *_expand(void *p, size_t n) { return p && malloc_usable_size(p) >= n ? p : NULL; }
int _heapchk(void) { return _HEAPOK; }

void *_recalloc(void *p, size_t num, size_t size)
{
    if (size && num > (size_t)-1 / size)
        return NULL;
    size_t n = num * size, old = p ? malloc_usable_size(p) : 0;
    void *q = realloc(p, n ? n : 1);
    if (q && n > old)
        memset((char *)q + old, 0, n - old);
    return q;
}

/* aligned blocks: [ ...padding... | header | user data ], header just below the user pointer */
struct ahdr { void *base; size_t size; size_t align; size_t offset; };

void *_aligned_offset_malloc(size_t size, size_t align, size_t offset)
{
    if (!align || (align & (align - 1)) || (size && offset >= size)) {
        errno = EINVAL;
        return NULL;
    }
    if (align < sizeof(void *))
        align = sizeof(void *);
    size_t total = size + align + sizeof(struct ahdr) + sizeof(void *);
    char *base = (char *)malloc(total);
    if (!base)
        return NULL;
    uintptr_t u = (uintptr_t)base + sizeof(struct ahdr) + offset;
    u = (u + align - 1) & ~(uintptr_t)(align - 1);
    char *user = (char *)(u - offset);
    struct ahdr *h = (struct ahdr *)(user - sizeof(struct ahdr));
    h->base = base;
    h->size = size;
    h->align = align;
    h->offset = offset;
    return user;
}

void *_aligned_malloc(size_t size, size_t align) { return _aligned_offset_malloc(size, align, 0); }

void _aligned_free(void *p)
{
    if (p)
        free(((struct ahdr *)((char *)p - sizeof(struct ahdr)))->base);
}

size_t _aligned_msize(void *p, size_t align, size_t offset)
{
    (void)align;
    (void)offset;
    return p ? ((struct ahdr *)((char *)p - sizeof(struct ahdr)))->size : (size_t)-1;
}

void *_aligned_realloc(void *p, size_t size, size_t align)
{
    if (!p)
        return _aligned_malloc(size, align);
    if (!size) {
        _aligned_free(p);
        return NULL;
    }
    struct ahdr *h = (struct ahdr *)((char *)p - sizeof(struct ahdr));
    void *q = _aligned_offset_malloc(size, align, 0);
    if (!q)
        return NULL;
    memcpy(q, p, h->size < size ? h->size : size);
    _aligned_free(p);
    return q;
}

/* ---- runtime */
int *_errno(void) { return &errno; }

void _invalid_parameter_noinfo(void)
{
    /* MSVC's default handler reports to Watson and terminates */
    os_fatal("CRT invalid parameter (_invalid_parameter_noinfo)");
}

void _purecall(void) { os_fatal("pure virtual function call"); }

/* clock(): MSVC counts wall time since process start, CLOCKS_PER_SEC = 1000 */
static uint64_t g_start_ns;
__attribute__((constructor(FNVNX_INIT_CLOCK))) static void clock_init(void) { g_start_ns = os_monotonic_ns(); }
unsigned int fnvnx_clock(void) { return (unsigned int)((os_monotonic_ns() - g_start_ns) / 1000000ull); }

/* rand/srand with MSVC's generator: per-thread state (seed 1), x = x * 214013 + 2531011, RAND_MAX 0x7FFF.
 * The engine scales rand() by 32767, so the libc generator (RAND_MAX 2^31-1 on newlib/glibc) cannot be used. */
static __thread uint32_t g_holdrand = 1;
void fnvnx_srand(unsigned int seed) { g_holdrand = seed; }
int fnvnx_rand(void)
{
    g_holdrand = g_holdrand * 214013u + 2531011u;
    return (int)((g_holdrand >> 16) & 0x7FFF);
}

long long _time64(long long *t)
{
    long long v = (long long)(os_wall_time_us() / 1000000);
    if (t)
        *t = v;
    return v;
}
