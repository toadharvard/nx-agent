/* test_msvcrt_exports.cpp - the msvcrt.dll imports of the lifted D3DX DLLs (platform/crt/msvcrt_exports.cpp):
 * every name resolves under "msvcrt.dll", and the MSVCRT.DLL contracts a host libc gets wrong hold. */
#include "s4_test.h"
#include "../../platform/win32/w32.h"

#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <sys/wait.h>
#include <unistd.h>

#include "fnvnx_iobuf.h"
#include "fnvnx_msvcrt.h"

extern "C" void *fnvnx_win32_lookup_export(const char *dll, const char *name);

namespace {
const char *const k_names[] = {
    "malloc", "calloc", "realloc", "free", "??2@YAPAXI@Z", "??3@YAXPAX@Z", "_strdup", "_iob", "__pioinfo",
    "__badioinfo", "fread", "fwrite", "fflush", "_write", "_lseeki64", "_fileno", "_isatty", "_tempnam", "memcpy",
    "memmove", "memset", "strchr", "strrchr", "strncmp", "strncpy", "_stricmp", "_strnicmp", "wcslen", "wcstombs",
    "wctomb", "isalnum", "isalpha", "isdigit", "isspace", "isxdigit", "isleadbyte", "iswalpha", "iswdigit",
    "iswpunct", "iswspace", "tolower", "toupper", "setlocale", "sprintf", "_snprintf", "_vsnprintf", "sscanf",
    "atof", "atoi", "atol", "strtod", "_itoa", "_ultoa", "qsort", "rand", "srand", "_strdate", "_strtime",
    "_initterm", "__dllonexit", "_onexit", "exit", "abort", "_amsg_exit", "_purecall", "_lock", "_unlock",
    "_errno", "getenv", "ceil", "floor", "modf", "frexp", "ldexp"};
const char *const k_l3[] = {"__CxxFrameHandler", "_CxxThrowException", "_except_handler3", "_setjmp3", "longjmp",
                            "_XcptFilter", "?terminate@@YAXXZ", "??1type_info@@UAE@XZ", "_CIsin", "_CIpow",
                            "_adjust_fdiv", "_clearfp", "_controlfp", "_fpclass", "_finite", "_isnan"};

void *X(const char *n) { return fnvnx_win32_lookup_export("msvcrt.dll", n); }
template <class F> F fn(const char *n) { return (F)X(n); }
typedef int (*vfmt_t)(char *, const char *, ...);
typedef int (*snp_t)(char *, size_t, const char *, ...);
typedef int (*sscanf_t)(const char *, const char *, ...);
typedef double (*strtod_t)(const char *, char **);
typedef int (*ctype_t)(int);

#define SP(buf, ...) fn<vfmt_t>("sprintf")(buf, __VA_ARGS__)
}

TEST(msvcrt_names_resolve)
{
    CHECK_EQ(sizeof k_names / sizeof *k_names, 74); /* the brief says 70; its list has 74 names */
    for (const char *n : k_names) {
        if (!X(n))
            fprintf(stderr, "  missing: %s\n", n);
        CHECK(X(n) != NULL);
    }
    for (const char *n : k_l3)
        CHECK(X(n) == NULL); /* provider L3 */
    CHECK(fnvnx_win32_lookup_export("MSVCRT.DLL", "malloc") == X("malloc"));
    CHECK(fnvnx_win32_lookup_export("kernel32.dll", "malloc") == NULL);
}

TEST(msvcrt_heap)
{
    auto m = fn<void *(*)(size_t)>("malloc");
    auto c = fn<void *(*)(size_t, size_t)>("calloc");
    auto r = fn<void *(*)(void *, size_t)>("realloc");
    auto fr = fn<void (*)(void *)>("free");
    auto nw = fn<void *(*)(size_t)>("??2@YAPAXI@Z");
    auto dl = fn<void (*)(void *)>("??3@YAXPAX@Z");
    void *a = m(0), *b = m(0);
    CHECK(a && b && a != b);
    CHECK(r(a, 0) == NULL); /* freed */
    void *p = r(NULL, 16);
    CHECK(p != NULL);
    memset(p, 7, 16);
    p = r(p, 64);
    CHECK(p && ((char *)p)[15] == 7);
    errno = 0;
    CHECK(c((size_t)1 << (sizeof(size_t) * 4), (size_t)1 << (sizeof(size_t) * 4)) == NULL);
    CHECK_EQ(errno, 12);
    errno = 0;
    CHECK(c((size_t)-1, 2) == NULL);
    CHECK_EQ(errno, 12);
    char *z = (char *)c(8, 4);
    CHECK(z && z[31] == 0 && z[0] == 0);
    void *n0 = nw(0);
    CHECK(n0 != NULL);
    dl(n0);
    fr(nw(10)); /* one heap */
    dl(m(10));
    dl(NULL);
    fr(NULL);
    fr(z);
    fr(p);
    fr(b);
}

TEST(msvcrt_iob_and_lowio)
{
    CreateDirectoryA("Data", NULL);
    _iobuf *iob = (_iobuf *)X("_iob");
    CHECK(iob == fnvnx_iob);
    CHECK_EQ(sizeof(fnvnx_iob) / sizeof(fnvnx_iob[0]), 20);
#if __SIZEOF_POINTER__ == 4
    static_assert(sizeof(struct _iobuf) == 32, "MSVCRT x86 FILE");
    static_assert(offsetof(struct _iobuf, _file) == 0x10 && offsetof(struct _iobuf, _tmpfname) == 0x1c, "layout");
#endif
    CHECK_EQ(iob[0]._file, 0);
    CHECK_EQ(iob[1]._file, 1);
    CHECK_EQ(iob[2]._file, 2);
    CHECK(iob[19]._flag == 0 && iob[19]._file == 0);
    auto fw = fn<size_t (*)(const void *, size_t, size_t, _iobuf *)>("fwrite");
    auto frd = fn<size_t (*)(void *, size_t, size_t, _iobuf *)>("fread");
    auto ff = fn<int (*)(_iobuf *)>("fflush");
    auto fno = fn<int (*)(_iobuf *)>("_fileno");
    _iobuf *f = fnvnx_iob_fopen("Data\\crtexp.bin", "wb");
    CHECK(f != NULL);
    CHECK_EQ(fw("abcdefgh", 2, 4, f), 4);
    CHECK_EQ(ff(f), 0);
    int fd = fno(f);
    CHECK_EQ(fd, f->_file);
    CHECK(fd >= 3);
    struct ioinfo { uint32_t osfhnd; unsigned char osfile; char pipech; unsigned char pad[2]; };
    static_assert(sizeof(ioinfo) == 8, "MSVCRT.DLL ioinfo");
    ioinfo **pio = (ioinfo **)X("__pioinfo");
    CHECK(pio[0] != NULL);
    CHECK(pio[0][1].osfile & 0x01);   /* FOPEN */
    CHECK(pio[0][1].osfile & 0x40);   /* FDEV: stdout */
    CHECK(pio[fd >> 5][fd & 31].osfile & 0x01);
    CHECK(!(pio[fd >> 5][fd & 31].osfile & 0x40));
    CHECK(!(pio[fd >> 5][fd & 31].osfile & 0x80));   /* FTEXT: opened "wb" */
    auto isatty_ = fn<int (*)(int)>("_isatty");
    CHECK(isatty_(1) != 0 && isatty_(fd) == 0 && isatty_(999) == 0 && isatty_(-1) == 0);
    auto lsk = fn<long long (*)(int, long long, int)>("_lseeki64");
    CHECK_EQ(lsk(fd, 0, 1), 8);
    CHECK_EQ(lsk(fd, 2, 0), 2);
    auto wr = fn<int (*)(int, const void *, unsigned)>("_write");
    CHECK_EQ(wr(fd, "XY", 2), 2);
    fnvnx_iob_fclose(f);
    CHECK(!(pio[fd >> 5][fd & 31].osfile & 0x01));
    f = fnvnx_iob_fopen("Data\\crtexp.bin", "rb");
    char buf[16] = {0};
    CHECK_EQ(frd(buf, 1, 12, f), 8);
    CHECK_STR(buf, "abXYefgh");
    fnvnx_iob_fclose(f);
    ioinfo *bad = (ioinfo *)X("__badioinfo");
    CHECK(bad->osfhnd == 0xFFFFFFFFu && !(bad->osfile & 1));
    auto tn = fn<char *(*)(const char *, const char *)>("_tempnam");
    char *t = tn("Data", "pre");
    CHECK(t && strstr(t, "pre") != NULL);
    char *t2 = tn("Data", "pre");
    CHECK(t2 && strcmp(t, t2) != 0);
    free(t);
    free(t2);
}

TEST(msvcrt_strings)
{
    char b[16];
    CHECK_STR(fn<char *(*)(const char *, int)>("strchr")("abc", 'c'), "c");
    CHECK(*fn<char *(*)(const char *, int)>("strchr")("abc", 0) == 0);
    CHECK_STR(fn<char *(*)(const char *, int)>("strrchr")("abcabc", 'b'), "bc");
    CHECK(fn<int (*)(const char *, const char *, size_t)>("strncmp")("abc", "abd", 2) == 0);
    memset(b, 'x', sizeof b);
    fn<char *(*)(char *, const char *, size_t)>("strncpy")(b, "ab", 5);
    CHECK(b[2] == 0 && b[4] == 0 && b[5] == 'x');
    CHECK(fn<int (*)(const char *, const char *)>("_stricmp")("ABC", "abc") == 0);
    CHECK(fn<int (*)(const char *, const char *, size_t)>("_strnicmp")("ABx", "aby", 2) == 0);
    const WCHAR w[] = {'h', 'i', 0x263A, 0};
    CHECK_EQ(fn<size_t (*)(const WCHAR *)>("wcslen")(w), 3);
    char *d = fn<char *(*)(const char *)>("_strdup")("dup");
    CHECK_STR(d, "dup");
    free(d);
    char s[8] = "abcdef";
    fn<void *(*)(void *, const void *, size_t)>("memmove")(s + 1, s, 5);
    CHECK_STR(s, "aabcde");
    /* itoa family */
    auto itoa_ = fn<char *(*)(int, char *, int)>("_itoa");
    auto ultoa_ = fn<char *(*)(unsigned long, char *, int)>("_ultoa");
    char o[40];
    CHECK_STR(itoa_(-255, o, 10), "-255");
    CHECK_STR(itoa_(-255, o, 16), "ffffff01");
    CHECK_STR(itoa_(255, o, 36), "73");
    CHECK_STR(itoa_(5, o, 2), "101");
    CHECK_STR(ultoa_(4000000000ul, o, 10), "4000000000");
    CHECK_STR(ultoa_(0xabcdef, o, 16), "abcdef");
    o[0] = 'q';
    itoa_(5, o, 1);
    CHECK_STR(o, ""); /* invalid radix: empty string */
    /* atoi family: MSVCRT's atox.c accumulates in a 32-bit long without a check (wraps) */
    CHECK_EQ(fn<int (*)(const char *)>("atoi")("  -42x"), -42);
    CHECK_EQ(fn<int (*)(const char *)>("atoi")("4294967296"), 0);
    CHECK_EQ(fn<int (*)(const char *)>("atoi")("2147483648"), INT32_MIN);
    CHECK_EQ(fn<int (*)(const char *)>("atoi")("+7"), 7);
    CHECK_EQ((int)fn<long (*)(const char *)>("atol")("123abc"), 123);
}

TEST(msvcrt_printf)
{
    char b[128];
    SP(b, "%I64d|%I64u|%I64x", -5ll, 18446744073709551615ull, 0xabcdef123456ll);
    CHECK_STR(b, "-5|18446744073709551615|abcdef123456");
    SP(b, "%I32d %lld %llu", -7, -8ll, 9ull);
    CHECK_STR(b, "-7 -8 9");
    SP(b, "%hs|%ls|%S|%C|%c", "n", L"wide", L"WS", (int)L'W', 'c');
    CHECK_STR(b, "n|wide|WS|W|c");
    SP(b, "%p|%p", (void *)(uintptr_t)0xbeef12, (void *)(uintptr_t)0xa);
    CHECK_STR(b, "00BEEF12|0000000A");
    SP(b, "%e|%E|%g|%G", 1.0, 12345.678, 1e-5, 1e20);
    CHECK_STR(b, "1.000000e+000|1.234568E+004|1e-005|1E+020");
    SP(b, "%10.2e|%-12e|%012.1e", 1.0, 1.0, -2.0);
    CHECK_STR(b, " 1.00e+000|1.000000e+000|-0002.0e+000");
    SP(b, "%f|%.3f|%5.1f|%+d|% d|%#x|%#o|%05d|%-5d|", 1.5, 2.0 / 3, 3.14159, 5, 5, 255, 8, 42, 42);
    CHECK_STR(b, "1.500000|0.667|  3.1|+5| 5|0xff|010|00042|42   |");
    int n = -1;
    SP(b, "ab%ncd", &n);
    CHECK_STR(b, "abcd");
    CHECK_EQ(n, 2);
    SP(b, "%zd|%jd|%td", 1, 2, 3); /* the letter is printed; no argument consumed */
    CHECK_STR(b, "zd|jd|td");
    SP(b, "%s|%.2s|%*d|%-*d|%.*f", "str", "abcdef", 4, 7, 4, 7, 1, 2.55);
    CHECK_STR(b, "str|ab|   7|7   |2.5");
    SP(b, "%s %%", (const char *)NULL);
    CHECK_STR(b, "(null) %");
    SP(b, "%hd %hu", 65537, 65537);
    CHECK_STR(b, "1 1");
    SP(b, "%x %X %o %u", -1, 0xabc, 64, -1);
    CHECK_STR(b, "ffffffff ABC 100 4294967295");
    SP(b, "%.0f|%.0e", 0.4, 26.0);
    CHECK_STR(b, "0|3e+001");
    SP(b, "%f", HUGE_VAL);
    CHECK_STR(b, "1.#INF00");
    SP(b, "%f", -HUGE_VAL);
    CHECK_STR(b, "-1.#INF00");
    SP(b, "%f", NAN);
    CHECK(!strncmp(b, "1.#QNAN", 7) || !strncmp(b, "-1.#IND", 7));
    /* _snprintf / _vsnprintf */
    auto sn = fn<snp_t>("_snprintf");
    char t[8];
    memset(t, 'z', sizeof t);
    CHECK_EQ(sn(t, 4, "abcdef"), -1);
    CHECK(memcmp(t, "abcdzzzz", 8) == 0); /* truncated: no terminator */
    memset(t, 'z', sizeof t);
    CHECK_EQ(sn(t, 4, "abcd"), 4);
    CHECK(memcmp(t, "abcdzzzz", 8) == 0); /* exactly full: count, no terminator */
    memset(t, 'z', sizeof t);
    CHECK_EQ(sn(t, 5, "abcd"), 4);
    CHECK(memcmp(t, "abcd\0zzz", 8) == 0);
    CHECK_EQ(sn(t, 0, "abc"), -1);
    CHECK_EQ(sn(NULL, 0, "abc"), -1);
    auto vs = fn<int (*)(char *, size_t, const char *, va_list)>("_vsnprintf");
    auto call_v = [&](char *buf, size_t sz, const char *f, ...) {
        va_list ap;
        va_start(ap, f);
        int r = vs(buf, sz, f, ap);
        va_end(ap);
        return r;
    };
    CHECK_EQ(call_v(t, 8, "%d-%s", 12, "ab"), 5);
    CHECK_STR(t, "12-ab");
    char big[600];
    SP(big, "%s", std::string(500, 'q').c_str());
    CHECK_EQ(strlen(big), 500);
}

/* float rounding of MSVCRT.DLL as the reference implements it: the exact decimal expansion, ties away from zero */
TEST(msvcrt_printf_rounding)
{
    char b[160];
    SP(b, "%.0f|%.0f|%.0f|%.0f|%.2f|%.1f", 0.5, 1.5, 2.5, -2.5, 0.125, 0.25);
    CHECK_STR(b, "1|2|3|-3|0.13|0.3");
    SP(b, "%.3f|%.0f|%.1f|%f", 2.0005, 0.49999999999999994, 0.05, 1e22);
    CHECK_STR(b, "2.001|0|0.1|10000000000000000000000.000000");
    SP(b, "%.0e|%.1e|%.0e|%e", 2.5, 0.25, 9.5, 0.0);
    CHECK_STR(b, "3e+000|2.5e-001|1e+001|0.000000e+000");
    SP(b, "%g|%g|%g|%g|%g|%.3g|%.2g|%#g", 100000.0, 1e6, 0.0001, 0.00001, 0.0, 2.5, 0.125, 1.0);
    CHECK_STR(b, "100000|1e+006|0.0001|1e-005|0|2.5|0.13|1.00000");
    SP(b, "%.0f|%.1f|%.3e|%g", 999.5, 9.95, 9.9995, 999999.5);
    CHECK_STR(b, "1000|9.9|9.999e+000|1e+006");
    SP(b, "%.20f|%.0f", 0.1, 5e-324);
    CHECK_STR(b, "0.10000000000000000555|0");
}

TEST(msvcrt_sscanf)
{
    auto ss = fn<sscanf_t>("sscanf");
    long long ll = 0;
    int a = 0, nn = 0, c = 0;
    char s[16] = {0}, s2[16] = {0};
    double d = 0;
    CHECK_EQ(ss("-123456789012 x", "%I64d", &ll), 1);
    CHECK(ll == -123456789012ll);
    CHECK_EQ(ss("abc123def", "%[a-z]%d%n", s, &a, &nn), 2);
    CHECK_STR(s, "abc");
    CHECK_EQ(a, 123);
    CHECK_EQ(nn, 6);
    CHECK_EQ(ss("ab,cd", "%[^,],%s", s, s2), 2);
    CHECK_STR(s2, "cd");
    CHECK_EQ(ss("1 2 3", "%*d %d %d", &a, &nn), 2);
    CHECK(a == 2 && nn == 3);
    CHECK_EQ(ss("", "%d", &a), EOF);
    CHECK_EQ(ss("   ", "%d", &a), EOF);
    CHECK_EQ(ss("x", "%d", &a), 0);
    CHECK_EQ(ss("7 x", "%d %d", &a, &nn), 1);
    CHECK_EQ(ss("0x1F 017 12", "%i %i %i", &a, &nn, &c), 3);
    CHECK(a == 31 && nn == 15 && c == 12);
    CHECK_EQ(ss("2.5e1 q", "%lf %c", &d, s), 2);
    CHECK(d == 25.0 && s[0] == 'q');
    CHECK_EQ(ss("ff", "%x", &a), 1);
    CHECK_EQ(a, 255);
    CHECK_EQ(ss("abcdef", "%3s", s), 1);
    CHECK_STR(s, "abc");
    CHECK_EQ(ss("100%", "%d%%", &a), 1);
}

TEST(msvcrt_strtod)
{
    auto sd = fn<strtod_t>("strtod");
    auto af = fn<double (*)(const char *)>("atof");
    char *e;
    errno = 0;
    CHECK(sd("1d2", &e) == 100.0 && *e == 0);
    CHECK(sd("1D-2", &e) == 0.01);
    CHECK(sd("  -0.5e1x", &e) == -5.0 && *e == 'x');
    const char *h = "0x10";
    CHECK(sd(h, &e) == 0.0 && e == h + 1); /* "0" is parsed, the x is not */
    const char *i = "inf";
    CHECK(sd(i, &e) == 0.0 && e == i);
    const char *nn = "nan";
    CHECK(sd(nn, &e) == 0.0 && e == nn);
    const char *w = "  abc";
    CHECK(sd(w, &e) == 0.0 && e == w);
    const char *ex = "1e+";
    CHECK(sd(ex, &e) == 1.0 && e == ex + 1);
    CHECK(sd(".5", &e) == 0.5 && sd("5.", &e) == 5.0 && *e == 0);
    CHECK(signbit(sd("-0", NULL)));
    CHECK(sd("2.2250738585072011e-308", NULL) == 2.2250738585072009e-308);   /* a subnormal-boundary case */
    CHECK(sd("0.1", NULL) == 0.1);
    CHECK(sd("9007199254740993", NULL) == 9007199254740992.0);   /* 2^53+1: ties to even */
    CHECK(sd("9007199254740993.0000000000000000000000000001", NULL) == 9007199254740994.0);
    CHECK(sd("1.00000000000000011102230246251565404236316680908203125", NULL) == 1.0);   /* exact tie */
    CHECK(sd("1.00000000000000011102230246251565404236316680908203126", NULL) == 1.0000000000000002);
    CHECK(sd("4.9406564584124654e-324", NULL) == 4.9406564584124654e-324);
    errno = 0;
    CHECK(sd("1e400", NULL) == HUGE_VAL && errno == 34);
    errno = 0;
    CHECK(sd("-1e400", NULL) == -HUGE_VAL && errno == 34);
    errno = 0;
    CHECK(sd("1e-400", NULL) == 0.0 && errno == 34);
    errno = 0;
    CHECK(sd("0e-400", NULL) == 0.0 && errno == 0);
    errno = 0;
    CHECK(sd("4e-320", NULL) > 0.0 && errno == 0);   /* subnormal result: no error */
    std::string longm = "0." + std::string(400, '0') + "1e400";
    CHECK(sd(longm.c_str(), NULL) == 0.1);
    CHECK(af("3.25") == 3.25 && af("1d1") == 10.0);
}

TEST(msvcrt_ctype_locale)
{
    auto sl = fn<char *(*)(int, const char *)>("setlocale");
    CHECK_STR(sl(0, NULL), "C");
    CHECK_STR(sl(0, "C"), "C");
    auto isal = fn<ctype_t>("isalpha"), isalnum_ = fn<ctype_t>("isalnum"), isd = fn<ctype_t>("isdigit"),
         isp = fn<ctype_t>("isspace"), isx = fn<ctype_t>("isxdigit"), lead = fn<ctype_t>("isleadbyte"),
         lo = fn<ctype_t>("tolower"), up = fn<ctype_t>("toupper");
    for (int c = -1; c <= 255; c++) {
        bool ascii = c >= 0 && c < 128;
        CHECK_EQ(isal(c) != 0, ascii && isalpha(c));
        CHECK_EQ(isalnum_(c) != 0, ascii && isalnum(c));
        CHECK_EQ(isd(c) != 0, ascii && isdigit(c));
        CHECK_EQ(isp(c) != 0, ascii && isspace(c));
        CHECK_EQ(isx(c) != 0, ascii && isxdigit(c));
        CHECK_EQ(lead(c), 0);
    }
    CHECK(!isal(-2) && !isal(256) && !isal(100000) && !isal(INT32_MIN) && !isal(INT32_MAX));
    CHECK_EQ(lo('Q'), 'q');
    CHECK_EQ(up('q'), 'Q');
    CHECK_EQ(lo(0xC9), 0xC9);
    CHECK_EQ(lo(-1), -1);
    CHECK_EQ(lo(1000), 1000);
    auto wa = fn<ctype_t>("iswalpha"), wd = fn<ctype_t>("iswdigit"), wp = fn<ctype_t>("iswpunct"),
         ws = fn<ctype_t>("iswspace");
    CHECK(wa('a') && wa(0x00E9) && wa(0x3042) && !wa('1') && !wa(0xFFFF));
    CHECK(wd('7') && !wd('x'));
    CHECK(wp('!') && wp(',') && !wp('a'));
    CHECK(ws(' ') && ws('\t') && ws(0x3000) && !ws('a'));
    /* the narrow functions in the 1252 locale agree with the NLS tables */
    CHECK_STR(sl(0, ""), "English_United States.1252");
    CHECK_STR(sl(0, NULL), "English_United States.1252");
    CHECK(isal(0xE9) && isal(0xC9) && !isal(0xD7) && !isal(0xA0));
    CHECK(isp(0xA0) || !isp(0xA0)); /* NLS decides */
    for (int c = 0; c < 256; c++) {
        WCHAR u;
        WORD t = 0;
        char ch = (char)c;
        MultiByteToWideChar(1252, 0, &ch, 1, &u, 1);
        GetStringTypeW(CT_CTYPE1, &u, 1, &t);
        CHECK_EQ(isd(c) != 0, (t & C1_DIGIT) != 0);
    }
    CHECK_EQ(lo(0xC9), 0xE9);
    CHECK_EQ(up(0xE9), 0xC9);
    CHECK_EQ(lead(0x81), 0);
    char dp[32];
    SP(dp, "%.1f", 1.5);
    CHECK_STR(dp, "1.5"); /* decimal point stays '.' */
    CHECK(sl(0, "xx_YY") == NULL);
    CHECK_STR(sl(0, NULL), "English_United States.1252"); /* unchanged */
    CHECK(sl(99, "C") == NULL);
    CHECK_STR(sl(0, "C"), "C");
    CHECK(!isal(0xE9) && lo(0xC9) == 0xC9);
    /* wcstombs / wctomb in "C": chars above 255 fail */
    auto wcs = fn<size_t (*)(char *, const WCHAR *, size_t)>("wcstombs");
    auto wct = fn<int (*)(char *, WCHAR)>("wctomb");
    const WCHAR ok[] = {'a', 0xE9, 0};
    const WCHAR bad[] = {'a', 0x263A, 0};
    char o[8] = {0};
    CHECK_EQ(wcs(o, ok, 8), 2);
    CHECK(o[0] == 'a' && (unsigned char)o[1] == 0xE9 && o[2] == 0);
    CHECK_EQ(wcs(NULL, ok, 0), 2);
    errno = 0;
    CHECK_EQ((int)wcs(o, bad, 8), -1);
    CHECK_EQ(errno, 42);
    CHECK_EQ(wcs(o, ok, 1), 1);
    CHECK_EQ(wct(o, 'z'), 1);
    errno = 0;
    CHECK_EQ(wct(o, 0x263A), -1);
    CHECK_EQ(errno, 42);
    CHECK_EQ(wct(NULL, 'z'), 0);
    sl(0, "");
    const WCHAR eu[] = {0x20AC, 0};
    CHECK_EQ(wcs(o, eu, 8), 1);
    CHECK_EQ((unsigned char)o[0], 0x80);
    errno = 0;
    CHECK_EQ((int)wcs(o, bad, 8), -1);
    sl(0, "C");
}

TEST(msvcrt_qsort_rand_time)
{
    auto qs = fn<void (*)(void *, size_t, size_t, int (*)(const void *, const void *))>("qsort");
    qs(NULL, 0, 4, NULL);
    int v[300];
    for (int i = 0; i < 300; i++)
        v[i] = (i * 7919) % 301;
    qs(v, 300, sizeof(int), [](const void *a, const void *b) { return *(const int *)a - *(const int *)b; });
    bool sorted = true;
    for (int i = 1; i < 300; i++)
        sorted &= v[i - 1] <= v[i];
    CHECK(sorted);
    int inc[50] = {0};   /* an inconsistent comparator must not run out of bounds (ASAN-free check: must return) */
    qs(inc, 50, sizeof(int), [](const void *, const void *) { return 1; });
    int sorted_in[1000];
    for (int i = 0; i < 1000; i++)
        sorted_in[i] = i;
    qs(sorted_in, 1000, sizeof(int), [](const void *a, const void *b) { return *(const int *)a - *(const int *)b; });
    CHECK(sorted_in[0] == 0 && sorted_in[999] == 999);
    size_t W = 65536;
    unsigned char *blk = (unsigned char *)malloc(W * 3);
    memset(blk, 3, W); memset(blk + W, 1, W); memset(blk + 2 * W, 2, W);
    qs(blk, 3, W, [](const void *a, const void *b) { return (int)*(const unsigned char *)a - (int)*(const unsigned char *)b; });
    CHECK(blk[0] == 1 && blk[W - 1] == 1 && blk[W] == 2 && blk[3 * W - 1] == 3);
    free(blk);
    /* rand: MSVCRT LCG */
    auto sr = fn<void (*)(unsigned)>("srand");
    auto rd = fn<int (*)()>("rand");
    CHECK(rd() >= 0);
    sr(1);
    CHECK_EQ(rd(), 41);
    CHECK_EQ(rd(), 18467);
    CHECK_EQ(rd(), 6334);
    sr(0);
    CHECK_EQ(rd(), 38);
    /* _strdate / _strtime */
    char d[16], t[16];
    fn<char *(*)(char *)>("_strdate")(d);
    fn<char *(*)(char *)>("_strtime")(t);
    CHECK(strlen(d) == 8 && d[2] == '/' && d[5] == '/' && isdigit(d[0]) && isdigit(d[7]));
    CHECK(strlen(t) == 8 && t[2] == ':' && t[5] == ':' && isdigit(t[0]) && isdigit(t[7]));
}

namespace {
int g_order[8], g_n;
void h1(void) { g_order[g_n++] = 1; }
void h2(void) { g_order[g_n++] = 2; }
int oh(void) { return 0; }
/* run f in a child; returns its exit status, and its stderr text */
int child(void (*f)(), std::string *err = nullptr, std::string *out = nullptr)
{
    int pe[2], po[2];
    pipe(pe);
    pipe(po);
    fflush((FILE *)NULL);
    pid_t p = fork();
    if (!p) {
        dup2(pe[1], 2);
        dup2(po[1], 1);
        f();
        _exit(99);
    }
    close(pe[1]);
    close(po[1]);
    auto rd = [](int fd, std::string *s) {
        char b[256];
        ssize_t n;
        while ((n = read(fd, b, sizeof b)) > 0)
            if (s)
                s->append(b, n);
        close(fd);
    };
    rd(pe[0], err);
    rd(po[0], out);
    int st = 0;
    waitpid(p, &st, 0);
    return WIFEXITED(st) ? WEXITSTATUS(st) : -1000 - WTERMSIG(st);
}
}

TEST(msvcrt_process)
{
    auto it = fn<void (*)(void (**)(), void (**)())>("_initterm");
    void (*tab[4])() = {h1, NULL, h2, h1};
    g_n = 0;
    it(tab, tab + 4);
    CHECK(g_n == 3 && g_order[0] == 1 && g_order[1] == 2 && g_order[2] == 1);
    it(tab, tab);
    CHECK_EQ(g_n, 3);
    /* __dllonexit grows the caller's table on the msvcrt heap */
    typedef int (*pf)(void);
    auto dx = fn<pf (*)(pf, pf **, pf **)>("__dllonexit");
    pf *b0 = (pf *)malloc(1), *e0 = b0;
    pf *b = b0, *e = e0;
    CHECK(dx(oh, &b, &e) == oh);
    CHECK(e == b + 1 && b[0] == oh);
    pf oh2 = (pf)h2;
    CHECK(dx(oh2, &b, &e) == oh2);
    CHECK(e == b + 2 && b[0] == oh && b[1] == oh2);
    CHECK(dx(oh, NULL, &e) == NULL);
    pf *nb = NULL, *ne = NULL;
    CHECK(dx(oh, &nb, &ne) == oh && ne == nb + 1);
    free(b);
    free(nb);
    /* locks */
    auto lk = fn<void (*)(int)>("_lock"), ul = fn<void (*)(int)>("_unlock");
    for (int i = 0; i < 40; i++) {
        lk(i);
        lk(i);
        ul(i);
        ul(i);
    }
    CHECK(fn<int *(*)()>("_errno")() == &errno);
    /* getenv */
    SetEnvironmentVariableA("FnvCrtTest", "val1");
    auto ge = fn<char *(*)(const char *)>("getenv");
    CHECK_STR(ge("FNVCRTTEST"), "val1");
    CHECK_STR(ge("fnvcrttest"), "val1");
    CHECK(ge("FnvNoSuchVar") == NULL);
    SetEnvironmentVariableA("FnvCrtTest", "second");
    CHECK_STR(ge("FnvCrtTest"), "second");
    CHECK(ge("") == NULL && ge(NULL) == NULL);
    /* termination, in children */
    std::string err;
    CHECK_EQ(child([] { fn<void (*)()>("abort")(); }, &err), 3);
    CHECK(err.find("abnormal program termination") != std::string::npos);
    err.clear();
    CHECK_EQ(child([] { fn<void (*)(int)>("_amsg_exit")(8); }, &err), 255);
    CHECK(err.find("R6008") != std::string::npos);
    err.clear();
    CHECK_EQ(child([] { fn<void (*)()>("_purecall")(); }, &err), 255);
    CHECK(err.find("R6025") != std::string::npos);
    std::string o;
    CHECK_EQ(child([] {
                 auto oe = fn<void *(*)(void (*)())>("_onexit");
                 oe([] { write(1, "A", 1); });
                 oe([] { write(1, "B", 1); });
                 oe([] { write(1, "C", 1); fn<void *(*)(void (*)())>("_onexit")([] { write(1, "D", 1); }); });
                 fn<void (*)(int)>("exit")(7);
             }, nullptr, &o), 7);
    CHECK_STR(o.c_str(), "CDBA");
}

TEST(msvcrt_math)
{
    auto ce = fn<double (*)(double)>("ceil"), fl = fn<double (*)(double)>("floor");
    auto mf = fn<double (*)(double, double *)>("modf");
    auto fx = fn<double (*)(double, int *)>("frexp");
    auto ld = fn<double (*)(double, int)>("ldexp");
    CHECK(ce(-0.5) == 0.0 && signbit(ce(-0.5)));
    CHECK(ce(1.2) == 2.0 && fl(-1.2) == -2.0 && fl(1.7) == 1.0);
    CHECK(isinf(ce(INFINITY)) && isinf(fl(-INFINITY)) && isnan(ce(NAN)));
    CHECK(signbit(fl(-0.0)) && ce(4.9e-324) == 1.0 && fl(-4.9e-324) == -1.0);
    double ip;
    double fr = mf(-3.25, &ip);
    CHECK(fr == -0.25 && ip == -3.0);
    fr = mf(-0.0, &ip);
    CHECK(signbit(fr) && signbit(ip));
    fr = mf(INFINITY, &ip);
    CHECK(fr == 0.0 && isinf(ip) && !signbit(fr));
    fr = mf(NAN, &ip);
    CHECK(isnan(fr) && isnan(ip));
    int e = 99;
    CHECK(fx(8.0, &e) == 0.5 && e == 4);
    CHECK(fx(0.0, &e) == 0.0 && e == 0);
    CHECK(signbit(fx(-0.0, &e)) && e == 0);
    CHECK(fx(4.9406564584124654e-324, &e) == 0.5 && e == -1073);
    e = 99;
    CHECK(isinf(fx(INFINITY, &e)) && e == 0);
    e = 99;
    CHECK(isnan(fx(NAN, &e)) && e == 0);
    errno = 0;
    CHECK(ld(1.0, 1024) == HUGE_VAL && errno == 34);
    errno = 0;
    CHECK(ld(-1.0, 5000) == -HUGE_VAL && errno == 34);
    errno = 0;
    CHECK(ld(1.0, -1074) == 4.9406564584124654e-324 && errno == 0);
    CHECK(ld(1.5, -1074) == 9.8813129168249309e-324);   /* 1.5 ulp: tie, to even (2) */
    CHECK(ld(1.0, -1075) == 0.0);                       /* tie to even: 0 */
    CHECK(ld(3.0, -1075) == 9.8813129168249309e-324);   /* 1.5 -> 2 (even) */
    CHECK(ld(0.5, 1) == 1.0 && ld(1.0, 0) == 1.0);
    CHECK(signbit(ld(-0.0, 10)) && ld(INFINITY, -3) == INFINITY && isnan(ld(NAN, 3)));
    errno = 0;
    CHECK(ld(INFINITY, 3) == INFINITY && errno == 0);
    CHECK(ld(1.0, INT32_MAX) == HUGE_VAL && ld(1.0, INT32_MIN) == 0.0);
}
