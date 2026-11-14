/* msvcrt_exports.cpp - the msvcrt.dll imports of the lifted D3DX DLLs, provided by name (fnvnx_msvcrt_lookup, called
 * by fnvnx_win32_lookup_export for dll "msvcrt"). Each function follows MSVCRT.DLL (the Windows system C runtime,
 * not a C99 libc): the table at the end says which come from the libc and which from the other platform/crt files. Provider and
 * status of every name: MSVCRT_EXPORTS.tsv. The exception / FPU runtime (provider L3) is deliberately absent.
 *
 * Rules (each closes its class): numbers are parsed by a grammar scanner and converted by the libc's correctly
 * rounded strtod (glibc and newlib's gdtoa round exactly, to nearest even); qsort is an in-place heapsort whose
 * indices stay below n whatever the comparator answers; character classes come from the layer's own NLS tables
 * (GetStringTypeW / LCMapStringW) for every code page the layer supports ("C" = identity below 128, 1252). */
#include "../win32/w32.h"

#include <cmath>
#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <utility>
#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "fnvnx_iobuf.h"
#include "fnvnx_msvcrt.h"

extern "C" {
struct fnvnx_ioinfo;
extern struct fnvnx_ioinfo *__pioinfo[64];   /* iob.c */
extern struct fnvnx_ioinfo __badioinfo;
void ExitProcess(UINT);
void GetLocalTime(LPSYSTEMTIME);
}

namespace fnvnx_msvcrt_impl {
typedef unsigned short u16;

/* ---------------------------------------------------------------- heap: one heap (the libc's) for all six */
extern "C" void *c_malloc(size_t n)
{
    void *p = malloc(n ? n : 1); /* malloc(0): a unique non-NULL block */
    if (!p)
        errno = ENOMEM;
    return p;
}
extern "C" void *c_calloc(size_t a, size_t b)
{
    if (b && a > (size_t)-1 / b) {
        errno = ENOMEM;
        return NULL;
    }
    size_t tot = a * b;
    void *p = calloc(tot ? tot : 1, 1);
    if (!p)
        errno = ENOMEM;
    return p;
}
extern "C" void *c_realloc(void *p, size_t n)
{
    if (!p)
        return c_malloc(n);
    if (!n) {
        free(p);
        return NULL;
    }
    void *q = realloc(p, n);
    if (!q)
        errno = ENOMEM;
    return q;
}
extern "C" void c_free(void *p) { free(p); }

/* ---------------------------------------------------------------- locale and ctype */
enum { T_UP = 1, T_LO = 2, T_DIG = 4, T_SP = 8, T_PU = 0x10, T_CT = 0x20, T_BL = 0x40, T_HEX = 0x80, T_AL = 0x100 };
struct ctab { u16 t[256]; unsigned char lo[256], up[256]; int cp; };
ctab g_ct[2]; /* [0] "C", [1] code page 1252 */
ctab *volatile g_cur = &g_ct[0];
os_lock_t g_loc_lock = OS_LOCK_INIT;
int g_built;
const char *g_cat[6] = {"C", "C", "C", "C", "C", "C"};
const char k_1252[] = "English_United States.1252";

void build_tables()
{
    for (int c = 0; c < 256; c++) {
        g_ct[0].lo[c] = g_ct[0].up[c] = g_ct[1].lo[c] = g_ct[1].up[c] = (unsigned char)c;
        u16 t = 0;
        if (c < 128) {
            if (c >= 'A' && c <= 'Z') t = T_UP | T_AL, g_ct[0].lo[c] = (unsigned char)(c + 32);
            if (c >= 'a' && c <= 'z') t = T_LO | T_AL, g_ct[0].up[c] = (unsigned char)(c - 32);
            if (c >= '0' && c <= '9') t = T_DIG | T_HEX;
            if ((c >= 'A' && c <= 'F') || (c >= 'a' && c <= 'f')) t |= T_HEX;
            if (c == ' ') t = T_SP | T_BL;
            if (c >= 9 && c <= 13) t = T_SP | T_CT | (c == 9 ? T_BL : 0);
            else if (c < 32 || c == 127) t = T_CT;
            if (c > 32 && c < 127 && !(t & (T_AL | T_DIG))) t = T_PU;
        }
        g_ct[0].t[c] = t;
        char ch = (char)c;
        WCHAR u = 0, m = 0;
        WORD ty = 0;
        MultiByteToWideChar(1252, 0, &ch, 1, &u, 1);
        GetStringTypeW(CT_CTYPE1, &u, 1, &ty);
        g_ct[1].t[c] = (u16)(ty & 0x1FF);
        for (int up = 0; up < 2; up++) {
            if (LCMapStringW(0x0409, up ? LCMAP_UPPERCASE : LCMAP_LOWERCASE, &u, 1, &m, 1) != 1)
                continue;
            char b = 0;
            BOOL used = FALSE;
            if (WideCharToMultiByte(1252, WC_NO_BEST_FIT_CHARS, &m, 1, &b, 1, NULL, &used) == 1 && !used)
                (up ? g_ct[1].up : g_ct[1].lo)[c] = (unsigned char)b;
        }
    }
    g_ct[0].cp = 0;
    g_ct[1].cp = 1252;
}

extern "C" const char *c_setlocale(int cat, const char *loc)
{
    static char composite[160];
    if (cat < 0 || cat > 5)
        return NULL;
    if (loc) {
        int cp;
        const char *nm;
        if (!strcmp(loc, "C"))
            nm = "C", cp = 0;
        else if (!*loc || !strcmp(loc, k_1252))
            nm = k_1252, cp = 1252;
        else
            return NULL;
        os_lock(&g_loc_lock);
        if (!g_built)
            build_tables(), g_built = 1;
        for (int i = 1; i < 6; i++)
            if (cat == 0 || cat == i)
                g_cat[i] = nm;
        if (cat == 0 || cat == 2)
            g_cur = &g_ct[cp ? 1 : 0];
        os_unlock(&g_loc_lock);
    }
    if (cat)
        return g_cat[cat];
    for (int i = 2; i < 6; i++)
        if (g_cat[i] != g_cat[1])
            goto mixed;
    return g_cat[1];
mixed:
    snprintf(composite, sizeof composite, "LC_COLLATE=%s;LC_CTYPE=%s;LC_MONETARY=%s;LC_NUMERIC=%s;LC_TIME=%s",
             g_cat[1], g_cat[2], g_cat[3], g_cat[4], g_cat[5]);
    return composite;
}

/* the C-locale table is built on first use of any ctype entry: setlocale(NULL) alone must not need it */
inline const ctab *cur()
{
    if (!g_built) {
        os_lock(&g_loc_lock);
        if (!g_built)
            build_tables(), g_built = 1;
        os_unlock(&g_loc_lock);
    }
    return g_cur;
}
inline int cls(int c, unsigned m) { return c >= 0 && c <= 255 ? cur()->t[c] & m : 0; } /* EOF and out of range: 0 */
extern "C" int c_isalpha(int c) { return cls(c, T_AL | T_UP | T_LO); }
extern "C" int c_isalnum(int c) { return cls(c, T_AL | T_UP | T_LO | T_DIG); }
extern "C" int c_isdigit(int c) { return cls(c, T_DIG); }
extern "C" int c_isspace(int c) { return cls(c, T_SP); }
extern "C" int c_isxdigit(int c) { return cls(c, T_HEX); }
extern "C" {
#include "env_const.gen.inc"   /* c_isleadbyte: manifest/env_const.tsv */
}
extern "C" int c_tolower(int c) { return c >= 0 && c <= 255 ? cur()->lo[c] : c; }
extern "C" int c_toupper(int c) { return c >= 0 && c <= 255 ? cur()->up[c] : c; }
int wcls(int c, unsigned m)
{
    if (c < 0 || c >= 0xFFFF) /* WEOF and out of range */
        return 0;
    WCHAR w = (WCHAR)c;
    WORD t = 0;
    GetStringTypeW(CT_CTYPE1, &w, 1, &t);
    return t & m;
}
extern "C" int c_iswalpha(int c) { return wcls(c, C1_ALPHA); }
extern "C" int c_iswdigit(int c) { return wcls(c, C1_DIGIT); }
extern "C" int c_iswpunct(int c) { return wcls(c, C1_PUNCT); }
extern "C" int c_iswspace(int c) { return wcls(c, C1_SPACE); }

/* wide -> narrow in the current code page; 1 on success */
int wc_to_byte(u16 wc, unsigned char *out)
{
    if (cur()->cp == 0) {
        *out = (unsigned char)wc;
        return wc < 256;
    }
    char b = 0;
    BOOL used = FALSE;
    WCHAR w = (WCHAR)wc;
    int r = WideCharToMultiByte(1252, WC_NO_BEST_FIT_CHARS, &w, 1, &b, 1, NULL, &used);
    *out = (unsigned char)b;
    return r == 1 && !used;
}
extern "C" size_t c_wcstombs(char *d, const u16 *s, size_t n)
{
    size_t k = 0;
    if (!s) {
        errno = EINVAL;
        return (size_t)-1;
    }
    for (;; s++) {
        unsigned char b;
        if (!wc_to_byte(*s, &b)) {
            errno = 42; /* MSVCRT EILSEQ (the host value differs) */
            return (size_t)-1;
        }
        if (d) {
            if (k >= n)
                return k;
            d[k] = (char)b;
        }
        if (!*s)
            return k;
        k++;
    }
}
extern "C" int c_wctomb(char *d, u16 wc)
{
    if (!d)
        return 0; /* stateless encoding */
    unsigned char b;
    if (!wc_to_byte(wc, &b)) {
        errno = 42; /* MSVCRT EILSEQ (the host value differs) */
        return -1;
    }
    *d = (char)b;
    return 1;
}

/* ---------------------------------------------------------------- printf and scanf: the engine is msvcrt_format.cpp */
extern "C" int c_sprintf(char *buf, const char *fmt, ...)
{
    if (!buf || !fmt) {
        errno = EINVAL;
        return -1;
    }
    va_list ap;
    va_start(ap, fmt);
    int r = fnvnx_msvc_format(buf, (size_t)-1, fmt, ap);
    va_end(ap);
    if (r >= 0)
        buf[r] = 0;
    return r;
}
extern "C" int c_sscanf(const char *in, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int r = fnvnx_msvc_scan(in, fmt, ap);
    va_end(ap);
    return r;
}

/* ---------------------------------------------------------------- numbers */
extern "C" double c_atof(const char *s) { return fnvnx_msvc_strtod(s, NULL); }
/* atox.c: a 32-bit long accumulates c - '0' unchecked: values past the range wrap, as in MSVCRT.DLL */
int32_t atox(const char *s)
{
    while (c_isspace((unsigned char)*s))
        s++;
    bool neg = *s == '-';
    if (*s == '-' || *s == '+')
        s++;
    uint32_t t = 0;
    for (; *s >= '0' && *s <= '9'; s++)
        t = 10u * t + (uint32_t)(*s - '0');
    return (int32_t)(neg ? 0u - t : t);
}
extern "C" int c_atoi(const char *s) { return atox(s); }
extern "C" long c_atol(const char *s) { return (long)atox(s); }

/* ---------------------------------------------------------------- strings, sort, misc */
extern "C" char *w_strchr(const char *s, int c) { return (char *)strchr(s, c); }
extern "C" char *w_strrchr(const char *s, int c) { return (char *)strrchr(s, c); }

/* the comparator is the game's: a guest address, called through the boundary */
FNVNX_CALLBACK_T(cmp_t, int, (const void *, const void *));
void swp(char *a, char *b, size_t w)
{
    char t[256];
    while (w) {
        size_t k = w < sizeof t ? w : sizeof t;
        memcpy(t, a, k), memcpy(a, b, k), memcpy(b, t, k);
        a += k, b += k, w -= k;
    }
}
void sift(char *a, size_t w, size_t root, size_t n, cmp_t cmp)
{
    for (;;) {
        size_t ch = 2 * root + 1;
        if (ch >= n)
            return;
        if (ch + 1 < n && cmp(a + ch * w, a + (ch + 1) * w) < 0)
            ch++;
        if (cmp(a + root * w, a + ch * w) >= 0)
            return;
        swp(a + root * w, a + ch * w, w);
        root = ch;
    }
}
/* heapsort: no recursion, no allocation, every index < n whatever the comparator returns */
extern "C" void c_qsort(void *base, size_t n, size_t w, cmp_t cmp)
{
    if (n < 2 || !w)
        return;
    if (!base || !cmp || n > (size_t)-1 / w) {
        errno = EINVAL;
        return;
    }
    char *a = (char *)base;
    for (size_t i = n / 2; i-- > 0;)
        sift(a, w, i, n, cmp);
    for (size_t e = n - 1; e > 0; e--) {
        swp(a, a + e * w, w);
        sift(a, w, 0, e, cmp);
    }
}

extern "C" char *c_strdate(char *b)
{
    SYSTEMTIME t;
    GetLocalTime(&t);
    char o[40];
    snprintf(o, sizeof o, "%02u/%02u/%02u", (unsigned)t.wMonth % 100, (unsigned)t.wDay % 100, (unsigned)(t.wYear % 100));
    memcpy(b, o, 9);
    return b;
}
extern "C" char *c_strtime(char *b)
{
    SYSTEMTIME t;
    GetLocalTime(&t);
    char o[40];
    snprintf(o, sizeof o, "%02u:%02u:%02u", (unsigned)t.wHour % 100, (unsigned)t.wMinute % 100, (unsigned)t.wSecond % 100);
    memcpy(b, o, 9);
    return b;
}

extern "C" char *c_tempnam(const char *dir, const char *prefix)
{
    static volatile uint32_t seq;
    char env[260], cand[3][260];
    env[0] = 0;
    GetEnvironmentVariableA("TMP", env, sizeof env);
    snprintf(cand[0], sizeof cand[0], "%s", env);
    snprintf(cand[1], sizeof cand[1], "%s", dir ? dir : "");
    snprintf(cand[2], sizeof cand[2], "\\");
    const char *d = cand[2];
    for (int i = 0; i < 3; i++)
        if (cand[i][0] && fnvnx_access(cand[i], 0) == 0) {
            d = cand[i];
            break;
        }
    size_t dl = strlen(d), pl = prefix ? strlen(prefix) : 0;
    char *r = (char *)malloc(dl + pl + 24);
    if (!r) {
        errno = ENOMEM;
        return NULL;
    }
    for (;;) {
        uint32_t k = __atomic_add_fetch(&seq, 1, __ATOMIC_RELAXED);
        snprintf(r, dl + pl + 24, "%s%s%s%x", d, dl && d[dl - 1] != '\\' && d[dl - 1] != '/' ? "\\" : "", prefix ? prefix : "", k);
        if (fnvnx_access(r, 0) != 0)
            return r;
    }
}

/* ---------------------------------------------------------------- process: init/exit, locks, environment */
FNVNX_CALLBACK_T(pvfv, void, (void));       /* _initterm's table entries: guest addresses (one word each in the game) */
FNVNX_CALLBACK_T(onexit_fn, int, (void));
extern "C" void c_initterm(pvfv *first, pvfv *last)
{
    for (; first < last; first++)
        if (*first)
            (*first)();   /* through the boundary */
}

/* _onexit: the handler (a game function pointer) goes into the exit-handler list (exitlist.cpp) */
extern "C" onexit_fn c_onexit(onexit_fn f) { return fnvnx_exit_register((uintptr_t)f) == 0 ? f : NULL; }
/* __dllonexit: the DLL's own table [*begin, *end), grown by one on the msvcrt heap; both pointers updated */
extern "C" onexit_fn c_dllonexit(onexit_fn f, onexit_fn **begin, onexit_fn **end)
{
    if (!f || !begin || !end || (*begin == NULL) != (*end == NULL) || *end < *begin)
        return NULL;
    size_t len = (size_t)(*end - *begin) + 1;
    onexit_fn *t = (onexit_fn *)realloc(*begin, len * sizeof *t);
    if (!t)
        return NULL;
    t[len - 1] = f;
    *begin = t, *end = t + len;
    return f;
}
extern "C" void c_exit(int code)
{
    fnvnx_doexit(code, 0, 1); /* the handlers, last registered first */
    fnvnx_iob_fflush(NULL);
    ExitProcess((UINT)code);
    os_exit_process(code);
}
extern "C" void c_abort(void)
{
    static const char m[] = "\nabnormal program termination\n";
    fnvnx_write(2, m, sizeof m - 1);
    ExitProcess(3);
    os_exit_process(3);
}
extern "C" void c_amsg_exit(int n)
{
    const char *t;
    switch (n) {
    case 8: t = "not enough space for arguments"; break;
    case 9: t = "not enough space for environment"; break;
    case 10: t = "abnormal program termination"; break;
    case 16: t = "not enough space for thread data"; break;
    case 17: t = "unexpected multithread lock error"; break;
    case 18: t = "unexpected heap error"; break;
    case 24: t = "not enough space for _onexit/atexit table"; break;
    case 25: t = "pure virtual function call"; break;
    default: t = "runtime error"; break;
    }
    char m[160];
    int l = snprintf(m, sizeof m, "\nR60%02d\n- %s\n", n, t);
    fnvnx_write(2, m, (unsigned)l);
    ExitProcess(255);
    os_exit_process(255);
}
extern "C" void c_purecall(void) { c_amsg_exit(25); }

struct rlock { os_lock_t m; void *volatile owner; int count; };
rlock g_locks[64];
extern "C" void c_lock(int n)
{
    if (n < 0 || n >= 64) {
        errno = EINVAL;
        return;
    }
    rlock &l = g_locks[n];
    void *me = (void *)&errno; /* the address of errno identifies the thread */
    if (l.owner == me) {
        l.count++;
        return;
    }
    os_glock(&l.m);   /* _lock: the game's own lock, held across its callbacks (os.h) */
    l.owner = me, l.count = 1;
}
extern "C" void c_unlock(int n)
{
    if (n < 0 || n >= 64)
        return;
    rlock &l = g_locks[n];
    if (l.owner != (void *)&errno || --l.count)
        return;
    l.owner = NULL;
    os_gunlock(&l.m);
}

struct envc { char *key, *val; envc *next; };
os_lock_t g_env_lock = OS_LOCK_INIT;
envc *g_envc;
extern "C" char *c_getenv(const char *name)
{
    if (!name || !*name || strchr(name, '='))
        return NULL;
    DWORD n = GetEnvironmentVariableA(name, NULL, 0);
    if (!n)
        return NULL;
    char *v = (char *)malloc(n + 1);
    if (!v)
        return NULL;
    DWORD got = GetEnvironmentVariableA(name, v, n + 1);
    if (got >= n + 1 || (!got && n > 1)) { /* changed under us: report what is there now, or nothing */
        free(v);
        return NULL;
    }
    char key[256];
    size_t kl = strlen(name);
    if (kl >= sizeof key) {
        free(v);
        return NULL;
    }
    for (size_t i = 0; i <= kl; i++)
        key[i] = name[i] >= 'a' && name[i] <= 'z' ? (char)(name[i] - 32) : name[i];
    char *r = v;
    os_lock(&g_env_lock);
    envc *e = g_envc;
    for (; e; e = e->next)
        if (!strcmp(e->key, key))
            break;
    if (!e) {
        e = (envc *)malloc(sizeof *e);
        if (!e) {
            os_unlock(&g_env_lock);
            free(v);
            return NULL;
        }
        e->key = strdup(key), e->val = NULL, e->next = g_envc, g_envc = e;
    }
    if (e->val && !strcmp(e->val, v))
        r = e->val, free(v);
    else
        e->val = v; /* the previous value stays allocated: another thread may still hold it */
    os_unlock(&g_env_lock);
    return r;
}

/* ---------------------------------------------------------------- math */
extern "C" double c_ldexp(double x, int e)
{
    double r = scalbn(x, e); /* one rounding, to nearest even, subnormals included */
    if (std::isfinite(x) && x != 0.0 && (std::isinf(r) || r == 0.0))
        errno = ERANGE;
    return r;
}
extern "C" double c_frexp(double x, int *e)
{
    if (!std::isfinite(x)) {
        *e = 0;
        return x;
    }
    return frexp(x, e);
}
extern "C" double c_ceil(double x) { return ceil(x); }
extern "C" double c_floor(double x) { return floor(x); }
extern "C" double c_modf(double x, double *ip) { return modf(x, ip); }

}   // namespace fnvnx_msvcrt_impl
using namespace fnvnx_msvcrt_impl;

/* the names that msvcrt shares with the libc: the body is the libc's, and a symbol of this product is what the name table (msvcrt_names.tsv)
   names, so that the gate `imports` proves the row from the product objects */
extern "C" void *fnvnx_msvcrt_memcpy(void *d, const void *s, size_t n) { return memcpy(d, s, n); }
extern "C" void *fnvnx_msvcrt_memmove(void *d, const void *s, size_t n) { return memmove(d, s, n); }
extern "C" void *fnvnx_msvcrt_memset(void *d, int c, size_t n) { return memset(d, c, n); }
extern "C" int fnvnx_msvcrt_strncmp(const char *a, const char *b, size_t n) { return strncmp(a, b, n); }
extern "C" char *fnvnx_msvcrt_strncpy(char *d, const char *s, size_t n) { return strncpy(d, s, n); }

/* the code-page conversions of the formatter and scanner (msvcrt_format.cpp): the current locale's narrow <-> UTF-16 */
extern "C" int fnvnx_msvcrt_wc_to_byte(unsigned short wc, unsigned char *out) { return wc_to_byte(wc, out); }
extern "C" int fnvnx_msvcrt_byte_to_wc(unsigned char b, unsigned short *out)
{
    if (cur()->cp == 0) {
        *out = b;
        return 1;
    }
    char c = (char)b;
    WCHAR w = 0;
    int r = MultiByteToWideChar(1252, 0, &c, 1, &w, 1);
    *out = (unsigned short)w;
    return r == 1;
}
