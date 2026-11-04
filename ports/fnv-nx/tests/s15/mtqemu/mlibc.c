/* mlibc.c - the few C library functions the mtqemu guest needs (no ILP32 libc exists on Linux hosts): a locked bump
 * allocator, string functions, a small vsnprintf, exit through the host. Not instrumented (hb.c checks the code
 * under test, not this). */
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>

#include "fnvnx/hostapi.h"
#include "reent.h"

/* ---- memory: one arena, 8-byte headers, freed blocks are not reused (tests are short) */
static unsigned char g_arena[48u << 20] __attribute__((aligned(64)));
static volatile uint32_t g_brk, g_lock;

static void lk(void) { while (__atomic_exchange_n(&g_lock, 1, __ATOMIC_ACQUIRE)) __asm__ volatile("yield"); }
static void ulk(void) { __atomic_store_n(&g_lock, 0, __ATOMIC_RELEASE); }

void *memalign(size_t align, size_t n) {
    if (align < 16) align = 16;
    lk();
    uint32_t p = (g_brk + 8 + (uint32_t)align - 1) & ~((uint32_t)align - 1);
    if (p + n > sizeof g_arena) { ulk(); return 0; }
    *(uint32_t *)(g_arena + p - 8) = (uint32_t)n;
    g_brk = p + (uint32_t)n;
    ulk();
    return g_arena + p;
}
void *malloc(size_t n) { return memalign(16, n); }
void free(void *p) { (void)p; }
void *memset(void *d, int c, size_t n) { unsigned char *p = d; while (n--) *p++ = (unsigned char)c; return d; }
void *memcpy(void *d, const void *s, size_t n) { unsigned char *p = d; const unsigned char *q = s; while (n--) *p++ = *q++; return d; }
void *memmove(void *d, const void *s, size_t n) {
    unsigned char *p = d; const unsigned char *q = s;
    if (p < q) while (n--) *p++ = *q++; else { p += n; q += n; while (n--) *--p = *--q; }
    return d;
}
int memcmp(const void *a, const void *b, size_t n) {
    const unsigned char *p = a, *q = b;
    for (; n; n--, p++, q++) if (*p != *q) return *p - *q;
    return 0;
}
void *calloc(size_t a, size_t b) { void *p = malloc(a * b); if (p) memset(p, 0, a * b); return p; }
void *realloc(void *o, size_t n) {
    void *p = malloc(n);
    if (p && o) { uint32_t on = *(uint32_t *)((unsigned char *)o - 8); memcpy(p, o, on < n ? on : n); }
    return p;
}
size_t strlen(const char *s) { size_t n = 0; while (s[n]) n++; return n; }
int strcmp(const char *a, const char *b) { while (*a && *a == *b) a++, b++; return (unsigned char)*a - (unsigned char)*b; }
int strncmp(const char *a, const char *b, size_t n) {
    for (; n; n--, a++, b++) { if (*a != *b || !*a) return (unsigned char)*a - (unsigned char)*b; }
    return 0;
}
char *strncpy(char *d, const char *s, size_t n) { size_t i = 0; for (; i < n && s[i]; i++) d[i] = s[i]; for (; i < n; i++) d[i] = 0; return d; }
char *strdup(const char *s) { size_t n = strlen(s) + 1; char *p = malloc(n); if (p) memcpy(p, s, n); return p; }

/* ---- formatting: %[0][width][l|ll|z]{d,i,u,x,p,s,c,%} */
int vsnprintf(char *buf, size_t cap, const char *f, va_list ap) {
    size_t o = 0;
#define PUT(c) do { if (o + 1 < cap) buf[o] = (char)(c); o++; } while (0)
    for (; *f; f++) {
        if (*f != '%') { PUT(*f); continue; }
        f++;
        int zero = 0, width = 0, lng = 0;
        if (*f == '0') { zero = 1; f++; }
        while (*f >= '0' && *f <= '9') width = width * 10 + (*f++ - '0');
        while (*f == 'l' || *f == 'z') { lng++; f++; }
        char tmp[24];
        int n = 0, neg = 0;
        unsigned long long v = 0;
        switch (*f) {
        case 's': { const char *s = va_arg(ap, const char *); if (!s) s = "(null)"; while (*s) PUT(*s++); continue; }
        case 'c': PUT(va_arg(ap, int)); continue;
        case '%': PUT('%'); continue;
        case 'd': case 'i': {
            long long x = lng >= 2 ? va_arg(ap, long long) : lng ? va_arg(ap, long) : va_arg(ap, int);
            if (x < 0) { neg = 1; v = (unsigned long long)-x; } else v = (unsigned long long)x;
            do tmp[n++] = (char)('0' + v % 10); while (v /= 10);
            break;
        }
        case 'u': case 'x': case 'p': {
            unsigned base = *f == 'u' ? 10 : 16;
            v = *f == 'p' ? (uintptr_t)va_arg(ap, void *) : lng >= 2 ? va_arg(ap, unsigned long long)
                : lng ? va_arg(ap, unsigned long) : va_arg(ap, unsigned);
            do tmp[n++] = "0123456789abcdef"[v % base]; while (v /= base);
            if (*f == 'p') { tmp[n++] = 'x'; tmp[n++] = '0'; }
            break;
        }
        default: PUT('%'); PUT(*f); continue;
        }
        for (int w = n + neg; w < width; w++) PUT(zero ? '0' : ' ');
        if (neg) PUT('-');
        while (n) PUT(tmp[--n]);
    }
    if (cap) buf[o < cap ? o : cap - 1] = 0;
    return (int)o;
#undef PUT
}
int snprintf(char *b, size_t n, const char *f, ...) { va_list ap; va_start(ap, f); int r = vsnprintf(b, n, f, ap); va_end(ap); return r; }
int printf(const char *f, ...) {
    char b[512];
    va_list ap; va_start(ap, f); int r = vsnprintf(b, sizeof b, f, ap); va_end(ap);
    fnvnx_hcall2(fnvnx_host->console_write, FNVNX_ADDR(b), strlen(b));
    return r;
}

int puts(const char *s) { printf("%s\n", s); return 0; }   /* GCC turns printf("...\n") into puts */

/* ---- process, newlib and C++ runtime odds and ends */
struct _reent g_reent0;
struct _reent *_impure_ptr = &g_reent0;
void _reclaim_reent(struct _reent *r) { (void)r; }
static int g_errno;
int *__errno_location(void) { return &g_errno; }
int fflush(void *f) { (void)f; return 0; }
void exit(int code) { fnvnx_hcall1(fnvnx_host->exit, (uint64_t)(unsigned)code); for (;;) ; }
void abort(void) { exit(134); }
void *__dso_handle;
int __cxa_atexit(void (*f)(void *), void *a, void *d) { (void)f; (void)a; (void)d; return 0; }
void __cxa_pure_virtual(void) { abort(); }
