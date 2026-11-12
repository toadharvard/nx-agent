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
int fnvnx_msvc_format(char *buf, size_t n, const char *fmt, va_list ap);
}

namespace {
typedef unsigned short u16;

/* ---------------------------------------------------------------- heap: one heap (the libc's) for all six */
void *c_malloc(size_t n)
{
    void *p = malloc(n ? n : 1); /* malloc(0): a unique non-NULL block */
    if (!p)
        errno = ENOMEM;
    return p;
}
void *c_calloc(size_t a, size_t b)
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
void *c_realloc(void *p, size_t n)
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
void c_free(void *p) { free(p); }

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

const char *c_setlocale(int cat, const char *loc)
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
int c_isalpha(int c) { return cls(c, T_AL | T_UP | T_LO); }
int c_isalnum(int c) { return cls(c, T_AL | T_UP | T_LO | T_DIG); }
int c_isdigit(int c) { return cls(c, T_DIG); }
int c_isspace(int c) { return cls(c, T_SP); }
int c_isxdigit(int c) { return cls(c, T_HEX); }
int c_isleadbyte(int) { return 0; } /* "C" and 1252 are single-byte code pages */
int c_tolower(int c) { return c >= 0 && c <= 255 ? cur()->lo[c] : c; }
int c_toupper(int c) { return c >= 0 && c <= 255 ? cur()->up[c] : c; }
int wcls(int c, unsigned m)
{
    if (c < 0 || c >= 0xFFFF) /* WEOF and out of range */
        return 0;
    WCHAR w = (WCHAR)c;
    WORD t = 0;
    GetStringTypeW(CT_CTYPE1, &w, 1, &t);
    return t & m;
}
int c_iswalpha(int c) { return wcls(c, C1_ALPHA); }
int c_iswdigit(int c) { return wcls(c, C1_DIGIT); }
int c_iswpunct(int c) { return wcls(c, C1_PUNCT); }
int c_iswspace(int c) { return wcls(c, C1_SPACE); }

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
size_t c_wcstombs(char *d, const u16 *s, size_t n)
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
int c_wctomb(char *d, u16 wc)
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

/* ---------------------------------------------------------------- printf */
struct Out {
    char *b;
    size_t n, len;
    void put(char c) { if (b && len < n) b[len] = c; len++; }
    void put(const char *s, size_t k) { while (k--) put(*s++); }
    void fill(char c, long k) { while (k-- > 0) put(c); }
};
/* prefix, zeros, body, padded to width */
void field(Out &o, const char *pre, size_t pl, long zeros, const char *body, size_t bl, long width, bool left, bool zpad)
{
    long total = (long)(pl + bl) + (zeros > 0 ? zeros : 0), padn = width > total ? width - total : 0;
    if (!left && !zpad)
        o.fill(' ', padn);
    o.put(pre, pl);
    if (!left && zpad)
        o.fill('0', padn);
    o.fill('0', zeros);
    o.put(body, bl);
    if (left)
        o.fill(' ', padn);
}
int to_narrow(const u16 *w, size_t maxn, char *out, size_t cap)
{   /* wide string -> narrow in the current code page, '?' for what does not convert */
    size_t k = 0;
    for (; k < maxn && w[k] && k < cap; k++) {
        unsigned char b;
        out[k] = wc_to_byte(w[k], &b) ? (char)b : '?';
    }
    return (int)k;
}
void put_float(Out &o, double v, char conv, bool plus, bool space, bool alt, int prec, long width, bool left, bool zero)
{
    char sign[2] = {0, 0};
    char tmp[400], *buf = tmp, *heap = NULL;
    if (std::signbit(v) && !std::isnan(v))
        sign[0] = '-';
    else if (plus)
        sign[0] = '+';
    else if (space)
        sign[0] = ' ';
    size_t bl;
    if (!std::isfinite(v)) {
        const char *word = std::isinf(v) ? "1.#INF" : "1.#QNAN";
        uint64_t bits;
        memcpy(&bits, &v, 8);
        if (std::isnan(v)) {
            if (bits >> 63) { /* the indefinite: sign set, empty payload */
                sign[0] = '-';
                word = (bits & 0x7FFFFFFFFFFFFull) ? "1.#QNAN" : "1.#IND";
            } else if (!(bits & 0x8000000000000ull))
                word = "1.#SNAN";
            else if (plus)
                sign[0] = '+';
        }
        bl = (size_t)snprintf(tmp, sizeof tmp, "%s", word);
        int digits = prec < 0 ? 6 : prec;   /* %f / %e: the digits after the point, padded with zeros */
        if (conv != 'g' && conv != 'G') {
            size_t keep = 2 + (size_t)digits, have = bl;
            if (keep < have)
                bl = keep > 2 ? keep : 1;
            for (; have < keep && bl < sizeof tmp - 8; have++)
                tmp[bl++] = '0';
            if (conv == 'e' || conv == 'E')
                bl += (size_t)snprintf(tmp + bl, 8, "%c+000", conv);
        }
        zero = false;
    } else {
        /* MSVCRT.DLL rounds the exact decimal expansion half UP (away from zero) at the requested digit; the
         * reference implementation compares twice the dropped part with the unit (>=). The exact expansion comes
         * from the C library's %.800e (a double has at most 767 significant decimal digits; glibc and gdtoa print
         * them exactly), the rounding and the layout are done here. */
        char ex[832];
        snprintf(ex, sizeof ex, "%.800e", std::fabs(v));
        char dg[804];                       /* dg[0..800]: significant digits, value = 0.d0d1... * 10^(x10+1) */
        dg[0] = ex[0];
        memcpy(dg + 1, ex + 2, 800);
        int x10 = atoi(strchr(ex, 'e') + 1);
        bool is0 = v == 0;
        auto round_at = [&](char *d, int n, int &x) { /* keep n digits (n >= 0) of d, half up; may carry into x */
            if (n > 800) n = 800;
            bool up = n < 801 && d[n < 0 ? 0 : n] >= '5' && n >= 0;
            if (n < 0) up = false;
            for (int i = (n < 0 ? 0 : n); i < 801; i++) d[i] = '0';
            for (int i = n - 1; up && i >= 0; i--) {
                if (d[i] == '9') d[i] = '0';
                else { d[i]++; up = false; }
            }
            if (up) { memmove(d + 1, d, 800); d[0] = '1'; x++; }
        };
        int P = prec < 0 ? 6 : prec;
        char c = conv;
        bool strip = false;
        if (conv == 'g' || conv == 'G') {
            if (P == 0) P = 1;
            char t[804];
            memcpy(t, dg, 801);
            int x = x10;
            round_at(t, P, x);
            if (is0) x = 0;
            strip = !alt;
            if (x < -4 || x >= P) { c = conv == 'g' ? 'e' : 'E'; P = P - 1; }
            else { c = 'f'; P = P - 1 - x; }
        }
        size_t cap = (size_t)P + (x10 > 0 ? (size_t)x10 : 0) + 32;
        if (cap > sizeof tmp)
            buf = heap = (char *)malloc(cap);
        if (!buf) {
            o.len = (size_t)-1;
            return;
        }
        bl = 0;
        if (c == 'f') {
            int x = x10, keep = x10 + 1 + P;          /* digits kept: integer part plus P fraction digits */
            if (keep < 0) { for (int i = 0; i < 801; i++) dg[i] = '0'; x = -P - 1; keep = 0; }
            else round_at(dg, keep, x);
            if (x < 0) buf[bl++] = '0';
            for (int i = 0; i <= x; i++) buf[bl++] = i < 801 ? dg[i] : '0';
            if (P > 0 || alt) buf[bl++] = '.';
            for (int i = 1; i <= P; i++) { int k = x + i; buf[bl++] = k < 0 || k > 800 ? '0' : dg[k]; }
        } else {
            int x = x10;
            round_at(dg, P + 1, x);
            if (is0) x = 0;
            buf[bl++] = dg[0];
            if (P > 0 || alt) buf[bl++] = '.';
            for (int i = 1; i <= P; i++) buf[bl++] = i < 801 ? dg[i] : '0';
            if (strip) { while (bl && buf[bl - 1] == '0') bl--; if (bl && buf[bl - 1] == '.') bl--; }
            bl += (size_t)snprintf(buf + bl, 12, "%c%c%02d", c, x < 0 ? '-' : '+', x < 0 ? -x : x);
            strip = false;
        }
        if (strip && memchr(buf, '.', bl)) { while (bl && buf[bl - 1] == '0') bl--; if (bl && buf[bl - 1] == '.') bl--; }
        buf[bl] = 0;
        if (buf[0] == '-') /* the sign is a field prefix, written ahead of the zero padding */
            memmove(buf, buf + 1, bl--);
        if (conv != 'f') { /* MSVCRT prints at least three exponent digits */
            char *e = (char *)memchr(buf, conv == 'e' || conv == 'g' ? 'e' : 'E', bl);
            if (e && e + 1 < buf + bl && (e[1] == '+' || e[1] == '-')) {
                size_t nd = (size_t)(buf + bl - (e + 2));
                if (nd < 3) {
                    memmove(e + 2 + (3 - nd), e + 2, nd + 1);
                    for (size_t i = 0; i < 3 - nd; i++)
                        e[2 + i] = '0';
                    bl += 3 - nd;
                }
            }
        }
    }
    field(o, sign, sign[0] ? 1 : 0, 0, buf, bl, width, left, zero && !left);
    free(heap);
}
void put_int(Out &o, unsigned long long mag, bool neg, int base, bool upper, bool plus, bool space, bool alt, int prec,
             long width, bool left, bool zero)
{
    char dig[72], pre[3];
    size_t nd = 0, pl = 0;
    const char *D = upper ? "0123456789ABCDEF" : "0123456789abcdef";
    for (unsigned long long m = mag; m; m /= (unsigned)base)
        dig[nd++] = D[m % (unsigned)base];
    if (prec == 0 && !mag)
        nd = 0;
    if (neg)
        pre[pl++] = '-';
    else if (base == 10 && plus)
        pre[pl++] = '+';
    else if (base == 10 && space)
        pre[pl++] = ' ';
    if (alt && base == 16 && mag)
        pre[pl++] = '0', pre[pl++] = upper ? 'X' : 'x';
    long zeros = prec > (long)nd ? prec - (long)nd : 0;
    if (alt && base == 8 && zeros == 0 && (nd == 0 || dig[nd - 1] != '0'))
        zeros = 1;
    if (!nd && !zeros && prec < 0)
        dig[nd++] = '0';
    for (size_t i = 0; i < nd / 2; i++)
        std::swap(dig[i], dig[nd - 1 - i]);
    bool zp = zero && prec < 0 && !left;
    long total = (long)(pl + nd) + zeros;
    if (zp && width > total)
        zeros += width - total;
    field(o, pre, pl, zeros, dig, nd, width, left, false);
}
}   // namespace

extern "C" int fnvnx_msvc_format(char *buf, size_t n, const char *fmt, va_list ap)
{
    Out o = {buf, n, 0};
    for (const char *p = fmt; *p;) {
        if (*p != '%') {
            o.put(*p++);
            continue;
        }
        p++;
        bool left = false, plus = false, space = false, alt = false, zero = false;
        for (;; p++) {
            if (*p == '-') left = true;
            else if (*p == '+') plus = true;
            else if (*p == ' ') space = true;
            else if (*p == '#') alt = true;
            else if (*p == '0') zero = true;
            else break;
        }
        long width = 0;
        int prec = -1;
        if (*p == '*') {
            int w = va_arg(ap, int);
            if (w < 0)
                left = true, w = -w;
            width = w, p++;
        } else
            for (; *p >= '0' && *p <= '9'; p++)
                width = width * 10 + (*p - '0');
        if (*p == '.') {
            p++;
            prec = 0;
            if (*p == '*') {
                prec = va_arg(ap, int);
                p++;
            } else
                for (; *p >= '0' && *p <= '9'; p++)
                    prec = prec * 10 + (*p - '0');
        }
        enum { S_INT, S_SHORT, S_LONG, S_I64, S_WIDE, S_PTR } sz = S_INT;
        for (;; ) {
            if (*p == 'h') sz = S_SHORT, p++;
            else if (*p == 'l') { if (p[1] == 'l') sz = S_I64, p += 2; else sz = S_LONG, p++; }
            else if (*p == 'L') p++;
            else if (*p == 'w') sz = S_WIDE, p++;
            else if (p[0] == 'I' && p[1] == '6' && p[2] == '4') sz = S_I64, p += 3;
            else if (p[0] == 'I' && p[1] == '3' && p[2] == '2') sz = S_INT, p += 3;
            else if (*p == 'I') sz = S_PTR, p++;
            else break;
        }
        char c = *p;
        if (!c)
            break;
        p++;
        switch (c) {
        case 'd': case 'i': {
            long long v = sz == S_I64 ? va_arg(ap, long long) : sz == S_PTR ? (long long)va_arg(ap, intptr_t)
                        : sz == S_SHORT ? (short)va_arg(ap, int) : (int)va_arg(ap, int);
            put_int(o, v < 0 ? 0ull - (unsigned long long)v : (unsigned long long)v, v < 0, 10, false, plus, space, alt, prec, width, left, zero);
            break;
        }
        case 'u': case 'o': case 'x': case 'X': {
            unsigned long long v = sz == S_I64 ? va_arg(ap, unsigned long long) : sz == S_PTR ? (unsigned long long)va_arg(ap, uintptr_t)
                                 : sz == S_SHORT ? (unsigned short)va_arg(ap, unsigned) : (unsigned)va_arg(ap, unsigned);
            put_int(o, v, false, c == 'u' ? 10 : c == 'o' ? 8 : 16, c == 'X', false, false, alt, prec, width, left, zero);
            break;
        }
        case 'p': {
            unsigned v = (unsigned)(uintptr_t)va_arg(ap, void *);
            put_int(o, v, false, 16, true, false, false, false, 8, width, left, false);
            break;
        }
        case 'c': case 'C': {
            bool wide = c == 'c' ? (sz == S_LONG || sz == S_WIDE) : sz != S_SHORT;
            int v = va_arg(ap, int);
            char b = (char)v;
            if (wide) {
                u16 w = (u16)v;
                to_narrow(&w, 1, &b, 1);
                if (!w)
                    b = 0;
            }
            field(o, "", 0, 0, &b, 1, width, left, zero && !left);
            break;
        }
        case 's': case 'S': {
            bool wide = c == 's' ? (sz == S_LONG || sz == S_WIDE) : sz != S_SHORT;
            const void *a = va_arg(ap, const void *);
            char *nbuf = NULL;
            const char *s = "(null)";
            size_t l = 6;
            if (a && !wide) {
                s = (const char *)a;
                for (l = 0; s[l] && (prec < 0 || (int)l < prec); l++) {}
            } else if (a) {
                const u16 *w = (const u16 *)a;
                size_t wl = 0;
                for (; w[wl] && (prec < 0 || (int)wl < prec); wl++) {}
                nbuf = (char *)malloc(wl + 1);
                if (!nbuf) {
                    errno = ENOMEM;
                    return -1;
                }
                l = (size_t)to_narrow(w, wl, nbuf, wl);
                s = nbuf;
            } else if (prec >= 0 && prec < 6)
                l = (size_t)prec;
            field(o, "", 0, 0, s, l, width, left, zero && !left);
            free(nbuf);
            break;
        }
        case 'n': {
            void *a = va_arg(ap, void *);
            if (a) {
                if (sz == S_SHORT) *(short *)a = (short)o.len; else *(int *)a = (int)o.len;
            }
            break;
        }
        case 'e': case 'E': case 'f': case 'g': case 'G':
            put_float(o, va_arg(ap, double), c, plus, space, alt, prec, width, left, zero);
            break;
        case '%':
            o.put('%');
            break;
        default: /* z j t F a ...: not MSVCRT conversions: the letter is printed, no argument is used */
            o.put(c);
        }
        if (o.len == (size_t)-1 || o.len > 0x7FFFFFFFu)
            return -1;
    }
    return (int)o.len;
}

namespace {
int c_sprintf(char *buf, const char *fmt, ...)
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

/* ---------------------------------------------------------------- numbers */
bool is_sp(int c) { return c == ' ' || (c >= 9 && c <= 13); }
/* length of the longest prefix of s (at most maxw) that is a decimal float: sign, digits with an optional point, and
 * an exponent (e E d D) only when digits follow it; 0 when there is no digit in the mantissa */
size_t scan_float(const char *s, size_t maxw)
{
    size_t i = 0, digits = 0;
    if (i < maxw && (s[i] == '+' || s[i] == '-'))
        i++;
    for (; i < maxw && s[i] >= '0' && s[i] <= '9'; i++)
        digits++;
    if (i < maxw && s[i] == '.') {
        size_t j = i + 1, fd = 0;
        for (; j < maxw && s[j] >= '0' && s[j] <= '9'; j++)
            fd++;
        if (digits + fd)
            i = j, digits += fd;
    }
    if (!digits)
        return 0;
    if (i < maxw && (s[i] == 'e' || s[i] == 'E' || s[i] == 'd' || s[i] == 'D')) {
        size_t j = i + 1;
        if (j < maxw && (s[j] == '+' || s[j] == '-'))
            j++;
        size_t k = j;
        for (; k < maxw && s[k] >= '0' && s[k] <= '9'; k++) {}
        if (k > j)
            i = k;
    }
    return i;
}
double c_strtod(const char *s, char **end)
{
    const char *p = s;
    while (is_sp((unsigned char)*p))
        p++;
    size_t l = scan_float(p, (size_t)-1);
    if (!l) {
        if (end)
            *end = (char *)s;
        return 0.0;
    }
    char small[128], *t = l < sizeof small ? small : (char *)malloc(l + 1);
    if (!t) { /* no memory for the copy: the token cannot be converted */
        errno = ENOMEM;
        if (end)
            *end = (char *)s;
        return 0.0;
    }
    bool nonzero = false;
    bool in_mant = true;
    for (size_t i = 0; i < l; i++) {
        char c = p[i];
        if (c == 'd' || c == 'D')
            c = 'e';
        if (c == 'e')
            in_mant = false;
        if (in_mant && c >= '1' && c <= '9')
            nonzero = true;
        t[i] = c;
    }
    t[l] = 0;
    int saved = errno;
    double r = strtod(t, NULL);
    errno = saved;
    if (std::isinf(r) || (r == 0.0 && nonzero))
        errno = ERANGE;
    if (t != small)
        free(t);
    if (end)
        *end = (char *)p + l;
    return r;
}
double c_atof(const char *s) { return c_strtod(s, NULL); }
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
int c_atoi(const char *s) { return atox(s); }
long c_atol(const char *s) { return (long)atox(s); }

/* ---------------------------------------------------------------- sscanf */
int c_vsscanf(const char *in, const char *fmt, va_list ap)
{
    if (!in || !fmt) {
        errno = EINVAL;
        return EOF;
    }
    const char *s = in;
    int n = 0;
    bool input_fail = false;
    for (const char *p = fmt; *p && !input_fail;) {
        if (is_sp((unsigned char)*p)) {
            while (is_sp((unsigned char)*s))
                s++;
            p++;
            continue;
        }
        if (*p != '%') {
            if (!*s) { input_fail = true; break; }
            if (*s != *p) break;
            s++, p++;
            continue;
        }
        p++;
        if (*p == '%') {
            while (is_sp((unsigned char)*s))
                s++;
            if (*s != '%') { if (!*s) input_fail = true; break; }
            s++, p++;
            continue;
        }
        bool suppress = *p == '*';
        p += suppress;
        size_t width = 0;
        for (; *p >= '0' && *p <= '9'; p++)
            width = width * 10 + (size_t)(*p - '0');
        int sz = 0; /* 1 h, 2 l/w (32-bit ints, double, wide chars), 3 64-bit */
        for (;;) {
            if (*p == 'h') sz = 1, p++;
            else if (*p == 'l') { if (p[1] == 'l') sz = 3, p += 2; else sz = 2, p++; }
            else if (*p == 'L') sz = 2, p++;
            else if (*p == 'w') sz = 2, p++;
            else if (p[0] == 'I' && p[1] == '6' && p[2] == '4') sz = 3, p += 3;
            else if (p[0] == 'I' && p[1] == '3' && p[2] == '2') sz = 0, p += 3;
            else break;
        }
        char c = *p++;
        size_t w = width ? width : (size_t)-1;
        if (c == 'n') {
            if (!suppress) {
                void *a = va_arg(ap, void *);
                if (sz == 1) *(short *)a = (short)(s - in); else *(int *)a = (int)(s - in);
            }
            continue;
        }
        if (c != 'c' && c != '[')
            while (is_sp((unsigned char)*s))
                s++;
        if (!*s) {
            if (!c)
                break;
            input_fail = true;
            break;
        }
        if (c == 'd' || c == 'i' || c == 'u' || c == 'o' || c == 'x' || c == 'X' || c == 'p') {
            size_t i = 0;
            bool neg = false;
            if (i < w && (s[i] == '+' || s[i] == '-'))
                neg = s[i++] == '-';
            int base = c == 'o' ? 8 : (c == 'x' || c == 'X' || c == 'p') ? 16 : c == 'i' ? 0 : 10;
            if ((base == 0 || base == 16) && i + 1 < w && s[i] == '0' && (s[i + 1] == 'x' || s[i + 1] == 'X')
                && isxdigit((unsigned char)s[i + 2]))
                i += 2, base = 16;
            else if (base == 0)
                base = s[i] == '0' ? 8 : 10;
            unsigned long long v = 0;
            size_t nd = 0;
            for (; i < w; i++, nd++) {
                int d = s[i] >= '0' && s[i] <= '9' ? s[i] - '0' : s[i] >= 'a' && s[i] <= 'f' ? s[i] - 'a' + 10
                      : s[i] >= 'A' && s[i] <= 'F' ? s[i] - 'A' + 10 : 99;
                if (d >= base)
                    break;
                v = v * (unsigned)base + (unsigned)d;
            }
            if (!nd)
                break;
            s += i;
            if (neg)
                v = 0ull - v;
            if (!suppress) {
                void *a = va_arg(ap, void *);
                if (c == 'p') *(uintptr_t *)a = (uintptr_t)v;
                else if (sz == 3) *(long long *)a = (long long)v;
                else if (sz == 1) *(short *)a = (short)v;
                else *(int *)a = (int)v;
                n++;
            }
        } else if (c == 'e' || c == 'f' || c == 'g' || c == 'E' || c == 'G') {
            size_t l = scan_float(s, w);
            if (!l)
                break;
            char tmp[400];
            size_t cl = l < sizeof tmp - 1 ? l : sizeof tmp - 1;
            memcpy(tmp, s, cl);
            tmp[cl] = 0;
            double v = c_strtod(tmp, NULL);
            s += l;
            if (!suppress) {
                void *a = va_arg(ap, void *);
                if (sz == 2) *(double *)a = v; else *(float *)a = (float)v;
                n++;
            }
        } else if (c == 's' || c == 'c' || c == '[') {
            unsigned char set[256];
            memset(set, 0, sizeof set);
            if (c == '[') {
                bool inv = *p == '^';
                p += inv;
                const unsigned char *q = (const unsigned char *)p;
                for (bool first = true; *q && (*q != ']' || first); first = false, q++) {
                    if (q[1] == '-' && q[2] && q[2] != ']') {
                        for (int ch = q[0]; ch <= q[2]; ch++)
                            set[ch] = 1;
                        q += 2;
                    } else
                        set[*q] = 1;
                }
                p = (const char *)q + (*q == ']');
                if (inv)
                    for (int i = 0; i < 256; i++)
                        set[i] ^= 1;
                set[0] = 0;
            } else if (c == 's')
                for (int i = 1; i < 256; i++)
                    set[i] = !is_sp(i);
            size_t cnt = c == 'c' ? (width ? width : 1) : w, i = 0;
            if (c == 'c') {
                for (; i < cnt && s[i]; i++) {}
                if (i < cnt)
                    break;
            } else {
                for (; i < cnt && s[i] && set[(unsigned char)s[i]]; i++) {}
                if (!i)
                    break;
            }
            if (!suppress) {
                void *a = va_arg(ap, void *);
                if (sz == 2) {
                    u16 *d = (u16 *)a;
                    for (size_t k = 0; k < i; k++)
                        d[k] = (unsigned char)s[k];
                    if (c != 'c') d[i] = 0;
                } else {
                    memcpy(a, s, i);
                    if (c != 'c') ((char *)a)[i] = 0;
                }
                n++;
            }
            s += i;
        } else
            break;
    }
    return n == 0 && input_fail ? EOF : n;
}
int c_sscanf(const char *in, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int r = c_vsscanf(in, fmt, ap);
    va_end(ap);
    return r;
}

/* ---------------------------------------------------------------- strings, sort, misc */
char *w_strchr(const char *s, int c) { return (char *)strchr(s, c); }
char *w_strrchr(const char *s, int c) { return (char *)strrchr(s, c); }

typedef int (*cmp_t)(const void *, const void *);
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
void c_qsort(void *base, size_t n, size_t w, cmp_t cmp)
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

char *c_strdate(char *b)
{
    SYSTEMTIME t;
    GetLocalTime(&t);
    char o[40];
    snprintf(o, sizeof o, "%02u/%02u/%02u", (unsigned)t.wMonth % 100, (unsigned)t.wDay % 100, (unsigned)(t.wYear % 100));
    memcpy(b, o, 9);
    return b;
}
char *c_strtime(char *b)
{
    SYSTEMTIME t;
    GetLocalTime(&t);
    char o[40];
    snprintf(o, sizeof o, "%02u:%02u:%02u", (unsigned)t.wHour % 100, (unsigned)t.wMinute % 100, (unsigned)t.wSecond % 100);
    memcpy(b, o, 9);
    return b;
}

char *c_tempnam(const char *dir, const char *prefix)
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
typedef void (*pvfv)(void);
typedef int (*onexit_fn)(void);
void c_initterm(pvfv *first, pvfv *last)
{
    for (; first < last; first++)
        if (*first)
            (*first)();
}

os_lock_t g_ox_lock = OS_LOCK_INIT;
onexit_fn *g_ox;
size_t g_ox_n, g_ox_cap;
onexit_fn c_onexit(onexit_fn f)
{
    if (!f)
        return NULL;
    os_lock(&g_ox_lock);
    if (g_ox_n == g_ox_cap) {
        size_t cap = g_ox_cap ? g_ox_cap * 2 : 32;
        onexit_fn *t = (onexit_fn *)realloc(g_ox, cap * sizeof *t);
        if (!t) {
            os_unlock(&g_ox_lock);
            return NULL;
        }
        g_ox = t, g_ox_cap = cap;
    }
    g_ox[g_ox_n++] = f;
    os_unlock(&g_ox_lock);
    return f;
}
/* __dllonexit: the DLL's own table [*begin, *end), grown by one on the msvcrt heap; both pointers updated */
onexit_fn c_dllonexit(onexit_fn f, onexit_fn **begin, onexit_fn **end)
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
void c_exit(int code)
{
    for (;;) { /* LIFO; handlers registered while running are run next */
        os_lock(&g_ox_lock);
        onexit_fn f = g_ox_n ? g_ox[--g_ox_n] : NULL;
        os_unlock(&g_ox_lock);
        if (!f)
            break;
        f();
    }
    fnvnx_iob_fflush(NULL);
    ExitProcess((UINT)code);
    os_exit_process(code);
}
void c_abort(void)
{
    static const char m[] = "\nabnormal program termination\n";
    fnvnx_write(2, m, sizeof m - 1);
    ExitProcess(3);
    os_exit_process(3);
}
void c_amsg_exit(int n)
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
void c_purecall(void) { c_amsg_exit(25); }

struct rlock { os_lock_t m; void *volatile owner; int count; };
rlock g_locks[64];
void c_lock(int n)
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
    os_lock(&l.m);
    l.owner = me, l.count = 1;
}
void c_unlock(int n)
{
    if (n < 0 || n >= 64)
        return;
    rlock &l = g_locks[n];
    if (l.owner != (void *)&errno || --l.count)
        return;
    l.owner = NULL;
    os_unlock(&l.m);
}

struct envc { char *key, *val; envc *next; };
os_lock_t g_env_lock = OS_LOCK_INIT;
envc *g_envc;
char *c_getenv(const char *name)
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
double c_ldexp(double x, int e)
{
    double r = scalbn(x, e); /* one rounding, to nearest even, subnormals included */
    if (std::isfinite(x) && x != 0.0 && (std::isinf(r) || r == 0.0))
        errno = ERANGE;
    return r;
}
double c_frexp(double x, int *e)
{
    if (!std::isfinite(x)) {
        *e = 0;
        return x;
    }
    return frexp(x, e);
}
double c_ceil(double x) { return ceil(x); }
double c_floor(double x) { return floor(x); }
double c_modf(double x, double *ip) { return modf(x, ip); }

/* ---------------------------------------------------------------- the table */
struct row { const char *dll, *name; void *fn; };
#define R(n, f) { "msvcrt.dll", n, (void *)(f) }
const row g_rows[] = {
    R("malloc", c_malloc), R("calloc", c_calloc), R("realloc", c_realloc), R("free", c_free),
    R("??2@YAPAXI@Z", c_malloc), R("??3@YAXPAX@Z", c_free), R("_strdup", _strdup),
    R("_iob", fnvnx_iob), R("__pioinfo", __pioinfo), R("__badioinfo", &__badioinfo),
    R("fread", fnvnx_iob_fread), R("fwrite", fnvnx_iob_fwrite), R("fflush", fnvnx_iob_fflush),
    R("_write", fnvnx_write), R("_lseeki64", fnvnx_lseeki64), R("_fileno", fnvnx_iob_fileno),
    R("_isatty", fnvnx_isatty), R("_tempnam", c_tempnam),
    R("memcpy", memcpy), R("memmove", memmove), R("memset", memset), R("strchr", w_strchr), R("strrchr", w_strrchr),
    R("strncmp", strncmp), R("strncpy", strncpy), R("_stricmp", _stricmp), R("_strnicmp", _strnicmp),
    R("wcslen", fnvnx_wcslen16), R("wcstombs", c_wcstombs), R("wctomb", c_wctomb),
    R("isalnum", c_isalnum), R("isalpha", c_isalpha), R("isdigit", c_isdigit), R("isspace", c_isspace),
    R("isxdigit", c_isxdigit), R("isleadbyte", c_isleadbyte), R("iswalpha", c_iswalpha), R("iswdigit", c_iswdigit),
    R("iswpunct", c_iswpunct), R("iswspace", c_iswspace), R("tolower", c_tolower), R("toupper", c_toupper),
    R("setlocale", c_setlocale),
    R("sprintf", c_sprintf), R("_snprintf", _snprintf), R("_vsnprintf", _vsnprintf), R("sscanf", c_sscanf),
    R("atof", c_atof), R("atoi", c_atoi), R("atol", c_atol), R("strtod", c_strtod), R("_itoa", _itoa), R("_ultoa", _ultoa),
    R("qsort", c_qsort), R("rand", fnvnx_rand), R("srand", fnvnx_srand), R("_strdate", c_strdate), R("_strtime", c_strtime),
    R("_initterm", c_initterm), R("__dllonexit", c_dllonexit), R("_onexit", c_onexit), R("exit", c_exit),
    R("abort", c_abort), R("_amsg_exit", c_amsg_exit), R("_purecall", c_purecall),
    R("_lock", c_lock), R("_unlock", c_unlock), R("_errno", _errno), R("getenv", c_getenv),
    R("ceil", c_ceil), R("floor", c_floor), R("modf", c_modf), R("frexp", c_frexp), R("ldexp", c_ldexp),
};
#undef R
}   // namespace

/* the hook fnvnx_win32_lookup_export calls for the dll "msvcrt": NULL when the name is not provided here */
extern "C" void *fnvnx_msvcrt_lookup(const char *name)
{
    for (const row &r : g_rows)
        if (!strcmp(r.name, name))
            return r.fn;
    return NULL;
}
