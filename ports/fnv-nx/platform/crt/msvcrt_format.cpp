/* msvcrt_format.cpp - the ONE printf formatter and the ONE scanf scanner of the platform, with the semantics of
 * MSVCRT.DLL (the Windows system C runtime, not C99). Both are templates over an argument SOURCE: a reader of the
 * next int / 64-bit int / double / pointer / pointer-sized int.
 *   - VaSrc reads a native va_list: the variadic native entry points (c_sprintf, _snprintf, c_sscanf, the iob
 *     printf family) use it.
 *   - WordSrc reads the 32-bit x86 stack words of a translated caller: int and pointers are one word, long long
 *     and double (a float is promoted to double; long double is double under MSVC) are two words, low first,
 *     in memory order with no alignment padding. A guest pointer is the zero-extended word (the guest address
 *     space is the process address space, ILP32 on the target). A "va_list" made by x86 code is such a word
 *     pointer, so the v* functions take it directly. The fnvnx_msvcrt_va_* entry points are what the import
 *     bridge calls.
 * Rules (each closes its class):
 *   - float digits come from the EXACT decimal expansion of the double (big-integer arithmetic below), rounded
 *     half up (away from zero) at the requested digit; no libc printf is involved, so the result is the same on
 *     every host;
 *   - decimal -> binary (scanf %f, strtod) is exact big-integer division rounded to nearest even at the target
 *     precision, subnormals and overflow included; digits beyond 801 significant ones only matter as a nonzero
 *     tail (a tie of a double has at most 770 significant digits), so the working size is bounded;
 *   - wide <-> narrow conversions go through the layer's NLS code page (msvcrt_exports.cpp). */
#include <errno.h>
#include <cmath>
#include <ctype.h>
#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fnvnx_msvcrt.h"

namespace {
typedef unsigned short u16;

/* ------------------------------------------------------------------ big integers (little-endian 32-bit limbs) */
const int BIG_W = 160; /* 5120 bits: the widest value is 10^1127 << 60 */
struct Big {
    uint32_t w[BIG_W];
    int n; /* limbs in use, no leading zero limb */
    void set(uint64_t v) { n = 0; for (; v; v >>= 32) w[n++] = (uint32_t)v; }
    void trim() { while (n && !w[n - 1]) n--; }
    void mul(uint32_t m)
    {
        uint64_t c = 0;
        for (int i = 0; i < n; i++) { c += (uint64_t)w[i] * m; w[i] = (uint32_t)c; c >>= 32; }
        if (c) w[n++] = (uint32_t)c;
    }
    void add(uint32_t a)
    {
        uint64_t c = a;
        for (int i = 0; c && i < n; i++) { c += w[i]; w[i] = (uint32_t)c; c >>= 32; }
        if (c) w[n++] = (uint32_t)c;
    }
    int bits() const
    {
        if (!n) return 0;
        int b = 32 * (n - 1);
        for (uint32_t t = w[n - 1]; t; t >>= 1) b++;
        return b;
    }
    void shl(int k)
    {
        if (!n || !k) return;
        int ws = k / 32, bs = k % 32;
        if (bs) {
            uint32_t c = 0;
            for (int i = 0; i < n; i++) { uint32_t t = w[i]; w[i] = (t << bs) | c; c = t >> (32 - bs); }
            if (c) w[n++] = c;
        }
        if (ws) {
            for (int i = n - 1; i >= 0; i--) w[i + ws] = w[i];
            for (int i = 0; i < ws; i++) w[i] = 0;
            n += ws;
        }
    }
    void shr1()
    {
        uint32_t c = 0;
        for (int i = n - 1; i >= 0; i--) { uint32_t t = w[i]; w[i] = (t >> 1) | c; c = t << 31; }
        trim();
    }
    uint32_t divmod(uint32_t d) /* this /= d; returns the remainder */
    {
        uint64_t r = 0;
        for (int i = n - 1; i >= 0; i--) { r = (r << 32) | w[i]; w[i] = (uint32_t)(r / d); r %= d; }
        trim();
        return (uint32_t)r;
    }
    bool bit(int k) const { return k / 32 < n && (w[k / 32] >> (k % 32)) & 1; }
};
int big_cmp(const Big &a, const Big &b)
{
    if (a.n != b.n) return a.n < b.n ? -1 : 1;
    for (int i = a.n - 1; i >= 0; i--)
        if (a.w[i] != b.w[i]) return a.w[i] < b.w[i] ? -1 : 1;
    return 0;
}
void big_sub(Big &a, const Big &b) /* a -= b, a >= b */
{
    int64_t c = 0;
    for (int i = 0; i < a.n; i++) {
        c += (int64_t)a.w[i] - (i < b.n ? b.w[i] : 0);
        a.w[i] = (uint32_t)c;
        c >>= 32;
    }
    a.trim();
}
void big_mul(Big &r, const Big &a, const Big &b) /* r = a * b (r distinct from a and b) */
{
    r.n = a.n + b.n;
    for (int i = 0; i < r.n; i++) r.w[i] = 0;
    for (int i = 0; i < a.n; i++) {
        uint64_t c = 0;
        for (int j = 0; j < b.n; j++) {
            c += (uint64_t)a.w[i] * b.w[j] + r.w[i + j];
            r.w[i + j] = (uint32_t)c;
            c >>= 32;
        }
        r.w[i + b.n] += (uint32_t)c;
    }
    r.trim();
}
void big_pow(Big &r, uint32_t base, int e) /* r = base^e */
{
    r.set(1);
    for (; e >= 13 && base == 5; e -= 13) r.mul(1220703125u);
    for (; e >= 9 && base == 10; e -= 9) r.mul(1000000000u);
    for (; e > 0; e--) r.mul(base);
}

/* the exact decimal expansion of a finite a > 0: dg[0..800] are the significant digits (zero padded), the value is
 * 0.d0d1d2... * 10^(x10+1) */
void exact_digits(double a, char *dg, int *x10)
{
    uint64_t bits;
    memcpy(&bits, &a, 8);
    int be = (int)((bits >> 52) & 0x7FF);
    uint64_t m = bits & 0xFFFFFFFFFFFFFull;
    int e2;
    if (be) { m |= 1ull << 52; e2 = be - 1075; } else e2 = -1074;
    Big N;
    N.set(m);
    int e10 = 0;
    if (e2 >= 0)
        N.shl(e2);
    else {
        Big f;
        big_pow(f, 5, -e2);
        Big r;
        big_mul(r, N, f);
        N = r;
        e10 = e2;
    }
    char tmp[800];
    int len = 0;
    while (N.n) { /* little-endian chunks of 9 digits */
        uint32_t ch = N.divmod(1000000000u);
        for (int k = 0; k < 9; k++) { tmp[len++] = (char)('0' + ch % 10); ch /= 10; }
    }
    while (len > 1 && tmp[len - 1] == '0') len--; /* leading zeros of the top chunk */
    for (int i = 0; i < 801; i++) dg[i] = i < len ? tmp[len - 1 - i] : '0';
    *x10 = len - 1 + e10;
}

/* ------------------------------------------------------------------ decimal -> binary, exact */
/* digits d[0..nd) (no leading zero, nd >= 1) times 10^k, rounded to nearest even to a binary format of p
 * significant bits whose least significant bit weighs at least 2^lsb_min (53/-1074 double, 24/-149 float);
 * tail: a nonzero digit follows (folded in by the caller as a last digit 1). Returns the value; overflow is inf. */
double decimal_to_binary(const char *d, int nd, long long k, int p, int lsb_min)
{
    long long e10 = nd + k;
    if (e10 > 309) return HUGE_VAL;
    if (e10 < -323) return 0.0;
    Big D, T;
    D.set(0);
    for (int i = 0; i < nd; i++) { D.mul(10); D.add((uint32_t)(d[i] - '0')); }
    uint64_t q;
    bool sticky = false;
    int e2 = 0;
    if (k >= 0) {
        big_pow(T, 10, (int)k);
        Big R;
        big_mul(R, D, T);
        int bl = R.bits();
        if (bl <= 64) {
            q = 0;
            for (int i = R.n - 1; i >= 0; i--) q = (q << 32) | R.w[i];
        } else {
            int sh = bl - 64;
            for (int i = 0; i < sh && !sticky; i++) sticky = R.bit(i);
            q = 0;
            for (int i = 63; i >= 0; i--) q = (q << 1) | (R.bit(sh + i) ? 1u : 0u);
            e2 = sh;
        }
    } else {
        big_pow(T, 10, (int)-k); /* denominator */
        int sh = T.bits() + 58 - D.bits(); /* scale so the quotient has 58..59 bits */
        if (sh >= 0) D.shl(sh); else T.shl(-sh);
        int t = D.bits() - T.bits();
        q = 0;
        T.shl(t);
        for (int j = t; j >= 0; j--) {
            if (big_cmp(D, T) >= 0) { big_sub(D, T); q |= 1ull << j; }
            T.shr1();
        }
        sticky = D.n != 0;
        e2 = -sh;
    }
    int bl = 0;
    for (uint64_t t = q; t; t >>= 1) bl++;
    if (!bl) return 0.0;
    int lsb = bl - 1 + e2 - (p - 1);
    if (lsb < lsb_min) lsb = lsb_min;
    int shift = lsb - e2;
    uint64_t kept;
    if (shift <= 0)
        kept = q;
    else if (shift >= 65)
        kept = 0;
    else {
        uint64_t rem, half;
        if (shift == 64) { kept = 0; rem = q; half = 1ull << 63; }
        else { kept = q >> shift; rem = q & ((1ull << shift) - 1); half = 1ull << (shift - 1); }
        if (rem > half || (rem == half && (sticky || (kept & 1))))
            kept++;
    }
    double r = ldexp((double)kept, shift <= 0 ? e2 : lsb);
    if (p == 24 && r > 3.4028234663852886e38) r = HUGE_VAL;
    return r;
}

/* ------------------------------------------------------------------ number grammar of scanf / strtod */
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
/* value of a token that scan_float accepted (l characters); *erange: overflow, or a nonzero mantissa that became 0 */
double token_value(const char *t, size_t l, int p, int lsb_min, bool *erange)
{
    size_t i = 0;
    bool neg = false;
    if (t[i] == '+' || t[i] == '-')
        neg = t[i++] == '-';
    char dig[804];
    int kept = 0;
    long long scale = 0;
    bool after = false, tail = false, nonzero = false;
    for (; i < l && (t[i] == '.' || (t[i] >= '0' && t[i] <= '9')); i++) {
        char c = t[i];
        if (c == '.') { after = true; continue; }
        if (c != '0') nonzero = true;
        if (!kept && c == '0') { if (after) scale--; continue; }
        if (kept < 801) { dig[kept++] = c; if (after) scale--; }
        else { if (c != '0') tail = true; if (!after) scale++; }
    }
    long long ex = 0;
    if (i < l) { /* e E d D */
        i++;
        bool en = false;
        if (t[i] == '+' || t[i] == '-')
            en = t[i++] == '-';
        for (; i < l; i++) {
            if (ex < 100000) ex = ex * 10 + (t[i] - '0');
        }
        if (en) ex = -ex;
    }
    double r = 0.0;
    if (kept) {
        if (tail) { dig[kept++] = '1'; scale--; }
        while (kept > 1 && dig[kept - 1] == '0') { kept--; scale++; }
        r = decimal_to_binary(dig, kept, scale + ex, p, lsb_min);
    }
    *erange = std::isinf(r) || (r == 0.0 && nonzero);
    return neg ? -r : r;
}

/* ------------------------------------------------------------------ argument sources */
struct VaSrc {
    va_list *ap;
    static const size_t kPtr = sizeof(void *);
    int i() { return va_arg(*ap, int); }
    long long q() { return va_arg(*ap, long long); }
    double d() { return va_arg(*ap, double); }
    void *p() { return va_arg(*ap, void *); }
    long long ip() { return (long long)va_arg(*ap, intptr_t); }
    unsigned long long uip() { return (unsigned long long)va_arg(*ap, uintptr_t); }
};
struct WordSrc {
    const uint32_t *w;
    static const size_t kPtr = 4;
    int i() { return (int)*w++; }
    long long q() { uint64_t lo = *w++, hi = *w++; return (long long)(lo | hi << 32); }
    double d() { long long b = q(); double r; memcpy(&r, &b, 8); return r; }
    void *p() { return (void *)(uintptr_t)*w++; }
    long long ip() { return (int)*w++; }
    unsigned long long uip() { return *w++; }
};

/* ------------------------------------------------------------------ output */
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

/* half up at digit n (keep n digits of d[0..800], n >= 0); a carry out of the first digit raises x */
void round_at(char *d, int n, int &x)
{
    if (n > 800) n = 800;
    bool up = d[n] >= '5';
    for (int i = n; i < 801; i++) d[i] = '0';
    for (int i = n - 1; up && i >= 0; i--) {
        if (d[i] == '9') d[i] = '0';
        else { d[i]++; up = false; }
    }
    if (up) { memmove(d + 1, d, 800); d[0] = '1'; x++; }
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
        /* the text "1.#INF" / "1.#IND" / "1.#QNAN" / "1.#SNAN" is treated as the digit string "1" "#INF": the
         * digits after the point are cut at the precision with the same half-up rule as numbers (the next
         * character >= '5' raises the last kept one by one), or padded with zeros (Wine printf.c: "%010.2lf" of
         * the indefinite is "-000001.#J") */
        const char *word = std::isinf(v) ? "#INF" : "#QNAN";
        uint64_t bits;
        memcpy(&bits, &v, 8);
        if (std::isnan(v)) {
            if (bits >> 63) { /* the indefinite: sign set, empty payload */
                sign[0] = '-';
                word = (bits & 0x7FFFFFFFFFFFFull) ? "#QNAN" : "#IND";
            } else if (!(bits & 0x8000000000000ull))
                word = "#SNAN";
            else if (plus)
                sign[0] = '+';
        }
        size_t wl = strlen(word);
        bl = 0;
        tmp[bl++] = '1';
        if (conv == 'g' || conv == 'G') {
            tmp[bl++] = '.';
            memcpy(tmp + bl, word, wl);
            bl += wl;
        } else {
            size_t P = prec < 0 ? 6 : (size_t)prec;
            if (P > 300) P = 300;
            if (P > 0 || alt)
                tmp[bl++] = '.';
            size_t at = bl;
            for (size_t i = 0; i < P; i++)
                tmp[bl++] = i < wl ? word[i] : '0';
            if (P < wl && word[P] >= '5' && P > 0)
                tmp[at + P - 1]++;
            if (conv == 'e' || conv == 'E')
                bl += (size_t)snprintf(tmp + bl, 8, "%c+000", conv);
        }
    } else {
        /* the exact expansion, rounded half up at the requested digit (the dropped part is compared with half a
         * unit: >=) */
        char dg[804]; /* dg[0..800]: significant digits, value = 0.d0d1... * 10^(x10+1) */
        int x10 = 0;
        bool is0 = v == 0;
        if (is0)
            memset(dg, '0', 801);
        else
            exact_digits(fabs(v), dg, &x10);
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
            /* MSVCRT prints at least three exponent digits */
            bl += (size_t)snprintf(buf + bl, 12, "%c%c%03d", c, x < 0 ? '-' : '+', x < 0 ? -x : x);
            strip = false;
        }
        if (strip && memchr(buf, '.', bl)) { while (bl && buf[bl - 1] == '0') bl--; if (bl && buf[bl - 1] == '.') bl--; }
        buf[bl] = 0;
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
    for (size_t i = 0; i < nd / 2; i++) {
        char t = dig[i];
        dig[i] = dig[nd - 1 - i];
        dig[nd - 1 - i] = t;
    }
    bool zp = zero && prec < 0 && !left;
    long total = (long)(pl + nd) + zeros;
    if (zp && width > total)
        zeros += width - total;
    field(o, pre, pl, zeros, dig, nd, width, left, false);
}

bool is_intconv(char c) { return c == 'd' || c == 'i' || c == 'o' || c == 'u' || c == 'x' || c == 'X'; }

/* ------------------------------------------------------------------ the formatter */
template <class A> int format_impl(char *buf, size_t n, const char *fmt, A &a)
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
            int w = a.i();
            if (w < 0)
                left = true, w = -w;
            width = w, p++;
        }
        /* digits follow a '*' too: the width is then 10 * (the argument) + digit (Wine printf.c "%*1d") */
        for (; *p >= '0' && *p <= '9'; p++)
            width = width * 10 + (*p - '0');
        if (*p == '.') {
            p++;
            prec = 0;
            if (*p == '*') {
                prec = a.i();
                p++;
            } else
                for (; *p >= '0' && *p <= '9'; p++)
                    prec = prec * 10 + (*p - '0');
        }
        bool h = false, l = false, w = false, i64 = false, ip = false;
        bool lit_i = false;
        for (;;) {
            if (*p == 'h') h = true, p++;
            else if (*p == 'l') { if (p[1] == 'l') i64 = true, p += 2; else l = true, p++; }
            else if (*p == 'L' || *p == 'F' || *p == 'N') p++;
            else if (*p == 'w') w = true, p++;
            else if (p[0] == 'I' && p[1] == '6' && p[2] == '4') i64 = true, p += 3;
            else if (p[0] == 'I' && p[1] == '3' && p[2] == '2') p += 3;
            else if (*p == 'I' && is_intconv(p[1])) ip = true, p++;
            else { lit_i = *p == 'I'; break; }
        }
        if (lit_i) { /* %I not followed by 64 / 32 / an integer conversion: the I is text */
            o.put('I');
            p++;
            continue;
        }
        char c = *p;
        if (!c)
            break;
        p++;
        switch (c) {
        case 'd': case 'i': {
            long long v = i64 ? a.q() : ip ? a.ip() : h ? (long long)(short)a.i() : (long long)a.i();
            put_int(o, v < 0 ? 0ull - (unsigned long long)v : (unsigned long long)v, v < 0, 10, false, plus, space, alt, prec, width, left, zero);
            break;
        }
        case 'u': case 'o': case 'x': case 'X': {
            unsigned long long v = i64 ? (unsigned long long)a.q() : ip ? a.uip()
                                 : h ? (unsigned long long)(unsigned short)a.i() : (unsigned long long)(unsigned)a.i();
            put_int(o, v, false, c == 'u' ? 10 : c == 'o' ? 8 : 16, c == 'X', false, false, alt, prec, width, left, zero);
            break;
        }
        case 'p': {
            unsigned v = (unsigned)(uintptr_t)a.p();
            put_int(o, v, false, 16, true, false, false, alt, 8, width, left, false);
            break;
        }
        case 'c': case 'C': {
            bool wide = c == 'c' ? (l || w) : !(h && !l && !w);
            int v = a.i();
            char b = (char)v;
            if (wide) {
                unsigned char nb;
                if (!fnvnx_msvcrt_wc_to_byte((u16)v, &nb))
                    break; /* a wide character with no code-page form: nothing is written */
                b = (char)nb;
                if (!(u16)v)
                    b = 0;
            }
            field(o, "", 0, 0, &b, 1, width, left, zero && !left);
            break;
        }
        case 's': case 'S': {
            bool wide = c == 's' ? (l || w) : !(h && !l && !w);
            const void *arg = a.p();
            char *nbuf = NULL;
            const char *s = "(null)";
            size_t sl = 6;
            if (arg && !wide) {
                s = (const char *)arg;
                for (sl = 0; s[sl] && (prec < 0 || (int)sl < prec); sl++) {}
            } else if (arg) {
                const u16 *ws = (const u16 *)arg;
                size_t wl = 0;
                for (; ws[wl] && (prec < 0 || (int)wl < prec); wl++) {}
                nbuf = (char *)malloc(wl + 1);
                if (!nbuf) {
                    errno = ENOMEM;
                    return -1;
                }
                for (size_t k = 0; k < wl; k++) {
                    unsigned char nb;
                    if (!fnvnx_msvcrt_wc_to_byte(ws[k], &nb)) { /* no code-page form: the call fails */
                        free(nbuf);
                        errno = 42; /* MSVCRT EILSEQ */
                        return -1;
                    }
                    nbuf[k] = (char)nb;
                }
                s = nbuf;
                sl = wl;
            } else if (prec >= 0 && prec < 6)
                sl = (size_t)prec;
            field(o, "", 0, 0, s, sl, width, left, zero && !left);
            free(nbuf);
            break;
        }
        case 'n': {
            void *dst = a.p();
            if (dst) {
                if (h) { short t = (short)o.len; memcpy(dst, &t, sizeof t); }
                else { int t = (int)o.len; memcpy(dst, &t, sizeof t); }
            }
            break;
        }
        case 'e': case 'E': case 'f': case 'g': case 'G':
            put_float(o, a.d(), c, plus, space, alt, prec, width, left, zero);
            break;
        case '%':
            o.put('%');
            break;
        default: /* a letter that is no conversion (a A k m q ...): printed, no argument is used */
            o.put(c);
        }
        if (o.len == (size_t)-1 || o.len > 0x7FFFFFFFu)
            return -1;
    }
    return (int)o.len;
}

/* ------------------------------------------------------------------ the scanner */
void put_bytes(void *dst, const void *src, size_t n) { memcpy(dst, src, n); }

template <class A> int scan_impl(const char *in, const char *fmt, A &a)
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
        /* '*', width digits and the length letters may come in any order; digits accumulate into one width
         * (Wine scanf.c: "%2h0d" reads up to 20 characters) */
        bool suppress = false, lf = false, ll = false, wf = false, i64 = false, Lf = false;
        int hc = 0;
        size_t width = 0;
        for (;;) {
            if (*p == '*') suppress = true, p++;
            else if (*p >= '0' && *p <= '9') width = width * 10 + (size_t)(*p++ - '0');
            else if (*p == 'h') hc++, p++;
            else if (*p == 'l') { if (p[1] == 'l') ll = true, p += 2; else lf = true, p++; }
            else if (*p == 'L') Lf = true, p++;
            else if (*p == 'w') wf = true, p++;
            else if (p[0] == 'I' && p[1] == '6' && p[2] == '4') i64 = true, p += 3;
            else if (p[0] == 'I' && p[1] == '3' && p[2] == '2') p += 3;
            else break;
        }
        char c = *p;
        if (!c)
            break;
        p++;
        size_t w = width ? width : (size_t)-1;
        if (c == 'n') {
            if (!suppress) {
                void *dst = a.p();
                if (hc & 1) { short t = (short)(s - in); put_bytes(dst, &t, sizeof t); }
                else { int t = (int)(s - in); put_bytes(dst, &t, sizeof t); }
            }
            continue;
        }
        if (c == '%') { /* "%%": the percent sign, after white space */
            while (is_sp((unsigned char)*s))
                s++;
            if (*s != '%') { if (!*s) input_fail = true; break; }
            s++;
            continue;
        }
        bool known = strchr("diuoxXpPeEfgGsScC[", c) != NULL;
        if (!known) { /* '%' followed by any other character: that character is matched as text */
            if (!*s) { input_fail = true; break; }
            if (*s != c) break;
            s++;
            continue;
        }
        if (c != 'c' && c != 'C' && c != '[')
            while (is_sp((unsigned char)*s))
                s++;
        if (!*s) {
            input_fail = true;
            break;
        }
        if (c == 'd' || c == 'i' || c == 'u' || c == 'o' || c == 'x' || c == 'X' || c == 'p' || c == 'P') {
            size_t i = 0;
            bool neg = false;
            if (i < w && (s[i] == '+' || s[i] == '-'))
                neg = s[i++] == '-';
            int base = c == 'o' ? 8 : (c == 'x' || c == 'X' || c == 'p' || c == 'P') ? 16 : c == 'i' ? 0 : 10;
            if (c != 'p' && c != 'P' && (base == 0 || base == 16) && i + 2 < w && s[i] == '0' && (s[i + 1] == 'x' || s[i + 1] == 'X')
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
                void *dst = a.p();
                if (c == 'p' || c == 'P') {
                    if (A::kPtr == 4) { uint32_t t = (uint32_t)v; put_bytes(dst, &t, 4); }
                    else put_bytes(dst, &v, sizeof v);
                } else if (ll || i64) put_bytes(dst, &v, 8);
                else if (hc & 1) { uint16_t t = (uint16_t)v; put_bytes(dst, &t, 2); }
                else { uint32_t t = (uint32_t)v; put_bytes(dst, &t, 4); }
                n++;
            }
        } else if (c == 'e' || c == 'f' || c == 'g' || c == 'E' || c == 'G') {
            size_t l = scan_float(s, w);
            if (!l)
                break;
            bool dbl = lf || Lf;
            bool er;
            double v = token_value(s, l, dbl ? 53 : 24, dbl ? -1074 : -149, &er);
            s += l;
            if (!suppress) {
                void *dst = a.p();
                if (dbl) put_bytes(dst, &v, 8); else { float f = (float)v; put_bytes(dst, &f, 4); }
                n++;
            }
        } else { /* s S c C [ */
            bool wide = (c == 'S' || c == 'C') ? !(hc & 1) : (lf || wf);
            bool one = c == 'c' || c == 'C';
            unsigned char set[256];
            memset(set, 0, sizeof set);
            if (c == '[') {
                bool inv = *p == '^';
                p += inv;
                const unsigned char *q = (const unsigned char *)p;
                for (bool first = true; *q && (*q != ']' || first); first = false, q++) {
                    if (q[1] == '-' && q[2] && q[2] != ']') {
                        int lo = q[0], hi = q[2];
                        if (lo > hi) { int t = lo; lo = hi; hi = t; } /* g-e is e-g (Wine scanf.c) */
                        for (int ch = lo; ch <= hi; ch++)
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
            } else if (!one)
                for (int i = 1; i < 256; i++)
                    set[i] = !is_sp(i);
            size_t cnt = one ? (width ? width : 1) : w, i = 0;
            if (one) {
                for (; i < cnt && s[i]; i++) {}
                if (i < cnt)
                    break;
            } else {
                for (; i < cnt && s[i] && set[(unsigned char)s[i]]; i++) {}
                if (!i)
                    break;
            }
            if (!suppress) {
                void *dst = a.p();
                if (wide) {
                    for (size_t k = 0; k < i; k++) {
                        u16 wc = 0;
                        fnvnx_msvcrt_byte_to_wc((unsigned char)s[k], &wc);
                        put_bytes((char *)dst + 2 * k, &wc, 2);
                    }
                    if (!one) { u16 z = 0; put_bytes((char *)dst + 2 * i, &z, 2); }
                } else {
                    put_bytes(dst, s, i);
                    if (!one) ((char *)dst)[i] = 0;
                }
                n++;
            }
            s += i;
        }
    }
    return n == 0 && input_fail ? EOF : n;
}
}   // namespace

/* ------------------------------------------------------------------ entry points */
extern "C" int fnvnx_msvc_format(char *buf, size_t n, const char *fmt, va_list ap)
{
    va_list ap2;
    va_copy(ap2, ap);
    VaSrc a = {&ap2};
    int r = format_impl(buf, n, fmt, a);
    va_end(ap2);
    return r;
}
extern "C" int fnvnx_msvc_format_words(char *buf, size_t n, const char *fmt, const uint32_t *args)
{
    WordSrc a = {args};
    return format_impl(buf, n, fmt, a);
}
/* _snprintf / _vsnprintf: the terminator is written only when it fits; a full or longer output is -1 */
extern "C" int fnvnx_msvc_bound(char *buf, size_t n, int len)
{
    if (len < 0)
        return -1;
    if ((size_t)len < n)
        buf[len] = 0;
    return (size_t)len <= n ? len : -1;
}
extern "C" int fnvnx_msvc_scan(const char *in, const char *fmt, va_list ap)
{
    va_list ap2;
    va_copy(ap2, ap);
    VaSrc a = {&ap2};
    int r = scan_impl(in, fmt, a);
    va_end(ap2);
    return r;
}
extern "C" int fnvnx_msvc_scan_words(const char *in, const char *fmt, const uint32_t *args)
{
    WordSrc a = {args};
    return scan_impl(in, fmt, a);
}

extern "C" int fnvnx_msvcrt_va_sprintf(char *dst, const char *fmt, const uint32_t *args)
{
    if (!dst || !fmt) {
        errno = EINVAL;
        return -1;
    }
    int r = fnvnx_msvc_format_words(dst, (size_t)-1, fmt, args);
    if (r >= 0)
        dst[r] = 0;
    return r;
}
extern "C" int fnvnx_msvcrt_va_vsnprintf(char *dst, size_t count, const char *fmt, const uint32_t *args)
{
    if (!fmt || (!dst && count)) {
        errno = FNVNX_EINVAL;
        return -1;
    }
    return fnvnx_msvc_bound(dst, count, fnvnx_msvc_format_words(dst, count, fmt, args));
}
extern "C" int fnvnx_msvcrt_va_snprintf(char *dst, size_t count, const char *fmt, const uint32_t *args)
{
    return fnvnx_msvcrt_va_vsnprintf(dst, count, fmt, args);
}
extern "C" int fnvnx_msvcrt_va_sscanf(const char *src, const char *fmt, const uint32_t *args)
{
    return fnvnx_msvc_scan_words(src, fmt, args);
}

/* strtod: leading white space, then the longest decimal float prefix; ERANGE on overflow and on a nonzero value
 * that rounds to 0 */
extern "C" double fnvnx_msvc_strtod(const char *s, char **end)
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
    bool er;
    double r = token_value(p, l, 53, -1074, &er);
    if (er)
        errno = ERANGE;
    if (end)
        *end = (char *)p + l;
    return r;
}
