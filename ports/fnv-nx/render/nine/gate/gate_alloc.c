/* fnv-nx host gate (stream S6c-nine): low-VA allocator for the gate's Nine library.
 *
 * On Switch every pointer of the process is below 4 GiB (32-bit address space), and the D3D9 proxy relies on it:
 * proxies keep the remote object as 32 bits, and Lock/LockRect hand the guest LP64 pointers it narrows. On x86-64
 * Linux the gate links the library with -Wl,--wrap=malloc,... so every allocation made by Mesa, nine, softpipe
 * and our host code comes from one arena mapped at a fixed low address (also mapped into Unicorn by test/gate.py,
 * so the emulated guest can read and write what the host returns, as on the console).
 *
 * Small blocks (<= 64 KiB with header): segregated power-of-two free lists. Large blocks: whole pages, best fit
 * from one free list (no splitting or coalescing). One mutex. A test allocator, not a general one. Pointers
 * outside the arena (glibc-internal allocations such as strdup results) go to the real functions. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#define ARENA_BASE 0x40000000ull
#define ARENA_SIZE 0x80000000ull          /* 2 GiB: [1 GiB, 3 GiB), below 4 GiB as on Switch */
#define MIN_SHIFT 5                       /* 32-byte smallest block (16-byte header + 16) */
#define SMALL_SHIFT 16                    /* power-of-two classes up to 64 KiB */
#define LARGE 0xFFu                       /* Hdr.cls of a page-granular block */
#define PAGE 4096u
#define HDR 16
#define MAGIC 0x4E584741u                 /* "AGXN" */

typedef struct { uint32_t magic, cls, off, pages; } Hdr;   /* just before the returned pointer */
typedef struct Big { struct Big* next; uint64_t pages; } Big;   /* a free large block */

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static uintptr_t g_head = ARENA_BASE;
static void* g_free[SMALL_SHIFT + 1];
static Big* g_big;
static int g_ready;
static uint64_t g_peak;

void* __real_realloc(void*, size_t);
void __real_free(void*);

__attribute__((constructor(101))) static void arena_init(void) {
    if (g_ready) return;
    void* p = mmap((void*)ARENA_BASE, ARENA_SIZE, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE | MAP_NORESERVE, -1, 0);
    if (p != (void*)ARENA_BASE) {
        fprintf(stderr, "gate_alloc: cannot map the arena at %#llx (%s)\n", ARENA_BASE, strerror(errno));
        abort();
    }
    g_ready = 1;
}

static int in_arena(const void* p) { return (uintptr_t)p >= ARENA_BASE && (uintptr_t)p < ARENA_BASE + ARENA_SIZE; }

static void* bump(uintptr_t size, uintptr_t align) {   /* with g_lock held */
    uintptr_t at = (g_head + align - 1) & ~(align - 1);
    if (at + size > ARENA_BASE + ARENA_SIZE) {
        fprintf(stderr, "gate_alloc: arena full (%llu bytes requested, %llu MiB used)\n",
                (unsigned long long)size, (unsigned long long)((g_head - ARENA_BASE) >> 20));
        return NULL;
    }
    g_head = at + size;
    if (g_head - ARENA_BASE > g_peak) g_peak = g_head - ARENA_BASE;
    return (void*)at;
}

static void* arena_alloc(size_t size, size_t align) {
    if (!g_ready) arena_init();
    if (align < HDR) align = HDR;
    if (size > (1ull << 31) || align > (1u << 20)) { errno = ENOMEM; return NULL; }
    size_t need = size + HDR + (align > HDR ? align : 0);
    uint32_t cls = MIN_SHIFT, pages = 0;
    void* b = NULL;
    pthread_mutex_lock(&g_lock);
    if (need <= (1u << SMALL_SHIFT)) {
        while (((size_t)1 << cls) < need) cls++;
        b = g_free[cls];
        if (b) g_free[cls] = *(void**)b;
        else b = bump((uintptr_t)1 << cls, (uintptr_t)1 << (cls < 12 ? cls : 12));
    } else {
        cls = LARGE;
        pages = (uint32_t)((need + PAGE - 1) / PAGE);
        Big** best = NULL;
        for (Big** it = &g_big; *it; it = &(*it)->next)
            if ((*it)->pages >= pages && (!best || (*it)->pages < (*best)->pages)) {
                best = it;
                if ((*it)->pages == pages) break;
            }
        if (best) { Big* hit = *best; *best = hit->next; pages = (uint32_t)hit->pages; b = hit; }
        else b = bump((uintptr_t)pages * PAGE, PAGE);
    }
    pthread_mutex_unlock(&g_lock);
    if (!b) { errno = ENOMEM; return NULL; }
    uintptr_t p = ((uintptr_t)b + HDR + align - 1) & ~(uintptr_t)(align - 1);
    Hdr* h = (Hdr*)(p - HDR);
    h->magic = MAGIC; h->cls = cls; h->off = (uint32_t)(p - (uintptr_t)b); h->pages = pages;
    return (void*)p;
}

static size_t usable(const void* p) {
    const Hdr* h = (const Hdr*)p - 1;
    return (h->cls == LARGE ? (size_t)h->pages * PAGE : (size_t)1 << h->cls) - h->off;
}

void* __wrap_malloc(size_t n) { return arena_alloc(n ? n : 1, HDR); }
void* __wrap_calloc(size_t a, size_t b) {
    if (b && a > SIZE_MAX / b) { errno = ENOMEM; return NULL; }
    size_t n = a * b;
    void* p = arena_alloc(n ? n : 1, HDR);
    if (p) memset(p, 0, n);
    return p;
}
void __wrap_free(void* p) {
    if (!p) return;
    if (!in_arena(p)) { __real_free(p); return; }
    Hdr* h = (Hdr*)p - 1;
    if (h->magic != MAGIC) { fprintf(stderr, "gate_alloc: bad free %p\n", p); abort(); }
    void* b = (char*)p - h->off;
    uint32_t cls = h->cls, pages = h->pages;
    h->magic = 0;
    pthread_mutex_lock(&g_lock);
    if (cls == LARGE) { Big* n = b; n->pages = pages; n->next = g_big; g_big = n; }
    else { *(void**)b = g_free[cls]; g_free[cls] = b; }
    pthread_mutex_unlock(&g_lock);
}
void* __wrap_realloc(void* p, size_t n) {
    if (!p) return __wrap_malloc(n);
    if (!in_arena(p)) return __real_realloc(p, n);
    if (!n) { __wrap_free(p); return NULL; }
    size_t have = usable(p);
    if (n <= have) return p;
    void* q = __wrap_malloc(n);
    if (!q) return NULL;
    memcpy(q, p, have);
    __wrap_free(p);
    return q;
}
int __wrap_posix_memalign(void** out, size_t align, size_t n) {
    if (!align || (align & (align - 1))) return EINVAL;
    void* p = arena_alloc(n ? n : 1, align);
    if (!p) return ENOMEM;
    *out = p;
    return 0;
}
void* __wrap_memalign(size_t align, size_t n) { void* p = NULL; return __wrap_posix_memalign(&p, align, n) ? NULL : p; }
void* __wrap_aligned_alloc(size_t align, size_t n) { return __wrap_memalign(align, n); }
char* __wrap_strdup(const char* s) {
    size_t n = strlen(s) + 1;
    char* d = __wrap_malloc(n);
    if (d) memcpy(d, s, n);
    return d;
}

__attribute__((visibility("default"))) uint64_t gate_arena_peak(void) { return g_peak; }
__attribute__((visibility("default"))) uint64_t gate_arena_base(void) { return ARENA_BASE; }
__attribute__((visibility("default"))) uint64_t gate_arena_size(void) { return ARENA_SIZE; }
