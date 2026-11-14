/* string.c - MSVC string, conversion and formatting functions with MSVC semantics.
 *
 * The secure (_s) functions follow the CRT's documented behaviour with the default invalid-parameter handler
 * replaced by "log, clear the destination, return the error" (the retail game never relies on the crash). */
#include "fnvnx_msvcrt.h"

#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../os/os.h"


static inline int lower(int c) { return c >= 'A' && c <= 'Z' ? c + 32 : c; }
static inline int upper(int c) { return c >= 'a' && c <= 'z' ? c - 32 : c; }

/* ---- comparisons (the "C" locale: ASCII folding to lower case, as MSVC's _stricmp does) */
int _stricmp(const char *a, const char *b)
{
    for (;; a++, b++) {
        int x = lower((unsigned char)*a), y = lower((unsigned char)*b);
        if (x != y || !x)
            return x - y;
    }
}

int _strnicmp(const char *a, const char *b, size_t n)
{
    for (; n; n--, a++, b++) {
        int x = lower((unsigned char)*a), y = lower((unsigned char)*b);
        if (x != y || !x)
            return x - y;
    }
    return 0;
}





char *_strdup(const char *s)
{
    if (!s)
        return NULL;
    size_t n = strlen(s) + 1;
    char *d = (char *)malloc(n);
    return d ? (char *)memcpy(d, s, n) : NULL;
}








size_t fnvnx_wcslen16(const unsigned short *s)
{
    size_t n = 0;
    while (s[n])
        n++;
    return n;
}


/* ---- integer <-> string */
static char *utoa_base(unsigned long long v, char *buf, int radix, int neg)
{
    char tmp[72];
    int n = 0;
    if (radix < 2 || radix > 36) {
        buf[0] = 0;
        return buf;
    }
    do {
        unsigned d = (unsigned)(v % (unsigned)radix);
        tmp[n++] = (char)(d < 10 ? '0' + d : 'a' + d - 10);
        v /= (unsigned)radix;
    } while (v);
    char *p = buf;
    if (neg)
        *p++ = '-';
    while (n)
        *p++ = tmp[--n];
    *p = 0;
    return buf;
}

/* MSVC: a negative value is written with '-' only in radix 10; other radices print the two's complement */
char *_itoa(int v, char *b, int r) { return r == 10 && v < 0 ? utoa_base(0u - (unsigned)v, b, r, 1) : utoa_base((unsigned)v, b, r, 0); }
char *_ultoa(unsigned long v, char *b, int r) { return utoa_base((uint32_t)v, b, r, 0); }












/* ---- formatting: MSVCRT.DLL's format language, one engine (msvcrt_format.cpp): writes at most n characters (no
 * terminator), returns the full length; buf may be NULL when n is 0. */
int _vsnprintf(char *buf, size_t n, const char *fmt, va_list ap)
{
    if (!fmt || (!buf && n)) {
        errno = FNVNX_EINVAL;
        return -1;
    }
    return fnvnx_msvc_bound(buf, n, fnvnx_msvc_format(buf, n, fmt, ap));
}

int _snprintf(char *buf, size_t n, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int r = _vsnprintf(buf, n, fmt, ap);
    va_end(ap);
    return r;
}








