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

static void invalid(const char *fn)
{
    char b[96];
    snprintf(b, sizeof b, "[fnvnx] CRT invalid parameter in %s", fn);
    os_debug_print(b);
}

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

int _strcmpi(const char *a, const char *b) { return _stricmp(a, b); }
int _stricoll(const char *a, const char *b) { return _stricmp(a, b); }
/* single-byte code page: the _mbs functions behave like their str counterparts */
int _mbscmp(const unsigned char *a, const unsigned char *b) { int r = strcmp((const char *)a, (const char *)b); return r < 0 ? -1 : r > 0; }
int _mbsicmp(const unsigned char *a, const unsigned char *b) { int r = _stricmp((const char *)a, (const char *)b); return r < 0 ? -1 : r > 0; }
int _mbsnicmp(const unsigned char *a, const unsigned char *b, size_t n) { int r = _strnicmp((const char *)a, (const char *)b, n); return r < 0 ? -1 : r > 0; }

char *_strlwr(char *s) { for (char *p = s; p && *p; p++) *p = (char)lower((unsigned char)*p); return s; }
char *_strupr(char *s) { for (char *p = s; p && *p; p++) *p = (char)upper((unsigned char)*p); return s; }

errno_t _strlwr_s(char *s, size_t n)
{
    if (!s || !n || !memchr(s, 0, n)) {
        invalid("_strlwr_s");
        if (s && n)
            s[0] = 0;
        return FNVNX_EINVAL;
    }
    _strlwr(s);
    return 0;
}

errno_t _strupr_s(char *s, size_t n)
{
    if (!s || !n || !memchr(s, 0, n)) {
        invalid("_strupr_s");
        if (s && n)
            s[0] = 0;
        return FNVNX_EINVAL;
    }
    _strupr(s);
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

/* ---- secure copies */
errno_t strcpy_s(char *d, size_t n, const char *s)
{
    if (!d || !n) {
        invalid("strcpy_s");
        return FNVNX_EINVAL;
    }
    if (!s) {
        d[0] = 0;
        invalid("strcpy_s");
        return FNVNX_EINVAL;
    }
    size_t l = strlen(s);
    if (l >= n) {
        d[0] = 0;
        invalid("strcpy_s");
        return FNVNX_ERANGE;
    }
    memcpy(d, s, l + 1);
    return 0;
}

errno_t strcat_s(char *d, size_t n, const char *s)
{
    if (!d || !n) {
        invalid("strcat_s");
        return FNVNX_EINVAL;
    }
    size_t dl = strnlen(d, n);
    if (dl == n || !s) {
        d[0] = 0;
        invalid("strcat_s");
        return FNVNX_EINVAL;
    }
    size_t l = strlen(s);
    if (dl + l >= n) {
        d[0] = 0;
        invalid("strcat_s");
        return FNVNX_ERANGE;
    }
    memcpy(d + dl, s, l + 1);
    return 0;
}

errno_t strncpy_s(char *d, size_t n, const char *s, size_t count)
{
    if (!d && !n && !count)
        return 0;
    if (!d || !n) {
        invalid("strncpy_s");
        return FNVNX_EINVAL;
    }
    if (!count) {
        d[0] = 0;
        return 0;
    }
    if (!s) {
        d[0] = 0;
        invalid("strncpy_s");
        return FNVNX_EINVAL;
    }
    size_t l = strnlen(s, count == _TRUNCATE ? n : count);
    if (count == _TRUNCATE && l >= n) {
        memcpy(d, s, n - 1);
        d[n - 1] = 0;
        return FNVNX_STRUNCATE;
    }
    if (l >= n) {
        d[0] = 0;
        invalid("strncpy_s");
        return FNVNX_ERANGE;
    }
    memcpy(d, s, l);
    d[l] = 0;
    return 0;
}

errno_t strncat_s(char *d, size_t n, const char *s, size_t count)
{
    if (!d || !n) {
        invalid("strncat_s");
        return FNVNX_EINVAL;
    }
    size_t dl = strnlen(d, n);
    if (dl == n) {
        d[0] = 0;
        invalid("strncat_s");
        return FNVNX_EINVAL;
    }
    if (!count)
        return 0;
    if (!s) {
        d[0] = 0;
        invalid("strncat_s");
        return FNVNX_EINVAL;
    }
    size_t l = strnlen(s, count == _TRUNCATE ? n : count);
    if (dl + l >= n) {
        if (count == _TRUNCATE) {
            memcpy(d + dl, s, n - dl - 1);
            d[n - 1] = 0;
            return FNVNX_STRUNCATE;
        }
        d[0] = 0;
        invalid("strncat_s");
        return FNVNX_ERANGE;
    }
    memcpy(d + dl, s, l);
    d[dl + l] = 0;
    return 0;
}

char *strtok_s(char *s, const char *delim, char **ctx)
{
    if (!ctx || !delim || (!s && !*ctx)) {
        invalid("strtok_s");
        return NULL;
    }
    char *p = s ? s : *ctx;
    p += strspn(p, delim);
    if (!*p) {
        *ctx = p;
        return NULL;
    }
    char *e = p + strcspn(p, delim);
    if (*e)
        *e++ = 0;
    *ctx = e;
    return p;
}

errno_t memcpy_s(void *d, size_t n, const void *s, size_t count)
{
    if (!count)
        return 0;
    if (!d) {
        invalid("memcpy_s");
        return FNVNX_EINVAL;
    }
    if (!s || n < count) {
        memset(d, 0, n);
        invalid("memcpy_s");
        return s ? FNVNX_ERANGE : FNVNX_EINVAL;
    }
    memcpy(d, s, count);
    return 0;
}

errno_t memmove_s(void *d, size_t n, const void *s, size_t count)
{
    if (!count)
        return 0;
    if (!d || !s) {
        invalid("memmove_s");
        return FNVNX_EINVAL;
    }
    if (n < count) {
        invalid("memmove_s");
        return FNVNX_ERANGE;
    }
    memmove(d, s, count);
    return 0;
}

size_t fnvnx_wcslen16(const unsigned short *s)
{
    size_t n = 0;
    while (s[n])
        n++;
    return n;
}

/* UTF-16 -> the ANSI code page (1252 subset: code units above 0xFF become '?') */
errno_t wcstombs_s(size_t *conv, char *d, size_t n, const unsigned short *s, size_t count)
{
    if (conv)
        *conv = 0;
    if ((!d && n) || !s) {
        invalid("wcstombs_s");
        return FNVNX_EINVAL;
    }
    size_t len = fnvnx_wcslen16(s);
    if (count != _TRUNCATE && count < len)
        len = count;
    if (!d) {
        if (conv)
            *conv = len + 1;
        return 0;
    }
    errno_t r = 0;
    if (len >= n) {
        if (count != _TRUNCATE) {
            d[0] = 0;
            invalid("wcstombs_s");
            return FNVNX_ERANGE;
        }
        len = n - 1;
        r = FNVNX_STRUNCATE;
    }
    for (size_t i = 0; i < len; i++)
        d[i] = s[i] < 0x100 ? (char)s[i] : '?';
    d[len] = 0;
    if (conv)
        *conv = len + 1;
    return r;
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
char *_ltoa(long v, char *b, int r) { return r == 10 && v < 0 ? utoa_base(0ul - (unsigned long)(uint32_t)v, b, r, 1) : utoa_base((uint32_t)v, b, r, 0); }
char *_ultoa(unsigned long v, char *b, int r) { return utoa_base((uint32_t)v, b, r, 0); }
char *_i64toa(long long v, char *b, int r) { return r == 10 && v < 0 ? utoa_base(0ull - (unsigned long long)v, b, r, 1) : utoa_base((unsigned long long)v, b, r, 0); }
char *_ui64toa(unsigned long long v, char *b, int r) { return utoa_base(v, b, r, 0); }

static errno_t xtoa_s(unsigned long long v, int neg, char *b, size_t n, int r)
{
    char tmp[72];
    if (!b || !n || r < 2 || r > 36) {
        if (b && n)
            b[0] = 0;
        invalid("_itoa_s");
        return FNVNX_EINVAL;
    }
    utoa_base(v, tmp, r, neg);
    if (strlen(tmp) >= n) {
        b[0] = 0;
        invalid("_itoa_s");
        return FNVNX_ERANGE;
    }
    strcpy(b, tmp);
    return 0;
}

errno_t _itoa_s(int v, char *b, size_t n, int r) { return r == 10 && v < 0 ? xtoa_s(0u - (unsigned)v, 1, b, n, r) : xtoa_s((unsigned)v, 0, b, n, r); }
errno_t _ltoa_s(long v, char *b, size_t n, int r) { return _itoa_s((int)v, b, n, r); }
errno_t _ultoa_s(unsigned long v, char *b, size_t n, int r) { return xtoa_s((uint32_t)v, 0, b, n, r); }

long long _atoi64(const char *s) { return strtoll(s, NULL, 10); }
long long _strtoi64(const char *s, char **e, int b) { return strtoll(s, e, b); }
unsigned long long _strtoui64(const char *s, char **e, int b) { return strtoull(s, e, b); }

/* _gcvt: like "%.*g" but MSVC keeps a trailing '.' for integral values ("1." for 1.0 with 2 digits) */
char *_gcvt(double v, int digits, char *buf)
{
    sprintf(buf, "%.*g", digits, v);
    if (!strchr(buf, '.') && !strchr(buf, 'e') && !strchr(buf, 'n') && !strchr(buf, 'i'))
        strcat(buf, ".");
    char *e = strchr(buf, 'e');
    if (e) { /* MSVC uses three exponent digits: 1e+010 */
        char sign = e[1];
        int ex = atoi(e + 2);
        sprintf(e, "e%c%03d", sign, ex);
    }
    return buf;
}

/* ---- path splitting (drive, dir, fname, ext) */
static void split(const char *path, const char **drv, size_t *dl, const char **dir, size_t *dirl,
                  const char **fn, size_t *fl, const char **ext, size_t *el)
{
    const char *p = path;
    *drv = p;
    *dl = 0;
    if (p[0] && p[1] == ':') {
        *dl = 2;
        p += 2;
    }
    const char *last_sep = NULL, *last_dot = NULL;
    for (const char *q = p; *q; q++) {
        if (*q == '\\' || *q == '/')
            last_sep = q;
        else if (*q == '.')
            last_dot = q;
    }
    *dir = p;
    *dirl = last_sep ? (size_t)(last_sep + 1 - p) : 0;
    const char *f = last_sep ? last_sep + 1 : p;
    if (last_dot && last_dot >= f) {
        *fn = f;
        *fl = (size_t)(last_dot - f);
        *ext = last_dot;
        *el = strlen(last_dot);
    } else {
        *fn = f;
        *fl = strlen(f);
        *ext = f + *fl;
        *el = 0;
    }
}

static void put(char *d, const char *s, size_t l)
{
    if (d) {
        memcpy(d, s, l);
        d[l] = 0;
    }
}

void _splitpath(const char *path, char *drive, char *dir, char *fname, char *ext)
{
    const char *a, *b, *c, *e;
    size_t al, bl, cl, el;
    split(path, &a, &al, &b, &bl, &c, &cl, &e, &el);
    put(drive, a, al);
    put(dir, b, bl);
    put(fname, c, cl);
    put(ext, e, el);
}

errno_t _splitpath_s(const char *path, char *drive, size_t dn, char *dir, size_t dirn, char *fname, size_t fnn,
                     char *ext, size_t en)
{
    if (!path || (drive && !dn) || (!drive && dn) || (dir && !dirn) || (!dir && dirn) || (fname && !fnn) ||
        (!fname && fnn) || (ext && !en) || (!ext && en)) {
        invalid("_splitpath_s");
        return FNVNX_EINVAL;
    }
    const char *a, *b, *c, *e;
    size_t al, bl, cl, el;
    split(path, &a, &al, &b, &bl, &c, &cl, &e, &el);
    if ((drive && al >= dn) || (dir && bl >= dirn) || (fname && cl >= fnn) || (ext && el >= en)) {
        if (drive) drive[0] = 0;
        if (dir) dir[0] = 0;
        if (fname) fname[0] = 0;
        if (ext) ext[0] = 0;
        invalid("_splitpath_s");
        return FNVNX_ERANGE;
    }
    put(drive, a, al);
    put(dir, b, bl);
    put(fname, c, cl);
    put(ext, e, el);
    return 0;
}

static size_t build(char *out, size_t n, const char *drive, const char *dir, const char *fname, const char *ext)
{
    size_t o = 0;
#define ADD(s, l) do { size_t l_ = (l); if (out && o + l_ < n) memcpy(out + o, (s), l_); o += l_; } while (0)
    if (drive && *drive) {
        ADD(drive, 1);
        ADD(":", 1);
    }
    if (dir && *dir) {
        size_t l = strlen(dir);
        ADD(dir, l);
        if (dir[l - 1] != '\\' && dir[l - 1] != '/')
            ADD("\\", 1);
    }
    if (fname)
        ADD(fname, strlen(fname));
    if (ext && *ext) {
        if (*ext != '.')
            ADD(".", 1);
        ADD(ext, strlen(ext));
    }
#undef ADD
    if (out && o < n)
        out[o] = 0;
    return o;
}

void _makepath(char *out, const char *drive, const char *dir, const char *fname, const char *ext)
{
    build(out, (size_t)-1, drive, dir, fname, ext);
}

errno_t _makepath_s(char *out, size_t n, const char *drive, const char *dir, const char *fname, const char *ext)
{
    if (!out || !n) {
        invalid("_makepath_s");
        return FNVNX_EINVAL;
    }
    size_t need = build(NULL, 0, drive, dir, fname, ext);
    if (need >= n) {
        out[0] = 0;
        invalid("_makepath_s");
        return FNVNX_ERANGE;
    }
    build(out, n, drive, dir, fname, ext);
    return 0;
}

/* ---- formatting: MSVCRT.DLL's format language, one engine (msvcrt_exports.cpp): writes at most n characters (no
 * terminator), returns the full length; buf may be NULL when n is 0. */
int fnvnx_msvc_format(char *buf, size_t n, const char *fmt, va_list ap);

int _vsnprintf(char *buf, size_t n, const char *fmt, va_list ap)
{
    if (!fmt || (!buf && n)) {
        errno = FNVNX_EINVAL;
        return -1;
    }
    int len = fnvnx_msvc_format(buf, n, fmt, ap);
    if (len < 0)
        return -1;
    if ((size_t)len < n)
        buf[len] = 0;
    return (size_t)len <= n ? len : -1; /* len == n: no terminator; longer: truncated, -1 */
}

int _snprintf(char *buf, size_t n, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int r = _vsnprintf(buf, n, fmt, ap);
    va_end(ap);
    return r;
}

int _vscprintf(const char *fmt, va_list ap) { return fmt ? fnvnx_msvc_format(NULL, 0, fmt, ap) : -1; }

int _scprintf(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int r = _vscprintf(fmt, ap);
    va_end(ap);
    return r;
}

int vsprintf_s(char *buf, size_t n, const char *fmt, va_list ap)
{
    if (!buf || !n || !fmt) {
        invalid("vsprintf_s");
        return -1;
    }
    int len = fnvnx_msvc_format(buf, n, fmt, ap);
    if (len < 0 || (size_t)len >= n) {
        buf[0] = 0;
        invalid("vsprintf_s");
        return -1;
    }
    buf[len] = 0;
    return len;
}

int sprintf_s(char *buf, size_t n, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int r = vsprintf_s(buf, n, fmt, ap);
    va_end(ap);
    return r;
}

int _vsnprintf_s(char *buf, size_t n, size_t count, const char *fmt, va_list ap)
{
    if (!buf || !n || !fmt) {
        invalid("_vsnprintf_s");
        return -1;
    }
    size_t lim = count == _TRUNCATE || count + 1 > n ? n : count + 1;
    int len = fnvnx_msvc_format(buf, lim, fmt, ap);
    if (len < 0)
        return -1;
    if ((size_t)len >= lim) {
        if (count == _TRUNCATE || count + 1 <= n) {
            buf[lim - 1] = 0;
            return -1; /* truncated as asked */
        }
        buf[0] = 0;
        invalid("_vsnprintf_s");
        return -1;
    }
    buf[len] = 0;
    return len;
}

int _snprintf_s(char *buf, size_t n, size_t count, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int r = _vsnprintf_s(buf, n, count, fmt, ap);
    va_end(ap);
    return r;
}

/* ---- S4b: functions identified from the decomp's calls (crt_symbols.tsv) */
unsigned long fnvnx_byteswap_ulong(unsigned long v) { return __builtin_bswap32((uint32_t)v); }
unsigned short fnvnx_byteswap_ushort(unsigned short v) { return __builtin_bswap16(v); }
unsigned long long fnvnx_byteswap_uint64(unsigned long long v) { return __builtin_bswap64(v); }

/* _mbstok keeps its own position (MSVC: _tiddata._mtoken, separate from strtok's) */
char *fnvnx_mbstok(char *s, const char *delim)
{
    static __thread char *pos;
    return strtok_s(s, delim, &pos);
}

/* the _mbs functions of the single-byte "C" code page: no lead bytes */
unsigned char *_mbsnbcpy(unsigned char *d, const unsigned char *s, size_t n)
{
    return (unsigned char *)strncpy((char *)d, (const char *)s, n);
}
unsigned char *_mbsncpy(unsigned char *d, const unsigned char *s, size_t n) { return _mbsnbcpy(d, s, n); }
int _mbsnbcmp(const unsigned char *a, const unsigned char *b, size_t n)
{
    int r = strncmp((const char *)a, (const char *)b, n);
    return r < 0 ? -1 : r > 0;
}
int _mbsnbicmp(const unsigned char *a, const unsigned char *b, size_t n)
{
    int r = _strnicmp((const char *)a, (const char *)b, n);
    return r < 0 ? -1 : r > 0;
}

/* _wcsicmp / wcscmp on the game's 16-bit wchar_t; the "C" locale folds A-Z only, as MSVC's fast path */
int fnvnx_wcsicmp16(const unsigned short *a, const unsigned short *b)
{
    for (;; a++, b++) {
        int x = *a >= 'A' && *a <= 'Z' ? *a + 32 : *a, y = *b >= 'A' && *b <= 'Z' ? *b + 32 : *b;
        if (x != y || !x)
            return x - y;
    }
}

int fnvnx_wcscmp16(const unsigned short *a, const unsigned short *b)
{
    for (; *a && *a == *b; a++, b++)
        ;
    return *a < *b ? -1 : *a > *b;
}
