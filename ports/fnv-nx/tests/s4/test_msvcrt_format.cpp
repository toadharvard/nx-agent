/* test_msvcrt_format.cpp - the MSVCRT.DLL printf / scanf contract (platform/crt/msvcrt_format.cpp), cases fmt_*.
 *
 * Every row runs through BOTH entry points of the one engine and must give the identical result:
 *   - the native variadic entry (name table "sprintf" / "sscanf": a real va_list), and
 *   - the guest-word entry (fnvnx_msvcrt_va_sprintf / _va_sscanf: the x86 stack words a translated caller pushes;
 *     the test builds the word array itself, int and pointers one word, long long and double two words low first).
 * A guest pointer is a 32-bit word, so every pointer argument and every scanf destination lives in a low-memory
 * arena (MAP_32BIT) on this LP64 host.
 *
 * Expected strings come from the MSVCRT documentation and from the expected values of Wine 9.0's msvcrt
 * conformance tests (dlls/msvcrt/tests/printf.c, scanf.c: the rows are marked "wine printf.c" / "wine scanf.c").
 * Where Wine records two answers (the current one and a "broken" one of an old msvcrt) the row states which. */
#include "s4_test.h"
#include "../../platform/win32/w32.h"

#include <float.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <cmath>
#include <sys/mman.h>

#include "fnvnx_msvcrt.h"

extern "C" void *fnvnx_win32_lookup_export(const char *dll, const char *name);

namespace {
typedef unsigned short u16;
typedef unsigned long long u64;
typedef int (*vfmt_t)(char *, const char *, ...);
typedef int (*snp_t)(char *, size_t, const char *, ...);
typedef int (*sscanf_t)(const char *, const char *, ...);
typedef double (*strtod_t)(const char *, char **);
void *X(const char *n) { return fnvnx_win32_lookup_export("msvcrt.dll", n); }

/* ------------------------------------------------------------------ low memory arena */
uint8_t *g_low;
size_t g_used;
void *lowalloc(size_t n)
{
    if (!g_low) {
        void *m = mmap(NULL, 1 << 20, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT, -1, 0);
        g_low = m == MAP_FAILED ? NULL : (uint8_t *)m;
        CHECK(g_low != NULL);
    }
    g_used = (g_used + 7) & ~(size_t)7;
    void *p = g_low + g_used;
    g_used += n;
    return p;
}
uint32_t lowaddr(const void *p) { return (uint32_t)(uintptr_t)p; }

/* ------------------------------------------------------------------ typed printf arguments */
struct S { const char *p; };       /* narrow string pointer (NULL allowed) */
struct WS { const u16 *p; };       /* wide string pointer */
struct PV { uint32_t v; };         /* a pointer value (for %p) */
struct IP { int v; };              /* a pointer-sized integer (%Id): intptr_t natively on this LP64 host, one word on x86 */
const void *nat(S s) { return s.p; }
const void *nat(WS s) { return s.p; }
void *nat(PV v) { return (void *)(uintptr_t)v.v; }
intptr_t nat(IP v) { return v.v; }
int nat(int v) { return v; }
int nat(char v) { return v; }
unsigned nat(unsigned v) { return v; }
u64 nat(u64 v) { return v; }
double nat(double v) { return v; }

void pw(uint32_t *w, size_t &n, int v) { w[n++] = (uint32_t)v; }
void pw(uint32_t *w, size_t &n, char v) { w[n++] = (uint32_t)(int)v; }
void pw(uint32_t *w, size_t &n, unsigned v) { w[n++] = v; }
void pw(uint32_t *w, size_t &n, u64 v) { w[n++] = (uint32_t)v; w[n++] = (uint32_t)(v >> 32); }
void pw(uint32_t *w, size_t &n, double v) { u64 b; memcpy(&b, &v, 8); pw(w, n, b); }
void pw(uint32_t *w, size_t &n, PV v) { w[n++] = v.v; }
void pw(uint32_t *w, size_t &n, IP v) { w[n++] = (uint32_t)v.v; }
void pw(uint32_t *w, size_t &n, S s)
{
    if (!s.p) { w[n++] = 0; return; }
    size_t l = strlen(s.p) + 1;
    char *d = (char *)lowalloc(l);
    memcpy(d, s.p, l);
    w[n++] = lowaddr(d);
}
void pw(uint32_t *w, size_t &n, WS s)
{
    if (!s.p) { w[n++] = 0; return; }
    size_t l = 0;
    while (s.p[l]) l++;
    u16 *d = (u16 *)lowalloc(2 * (l + 1));
    memcpy(d, s.p, 2 * (l + 1));
    w[n++] = lowaddr(d);
}

int g_row_fail;
void rowfail(int line, const char *what, const char *fmt, const char *got, const char *want)
{
    fprintf(stderr, "  row at line %d (%s): format \"%s\": got \"%s\", want \"%s\"\n", line, what, fmt, got, want);
    g_row_fail++;
}

/* run one format through both entries; expect == NULL: only the return value is checked */
template <class... T> void prow(int line, const char *fmt, const char *expect, int ret, T... args)
{
    g_used = 0;
    char b1[1200], b2[1200];
    memset(b1, 'x', sizeof b1);
    memset(b2, 'x', sizeof b2);
    int r1 = ((vfmt_t)X("sprintf"))(b1, fmt, nat(args)...);
    uint32_t w[96] = {0};
    size_t n = 0;
    int dummy[] = {0, (pw(w, n, args), 0)...};
    (void)dummy;
    (void)n;
    int r2 = fnvnx_msvcrt_va_sprintf(b2, fmt, w);
    CHECK_EQ(r1, ret);
    CHECK_EQ(r2, ret);
    if (r1 != ret) rowfail(line, "native return", fmt, "", "");
    if (r2 != ret) rowfail(line, "words return", fmt, "", "");
    if (expect) {
        CHECK_STR(b1, expect);
        CHECK_STR(b2, expect);
        if (strcmp(b1, expect)) rowfail(line, "native", fmt, b1, expect);
        if (strcmp(b2, expect)) rowfail(line, "words", fmt, b2, expect);
    }
}
#define W(l) WS{(const u16 *)(l)}
#define ROW(f, e, ...) prow(__LINE__, f, e, (int)strlen(e), ##__VA_ARGS__)
#define ROWR(f, r, ...) prow(__LINE__, f, NULL, r, ##__VA_ARGS__)

double make_ind() { u64 b = 0xFFF8000000000000ull; double d; memcpy(&d, &b, 8); return d; }
double make_nan() { u64 b = 0x7FF8000000000001ull; double d; memcpy(&d, &b, 8); return d; }
double make_snan() { u64 b = 0x7FF0000000000001ull; double d; memcpy(&d, &b, 8); return d; }

/* ------------------------------------------------------------------ scanf rows */
/* spec: one letter per destination -
 *   i int32 (prefill -77777), h int32 prefilled 0xdeadbeef rendered as 8 hex digits, I int64 (prefill -9),
 *   f float, d double (prefill -1), p 32-bit pointer word rendered as 8 hex digits (prefill 0),
 *   s narrow string buffer (zero filled), c narrow buffer filled with 'Z' (first 4 bytes rendered raw),
 *   w wide string buffer (zero filled, chars >= 256 render as '?').  Destinations render joined by '|'. */
void render(char *out, size_t cap, char kind, const void *slot)
{
    char t[160];
    switch (kind) {
    case 'i': snprintf(t, sizeof t, "%d", *(const int *)slot); break;
    case 'h': snprintf(t, sizeof t, "%08x", *(const unsigned *)slot); break;
    case 'I': snprintf(t, sizeof t, "%lld", *(const long long *)slot); break;
    case 'f': snprintf(t, sizeof t, "%.9g", (double)*(const float *)slot); break;
    case 'd': snprintf(t, sizeof t, "%.17g", *(const double *)slot); break;
    case 'p': snprintf(t, sizeof t, "%08x", *(const unsigned *)slot); break;
    case 's': snprintf(t, sizeof t, "%s", (const char *)slot); break;
    case 'c': snprintf(t, sizeof t, "%.4s", (const char *)slot); break;
    default: { /* w */
        const u16 *w = (const u16 *)slot;
        size_t k = 0;
        for (; w[k] && k < 100; k++) t[k] = w[k] < 256 ? (char)w[k] : '?';
        t[k] = 0;
    }
    }
    strncat(out, t, cap - strlen(out) - 1);
}
void srow(int line, const char *in, const char *fmt, int ret, const char *spec, const char *expect)
{
    for (int path = 0; path < 2; path++) {
        g_used = 0;
        size_t ns = strlen(spec);
        void *slot[8];
        for (size_t i = 0; i < ns; i++) {
            slot[i] = lowalloc(128);
            memset(slot[i], 0, 128);
            switch (spec[i]) {
            case 'i': *(int *)slot[i] = -77777; break;
            case 'h': *(unsigned *)slot[i] = 0xdeadbeef; break;
            case 'I': *(long long *)slot[i] = -9; break;
            case 'f': *(float *)slot[i] = -1.0f; break;
            case 'd': *(double *)slot[i] = -1.0; break;
            case 'c': memset(slot[i], 'Z', 120); break;
            default: break;
            }
        }
        int r;
        if (path == 0) {
            sscanf_t ss = (sscanf_t)X("sscanf");
            switch (ns) {
            case 0: r = ss(in, fmt); break;
            case 1: r = ss(in, fmt, slot[0]); break;
            case 2: r = ss(in, fmt, slot[0], slot[1]); break;
            case 3: r = ss(in, fmt, slot[0], slot[1], slot[2]); break;
            case 4: r = ss(in, fmt, slot[0], slot[1], slot[2], slot[3]); break;
            default: r = ss(in, fmt, slot[0], slot[1], slot[2], slot[3], slot[4]); break;
            }
        } else {
            uint32_t w[8];
            for (size_t i = 0; i < ns; i++) w[i] = lowaddr(slot[i]);
            r = fnvnx_msvcrt_va_sscanf(in, fmt, w);
        }
        char got[400] = "";
        for (size_t i = 0; i < ns; i++) {
            if (i) strcat(got, "|");
            render(got, sizeof got, spec[i], slot[i]);
        }
        CHECK_EQ(r, ret);
        CHECK_STR(got, expect);
        if (r != ret || strcmp(got, expect)) {
            fprintf(stderr, "  scan row at line %d (%s): input \"%s\" format \"%s\": ret %d want %d, got \"%s\" want \"%s\"\n",
                    line, path ? "words" : "native", in, fmt, r, ret, got, expect);
            g_row_fail++;
        }
    }
}
#define SROW(in, fmt, ret, spec, expect) srow(__LINE__, in, fmt, ret, spec, expect)
}   // namespace

/* ================================================================== printf: integers */
TEST(fmt_int)
{
    ROW("%d", "-5", -5);
    ROW("%i", "-1", -1);
    ROW("%5d", "   42", 42);
    ROW("%-5d|", "42   |", 42);
    ROW("%05d", "00042", 42);
    ROW("%05d", "-0042", -42);
    ROW("%+d", "+7", 7);
    ROW("%+d", "-7", -7);
    ROW("% d", " 7", 7);
    ROW("%+ d", "+7", 7);                                  /* wine printf.c: '+' wins over ' ' */
    ROW("%.3d", "007", 7);
    ROW("%8.3d", "     007", 7);
    ROW("%-8.3d|", "007     |", 7);
    ROW("%08.3d", "     007", 7);                          /* a precision cancels the 0 flag */
    ROW("%.0d", "", 0);
    ROW("%.0d", "1", 1);
    ROW("%3d", "1234", 1234);                              /* wine printf.c */
    ROW("%-1d", "2", 2);                                   /* wine printf.c */
    ROW("%-i", "-1", -1);
    ROW("%+i", "+1", 1);
    ROW("%u", "4294967295", -1);                           /* wine printf.c */
    ROW("%u", "0", 0);
    ROW("%o", "12", 10);                                   /* wine printf.c */
    ROW("%o", "37777777777", -1);
    ROW("%x", "ffffffff", -1);
    ROW("%X", "DEADBEEF", 0xdeadbeefu);
    ROW("%#x", "0x1f", 31);
    ROW("%#X", "0X1F", 31);
    ROW("%#o", "037", 31);
    ROW("%#x", "0", 0);
    ROW("%#o", "0", 0);                                    /* wine printf.c */
    ROW("%#o", "01", 1);                                   /* wine printf.c */
    ROW("%#08o", "00000001", 1);                           /* wine printf.c */
    ROW("%#012x", "0x0000000001", 1);                      /* wine printf.c */
    ROW("%#012x", "000000000000", 0);                      /* wine printf.c */
    ROW("%#04.8x", "0x00000001", 1);                       /* wine printf.c */
    ROW("%#04.8x", "00000000", 0);                         /* wine printf.c */
    ROW("%#-08.2x", "0x01    ", 1);                        /* wine printf.c */
    ROW("%#-08.2x", "00      ", 0);                        /* wine printf.c */
    ROW("%#.0x", "0x1", 1);                                /* wine printf.c */
    ROW("%#.0x", "", 0);                                   /* wine printf.c */
    ROW("%.80d", "00000000000000000000000000000000000000000000000000000000000000000000000000000001", 1);
    ROW("% .80d", " 00000000000000000000000000000000000000000000000000000000000000000000000000000001", 1); /* wine printf.c */
    /* h: 16 bits (hh too: wine printf.c "%hhx") */
    ROW("%hd", "-1", 65535);
    ROW("%hu", "65535", -1);
    ROW("%hx", "2345", 0x12345);                           /* wine printf.c */
    ROW("%hhx", "123", 0x123);                             /* wine printf.c */
    ROW("%hhx", "2345", 0x12345);                          /* wine printf.c */
    ROW("%ld", "-1", -1);
    ROW("%lu", "4294967295", -1);
    ROW("%lx", "ffffffff", -1);
    ROW("%Ld", "5", 5);                                    /* L is ignored for integers */
    /* 64 bits: I64 and ll (two stack words, low first) */
    ROW("%I64d", "-8589934591", (u64)0xffffffff * 0xffffffff);   /* wine printf.c */
    ROW("%+8I64d", "    +100", (u64)100);                  /* wine printf.c */
    ROW("%+.8I64d", "+00000100", (u64)100);                /* wine printf.c */
    ROW("%+10.8I64d", " +00000100", (u64)100);             /* wine printf.c */
    ROW("%-1.5I64d", "-00100", (u64)-100);                 /* wine printf.c */
    ROW("%5I64d", "  100", (u64)100);
    ROW("%5I64d", " -100", (u64)-100);
    ROW("%-5I64d", "100  ", (u64)100);
    ROW("%-5I64d", "-100 ", (u64)-100);
    ROW("%-.5I64d", "00100", (u64)100);
    ROW("%-.5I64d", "-00100", (u64)-100);
    ROW("%-8.5I64d", "00100   ", (u64)100);
    ROW("%-8.5I64d", "-00100  ", (u64)-100);
    ROW("%05I64d", "00100", (u64)100);
    ROW("%05I64d", "-0100", (u64)-100);
    ROW("% I64d", " 100", (u64)100);
    ROW("% I64d", "-100", (u64)-100);
    ROW("% 5I64d", "  100", (u64)100);
    ROW("% 5I64d", " -100", (u64)-100);
    ROW("% .5I64d", " 00100", (u64)100);
    ROW("% .5I64d", "-00100", (u64)-100);
    ROW("% 8.5I64d", "   00100", (u64)100);
    ROW("% 8.5I64d", "  -00100", (u64)-100);
    ROW("%.0I64d", "", (u64)0);
    ROW("%#+21.18I64x", " 0x00ffffffffffffff9c", (u64)-100);
    ROW("%#.25I64o", "0001777777777777777777634", (u64)-100);
    ROW("%#+24.20I64o", " 01777777777777777777634", (u64)-100);
    ROW("%#+18.21I64X", "0X00000FFFFFFFFFFFFFF9C", (u64)-100);
    ROW("%#+20.24I64o", "001777777777777777777634", (u64)-100);
    ROW("%#+25.22I64u", "   0018446744073709551615", (u64)-1);
    ROW("%#+30.25I64u", "     0000018446744073709551615", (u64)-1);
    ROW("%+#25.22I64d", "  -0000000000000000000001", (u64)-1);
    ROW("%#-8.5I64o", "00144   ", (u64)100);
    ROW("%#-+ 08.5I64d", "+00100  ", (u64)100);
    ROW("%.80I64d", "00000000000000000000000000000000000000000000000000000000000000000000000000000001", (u64)1);
    ROW("% .80I64d", " 00000000000000000000000000000000000000000000000000000000000000000000000000000001", (u64)1);
    ROW("%I64u", "18446744073709551615", ~(u64)0);
    ROW("%I64x", "ffffffffffffff9c", (u64)-100);
    ROW("%I64d", "-9223372036854775808", (u64)1 << 63);
    ROW("%lld", "-8589934591", (u64)0xffffffff * 0xffffffff);   /* wine printf.c (an old msvcrt printed "1") */
    ROW("%llu", "18446744073709551615", ~(u64)0);
    ROW("%llx", "123456789abcdef0", (u64)0x123456789abcdef0ull);
    ROW("%I32d", "1", 1);                                  /* wine printf.c (an old msvcrt printed "I32d") */
    ROW("%I32u", "4294967295", -1);
    ROW("%Id %d", "-1 7", IP{-1}, 7);                          /* I: pointer-sized, one word on x86 */
    ROW("%Ix %d", "123456 1", IP{0x123456}, 1);                /* wine printf.c (32-bit branch) */
    ROW("%Iu", "123456", IP{123456});
    /* '*' width and precision */
    ROW("%*d", "   42", 5, 42);
    ROW("%-*d|", "42   |", 5, 42);
    ROW("%*d|", "42|", 0, 42);
    ROW("%*d|", "42   |", -5, 42);
    ROW("%.*d", "00042", 5, 42);
    ROW("%*.*d", "  042", 5, 3, 42);
    ROW("%.*s", "f", 1, S{"foo"});                         /* wine printf.c */
    ROW("%*s", "foo  ", -5, S{"foo"});                     /* wine printf.c */
    ROW("%*1d", "          3", 1, 3);                      /* wine printf.c: the width is 10 * 1 + 1 */
    ROW("%0*0d", "0000000002", 1, 2);                      /* wine printf.c */
    ROW("% *2d", " 7", 0, 7);                              /* wine printf.c */
    /* several arguments: the words follow each other without padding */
    ROW("%d %I64d %d", "1 -2 3", 1, (u64)-2, 3);
    ROW("%d %d %d %d", "1 2 3 4", 1, 2, 3, 4);
    ROW("%c%c%c", "abc", 'a', 'b', 'c');
}

/* ================================================================== printf: characters and strings */
TEST(fmt_charstr)
{
    ROW("%c", "a", 'a');                                   /* wine printf.c */
    ROW("%3c", "  a", 'a');                                /* wine printf.c */
    ROW("%-3c|", "a  |", 'a');
    ROW("%04c", "0001", '1');                              /* wine printf.c */
    ROW("%-04c", "1   ", '1');                             /* wine printf.c */
    ROW("%c", "\x82", 0xa082);                             /* wine printf.c: the low byte */
    ROW("x%cx", "xXx", 0x100 + 'X');                       /* wine printf.c */
    ROW("%C", "a", 'a');                                   /* wine printf.c */
    ROW("%C", "", 0x3042);                                 /* wine printf.c: no form in the code page: nothing */
    ROW("a%Cb", "ab", 0x3042);                             /* wine printf.c */
    ROW("%lc", "a", 'a');
    ROW("%wc", "a", 'a');
    ROW("%hc", "a", 'a');
    ROW("%hC", "a", 'a');
    ROW("%s", "abc", S{"abc"});
    ROW("%5s", "  abc", S{"abc"});
    ROW("%-5s|", "abc  |", S{"abc"});
    ROW("%.2s", "ab", S{"abc"});
    ROW("%5.2s", "   ab", S{"abc"});
    ROW("%.0s", "", S{"abc"});
    ROW("%.1s", "f", S{"foo"});                            /* wine printf.c */
    ROW("%04s", "0foo", S{"foo"});                         /* wine printf.c */
    ROW("%s", "(null)", S{NULL});                          /* wine printf.c */
    ROW("%s", "%%%%", S{"%%%%"});                          /* wine printf.c */
    ROW("%hs", "abc", S{"abc"});
    ROW("%hS", "abc", S{"abc"});
    ROW("%Ls", "not wide", S{"not wide"});                 /* wine printf.c */
    ROW("%ls", "wide", W(L"wide"));                       /* wine printf.c */
    ROW("%ws", "wide", W(L"wide"));                       /* wine printf.c */
    ROW("%S", "wide", W(L"wide"));                        /* wine printf.c */
    ROW("%-10ws|", "wide      |", W(L"wide"));            /* wine printf.c */
    ROW("%10ws", "      wide", W(L"wide"));               /* wine printf.c */
    ROW("%#+ -03whlls", "wide", W(L"wide"));              /* wine printf.c: w, h, l, l then s: wide */
    ROW("%w0s", "0s", W(L"wide"));                        /* wine printf.c */
    ROW("%w-s", "-s", W(L"wide"));                        /* wine printf.c */
    ROW("%.2ls", "wi", W(L"wide"));
    ROW("%ls", "caf\xe9", W(L"caf\xe9"));                 /* "C" locale: characters below 256 map to themselves */
    ROW("%s", "(null)", S{NULL});
    ROW("%ls", "(null)", WS{NULL});
    ROWR("%S", -1, W(L"\x3042"));                         /* wine printf.c: no form in the code page: -1 */
    ROWR("%ls", -1, W(L"ab\x3042"));
    /* a wide string and a narrow one in one call: each takes one word */
    ROW("%ls|%s|%d", "ab|cd|5", W(L"ab"), S{"cd"}, 5);
}

/* the ANSI code page 1252 of the layer's NLS conversion: the locale "" of MSVCRT */
TEST(fmt_codepage_1252)
{
    auto sl = (char *(*)(int, const char *))X("setlocale");
    CHECK(sl(0, "") != NULL);
    ROW("%ls", "\x80", W(L"\x20ac"));                     /* U+20AC is 0x80 in 1252 */
    ROW("%C", "\x9f", 0x178);
    ROW("%c", "a", 'a');
    ROWR("%ls", -1, W(L"\x3042"));
    ROW("%C", "", 0x3042);
    CHECK(sl(0, "C") != NULL);
    ROW("%C", "", 0x20ac);                                 /* back in the "C" locale: above 255 has no form */
    SROW("\x80", "%lc", 1, "w", "\x80");                   /* "C": identity, 0x80 is U+0080 */
    CHECK(sl(0, "") != NULL);
    SROW("\x80zz", "%ls", 1, "w", "?zz");                  /* 1252: 0x80 is U+20AC (>= 256 renders '?') */
    CHECK(sl(0, "C") != NULL);
}

/* ================================================================== printf: floating point */
TEST(fmt_float)
{
    ROW("%f", "3.141590", 3.14159);
    ROW("%f", "-3.141590", -3.14159);
    ROW("%f", "0.000000", 0.0);
    ROW("%.2f", "3.14", 3.14159);
    ROW("%.0f", "3", 3.14159);
    ROW("%#.0f", "3.", 3.14159);
    ROW("%10.3f", "     3.142", 3.14159);
    ROW("%-10.3f|", "3.142     |", 3.14159);
    ROW("%010.3f", "000003.142", 3.14159);
    ROW("%010.2f", "-000002.50", -2.5);
    ROW("%+.1f", "+2.5", 2.5);
    ROW("% f", " 1.000000", 1.0);
    ROW("%2.4f", "8.6000", 8.6);                           /* wine printf.c */
    ROW("%0f", "0.600000", 0.6);                           /* wine printf.c */
    ROW("%.0f", "1", 0.6);                                 /* wine printf.c */
    ROW("%.0f", "-2", -1.5);                               /* wine printf.c: ties away from zero */
    ROW("%.0f", "-1", -0.5);                               /* wine printf.c */
    ROW("%.0f", "1", 0.5);                                 /* wine printf.c */
    ROW("%.0f", "2", 1.5);                                 /* wine printf.c */
    ROW("%.0f", "3", 2.5);
    ROW("%.0f", "0", 0.49999999999999994);
    ROW("%.2f", "0.13", 0.125);
    ROW("%.1f", "0.1", 0.05);
    ROW("%.3f", "2.001", 2.0005);
    ROW("%f", "10000000000000000000000.000000", 1e22);
    ROW("%.0f", "1000", 999.5);
    ROW("%.20f", "0.10000000000000000555", 0.1);           /* exact expansion (wine printf.c "%.30f" differs: see report) */
    ROW("%.0f", "0", 5e-324);
    ROW("%f", "340282366920938463463374607431768211456.000000", ldexp(1.0, 128));
    /* e / E */
    ROW("%e", "1.234568e+004", 12345.678);
    ROW("%E", "1.234568E+004", 12345.678);
    ROW("%e", "0.000000e+000", 0.0);
    ROW("%e", "1.000000e-005", 1e-5);
    ROW("%e", "1.000000e+100", 1e100);
    ROW("%e", "1.797693e+308", DBL_MAX);
    ROW("%e", "4.940656e-324", 5e-324);
    ROW("%.0e", "3e+001", 26.0);
    ROW("%.0e", "3e+000", 2.5);
    ROW("%.1e", "2.5e-001", 0.25);
    ROW("%.0e", "1e+001", 9.5);
    ROW("%.3e", "9.999e+000", 9.9995);
    ROW("%.2e", "1.00e+000", 1.0);
    ROW("%#.0e", "3.e+001", 26.0);
    ROW("%+#23.15e", "+7.894561230000000e+008", 789456123.0);   /* wine printf.c */
    ROW("%-#23.15e", "7.894561230000000e+008 ", 789456123.0);   /* wine printf.c */
    ROW("%#23.15e", " 7.894561230000000e+008", 789456123.0);    /* wine printf.c */
    ROW("%2.4e", "8.6000e+000", 8.6);                      /* wine printf.c */
    ROW("% 2.4e", " 8.6000e+000", 8.6);                    /* wine printf.c */
    ROW("% 014.4e", " 008.6000e+000", 8.6);                /* wine printf.c */
    ROW("% 2.4e", "-8.6000e+000", -8.6);                   /* wine printf.c */
    ROW("%+2.4e", "+8.6000e+000", 8.6);                    /* wine printf.c */
    /* g / G */
    ROW("%g", "100000", 100000.0);
    ROW("%g", "1e+006", 1e6);
    ROW("%g", "0.0001", 0.0001);
    ROW("%g", "1e-005", 0.00001);
    ROW("%g", "0", 0.0);
    ROW("%g", "123457", 123456.7);
    ROW("%g", "1.23457e+006", 1234567.0);
    ROW("%g", "3.14159", 3.14159);
    ROW("%G", "1E-010", 1e-10);
    ROW("%.3g", "2.5", 2.5);
    ROW("%.2g", "0.13", 0.125);
    ROW("%#g", "1.00000", 1.0);
    ROW("%#1.1g", "8.e+008", 789456123.0);                 /* wine printf.c */
    ROW("%2.4g", "8.6", 8.6);                              /* wine printf.c */
    ROW("%g", "1e+006", 999999.5);
    ROW("%.17g", "0.10000000000000001", 0.1);
    ROW("%10g|", "      3.14|", 3.14);
    ROW("%-10g|", "3.14      |", 3.14);
    ROW("%+g", "+3.14", 3.14);
    ROW("%.0g", "1e+001", 9.5);
    /* mixed: the double takes two words */
    ROW("%d %f %d", "1 2.500000 3", 1, 2.5, 3);
    ROW("%f %f", "1.500000 2.500000", 1.5, 2.5);
    ROW("%I64d %e %c", "7 1.000000e+000 z", (u64)7, 1.0, 'z');
    ROW("%Lf", "2.500000", 2.5);                           /* long double is double under MSVC */
    ROW("%lf", "2.500000", 2.5);
}

/* infinities and NaN: "1.#INF", "-1.#IND", "1.#QNAN" with the digit-string rules of the C runtime */
TEST(fmt_float_special)
{
    double ind = make_ind(), nan = make_nan(), inf = INFINITY;
    ROW("%lf", "-1.#IND00", ind);                          /* wine printf.c */
    ROW("%lf", "1.#QNAN0", nan);                           /* wine printf.c */
    ROW("%lf", "1.#INF00", inf);                           /* wine printf.c */
    ROW("%le", "-1.#IND00e+000", ind);                     /* wine printf.c */
    ROW("%le", "1.#QNAN0e+000", nan);                      /* wine printf.c */
    ROW("%le", "1.#INF00e+000", inf);                      /* wine printf.c */
    ROW("%lg", "-1.#IND", ind);                            /* wine printf.c */
    ROW("%lg", "1.#QNAN", nan);                            /* wine printf.c */
    ROW("%lg", "1.#INF", inf);                             /* wine printf.c */
    ROW("%010.2lf", "-000001.#J", ind);                    /* wine printf.c: "#IND" cut at 2 and rounded */
    ROW("%010.2lf", "0000001.#R", nan);                    /* wine printf.c */
    ROW("%010.2lf", "0000001.#J", inf);                    /* wine printf.c */
    ROW("%f", "-1.#INF00", -inf);
    ROW("%.0f", "1", inf);
    ROW("%.3f", "1.#IO", inf);                             /* "#INF" cut at 3: the dropped 'F' >= '5' raises the N */
    ROW("%f", "1.#SNAN0", make_snan());
}

/* ================================================================== printf: pointers, %n, text, bad conversions */
TEST(fmt_ptr_n_misc)
{
    ROW("%p", "00000039", PV{57});                         /* wine printf.c (32-bit branch) */
    ROW("%p", "00000000", PV{0});                          /* wine printf.c */
    ROW("%p", "DEADBEEF", PV{0xdeadbeef});
    ROW("%#012p", "  0X00000039", PV{57});                 /* wine printf.c */
    ROW("%#-012p", "0X00000039  ", PV{57});                /* wine printf.c */
    ROW("%Fp", "00000039", PV{57});                        /* wine printf.c */
    ROW("%Np", "00000039", PV{57});                        /* wine printf.c */
    ROW("%10p", "  00000039", PV{57});
    ROW("%-10p|", "00000039  |", PV{57});
    ROW("%%", "%");
    ROW("%%0", "%0");                                      /* wine printf.c */
    ROW("100%%d", "100%d");
    ROW("hello", "hello");                                 /* wine printf.c */
    ROW("", "");
    ROW("%b", "b");                                        /* wine printf.c */
    ROW("%3h", "");                                        /* wine printf.c */
    ROW("%w", "", -1);                                     /* wine printf.c */
    ROW("%h", "", -1);                                     /* wine printf.c */
    ROW("%F", "", -1);                                     /* wine printf.c */
    ROW("%N", "", -1);                                     /* wine printf.c */
    ROW("%H", "H", -1);                                    /* wine printf.c */
    ROW("%_1I64d", "_1I64d", (u64)100);                    /* wine printf.c */
    ROW("%k%m%q%r%t%v%y%z", "kmqrtvyz");                   /* wine printf.c */
    ROW("%I", "I", 1);                                     /* wine printf.c */
    ROW("%Iq", "Iq", 1);                                   /* wine printf.c */
    ROW("%Ihd", "Ihd", 1);                                 /* wine printf.c */
    ROW("%I0d", "I0d", 1);                                 /* wine printf.c */
    ROW("%I64D", "D", (u64)-1);                            /* wine printf.c */
    /* old msvcrt generation (the "broken" answers of wine printf.c): z, t, j are not length modifiers */
    ROW("%zx", "zx", 1);
    ROW("%tx", "tx", 1);
    ROW("%j", "j", (u64)-1);
    ROW("%jd", "jd", (u64)-1);
    ROW("%zd|%jd|%td", "zd|jd|td");
    ROW("%a|%A", "a|A", 1.0, 1.0);
    /* %n: the count so far goes through the pointer (one word) */
    for (int path = 0; path < 2; path++) {
        g_used = 0;
        int *x = (int *)lowalloc(8);
        char b[32];
        *x = -1;
        int r;
        if (path == 0)
            r = ((vfmt_t)X("sprintf"))(b, "asdf%n", x);
        else {
            uint32_t w[1] = {lowaddr(x)};
            r = fnvnx_msvcrt_va_sprintf(b, "asdf%n", w);
        }
        CHECK_EQ(r, 4);
        CHECK_EQ(*x, 4);                                   /* wine printf.c */
        CHECK_STR(b, "asdf");
        *x = 0x12345678;
        uint32_t w2[1] = {lowaddr(x)};
        r = fnvnx_msvcrt_va_sprintf(b, "abc%hn", w2);
        CHECK_EQ(r, 3);
        CHECK_EQ((unsigned)*x, 0x12340003u);               /* %hn writes 16 bits */
        uint32_t w3[3] = {7, lowaddr(x), 8};
        r = fnvnx_msvcrt_va_sprintf(b, "%d%n%d", w3);
        CHECK_STR(b, "78");
        CHECK_EQ(*x, 1);
    }
    /* a large precision and width */
    ROWR("%1000d", 1000, 5);
    ROW("%.50s|", "short|", S{"short"});
}

/* ================================================================== _snprintf / _vsnprintf return and truncation */
namespace {
/* path 0: the native variadic _snprintf; 1: fnvnx_msvcrt_va_snprintf; 2: fnvnx_msvcrt_va_vsnprintf */
template <class... T> int snrun(int path, char *buf, size_t cnt, const char *fmt, T... args)
{
    g_used = 0;
    if (path == 0)
        return ((snp_t)X("_snprintf"))(buf, cnt, fmt, nat(args)...);
    uint32_t w[16] = {0};
    size_t n = 0;
    int dummy[] = {0, (pw(w, n, args), 0)...};
    (void)dummy;
    (void)n;
    return path == 1 ? fnvnx_msvcrt_va_snprintf(buf, cnt, fmt, w) : fnvnx_msvcrt_va_vsnprintf(buf, cnt, fmt, w);
}
}
TEST(fmt_snprintf)
{
    for (int path = 0; path < 3; path++) {
        char t[8];
        memset(t, 'z', sizeof t);
        CHECK_EQ(snrun(path, t, 4, "abcdef"), -1);
        CHECK(memcmp(t, "abcdzzzz", 8) == 0);             /* truncated: no terminator, count bytes written */
        memset(t, 'z', sizeof t);
        CHECK_EQ(snrun(path, t, 4, "abcd"), 4);
        CHECK(memcmp(t, "abcdzzzz", 8) == 0);             /* exactly full: the length, no terminator */
        memset(t, 'z', sizeof t);
        CHECK_EQ(snrun(path, t, 5, "abcd"), 4);
        CHECK(memcmp(t, "abcd\0zzz", 8) == 0);
        memset(t, 'z', sizeof t);
        CHECK_EQ(snrun(path, t, 8, "%d", 12345), 5);
        CHECK(memcmp(t, "12345\0zz", 8) == 0);
        memset(t, 'z', sizeof t);
        CHECK_EQ(snrun(path, t, 3, "%s", S{"abcdef"}), -1);
        CHECK(memcmp(t, "abczzzzz", 8) == 0);
        memset(t, 'z', sizeof t);
        CHECK_EQ(snrun(path, t, 5, "%s%d", S{"ab"}, 7), 3);
        CHECK(memcmp(t, "ab7\0zzzz", 8) == 0);
        memset(t, 'z', sizeof t);
        CHECK_EQ(snrun(path, t, 0, "abc"), -1);
        CHECK(memcmp(t, "zzzzzzzz", 8) == 0);             /* count 0: nothing written */
        CHECK_EQ(snrun(path, t, 0, ""), 0);
        CHECK_EQ(snrun(path, NULL, 0, "abc"), -1);
        memset(t, 'z', sizeof t);
        CHECK_EQ(snrun(path, t, 1, ""), 0);
        CHECK(t[0] == 0 && t[1] == 'z');
        memset(t, 'z', sizeof t);
        CHECK_EQ(snrun(path, t, 8, "%f|%I64d", 1.5, (u64)-2), -1);   /* "1.500000|-2" is 11 characters */
        CHECK(memcmp(t, "1.500000", 8) == 0);
    }
}

/* ================================================================== the word layout */
TEST(fmt_words_layout)
{
    /* every word the format consumes is read in order: int (1), pointer (1), int64 (2), double (2), char (1) */
    g_used = 0;
    uint32_t w[16] = {0};
    size_t n = 0;
    pw(w, n, 1);
    pw(w, n, S{"xyz"});
    pw(w, n, (u64)0x100000002ull);
    pw(w, n, 0.5);
    pw(w, n, 'q');
    pw(w, n, 9);
    char b[100];
    int r = fnvnx_msvcrt_va_sprintf(b, "%d %s %I64x %f %c %d", w);
    CHECK_STR(b, "1 xyz 100000002 0.500000 q 9");
    CHECK_EQ(r, 28);
    /* *_words and a bounded call with a width taken from the words */
    uint32_t w2[3] = {6, 3, 42};
    r = fnvnx_msvcrt_va_snprintf(b, sizeof b, "%*.*d|", w2);
    CHECK_STR(b, "   042|");
    CHECK_EQ(r, 7);
    /* %I is pointer sized: one word, zero-extended when unsigned */
    const uint32_t w5[1] = {0xffffffffu};
    r = fnvnx_msvcrt_va_sprintf(b, "%Iu", w5);
    CHECK_STR(b, "4294967295");
    r = fnvnx_msvcrt_va_sprintf(b, "%Id", w5);
    CHECK_STR(b, "-1");
    /* the generic v* form: the same pointer serves _vsnprintf */
    const uint32_t w4[2] = {5, 6};
    r = fnvnx_msvcrt_va_vsnprintf(b, sizeof b, "%d,%d", w4);
    CHECK_STR(b, "5,6");
}

/* ================================================================== oracle cross-check of the digit engine */
namespace {
/* glibc's printf is exact and rounds ties to even, the MSVCRT rule rounds them up: a value whose exact expansion
 * ends in a 5 right after the kept digits is a tie, and is left out of the comparison */
bool is_tie(double d, bool fixed, int p)
{
    static char ex[1300];
    snprintf(ex, sizeof ex, "%.1100e", fabs(d));
    int x10 = atoi(strchr(ex, 'e') + 1);
    int keep = fixed ? x10 + 1 + p : p + 1; /* digits kept */
    if (keep < 0) return false;
    char dg[1200];
    int nd = 0;
    for (char *c = ex; *c != 'e'; c++)
        if (*c != '.') dg[nd++] = *c;
    if (keep >= nd) return false;
    if (dg[keep] != '5') return false;
    for (int i = keep + 1; i < nd; i++)
        if (dg[i] != '0') return false;
    return true;
}
}
/* The rule (exact expansion, half up) agrees with glibc's exact printf everywhere except exact decimal ties, which
 * random doubles do not hit at these precisions. A check of the rule, not its proof. */
TEST(fmt_float_oracle)
{
    uint64_t st = 0x9E3779B97F4A7C15ull;
    auto rnd = [&]() { st ^= st << 13; st ^= st >> 7; st ^= st << 17; return st; };
    int bad = 0;
    for (int i = 0; i < 4000; i++) {
        u64 b = rnd();
        double d;
        memcpy(&d, &b, 8);
        if (!std::isfinite(d)) continue;
        if (i & 1) d = (double)(int64_t)(rnd() >> 12) / (double)(1ull << (rnd() % 40)); /* moderate values */
        static const int ps[] = {0, 3, 6, 15, 20, 40};
        char fm[16], want[1600], got[1600], g1[1600];
        uint32_t w[4];
        size_t n = 0;
        pw(w, n, d);
        for (int p : ps) {
            snprintf(fm, sizeof fm, "%%.%df", p);
            if (is_tie(d, true, p)) continue;
            snprintf(want, sizeof want, fm, d);
            fnvnx_msvcrt_va_sprintf(got, fm, w);
            if (strcmp(want, got)) { bad++; if (bad < 4) fprintf(stderr, "  %s of %a: got %s want %s\n", fm, d, got, want); }
            snprintf(fm, sizeof fm, "%%.%de", p);
            if (is_tie(d, false, p)) continue;
            snprintf(g1, sizeof g1, fm, d);
            fnvnx_msvcrt_va_sprintf(got, fm, w);
            /* glibc: e+05 / MSVCRT: e+005 */
            char *e = strchr(g1, 'e');
            char fixed[1600];
            snprintf(fixed, sizeof fixed, "%.*se%c%03d", (int)(e - g1), g1, e[1], atoi(e + 2));
            if (strcmp(fixed, got)) { bad++; if (bad < 4) fprintf(stderr, "  %s of %a: got %s want %s\n", fm, d, got, fixed); }
        }
    }
    CHECK_EQ(bad, 0);
}

/* strtod / scanf %f: exact decimal-to-binary, against glibc's correctly rounded strtod (an oracle for the rule) */
TEST(fmt_strtod_exact)
{
    auto sd = (strtod_t)X("strtod");
    uint64_t st = 0x2545F4914F6CDD1Dull;
    auto rnd = [&]() { st ^= st << 13; st ^= st >> 7; st ^= st << 17; return st; };
    int bad = 0;
    for (int i = 0; i < 6000; i++) {
        char s[900];
        size_t n = 0;
        int nd = 1 + (int)(rnd() % (i % 7 == 0 ? 780 : 25));
        if (rnd() & 1) s[n++] = '-';
        for (int k = 0; k < nd; k++) {
            if (k == 1 + (int)(rnd() % 3)) s[n++] = '.';
            s[n++] = (char)('0' + rnd() % 10);
        }
        int ex = (int)(rnd() % 700) - 350;
        if (i % 5 != 0) n += (size_t)snprintf(s + n, 12, "e%d", ex);
        s[n] = 0;
        double a = sd(s, NULL), b = strtod(s, NULL);
        u64 ba, bb;
        memcpy(&ba, &a, 8);
        memcpy(&bb, &b, 8);
        if (ba != bb) { bad++; if (bad < 4) fprintf(stderr, "  strtod(%.60s): got %a want %a\n", s, a, b); }
    }
    CHECK_EQ(bad, 0);
    /* exact ties and the boundaries of the range */
    CHECK(sd("9007199254740993", NULL) == 9007199254740992.0);
    CHECK(sd("9007199254740995", NULL) == 9007199254740996.0);
    CHECK(sd("9007199254740993.0000000000000000000000000001", NULL) == 9007199254740994.0);
    CHECK(sd("2.4703282292062327e-324", NULL) == 0.0);
    CHECK(sd("2.4703282292062328e-324", NULL) == 4.9406564584124654e-324);
    CHECK(sd("1.7976931348623157e308", NULL) == DBL_MAX);
    CHECK(sd("1.7976931348623158e308", NULL) == DBL_MAX);
    CHECK(std::isinf(sd("1.7976931348623159e308", NULL)));
    CHECK(sd((std::string("0.") + std::string(62, '0') + "1e63").c_str(), NULL) == 1.0);
    CHECK(sd((std::string("1") + std::string(45, '0') + "e-45").c_str(), NULL) == 1.0);
    CHECK(sd("1e-99999999999", NULL) == 0.0);
    CHECK(std::isinf(sd("1e99999999999", NULL)));
    CHECK(sd("0e99999", NULL) == 0.0);
}

/* ================================================================== scanf */
TEST(fmt_scan_int)
{
    SROW("123", "%d", 1, "i", "123");
    SROW("-123", "%d", 1, "i", "-123");
    SROW("+5", "%d", 1, "i", "5");
    SROW("  12abc", "%d", 1, "i", "12");
    SROW("abc", "%d", 0, "i", "-77777");
    SROW("", "%d", EOF, "i", "-77777");                    /* wine scanf.c */
    SROW("   ", "%d", EOF, "i", "-77777");
    SROW("x", "%d", 0, "i", "-77777");
    SROW("-", "%d", 0, "i", "-77777");
    SROW("017", "%i", 1, "i", "15");                       /* wine scanf.c */
    SROW("0x17", "%i", 1, "i", "23");                      /* wine scanf.c */
    SROW("-1", "%i", 1, "i", "-1");                        /* wine scanf.c */
    SROW("123", "%i", 1, "i", "123");                      /* wine scanf.c */
    SROW("19", "%i", 1, "i", "19");
    SROW("08", "%i", 1, "i", "0");                         /* octal 0, the 8 stays */
    SROW("ff", "%x", 1, "i", "255");
    SROW("FF", "%X", 1, "i", "255");
    SROW("0x519", "%x", 1, "i", "1305");                   /* wine scanf.c */
    SROW("0x51a", "%x", 1, "i", "1306");                   /* wine scanf.c */
    SROW("0x51g", "%x", 1, "i", "81");                     /* wine scanf.c */
    SROW("-1", "%x", 1, "i", "-1");                        /* wine scanf.c */
    SROW("-1", "%o", 1, "i", "-1");                        /* wine scanf.c */
    SROW("-1", "%u", 1, "i", "-1");                        /* wine scanf.c */
    SROW("17", "%o", 1, "i", "15");
    SROW("4294967295", "%u", 1, "i", "-1");
    SROW("12 34", "%d %d", 2, "ii", "12|34");
    SROW("12 34", "%d%d", 2, "ii", "12|34");
    SROW("12:34", "%d:%d", 2, "ii", "12|34");
    SROW("18:59", "%d:%d%c", 2, "iic", "18|59|ZZZZ");      /* wine scanf.c: %c at the end of input fails */
    SROW("5 x", "%d %d", 1, "ii", "5|-77777");
    SROW("5", "%d %d", 1, "ii", "5|-77777");
    SROW("12345", "%3d%d", 2, "ii", "123|45");
    SROW("12345", "%2i", 1, "i", "12");
    SROW("1 2 3", "%*d %d %d", 2, "ii", "2|3");
    SROW("100%", "%d%%", 1, "i", "100");
    SROW("100 %", "%d %%", 1, "i", "100");
    SROW("a1", "a%d", 1, "i", "1");
    SROW("b1", "a%d", 0, "i", "-77777");
    SROW("", "a%d", EOF, "i", "-77777");
    /* length modifiers */
    SROW("12345678", "%hd", 1, "h", "dead614e");           /* wine scanf.c: 16 bits written */
    SROW("12345678", "%02hd", 1, "h", "dead000c");         /* wine scanf.c */
    SROW("12345678", "%h02d", 1, "h", "dead000c");         /* wine scanf.c */
    SROW("12345678", "%000h02d", 1, "h", "dead000c");      /* wine scanf.c */
    SROW("12345678", "%2h0d", 1, "h", "dead614e");         /* wine scanf.c: the width is 20 */
    SROW("12345678", "%hhd", 1, "h", "00bc614e");          /* wine scanf.c: hh writes the whole int */
    SROW("12345678", "%hu", 1, "h", "dead614e");
    SROW("12345678901234", "%lld", 1, "I", "12345678901234");   /* wine scanf.c */
    SROW("-12345678901234", "%I64d", 1, "I", "-12345678901234");
    SROW("ffffffffff", "%I64x", 1, "I", "1099511627775");
    SROW("123456789", "%ld", 1, "i", "123456789");
    SROW("123456789", "%I32d", 1, "i", "123456789");
    /* %n */
    SROW("21:59:20", "%d%n", 1, "ii", "21|2");             /* wine scanf.c */
    SROW(":45", "%*c%n", 0, "i", "1");                     /* wine scanf.c */
    SROW("abc   def", "%s %n%s", 2, "sis", "abc|6|def");   /* wine scanf.c: %n is no conversion */
    SROW("3:45", "%d:%d%n", 2, "iii", "3|45|4");           /* wine scanf.c */
    SROW("abc123def", "%[a-z]%d%n", 2, "sii", "abc|123|6");
    /* %p: hexadecimal digits only, no 0x */
    SROW("000000000046F170", "%p", 1, "p", "0046f170");    /* wine scanf.c */
    SROW("0046F171", "%p", 1, "p", "0046f171");            /* wine scanf.c */
    SROW("46F172", "%p", 1, "p", "0046f172");              /* wine scanf.c */
    SROW("0x46F173", "%p", 1, "p", "00000000");            /* wine scanf.c: "0" read, the x stops it */
    SROW("-46F174", "%p", 1, "p", "ffb90e8c");             /* wine scanf.c */
    SROW("+46F175", "%p", 1, "p", "0046f175");             /* wine scanf.c */
    SROW("1233", "%p", 1, "p", "00001233");                /* wine scanf.c */
    SROW("1234", "%P", 1, "p", "00001234");                /* wine scanf.c */
}

TEST(fmt_scan_float)
{
    SROW("1.5", "%f", 1, "f", "1.5");
    SROW("1.5", "%lf", 1, "d", "1.5");
    SROW("1.5", "%Lf", 1, "d", "1.5");                     /* wine scanf.c: L reads a double */
    SROW("1.5", "%e", 1, "f", "1.5");
    SROW("1.5", "%g", 1, "f", "1.5");
    SROW("1.5", "%E", 1, "f", "1.5");
    SROW("1.5", "%G", 1, "f", "1.5");
    SROW("-82.626700 27.760000", "%f%f", 2, "ff", "-82.6267014|27.7600002");   /* wine scanf.c */
    SROW("32.715000", "%lf", 1, "d", "32.715000000000003");   /* wine scanf.c */
    SROW("1.1e-30", "%lf", 1, "d", "1.0999999999999999e-30");  /* wine scanf.c */
    SROW("", "%lf", EOF, "d", "-1");                       /* wine scanf.c */
    SROW("abc", "%f", 0, "f", "-1");
    SROW(".5", "%f", 1, "f", "0.5");
    SROW("5.", "%f", 1, "f", "5");
    SROW("-.5e1", "%lf", 1, "d", "-5");
    SROW("1e", "%lf", 1, "d", "1");
    SROW("1e+", "%lf", 1, "d", "1");
    SROW("1.5xyz", "%lf", 1, "d", "1.5");
    SROW("2.5e1 q", "%lf %c", 2, "dc", "25|qZZZ");
    SROW("1d2", "%lf", 1, "d", "100");                     /* D / d exponent letters of the C runtime */
    SROW("12345", "%3lf", 1, "d", "123");
    SROW("0.1", "%lf", 1, "d", "0.10000000000000001");
    SROW("0.1", "%f", 1, "f", "0.100000001");
    SROW("1e308", "%lf", 1, "d", "1e+308");
    SROW("1e309", "%lf", 1, "d", "inf");
    SROW("4.9e-324", "%lf", 1, "d", "4.9406564584124654e-324");
    SROW("2.4703282292062327e-324", "%lf", 1, "d", "0");   /* just below half the least subnormal */
    SROW("2.4703282292062328e-324", "%lf", 1, "d", "4.9406564584124654e-324");
    SROW("9007199254740993", "%lf", 1, "d", "9007199254740992");   /* exact tie: to even */
    SROW("9007199254740995", "%lf", 1, "d", "9007199254740996");
    SROW("9007199254740993.0000000001", "%lf", 1, "d", "9007199254740994");
    SROW("16777217", "%f", 1, "f", "16777216");            /* binary32 rounded once from the decimal */
    SROW("16777219", "%f", 1, "f", "16777220");
    SROW("1e39", "%f", 1, "f", "inf");
    SROW("3.4028235e38", "%f", 1, "f", "3.40282347e+38");
    SROW("1e-46", "%f", 1, "f", "0");
    SROW("1.4e-45", "%f", 1, "f", "1.40129846e-45");
    SROW("5 6", "%*f %f", 1, "f", "6");
}

TEST(fmt_scan_string)
{
    SROW("test\n", "%s%c", 2, "sc", "test|\nZZZ");          /* wine scanf.c */
    SROW(" \t\n\n", "%s", EOF, "s", "");                   /* wine scanf.c */
    SROW("a", "%s%s", 1, "ss", "a|");                      /* wine scanf.c */
    SROW("  abc def", "%s", 1, "s", "abc");
    SROW("abcdef", "%3s", 1, "s", "abc");
    SROW("abc", "%c", 1, "c", "aZZZ");
    SROW("abc", "%3c", 1, "c", "abcZ");                    /* %c adds no terminator */
    SROW("ab", "%3c", 0, "c", "ZZZZ");
    SROW(" a", "%c", 1, "c", " ZZZ");                      /* wine scanf.c: %c does not skip white space */
    SROW("a", "%*c", 0, "", "");
    SROW(" St. Petersburg, Florida\n", "%*c%[^\n]", 1, "s", "St. Petersburg, Florida");   /* wine scanf.c */
    SROW("ab,cd", "%[^,],%s", 2, "ss", "ab|cd");
    SROW("abcefgdh", "%*[a-cg-e]%c", 1, "c", "dZZZ");      /* wine scanf.c: g-e is e-g */
    SROW("abcefgdh", "%*[a-cd-dg-e]%c", 1, "c", "hZZZ");   /* wine scanf.c */
    SROW("-123", "%[-0-9]", 1, "s", "-123");               /* wine scanf.c */
    SROW("-321", "%[0-9-]", 1, "s", "-321");               /* wine scanf.c */
    SROW("-4123", "%[1-2-4]", 1, "s", "-412");             /* wine scanf.c */
    SROW("-456123", "%[1-2-45-6]", 1, "s", "-45612");      /* wine scanf.c */
    SROW("]a]b", "%[]a]", 1, "s", "]a]");                  /* a ] first is a member */
    SROW("abc", "%[^b]", 1, "s", "a");
    SROW("bbb", "%[^b]", 0, "s", "");
    SROW("test=value\xda", "%[^=] = %[^;]", 2, "ss", "test|value\xda");   /* wine scanf.c */
    SROW("\x81\x82test", "\x81%\x82%s", 1, "s", "test");   /* wine scanf.c: '%' + a char matches the char */
    SROW("\"%12@", "%\"%%%d%@", 1, "i", "12");             /* wine scanf.c */
    SROW("a b", "%s %s", 2, "ss", "a|b");
    SROW("a  \t b", "a b", 0, "", "");
    SROW("ab", "a%*[x]b", 0, "", "");
    /* wide destinations */
    SROW("abc", "%ls", 1, "w", "abc");
    SROW("abc def", "%ws %s", 2, "ws", "abc|def");
    SROW("abc", "%S", 1, "w", "abc");                      /* %S is wide in the narrow function */
    SROW("abc", "%hS", 1, "s", "abc");
    SROW("abc", "%lc", 1, "w", "a");
    SROW("abc", "%C", 1, "w", "a");
    SROW("abc", "%2lc", 1, "w", "ab");
    SROW("a1b2", "%l[a-z]", 1, "w", "a");
    SROW("caf\xe9", "%ls", 1, "w", "caf\xe9");             /* "C" locale: bytes below 256 map to themselves */
}

/* the return value rules and the white space handling shared by all conversions */
TEST(fmt_scan_return)
{
    SROW("", "%s", EOF, "s", "");
    SROW("", "%c", EOF, "c", "ZZZZ");
    SROW("", "%d%d", EOF, "ii", "-77777|-77777");
    SROW("", "abc", EOF, "", "");
    SROW("x", "abc", 0, "", "");
    SROW("abc", "abc", 0, "", "");
    SROW("1", "%d%d", 1, "ii", "1|-77777");                /* input ends after one conversion: its count */
    SROW("1 x", "%d%d", 1, "ii", "1|-77777");              /* a mismatch: its count */
    SROW("12", "%n", 0, "i", "0");
    SROW("", "%n", 0, "i", "0");
    SROW("1\t\n 2", "%d %d", 2, "ii", "1|2");
    SROW("1 2", "%d\n%d", 2, "ii", "1|2");
}
