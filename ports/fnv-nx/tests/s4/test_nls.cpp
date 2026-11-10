/* test_nls.cpp - platform/win32/nls.cpp: code pages, MultiByteToWideChar/WideCharToMultiByte, GetCPInfo, locale
 * info, LCMapString, CompareString, GetStringType, locale enumeration. Environment: ACP 1252, OEM 437, en-US only. */
#include "s4_test.h"

#include "../../platform/win32/w32.h"

#define D(n, v) enum { n = v }
D(T_MB_COMPOSITE, 2); D(T_MB_GLYPH, 4); D(T_MB_ERR, 8);
D(T_WC_DISCARDNS, 0x10); D(T_WC_SEPCHARS, 0x20); D(T_WC_DEFAULTCHAR, 0x40); D(T_WC_ERR, 0x80);
D(T_WC_COMPOSITECHECK, 0x200); D(T_WC_NOBF, 0x400);
D(T_LOWER, 0x100); D(T_UPPER, 0x200); D(T_SORTKEY, 0x400); D(T_BYTEREV, 0x800); D(T_HIRA, 0x100000);
D(T_KATA, 0x200000); D(T_HALF, 0x400000); D(T_FULL, 0x800000);
D(T_IGNCASE, 1); D(T_IGNNS, 2); D(T_IGNSYM, 4); D(T_STRSORT, 0x1000); D(T_IGNKANA, 0x10000); D(T_IGNWIDTH, 0x20000);
#undef D
#ifndef LOCALE_INVARIANT
#define LOCALE_INVARIANT 0x007F
#endif
#define E_INSUF 122
#define LC 0x0409

static const WCHAR *U(const char *s, WCHAR *w) /* ASCII to WCHAR */
{
    int i = 0;
    for (; s[i]; i++)
        w[i] = (unsigned char)s[i];
    w[i] = 0;
    return w;
}

/* ---- code pages */
TEST(nls_code_pages)
{
    CHECK_EQ(GetACP(), 1252);
    CHECK_EQ(GetOEMCP(), 437);
    static const UINT ok[] = { 1252, 437, 65001, 65000, 20127, 28591, 10000, 0, 1, 2, 3, 42 };
    for (UINT cp : ok)
        CHECK(IsValidCodePage(cp));
    static const UINT bad[] = { 932, 1250, 850, 936, 12000, 4, 41, 65002, 0xFFFFFFFFu };
    for (UINT cp : bad)
        CHECK(!IsValidCodePage(cp));
    CPINFO ci;
    static const struct { UINT cp, max; } mx[] = { { 1252, 1 }, { 437, 1 }, { 10000, 1 }, { 20127, 1 }, { 28591, 1 },
        { 42, 1 }, { 65001, 4 }, { 65000, 5 }, { 0, 1 }, { 1, 1 }, { 2, 1 }, { 3, 1 } };
    for (auto &m : mx) {
        memset(&ci, 0x55, sizeof ci);
        CHECK(GetCPInfo(m.cp, &ci));
        CHECK_EQ(ci.MaxCharSize, m.max);
        CHECK_EQ(ci.DefaultChar[0], '?');
        CHECK_EQ(ci.DefaultChar[1], 0);
        for (int i = 0; i < 12; i++)
            CHECK_EQ(ci.LeadByte[i], 0);
    }
    SetLastError(0);
    CHECK(!GetCPInfo(932, &ci));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK(!GetCPInfo(1252, NULL));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
}

/* ---- MultiByteToWideChar */
TEST(nls_mb2wc_protocol)
{
    WCHAR w[16];
    char b[16] = "abc";
    SetLastError(0);
    CHECK_EQ(MultiByteToWideChar(1252, 0, b, -1, w, 3), 0);
    CHECK_EQ(GetLastError(), E_INSUF);
    CHECK_EQ(MultiByteToWideChar(1252, 0, b, 3, NULL, 0), 3);
    CHECK_EQ(MultiByteToWideChar(1252, 0, b, -1, NULL, 0), 4);
    CHECK_EQ(MultiByteToWideChar(1252, 0, b, -1, w, 4), 4);
    static const struct { UINT cp; DWORD fl; int err; } t[] = {
        { 1252, 2 | 1, 1004 }, { 1252, 0x100, 1004 }, { 1252, 0x10, 1004 }, { 65001, 1, 1004 }, { 65001, 4, 1004 },
        { 65000, 8, 1004 }, { 42, 1, 1004 }, { 1252, 8, 0 }, { 1252, 2, 0 }, { 437, 4, 0 }, { 65001, 8, 0 },
        { 932, 0, 87 }, { 65002, 0, 87 }, { 1252, 0, 0 } };
    for (auto &c : t) {
        SetLastError(0);
        int r = MultiByteToWideChar(c.cp, c.fl, b, 3, w, 16);
        CHECK_EQ(r != 0, c.err == 0);
        CHECK_EQ(GetLastError(), c.err);
    }
    static const struct { const char *s; int n, dn; int err; } p[] = {
        { NULL, -1, 8, 87 }, { "a", 0, 8, 87 }, { "a", -2, 8, 87 }, { "a", -1, -1, 87 }, { "a", -1, 8, 0 } };
    for (auto &c : p) {
        SetLastError(0);
        int r = MultiByteToWideChar(1252, 0, c.s, c.n, w, c.dn);
        CHECK_EQ(r != 0, c.err == 0);
        CHECK_EQ(GetLastError(), c.err);
    }
    SetLastError(0);
    CHECK_EQ(MultiByteToWideChar(1252, 0, "a", 1, NULL, 3), 0);   /* dst NULL with a size */
    CHECK_EQ(GetLastError(), 87);
    SetLastError(0);
    CHECK_EQ(MultiByteToWideChar(1252, 0, (const char *)w, 2, w, 8), 0);   /* src == dst */
    CHECK_EQ(GetLastError(), 87);
}

TEST(nls_mb2wc_pages)
{
    WCHAR w[8];
    CHECK_EQ(MultiByteToWideChar(437, 0, "\x80\xe1\xff", 3, w, 8), 3);
    CHECK_EQ(w[0], 0xC7); CHECK_EQ(w[1], 0xDF); CHECK_EQ(w[2], 0xA0);
    CHECK_EQ(MultiByteToWideChar(437, 0, "\x01\x7f", 2, w, 8), 2);
    CHECK_EQ(w[0], 1); CHECK_EQ(w[1], 0x7F);
    CHECK_EQ(MultiByteToWideChar(437, T_MB_GLYPH, "\x01\x1f\x7f", 3, w, 8), 3);
    CHECK_EQ(w[0], 0x263A); CHECK_EQ(w[1], 0x25BC); CHECK_EQ(w[2], 0x2302);
    CHECK_EQ(MultiByteToWideChar(10000, 0, "\x80\xdb\xf0", 3, w, 8), 3);
    CHECK_EQ(w[0], 0xC4); CHECK_EQ(w[1], 0xA4); CHECK_EQ(w[2], 0xF8FF);
    CHECK_EQ(MultiByteToWideChar(CP_MACCP, 0, "\x80", 1, w, 8), 1);
    CHECK_EQ(w[0], 0xC4);
    CHECK_EQ(MultiByteToWideChar(28591, 0, "\x80\xe9", 2, w, 8), 2);
    CHECK_EQ(w[0], 0x80); CHECK_EQ(w[1], 0xE9);
    CHECK_EQ(MultiByteToWideChar(20127, 0, "A\xe9", 2, w, 8), 2);
    CHECK_EQ(w[0], 'A'); CHECK_EQ(w[1], 0xFFFD);
    SetLastError(0);
    CHECK_EQ(MultiByteToWideChar(20127, T_MB_ERR, "A\xe9", 2, w, 8), 0);
    CHECK_EQ(GetLastError(), ERROR_NO_UNICODE_TRANSLATION);
    CHECK_EQ(MultiByteToWideChar(42, 0, "A\x0a\xff", 3, w, 8), 3);
    CHECK_EQ(w[0], 0xF041); CHECK_EQ(w[1], 0x0A); CHECK_EQ(w[2], 0xF0FF);
    CHECK_EQ(MultiByteToWideChar(CP_THREAD_ACP, 0, "\x80", 1, w, 8), 1);
    CHECK_EQ(w[0], 0x20AC);
    /* MB_COMPOSITE decomposes, MB_PRECOMPOSED does not */
    CHECK_EQ(MultiByteToWideChar(1252, T_MB_COMPOSITE, "\xe9z", 2, NULL, 0), 3);
    CHECK_EQ(MultiByteToWideChar(1252, T_MB_COMPOSITE, "\xe9z", 2, w, 8), 3);
    CHECK_EQ(w[0], 'e'); CHECK_EQ(w[1], 0x301); CHECK_EQ(w[2], 'z');
    CHECK_EQ(MultiByteToWideChar(1252, 1, "\xe9z", 2, NULL, 0), 2);
    /* every byte of every page round-trips through the Unicode side */
    static const UINT pages[] = { 1252, 437, 10000, 28591 };
    for (UINT cp : pages) {
        char all[256], back[256];
        WCHAR wall[256];
        BOOL used = TRUE;
        for (int i = 0; i < 256; i++)
            all[i] = (char)i;
        CHECK_EQ(MultiByteToWideChar(cp, 0, all, 256, wall, 256), 256);
        CHECK_EQ(WideCharToMultiByte(cp, T_WC_NOBF, wall, 256, back, 256, NULL, &used), 256);
        CHECK(!used);
        CHECK(!memcmp(all, back, 256));
    }
}

TEST(nls_utf8_decode)
{
    WCHAR w[16];
    static const struct { const char *s; int n; int out; } t[] = {
        { "\xe0\x80\x80", 3, 3 }, { "\xf0\x90\x80", 3, 1 }, { "\xf4\x90\x80\x80", 4, 4 }, { "\xc0\xaf", 2, 2 },
        { "\xed\xa0\x80", 3, 3 }, { "\xe2\x82", 2, 1 }, { "\xe2\x28\xa1", 3, 3 }, { "\xf0\x9f\x98\x80", 4, 2 },
        { "\x80\x80", 2, 2 }, { "\xf8\x88\x80\x80\x80", 5, 5 }, { "a\xc3", 2, 2 } };
    for (auto &c : t) {
        CHECK_EQ(MultiByteToWideChar(CP_UTF8, 0, c.s, c.n, w, 16), c.out);
        SetLastError(0);
        int r = MultiByteToWideChar(CP_UTF8, T_MB_ERR, c.s, c.n, w, 16);
        CHECK_EQ(r != 0, c.out == 2 && c.n == 4);   /* only the valid pair passes */
    }
    CHECK_EQ(MultiByteToWideChar(CP_UTF8, 0, "\xe2\x28\xa1", 3, w, 16), 3);
    CHECK_EQ(w[0], 0xFFFD); CHECK_EQ(w[1], '('); CHECK_EQ(w[2], 0xFFFD);
}

/* ---- WideCharToMultiByte */
TEST(nls_wc2mb_protocol)
{
    char a[16];
    WCHAR w[8];
    U("abc", w);
    SetLastError(0);
    CHECK_EQ(WideCharToMultiByte(1252, 0, w, -1, a, 3, NULL, NULL), 0);
    CHECK_EQ(GetLastError(), E_INSUF);
    CHECK_EQ(WideCharToMultiByte(1252, 0, w, -1, NULL, 0, NULL, NULL), 4);
    CHECK_EQ(WideCharToMultiByte(1252, 0, w, 3, NULL, 0, NULL, NULL), 3);
    static const struct { UINT cp; DWORD fl; int err; } t[] = {
        { 1252, T_WC_ERR, 1004 }, { 1252, T_WC_DISCARDNS, 1004 }, { 1252, T_WC_COMPOSITECHECK | T_WC_DISCARDNS | T_WC_SEPCHARS, 1004 },
        { 1252, 0x1, 1004 }, { 1252, T_WC_COMPOSITECHECK, 0 }, { 1252, T_WC_NOBF, 0 }, { 65001, T_WC_ERR, 0 },
        { 65001, T_WC_NOBF, 1004 }, { 65001, 0x10, 1004 }, { 65000, T_WC_ERR, 1004 }, { 42, 4, 1004 },
        { 932, 0, 87 }, { 1252, T_WC_COMPOSITECHECK | T_WC_SEPCHARS, 0 } };
    for (auto &c : t) {
        SetLastError(0);
        int r = WideCharToMultiByte(c.cp, c.fl, w, 3, a, 16, NULL, NULL);
        CHECK_EQ(r != 0, c.err == 0);
        CHECK_EQ(GetLastError(), c.err);
    }
    BOOL used;
    static const UINT nodef[] = { 65000, 65001, 42 };
    for (UINT cp : nodef) {
        SetLastError(0);
        CHECK_EQ(WideCharToMultiByte(cp, 0, w, 3, a, 16, "?", NULL), 0);
        CHECK_EQ(GetLastError(), 87);
        SetLastError(0);
        CHECK_EQ(WideCharToMultiByte(cp, 0, w, 3, a, 16, NULL, &used), 0);
        CHECK_EQ(GetLastError(), 87);
    }
    SetLastError(0);
    CHECK_EQ(WideCharToMultiByte(1252, 0, NULL, -1, a, 16, NULL, NULL), 0);
    CHECK_EQ(GetLastError(), 87);
    SetLastError(0);
    CHECK_EQ(WideCharToMultiByte(1252, 0, w, 0, a, 16, NULL, NULL), 0);
    CHECK_EQ(GetLastError(), 87);
    SetLastError(0);
    CHECK_EQ(WideCharToMultiByte(1252, 0, w, -5, a, 16, NULL, NULL), 0);
    CHECK_EQ(GetLastError(), 87);
    SetLastError(0);
    CHECK_EQ(WideCharToMultiByte(1252, 0, w, 3, a, -1, NULL, NULL), 0);
    CHECK_EQ(GetLastError(), 87);
    SetLastError(0);
    CHECK_EQ(WideCharToMultiByte(1252, 0, w, 3, NULL, 4, NULL, NULL), 0);
    CHECK_EQ(GetLastError(), 87);
    SetLastError(0);
    CHECK_EQ(WideCharToMultiByte(1252, 0, (LPCWSTR)a, 2, (LPSTR)a, 8, NULL, NULL), 0);   /* src == dst */
    CHECK_EQ(GetLastError(), 87);
}

TEST(nls_wc2mb_bestfit)
{
    char a[16];
    BOOL used = TRUE;
    static const WCHAR w[] = { 0x100, 0x102, 0xFF21, 0xC5, 0x4E2D, 0 };
    CHECK_EQ(WideCharToMultiByte(1252, 0, w, 5, a, 16, NULL, &used), 5);
    CHECK(!memcmp(a, "AAA\xc5?", 5));   /* A-macron, A-breve, fullwidth A -> A; A-ring direct; CJK -> ? */
    CHECK(used);
    used = FALSE;
    CHECK_EQ(WideCharToMultiByte(1252, 0, w, 3, a, 16, NULL, &used), 3);
    CHECK(!used);   /* a best-fit character is not a default character */
    CHECK_EQ(WideCharToMultiByte(1252, T_WC_NOBF, w, 5, a, 16, "#", &used), 5);
    CHECK(!memcmp(a, "###\xc5#", 5));
    CHECK(used);
    CHECK_EQ(WideCharToMultiByte(437, 0, w, 5, a, 16, NULL, NULL), 5);
    CHECK_EQ((unsigned char)a[3], 0x8F);   /* A-ring is 0x8F in 437 */
    static const WCHAR sur[] = { 0xD83D, 0xDE00, 'x', 0xDC00, 0 };   /* a pair is one character, a lone one too */
    CHECK_EQ(WideCharToMultiByte(1252, 0, sur, 4, a, 16, NULL, &used), 3);
    CHECK(!memcmp(a, "?x?", 3));
    /* composite check */
    static const WCHAR ec[] = { 'e', 0x301, 'z', 0x302, 0 };
    CHECK_EQ(WideCharToMultiByte(1252, T_WC_COMPOSITECHECK, ec, 2, a, 16, NULL, NULL), 1);
    CHECK_EQ((unsigned char)a[0], 0xE9);
    CHECK_EQ(WideCharToMultiByte(1252, T_WC_COMPOSITECHECK | T_WC_DISCARDNS, ec + 2, 2, a, 16, NULL, NULL), 1);
    CHECK_EQ(a[0], 'z');
    CHECK_EQ(WideCharToMultiByte(1252, T_WC_COMPOSITECHECK | T_WC_DEFAULTCHAR, ec + 2, 2, a, 16, NULL, NULL), 1);
    CHECK_EQ(a[0], '?');
    CHECK_EQ(WideCharToMultiByte(1252, T_WC_COMPOSITECHECK | T_WC_SEPCHARS, ec + 2, 2, a, 16, NULL, NULL), 2);
    CHECK(!memcmp(a, "z?", 2));
    CHECK_EQ(WideCharToMultiByte(1252, 0, ec, 2, a, 16, NULL, NULL), 2);   /* without the check: separate characters */
    CHECK_EQ(a[0], 'e');
    CHECK_EQ(WideCharToMultiByte(20127, 0, ec, 1, a, 16, NULL, NULL), 1);
    static const WCHAR hi[] = { 0xE9, 0 };
    CHECK_EQ(WideCharToMultiByte(20127, 0, hi, 1, a, 16, NULL, NULL), 1);
    CHECK_EQ(a[0], 'e');   /* best fit: e-acute -> e */
    CHECK_EQ(WideCharToMultiByte(42, 0, (const WCHAR[]){ 0xF041, 0x0A, 0x41 }, 3, a, 16, NULL, NULL), 3);
    CHECK(!memcmp(a, "A\x0a?", 3));
}

TEST(nls_utf8_encode_utf7)
{
    char b[32];
    SetLastError(0);
    static const WCHAR lone[] = { 'a', 0xD800, 'b', 0xDFFF };
    CHECK_EQ(WideCharToMultiByte(CP_UTF8, 0, lone, 4, b, 32, NULL, NULL), 1 + 3 + 1 + 3);
    CHECK(!memcmp(b + 1, "\xef\xbf\xbd", 3));
    CHECK_EQ(WideCharToMultiByte(CP_UTF8, T_WC_ERR, lone, 4, b, 32, NULL, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_NO_UNICODE_TRANSLATION);
    /* UTF-7: RFC 2152's example, '+', optional direct characters are encoded, a NUL stays a byte */
    static const WCHAR rfc[] = { 'A', 0x2262, 0x391, '.', 0 };
    CHECK_EQ(WideCharToMultiByte(CP_UTF7, 0, rfc, 4, b, 32, NULL, NULL), 10);
    CHECK(!memcmp(b, "A+ImIDkQ-.", 10));
    static const WCHAR mix[] = { '+', 'a', '!', 0xE9, 'b', 0 };
    CHECK_EQ(WideCharToMultiByte(CP_UTF7, 0, mix, 6, b, 32, NULL, NULL), 13);
    CHECK(!memcmp(b, "+-a+ACEA6Q-b", 13));   /* "!" and e-acute share one base64 run; the NUL stays a byte */
    CHECK_EQ(b[12], 0);
    WCHAR w[16];
    CHECK_EQ(MultiByteToWideChar(CP_UTF7, 0, "A+ImIDkQ.", 9, w, 16), 4);
    CHECK_EQ(w[1], 0x2262); CHECK_EQ(w[2], 0x391); CHECK_EQ(w[3], '.');
    CHECK_EQ(MultiByteToWideChar(CP_UTF7, 0, "+-a+ACEA6Q-b", 12, w, 16), 5);
    CHECK_EQ(w[0], '+'); CHECK_EQ(w[2], '!'); CHECK_EQ(w[3], 0xE9); CHECK_EQ(w[4], 'b');
    CHECK_EQ(MultiByteToWideChar(CP_UTF7, 0, "Hi Mom -+Jjo--!", 15, w, 16), 11);
    CHECK_EQ(w[8], 0x263A); CHECK_EQ(w[9], '-'); CHECK_EQ(w[10], '!');
    /* round trip of a mixed string */
    static const WCHAR rt[] = { 'x', 0x4E2D, 0x6587, ' ', 0xD83D, 0xDE00, '+', '~', '\\', 'z', 0 };
    int n = WideCharToMultiByte(CP_UTF7, 0, rt, 11, b, 32, NULL, NULL);
    CHECK(n > 0);
    CHECK_EQ(MultiByteToWideChar(CP_UTF7, 0, b, n, w, 16), 11);
    CHECK(!memcmp(w, rt, sizeof rt));
}

/* ---- locale info */
TEST(nls_locale_info)
{
    char a[80];
    WCHAR w[80];
    static const struct { LCTYPE t; const char *v; } t[] = {
        { 0x2, "English (United States)" }, { 0x3, "ENU" }, { 0x7, "USA" }, { 0xB, "437" }, { 0x1004, "1252" },
        { 0x1001, "English" }, { 0x1002, "United States" }, { 0x59, "en" }, { 0x5A, "US" }, { 0x5C, "en-US" },
        { 0x1F, "M/d/yyyy" }, { 0x20, "dddd, MMMM dd, yyyy" }, { 0x1003, "h:mm:ss tt" }, { 0x28, "AM" },
        { 0x29, "PM" }, { 0xC, "," }, { 0xE, "." }, { 0xF, "," }, { 0x10, "3;0" }, { 0x14, "$" }, { 0xD, "1" },
        { 0x100C, "6" }, { 0x1D, "/" }, { 0x1E, ":" }, { 0x2A, "Monday" }, { 0x30, "Sunday" }, { 0x31, "Mon" },
        { 0x43, "December" }, { 0x44, "Jan" }, { 0x4F, "Dec" }, { 0x38, "January" }, { 0x67, "eng" },
        { 0x68, "USA" }, { 0x60, "Mo" }, { 0x66, "Su" }, { 0x69, "NaN" }, { 0x6A, "Infinity" }, { 0x6B, "-Infinity" },
        { 0x1011, "10000" }, { 0x15, "USD" }, { 0x51, "-" }, { 0x50, "" }, { 0x100E, "" }, { 0x5D, "h:mm:ss" },
        { 0x79, "h:mm tt" }, { 0x1006, "MMMM, yyyy" }, { 0x1007, "US Dollar" }, { 0x6D, "en" } };
    for (auto &c : t) {
        int n = (int)strlen(c.v) + 1;
        CHECK_EQ(GetLocaleInfoA(LC, c.t, NULL, 0), n);
        CHECK_EQ(GetLocaleInfoA(LC, c.t, a, 80), n);
        CHECK_STR(a, c.v);
        CHECK_EQ(GetLocaleInfoW(LC, c.t | LOCALE_NOUSEROVERRIDE, w, 80), n);
        for (int i = 0; i < n; i++)
            CHECK_EQ(w[i], (unsigned char)c.v[i]);
    }
    /* every LCTYPE Windows 7 defines answers for en-US */
    for (LCTYPE t = 1; t <= 0x7A; t++) {
        bool defined = t <= 0x58 || (t >= 0x59 && t <= 0x6F && t != 0x5F) || t == 0x70 || t == 0x71 || t == 0x72 ||
                       t == 0x73 || t == 0x79 || t == 0x7A;
        CHECK_EQ(GetLocaleInfoW(LC, t, NULL, 0) > 0, defined);
    }
    static const LCTYPE hi[] = { 0x1001, 0x1002, 0x1003, 0x1004, 0x1005, 0x1006, 0x1007, 0x1008, 0x1009, 0x100A,
                                 0x100B, 0x100C, 0x100D, 0x100E, 0x100F, 0x1010, 0x1011, 0x1012, 0x1013, 0x1014 };
    for (LCTYPE t : hi)
        CHECK(GetLocaleInfoW(LC, t, NULL, 0) > 0);
    /* aliases and the invariant locale */
    static const LCID same[] = { 0x400, 0x800, 0, 0x9, 0x0409 };
    for (LCID l : same) {
        CHECK_EQ(GetLocaleInfoA(l, 0x5C, a, 80), 6);
        CHECK_STR(a, "en-US");
    }
    CHECK_EQ(GetLocaleInfoA(0x7F, 0x1F, a, 80), 11);
    CHECK_STR(a, "MM/dd/yyyy");
    CHECK_EQ(GetLocaleInfoA(0x7F, 0x14, a, 80), 2);
    CHECK_EQ((unsigned char)a[0], 0xA4);
    CHECK_EQ(GetLocaleInfoW(0x7F, 0x14, w, 80), 2);
    CHECK_EQ(w[0], 0xA4);
    CHECK_EQ(GetLocaleInfoA(0x7F, 0x38, a, 80), 8);
    CHECK_STR(a, "January");
    /* errors */
    SetLastError(0);
    CHECK_EQ(GetLocaleInfoA(LC, 0x7777, a, 80), 0);
    CHECK_EQ(GetLastError(), 1004);
    CHECK_EQ(GetLocaleInfoA(LC, 0, a, 80), 0);
    CHECK_EQ(GetLastError(), 1004);
    static const LCID badl[] = { 0x0407, 0x10409, 0x0809, 0x7E, 0x1 };
    for (LCID l : badl) {
        SetLastError(0);
        CHECK_EQ(GetLocaleInfoW(l, 0x2, w, 80), 0);
        CHECK_EQ(GetLastError(), 87);
    }
    SetLastError(0);
    CHECK_EQ(GetLocaleInfoA(LC, 0x1001, a, 7), 0);
    CHECK_EQ(GetLastError(), E_INSUF);
    CHECK_EQ(GetLocaleInfoW(LC, 0x1001, w, 7), 0);
    CHECK_EQ(GetLastError(), E_INSUF);
    CHECK_EQ(GetLocaleInfoW(LC, 0x1001, w, -1), 0);
    CHECK_EQ(GetLastError(), 87);
    CHECK_EQ(GetLocaleInfoW(LC, 0x1001, NULL, 5), 0);
    CHECK_EQ(GetLastError(), 87);
    /* LOCALE_RETURN_NUMBER */
    uint32_t num = 0;
    CHECK_EQ(GetLocaleInfoW(LC, 0xD | LOCALE_RETURN_NUMBER, NULL, 0), 2);
    CHECK_EQ(GetLocaleInfoA(LC, 0xD | LOCALE_RETURN_NUMBER, NULL, 0), 4);
    CHECK_EQ(GetLocaleInfoW(LC, 0xD | LOCALE_RETURN_NUMBER, (WCHAR *)&num, 2), 2);
    CHECK_EQ(num, 1);
    CHECK_EQ(GetLocaleInfoA(LC, 0x100C | LOCALE_RETURN_NUMBER, (char *)&num, 4), 4);
    CHECK_EQ(num, 6);
    SetLastError(0);
    CHECK_EQ(GetLocaleInfoW(LC, 0xD | LOCALE_RETURN_NUMBER, (WCHAR *)&num, 1), 0);
    CHECK_EQ(GetLastError(), E_INSUF);
    CHECK_EQ(GetLocaleInfoW(LC, 0x1001 | LOCALE_RETURN_NUMBER, (WCHAR *)&num, 2), 0);   /* a string item */
    CHECK_EQ(GetLastError(), 1004);
    CHECK_EQ(GetLocaleInfoA(LC, 0x1004 | LOCALE_USE_CP_ACP, a, 80), 5);
}

static int g_calls, g_stop;
static char g_id[16];
static BOOL enum_cb(LPSTR s)
{
    g_calls++;
    snprintf(g_id, sizeof g_id, "%s", s);
    return !g_stop;
}

TEST(nls_locale_set)
{
    CHECK_EQ(GetUserDefaultLCID(), 0x409);
    static const LCID ok[] = { 0x409, 0x400, 0x800, 0, 0x7F, 0x9 };
    for (LCID l : ok) {
        CHECK(IsValidLocale(l, 1));
        CHECK(IsValidLocale(l, 2));
    }
    static const LCID bad[] = { 0x407, 0x10409, 0x40A };
    for (LCID l : bad)
        CHECK(!IsValidLocale(l, 2));
    SetLastError(0);
    CHECK(!IsValidLocale(0x409, 0));
    CHECK_EQ(GetLastError(), 1004);
    SetLastError(0);
    CHECK(!IsValidLocale(0x409, 8));
    CHECK_EQ(GetLastError(), 1004);
    g_calls = 0;
    CHECK(EnumSystemLocalesA(enum_cb, 1));
    CHECK(EnumSystemLocalesA(enum_cb, 2));
    CHECK_EQ(g_calls, 2);
    CHECK_STR(g_id, "00000409");
    g_calls = 0;
    CHECK(EnumSystemLocalesA(enum_cb, 4));   /* alternate sorts: none for en-US */
    CHECK_EQ(g_calls, 0);
    g_stop = 1;
    CHECK(EnumSystemLocalesA(enum_cb, 3));
    CHECK_EQ(g_calls, 1);
    g_stop = 0;
    static const DWORD badf[] = { 0, 8, 0x10, 0x80 };
    for (DWORD f : badf) {
        SetLastError(0);
        CHECK(!EnumSystemLocalesA(enum_cb, f));
        CHECK_EQ(GetLastError(), 1004);
    }
    SetLastError(0);
    CHECK(!EnumSystemLocalesA(NULL, 1));
    CHECK_EQ(GetLastError(), 87);
}

/* ---- GetStringType */
TEST(nls_string_types)
{
    WORD t[8];
    static const WCHAR s2[] = { 'a', '1', ' ', 0x5D0, '!', ',', '+', '$' };
    static const int c2[] = { 1, 3, 10, 2, 11, 7, 4, 5 };
    CHECK(GetStringTypeW(CT_CTYPE2, s2, 8, t));
    for (int i = 0; i < 8; i++)
        CHECK_EQ(t[i], c2[i]);
    static const WCHAR s3[] = { 0x301, 'a', 0x30A2, 0x3042, 0xFF41, 0xFF71, 0x4E2D, 0x640 };
    static const int c3[] = { 3, 0x8000, 0x8010, 0x8020, 0x8080, 0x8050, 0x8100, 0x8200 };
    CHECK(GetStringTypeW(CT_CTYPE3, s3, 8, t));
    for (int i = 0; i < 8; i++)
        CHECK_EQ(t[i], c3[i]);
    static const WCHAR s4[] = { '-', 0xD800, 0xDC00, 0x20AC };
    CHECK(GetStringTypeW(CT_CTYPE3, s4, 4, t));
    CHECK_EQ(t[0], 0x408); CHECK_EQ(t[1], 0x800); CHECK_EQ(t[2], 0x1000); CHECK_EQ(t[3], 8);
    /* the whole BMP: letters are alpha and defined, upper and lower exclude digits */
    for (unsigned c = 0; c < 0x10000; c++) {
        WCHAR ch = (WCHAR)c;
        WORD a1 = 0, a2 = 0, a3 = 0;
        CHECK(GetStringTypeW(CT_CTYPE1, &ch, 1, &a1));
        CHECK(GetStringTypeW(CT_CTYPE2, &ch, 1, &a2));
        CHECK(GetStringTypeW(CT_CTYPE3, &ch, 1, &a3));
        if ((a1 & (C1_UPPER | C1_LOWER)) && !(a1 & C1_ALPHA && a1 & C1_DEFINED)) {
            CHECK(0);
            break;
        }
        if (a2 > 11 || ((a1 & C1_DIGIT) && (a1 & C1_ALPHA))) {
            CHECK(0);
            break;
        }
    }
    SetLastError(0);
    CHECK(!GetStringTypeW(CT_CTYPE1, s2, 0, t));
    CHECK_EQ(GetLastError(), 87);
    CHECK(!GetStringTypeW(CT_CTYPE1, NULL, 1, t));
    CHECK_EQ(GetLastError(), 87);
    CHECK(!GetStringTypeW(CT_CTYPE1, s2, 1, NULL));
    CHECK_EQ(GetLastError(), 87);
    CHECK(!GetStringTypeW(3, s2, 1, t));
    CHECK_EQ(GetLastError(), 1004);
    CHECK(!GetStringTypeW(0, s2, 1, t));
    CHECK_EQ(GetLastError(), 1004);
    CHECK(GetStringTypeA(LC, CT_CTYPE2, "a", -1, t));   /* -1: the NUL too */
    CHECK_EQ(t[0], 1); CHECK_EQ(t[1], 0);
    CHECK(GetStringTypeA(LOCALE_SYSTEM_DEFAULT, CT_CTYPE3, "\x80", 1, t));
    CHECK_EQ(t[0], 8);
    CHECK(!GetStringTypeA(0x407, CT_CTYPE1, "a", 1, t));
    CHECK_EQ(GetLastError(), 87);
    CHECK(!GetStringTypeA(LC, 0x10, "a", 1, t));
    CHECK_EQ(GetLastError(), 1004);
}

/* ---- LCMapString */
TEST(nls_lcmap)
{
    WCHAR w[16], s[16];
    char a[16];
    static const WCHAR up[] = { 'a', 0xDF, 0xB5, 0xFF, 0x131, 0x17F, 0x3B1, 0x44F, 0xFF41, 0 };
    CHECK_EQ(LCMapStringW(LC, T_UPPER, up, -1, w, 16), 10);
    static const int ue[] = { 'A', 0xDF, 0x39C, 0x178, 'I', 'S', 0x391, 0x42F, 0xFF21, 0 };
    for (int i = 0; i < 10; i++)
        CHECK_EQ(w[i], ue[i]);
    CHECK_EQ(LCMapStringW(LC, T_UPPER | 0x08000000, up, 2, w, 16), 2);   /* linguistic casing: same for en-US */
    CHECK_EQ(w[0], 'A');
    memcpy(s, up, sizeof up);
    CHECK_EQ(LCMapStringW(LC, T_UPPER, s, -1, s, 16), 10);   /* in place */
    CHECK_EQ(s[3], 0x178);
    CHECK_EQ(LCMapStringW(LC, T_LOWER, (const WCHAR[]){ 0x130, 0x1E9E, 0x3A3 }, 3, w, 16), 3);
    CHECK_EQ(w[0], 'i'); CHECK_EQ(w[1], 0xDF); CHECK_EQ(w[2], 0x3C3);
    /* flag classes */
    static const DWORD badf[] = { 0, T_LOWER | T_UPPER, T_SORTKEY | T_UPPER, T_HIRA | T_KATA, T_HALF | T_FULL,
                                  0x1000000, T_IGNCASE, T_LOWER | T_SORTKEY, 0x08000000, 0x08000000 | T_SORTKEY };
    for (DWORD f : badf) {
        SetLastError(0);
        CHECK_EQ(LCMapStringW(LC, f, up, 2, w, 16), 0);
        CHECK_EQ(GetLastError(), 1004);
    }
    SetLastError(0);
    CHECK_EQ(LCMapStringW(LC, T_FULL, s, 2, s, 16), 0);   /* in place is for case mapping only */
    CHECK_EQ(GetLastError(), 1004);
    CHECK_EQ(LCMapStringW(LC, T_UPPER, up, 2, w, 1), 0);
    CHECK_EQ(GetLastError(), E_INSUF);
    CHECK_EQ(LCMapStringW(LC, T_UPPER, up, 2, NULL, 0), 2);
    CHECK_EQ(LCMapStringW(LC, T_UPPER, up, 0, w, 16), 0);
    CHECK_EQ(GetLastError(), 87);
    CHECK_EQ(LCMapStringW(LC, T_UPPER, NULL, 1, w, 16), 0);
    CHECK_EQ(GetLastError(), 87);
    CHECK_EQ(LCMapStringW(LC, T_UPPER, up, 1, NULL, 3), 0);
    CHECK_EQ(GetLastError(), 87);
    CHECK_EQ(LCMapStringW(0x407, T_UPPER, up, 1, w, 16), 0);
    CHECK_EQ(GetLastError(), 87);
    /* width and kana */
    static const WCHAR fw[] = { 'A', ' ', '~', 0xA2, 0xFF71, 0x30A2, 0x3002 };
    CHECK_EQ(LCMapStringW(LC, T_FULL, fw, 7, w, 16), 7);
    static const int fe[] = { 0xFF21, 0x3000, 0xFF5E, 0xFFE0, 0x30A2, 0x30A2, 0x3002 };
    for (int i = 0; i < 7; i++)
        CHECK_EQ(w[i], fe[i]);
    static const WCHAR hw[] = { 0xFF21, 0x3000, 0x30A2, 'A', 0x3002 };
    CHECK_EQ(LCMapStringW(LC, T_HALF, hw, 5, w, 16), 5);
    static const int he[] = { 'A', ' ', 0xFF71, 'A', 0xFF61 };
    for (int i = 0; i < 5; i++)
        CHECK_EQ(w[i], he[i]);
    CHECK_EQ(LCMapStringW(LC, T_KATA, (const WCHAR[]){ 0x3042, 0x3093, 'a' }, 3, w, 16), 3);
    CHECK_EQ(w[0], 0x30A2); CHECK_EQ(w[1], 0x30F3); CHECK_EQ(w[2], 'a');
    CHECK_EQ(LCMapStringW(LC, T_HIRA, (const WCHAR[]){ 0x30A2, 0x30F3 }, 2, w, 16), 2);
    CHECK_EQ(w[0], 0x3042); CHECK_EQ(w[1], 0x3093);
    CHECK_EQ(LCMapStringW(LC, T_BYTEREV, (const WCHAR[]){ 0x3450, 0x4822 }, 2, w, 16), 2);
    CHECK_EQ(w[0], 0x5034); CHECK_EQ(w[1], 0x2248);
    CHECK_EQ(LCMapStringW(LC, T_BYTEREV | T_UPPER, (const WCHAR[]){ 'a' }, 1, w, 16), 1);
    CHECK_EQ(w[0], 0x4100);
    /* A: through the code page; a case partner outside the page keeps the byte */
    CHECK_EQ(LCMapStringA(LC, T_UPPER, "ab\xe9\xb5\xff", -1, a, 16), 6);
    CHECK_STR(a, "AB\xc9\xb5\x9f");
    CHECK_EQ(LCMapStringA(LC, T_BYTEREV, "abcd", 4, a, 16), 4);
    CHECK(!memcmp(a, "badc", 4));
    CHECK_EQ(LCMapStringA(LC, T_FULL, "a", 1, a, 16), 1);   /* fullwidth A is not in 1252: stays */
    CHECK_EQ(a[0], 'a');
}

/* the sign of the byte-wise order of two sort keys, shorter first on a common prefix */
static int key_cmp(DWORD fl, const WCHAR *x, const WCHAR *y)
{
    BYTE kx[512], ky[512];
    int nx = LCMapStringW(LC, T_SORTKEY | fl, x, -1, (WCHAR *)kx, 512);
    int ny = LCMapStringW(LC, T_SORTKEY | fl, y, -1, (WCHAR *)ky, 512);
    CHECK(nx > 0 && ny > 0);
    int m = nx < ny ? nx : ny, r = memcmp(kx, ky, (size_t)m);
    return r ? (r < 0 ? -1 : 1) : (nx < ny ? -1 : nx > ny ? 1 : 0);
}

TEST(nls_sortkey_matches_compare)
{
    static const WCHAR *const S[] = {
        L"", L"a", L"A", L"b", L"B", L"ab", L"Ab", L"a-b", L"ab-", L"co-op", L"coop", L"cop", L"co op", L"1", L"10",
        L"!", L"é", L"é", L"E", L"e", L"f", L"ā", L"Ā", L"あ", L"ア", L"Ａ", L"ｱ",
        L"ẑ", L"ü", L"u", L"ß", L"中", L"a'b", L"a\u0001b", L"Α", L"α" };
    static const DWORD F[] = { 0, T_IGNCASE, T_IGNNS, T_STRSORT, T_IGNSYM, T_IGNKANA, T_IGNWIDTH,
                               T_IGNCASE | T_IGNNS | T_IGNSYM | T_IGNKANA | T_IGNWIDTH | T_STRSORT, T_IGNCASE | T_STRSORT };
    int bad = 0;
    for (DWORD f : F)
        for (const WCHAR *x : S)
            for (const WCHAR *y : S) {
                int c = CompareStringW(LC, f, x, -1, y, -1);
                int want = c == CSTR_LESS_THAN ? -1 : c == CSTR_GREATER_THAN ? 1 : 0;
                bad += c == 0 || key_cmp(f, x, y) != want;
                bad += CompareStringW(LC, f, y, -1, x, -1) != 4 - c;   /* antisymmetric */
            }
    CHECK_EQ(bad, 0);
    BYTE k1[64], k2[64];
    int n1 = LCMapStringA(LC, T_SORTKEY, "abc", -1, (char *)k1, 64);
    int n2 = LCMapStringW(LC, T_SORTKEY, L"abc", -1, (WCHAR *)k2, 64);
    CHECK(n1 > 0);
    CHECK_EQ(n1, n2);
    CHECK(!memcmp(k1, k2, (size_t)n1));
    CHECK_EQ(LCMapStringA(LC, T_SORTKEY, "abc", -1, NULL, 0), n1);
    SetLastError(0);
    CHECK_EQ(LCMapStringA(LC, T_SORTKEY, "abc", -1, (char *)k1, n1 - 1), 0);
    CHECK_EQ(GetLastError(), E_INSUF);
}

/* ---- CompareString */
#define CW(fl, x, y) CompareStringW(LC, fl, x, -1, y, -1)
TEST(nls_compare)
{
    CHECK_EQ(CW(0, L"a", L"B"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"B", L"c"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"a", L"A"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"A", L"a"), CSTR_GREATER_THAN);
    CHECK_EQ(CW(0, L"A", L"b"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"coop", L"co-op"), CSTR_LESS_THAN);   /* word sort: the hyphen is a last-level difference */
    CHECK_EQ(CW(0, L"co-op", L"cop"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"coop", L"cop"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"it's", L"its"), CSTR_GREATER_THAN);
    CHECK_EQ(CW(T_STRSORT, L"co-op", L"coop"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"abc", L"abcd"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"", L"a"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"", L""), CSTR_EQUAL);
    CHECK_EQ(CW(0, L"1", L"a"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"!", L"1"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"_", L"0"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"9", L"A"), CSTR_LESS_THAN);
    CHECK_EQ(CW(T_IGNCASE, L"ABC", L"abc"), CSTR_EQUAL);
    CHECK_EQ(CW(0x10, L"ABC", L"abc"), CSTR_EQUAL);   /* LINGUISTIC_IGNORECASE */
    CHECK_EQ(CW(0, L"abc", L"abc"), CSTR_EQUAL);
    /* diacritics: secondary, above case */
    CHECK_EQ(CW(0, L"e", L"é"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"é", L"f"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"E", L"é"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"a", L"Ā"), CSTR_LESS_THAN);
    CHECK_EQ(CW(0, L"é", L"é"), CSTR_EQUAL);
    CHECK_EQ(CW(T_IGNNS, L"é", L"e"), CSTR_EQUAL);
    CHECK_EQ(CW(T_IGNNS, L"é", L"E"), CSTR_LESS_THAN);   /* case still counts */
    CHECK_EQ(CW(T_IGNNS | T_IGNCASE, L"é", L"E"), CSTR_EQUAL);
    CHECK_EQ(CW(T_IGNCASE, L"É", L"é"), CSTR_EQUAL);
    CHECK_EQ(CW(T_IGNSYM, L"a-b c", L"abc"), CSTR_EQUAL);
    CHECK_EQ(CW(0, L"aあ", L"aア"), CSTR_LESS_THAN);
    CHECK_EQ(CW(T_IGNKANA, L"aあ", L"aア"), CSTR_EQUAL);
    CHECK_EQ(CW(0, L"A", L"Ａ"), CSTR_LESS_THAN);
    CHECK_EQ(CW(T_IGNWIDTH, L"A", L"Ａ"), CSTR_EQUAL);
    CHECK_EQ(CW(0, L"z", L"中"), CSTR_LESS_THAN);   /* other characters after the Latin ones, by code point */
    CHECK_EQ(CW(0, L"中", L"丮"), CSTR_LESS_THAN);
    /* counts, embedded NULs, ASCII/ANSI */
    CHECK_EQ(CompareStringW(LC, 0, L"abcX", 3, L"abcY", 3), CSTR_EQUAL);
    CHECK_EQ(CompareStringW(LC, 0, L"a\0b", 3, L"a\0c", 3), CSTR_LESS_THAN);
    CHECK_EQ(CompareStringW(LC, 0, L"a", 0, L"", 0), CSTR_EQUAL);
    CHECK_EQ(CompareStringW(LC, 0, L"a", 1, L"", 0), CSTR_GREATER_THAN);
    CHECK_EQ(CompareStringA(LC, 0, "a", -1, "B", -1), CSTR_LESS_THAN);
    CHECK_EQ(CompareStringA(LC, T_IGNCASE, "H\xc9", -1, "h\xe9", -1), CSTR_EQUAL);
    CHECK_EQ(CompareStringA(LC, 0, "e", -1, "\xe9", -1), CSTR_LESS_THAN);
    CHECK_EQ(CompareStringA(LC, 0, "\x80", -1, "a", -1), CSTR_LESS_THAN);   /* euro sign: a symbol */
    static const DWORD badf[] = { 0x8, 0x80, 0x4000, 0x40000, 0x100 };
    for (DWORD f : badf) {
        SetLastError(0);
        CHECK_EQ(CW(f, L"a", L"b"), 0);
        CHECK_EQ(GetLastError(), 1004);
    }
    SetLastError(0);
    CHECK_EQ(CompareStringW(LC, 0, NULL, -1, L"a", -1), 0);
    CHECK_EQ(GetLastError(), 87);
    CHECK_EQ(CompareStringW(LC, 0, L"a", -1, NULL, 1), 0);
    CHECK_EQ(GetLastError(), 87);
    CHECK_EQ(CompareStringW(LC, 0, L"a", -2, L"a", -1), 0);
    CHECK_EQ(GetLastError(), 87);
    CHECK_EQ(CompareStringW(0x407, 0, L"a", -1, L"a", -1), 0);
    CHECK_EQ(GetLastError(), 87);
    CHECK_EQ(CompareStringW(LOCALE_USER_DEFAULT, 0, L"a", -1, L"a", -1), CSTR_EQUAL);
    CHECK_EQ(CompareStringW(LOCALE_INVARIANT, 0, L"a", -1, L"b", -1), CSTR_LESS_THAN);
}
