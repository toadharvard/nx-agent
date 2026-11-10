/* test_crt_kernel32.cpp - the exe CRT's kernel32/user32/advapi32 imports (platform/win32/crt_kernel32.cpp): code
 * page conversions and their size conventions, NLS case mapping/comparison/types/locale info, standard handles,
 * environment block, startup info, exception filter, drives, user name, and their export table entries. */
#include "s4_test.h"

#include "../../platform/win32/w32.h"

#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

static size_t w16len(const WCHAR *s)
{
    size_t n = 0;
    while (s[n])
        n++;
    return n;
}

TEST(crt_k32_multibyte_to_wide_1252)
{
    static const char s[] = "A\x80\xe9\x9f\x81";   /* A euro e-acute Y-diaeresis (undefined 0x81) */
    CHECK_EQ(MultiByteToWideChar(CP_ACP, 0, s, -1, NULL, 0), 6);   /* -1: the NUL counts */
    CHECK_EQ(MultiByteToWideChar(CP_ACP, 0, s, 5, NULL, 0), 5);
    WCHAR w[8] = { 0 };
    CHECK_EQ(MultiByteToWideChar(1252, MB_PRECOMPOSED, s, -1, w, 8), 6);
    CHECK_EQ(w[0], 'A');
    CHECK_EQ(w[1], 0x20AC);
    CHECK_EQ(w[2], 0xE9);
    CHECK_EQ(w[3], 0x178);
    CHECK_EQ(w[4], 0x81);
    CHECK_EQ(w[5], 0);
    SetLastError(0);
    CHECK_EQ(MultiByteToWideChar(CP_ACP, 0, s, -1, w, 5), 0);   /* one short */
    CHECK_EQ(GetLastError(), ERROR_INSUFFICIENT_BUFFER);
    CHECK_EQ(MultiByteToWideChar(CP_OEMCP, 0, s, 0, w, 8), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK_EQ(MultiByteToWideChar(CP_ACP, 0, NULL, -1, w, 8), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK_EQ(MultiByteToWideChar(932, 0, s, -1, w, 8), 0);   /* no Shift-JIS */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
}

TEST(crt_k32_multibyte_to_wide_utf8)
{
    static const char s[] = "a\xc3\xa9\xe2\x82\xac\xf0\x9f\x98\x80";   /* a e-acute euro U+1F600 */
    CHECK_EQ(MultiByteToWideChar(CP_UTF8, 0, s, -1, NULL, 0), 6);
    WCHAR w[8] = { 0 };
    CHECK_EQ(MultiByteToWideChar(CP_UTF8, 0, s, -1, w, 8), 6);
    CHECK_EQ(w[0], 'a');
    CHECK_EQ(w[1], 0xE9);
    CHECK_EQ(w[2], 0x20AC);
    CHECK_EQ(w[3], 0xD83D);
    CHECK_EQ(w[4], 0xDE00);
    CHECK_EQ(w[5], 0);
    static const char bad[] = "x\xff" "y\xc3";   /* invalid byte, truncated sequence */
    CHECK_EQ(MultiByteToWideChar(CP_UTF8, 0, bad, 4, w, 8), 4);
    CHECK_EQ(w[1], 0xFFFD);
    CHECK_EQ(w[2], 'y');
    CHECK_EQ(w[3], 0xFFFD);
    SetLastError(0);
    CHECK_EQ(MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bad, 4, w, 8), 0);
    CHECK_EQ(GetLastError(), ERROR_NO_UNICODE_TRANSLATION);
    CHECK_EQ(MultiByteToWideChar(CP_UTF8, 0, "\xed\xa0\x80", 3, NULL, 0), 2);   /* an encoded surrogate: rejected after ED A0, then 80 alone */
}

TEST(crt_k32_wide_to_multibyte)
{
    const WCHAR w[] = { 'H', 0xE9, 0x20AC, 0x0100, 0 };   /* A-macron is not in 1252 */
    CHECK_EQ(WideCharToMultiByte(CP_ACP, 0, w, -1, NULL, 0, NULL, NULL), 5);
    char a[8];
    BOOL used = TRUE;
    CHECK_EQ(WideCharToMultiByte(CP_ACP, 0, w, 3, a, 8, NULL, &used), 3);
    CHECK(!used);
    CHECK(!memcmp(a, "H\xe9\x80", 3));
    CHECK_EQ(WideCharToMultiByte(CP_ACP, 0, w, -1, a, 8, "#", &used), 5);   /* best fit: A-macron -> A */
    CHECK(!used);
    CHECK_STR(a, "H\xe9\x80" "A");
    CHECK_EQ(WideCharToMultiByte(CP_ACP, WC_NO_BEST_FIT_CHARS, w, -1, a, 8, "#", &used), 5);
    CHECK(used);
    CHECK_STR(a, "H\xe9\x80#");
    CHECK_EQ(WideCharToMultiByte(CP_ACP, WC_NO_BEST_FIT_CHARS, w, -1, a, 8, NULL, NULL), 5);
    CHECK_STR(a, "H\xe9\x80?");
    SetLastError(0);
    CHECK_EQ(WideCharToMultiByte(CP_ACP, 0, w, -1, a, 4, NULL, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_INSUFFICIENT_BUFFER);
    /* UTF-8: sizes, surrogate pairs, lone surrogates -> U+FFFD; no default char allowed */
    const WCHAR u[] = { 'a', 0xE9, 0x20AC, 0xD83D, 0xDE00, 0xDC00, 0 };
    CHECK_EQ(WideCharToMultiByte(CP_UTF8, 0, u, -1, NULL, 0, NULL, NULL), 1 + 2 + 3 + 4 + 3 + 1);
    char b[16];
    CHECK_EQ(WideCharToMultiByte(CP_UTF8, 0, u, -1, b, 16, NULL, NULL), 14);
    CHECK_STR(b, "a\xc3\xa9\xe2\x82\xac\xf0\x9f\x98\x80\xef\xbf\xbd");
    used = FALSE;
    CHECK_EQ(WideCharToMultiByte(CP_UTF8, 0, u, -1, b, 16, NULL, &used), 14);   /* the reference accepts it, used = a lone surrogate was replaced */
    CHECK(used);
    CHECK_EQ(WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, u, -1, b, 16, NULL, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_NO_UNICODE_TRANSLATION);
    /* every 1252 byte round-trips */
    char all[256], back[256];
    WCHAR wall[256];
    for (int i = 0; i < 256; i++)
        all[i] = (char)i;
    CHECK_EQ(MultiByteToWideChar(CP_ACP, 0, all, 256, wall, 256), 256);
    CHECK_EQ(WideCharToMultiByte(CP_ACP, 0, wall, 256, back, 256, NULL, &used), 256);
    CHECK(!used);
    CHECK(!memcmp(all, back, 256));
}

TEST(crt_k32_code_pages)
{
    CHECK_EQ(GetACP(), 1252);
    CHECK_EQ(GetOEMCP(), 437);   /* the environment: OEM code page 437 */
    CHECK_EQ(GetConsoleCP(), 1252);
    CHECK_EQ(GetConsoleOutputCP(), 1252);
    CHECK(IsValidCodePage(1252));
    CHECK(IsValidCodePage(CP_UTF8));
    CHECK(!IsValidCodePage(932));
    CPINFO ci;
    memset(&ci, 0x55, sizeof ci);
    CHECK(GetCPInfo(CP_ACP, &ci));
    CHECK_EQ(ci.MaxCharSize, 1);
    CHECK_EQ(ci.DefaultChar[0], '?');
    CHECK_EQ(ci.DefaultChar[1], 0);
    for (int i = 0; i < 12; i++)
        CHECK_EQ(ci.LeadByte[i], 0);
    CHECK(GetCPInfo(CP_UTF8, &ci));
    CHECK_EQ(ci.MaxCharSize, 4);
    CHECK(!GetCPInfo(936, &ci));
}

TEST(crt_k32_lcmapstring)
{
    char a[16];
    CHECK_EQ(LCMapStringA(0x0409, LCMAP_UPPERCASE, "abc\xe9\xff\xb5\x9a", -1, NULL, 0), 8);
    CHECK_EQ(LCMapStringA(0x0409, LCMAP_UPPERCASE, "abc\xe9\xff\xb5\x9a", -1, a, 16), 8);
    CHECK_STR(a, "ABC\xc9\x9f\xb5\x8a");   /* y-diaeresis -> 0x9f, mu has no 1252 upper case, s-caron -> 0x8a */
    CHECK_EQ(LCMapStringA(LOCALE_USER_DEFAULT, LCMAP_LOWERCASE, "XyZ\xc0\xd7", 5, a, 16), 5);
    CHECK(!memcmp(a, "xyz\xe0\xd7", 5));
    SetLastError(0);
    CHECK_EQ(LCMapStringA(0x0409, LCMAP_LOWERCASE, "abcd", -1, a, 4), 0);   /* NUL does not fit */
    CHECK_EQ(GetLastError(), ERROR_INSUFFICIENT_BUFFER);
    CHECK_EQ(LCMapStringA(0x0409, LCMAP_LOWERCASE | LCMAP_UPPERCASE, "a", -1, a, 4), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_FLAGS);
    CHECK_EQ(LCMapStringA(0x0409, LCMAP_LOWERCASE, "a", 0, a, 4), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    /* the CRT's probe */
    WCHAR w[16];
    CHECK_EQ(LCMapStringW(0, LCMAP_LOWERCASE, L"\0", 1, NULL, 0), 1);
    CHECK_EQ(LCMapStringW(0x0409, LCMAP_UPPERCASE, L"a\u00e9\u0101\u03b1\u0436", -1, w, 16), 6);
    CHECK_EQ(w[0], 'A');
    CHECK_EQ(w[1], 0xC9);
    CHECK_EQ(w[2], 0x100);
    CHECK_EQ(w[3], 0x391);
    CHECK_EQ(w[4], 0x416);
    CHECK_EQ(w[5], 0);
    CHECK_EQ(LCMapStringW(0x0409, LCMAP_LOWERCASE, L"\u0178\u0130Q", 3, w, 3), 3);
    CHECK_EQ(w[0], 0xFF);
    CHECK_EQ(w[1], 0x130);   /* U+0130 has no lower-case partner in the non-linguistic casemap */
    CHECK_EQ(w[2], 'q');
    /* sort keys order as CompareString: a < A < ab < B */
    BYTE k1[32], k2[32], k3[32], k4[32];
    int n1 = LCMapStringA(0x0409, LCMAP_SORTKEY, "a", -1, (char *)k1, 32);
    int n2 = LCMapStringA(0x0409, LCMAP_SORTKEY, "A", -1, (char *)k2, 32);
    int n3 = LCMapStringA(0x0409, LCMAP_SORTKEY, "ab", -1, (char *)k3, 32);
    int n4 = LCMapStringW(0x0409, LCMAP_SORTKEY, L"B", -1, (WCHAR *)k4, 32);
    CHECK_EQ(n1, 7);   /* script+primary, 01, 01, 01, 01, 00: only the case level of "A" is non-default (0x12) */
    CHECK_EQ(LCMapStringA(0x0409, LCMAP_SORTKEY, "a", -1, NULL, 0), 7);
    CHECK_EQ(n2, 8);
    CHECK_EQ(n4, 8);
    CHECK_EQ(n3, 9);
    CHECK(!memcmp(k1, "\x0e\x02\x01\x01\x01\x01\x00", 7));
    CHECK(memcmp(k1, k2, 7) < 0);
    CHECK(memcmp(k2, k3, 8) < 0);
    CHECK(memcmp(k3, k4, 8) < 0);
    CHECK_EQ(LCMapStringA(0x0409, LCMAP_SORTKEY, "ab", -1, (char *)k3, 8), 0);
    CHECK_EQ(GetLastError(), ERROR_INSUFFICIENT_BUFFER);
}

TEST(crt_k32_comparestring)
{
    CHECK_EQ(CompareStringA(0x0409, 0, "abc", -1, "abc", -1), CSTR_EQUAL);
    CHECK_EQ(CompareStringA(0x0409, 0, "abc", -1, "abd", -1), CSTR_LESS_THAN);
    CHECK_EQ(CompareStringA(0x0409, 0, "abd", -1, "abc", -1), CSTR_GREATER_THAN);
    CHECK_EQ(CompareStringA(0x0409, 0, "ab", -1, "abc", -1), CSTR_LESS_THAN);
    CHECK_EQ(CompareStringA(0x0409, 0, "a", -1, "B", -1), CSTR_LESS_THAN);   /* not code unit order */
    CHECK_EQ(CompareStringA(0x0409, 0, "a", -1, "A", -1), CSTR_LESS_THAN);
    CHECK_EQ(CompareStringA(0x0409, 0, "A", -1, "a", -1), CSTR_GREATER_THAN);
    CHECK_EQ(CompareStringA(0x0409, NORM_IGNORECASE, "HeLLo\xc9", -1, "hello\xe9", -1), CSTR_EQUAL);
    CHECK_EQ(CompareStringA(0x0409, NORM_IGNORECASE, "abcX", 3, "ABCY", 3), CSTR_EQUAL);   /* counted */
    CHECK_EQ(CompareStringW(0x0409, NORM_IGNORECASE, L"\u0436\u0101", -1, L"\u0416\u0100", -1), CSTR_EQUAL);
    CHECK_EQ(CompareStringW(0x0409, 0, L"x", 1, L"xy", 2), CSTR_LESS_THAN);
    SetLastError(0);
    CHECK_EQ(CompareStringA(0x0409, 0, NULL, -1, "a", -1), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
}

TEST(crt_k32_string_types)
{
    WORD t[8];
    CHECK(GetStringTypeA(0x0409, CT_CTYPE1, "aZ5 \t!\x01\xe9", 8, t));
    CHECK_EQ(t[0], C1_ALPHA | C1_LOWER | C1_XDIGIT | C1_DEFINED);
    CHECK_EQ(t[1], C1_ALPHA | C1_UPPER | C1_DEFINED);
    CHECK_EQ(t[2], C1_DIGIT | C1_XDIGIT | C1_DEFINED);
    CHECK_EQ(t[3], C1_SPACE | C1_BLANK | C1_DEFINED);
    CHECK_EQ(t[4], C1_CNTRL | C1_SPACE | C1_BLANK | C1_DEFINED);
    CHECK_EQ(t[5], C1_PUNCT | C1_DEFINED);
    CHECK_EQ(t[6], C1_CNTRL | C1_DEFINED);
    CHECK_EQ(t[7], C1_ALPHA | C1_LOWER | C1_DEFINED);
    /* 1252 bytes classify as their Unicode character: euro is punctuation, S-caron an upper-case letter */
    CHECK(GetStringTypeA(0x0409, CT_CTYPE1, "\x80\x8a\xa0\xd7", 4, t));
    CHECK_EQ(t[0], C1_DEFINED);   /* the euro sign is a currency symbol: defined only */
    CHECK_EQ(t[1], C1_ALPHA | C1_UPPER | C1_DEFINED);
    CHECK_EQ(t[2], C1_SPACE | C1_BLANK | C1_DEFINED);
    CHECK_EQ(t[3], C1_PUNCT | C1_DEFINED);
    memset(t, 0x55, sizeof t);
    CHECK(GetStringTypeW(CT_CTYPE1, L"\u0416\u0436\u3000", -1, t));   /* -1: the NUL too */
    CHECK_EQ(t[0], C1_ALPHA | C1_UPPER | C1_DEFINED);
    CHECK_EQ(t[1], C1_ALPHA | C1_LOWER | C1_DEFINED);
    CHECK_EQ(t[2], C1_SPACE | C1_BLANK | C1_DEFINED);
    CHECK_EQ(t[3], C1_CNTRL | C1_DEFINED);
    CHECK_EQ(t[4], 0x5555);
    CHECK(GetStringTypeW(CT_CTYPE2, L"a", 1, t));
    CHECK_EQ(t[0], 1);   /* C2_LEFTTORIGHT */
    SetLastError(0);
    CHECK(!GetStringTypeW(0x10, L"a", 1, t));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
}

static int g_enum_calls;
static char g_enum_id[16];
static BOOL enum_proc(LPSTR id)
{
    g_enum_calls++;
    snprintf(g_enum_id, sizeof g_enum_id, "%s", id);
    return TRUE;
}

TEST(crt_k32_locale_info)
{
    char a[64];
    CHECK_EQ(GetLocaleInfoA(0x0409, LOCALE_IDEFAULTANSICODEPAGE, NULL, 0), 5);
    CHECK_EQ(GetLocaleInfoA(0x0409, LOCALE_IDEFAULTANSICODEPAGE, a, 64), 5);
    CHECK_STR(a, "1252");
    CHECK_EQ(GetLocaleInfoA(LOCALE_USER_DEFAULT, LOCALE_IDEFAULTCODEPAGE, a, 64), 4);
    CHECK_STR(a, "437");   /* the OEM code page */
    CHECK_EQ(GetLocaleInfoA(0x0409, LOCALE_ILANGUAGE, a, 64), 5);
    CHECK_STR(a, "0409");
    CHECK_EQ(GetLocaleInfoA(0x0409, LOCALE_SENGLANGUAGE | LOCALE_NOUSEROVERRIDE, a, 64), 8);
    CHECK_STR(a, "English");
    CHECK_EQ(GetLocaleInfoA(LOCALE_SYSTEM_DEFAULT, LOCALE_SENGCOUNTRY, a, 64), 14);
    CHECK_STR(a, "United States");
    CHECK_EQ(GetLocaleInfoA(0x0409, LOCALE_SABBREVLANGNAME, a, 64), 4);
    CHECK_STR(a, "ENU");
    CHECK_EQ(GetLocaleInfoA(0x0409, LOCALE_SABBREVCTRYNAME, a, 64), 4);
    CHECK_STR(a, "USA");
    CHECK_EQ(GetLocaleInfoA(0x0409, LOCALE_SDECIMAL, a, 64), 2);
    CHECK_STR(a, ".");
    CHECK_EQ(GetLocaleInfoA(0x0409, LOCALE_SDAYNAME1 + 6, a, 64), 7);
    CHECK_STR(a, "Sunday");
    CHECK_EQ(GetLocaleInfoA(0x0409, LOCALE_SMONTHNAME1 + 11, a, 64), 9);
    CHECK_STR(a, "December");
    SetLastError(0);
    CHECK_EQ(GetLocaleInfoA(0x0409, LOCALE_SENGLANGUAGE, a, 7), 0);   /* "English" + NUL is 8 */
    CHECK_EQ(GetLastError(), ERROR_INSUFFICIENT_BUFFER);
    CHECK_EQ(GetLocaleInfoA(0x0409, 0x7777, a, 64), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_FLAGS);
    CHECK_EQ(GetLocaleInfoA(0x0407, LOCALE_SENGLANGUAGE, a, 64), 0);   /* German is not installed */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    WCHAR w[32];
    CHECK_EQ(GetLocaleInfoW(0x0409, LOCALE_SENGCOUNTRY, NULL, 0), 14);
    CHECK_EQ(GetLocaleInfoW(0x0409, LOCALE_SENGCOUNTRY, w, 32), 14);
    CHECK_EQ(w16len(w), 13);
    CHECK_EQ(w[0], 'U');
    CHECK_EQ(w[7], 'S');
    /* LOCALE_RETURN_NUMBER: a DWORD; the LANGID is hex */
    uint32_t num = 0;
    CHECK_EQ(GetLocaleInfoW(0x0409, LOCALE_IDEFAULTANSICODEPAGE | LOCALE_RETURN_NUMBER, (WCHAR *)&num, 2), 2);
    CHECK_EQ(num, 1252);
    CHECK_EQ(GetLocaleInfoA(0x0409, LOCALE_ILANGUAGE | LOCALE_RETURN_NUMBER, (char *)&num, 4), 4);
    CHECK_EQ(num, 0x409);
    CHECK_EQ(GetLocaleInfoA(0x0409, LOCALE_ILANGUAGE | LOCALE_RETURN_NUMBER, NULL, 0), 4);
    /* the locale set */
    CHECK_EQ(GetUserDefaultLCID(), 0x0409);
    CHECK(IsValidLocale(0x0409, 2 /* LCID_SUPPORTED */));
    CHECK(!IsValidLocale(0x0407, 2));
    g_enum_calls = 0;
    CHECK(EnumSystemLocalesA(enum_proc, 2));
    CHECK_EQ(g_enum_calls, 1);
    CHECK_STR(g_enum_id, "00000409");
    CHECK(!EnumSystemLocalesA(NULL, 2));
}

TEST(crt_k32_time_zone)
{
    TIME_ZONE_INFORMATION tz;
    memset(&tz, 0x55, sizeof tz);
    CHECK_EQ(GetTimeZoneInformation(&tz), TIME_ZONE_ID_UNKNOWN);
    CHECK_EQ(tz.Bias, -(LONG)os_tz_offset_minutes());
    CHECK_EQ(tz.StandardBias, 0);
    CHECK_EQ(tz.DaylightBias, 0);
    CHECK_EQ(tz.StandardName[0], 0);
    CHECK_EQ(tz.DaylightDate.wMonth, 0);
}

TEST(crt_k32_std_handles)
{
    HANDLE in = GetStdHandle(STD_INPUT_HANDLE), out = GetStdHandle(STD_OUTPUT_HANDLE),
           err = GetStdHandle(STD_ERROR_HANDLE);
    CHECK(in && out && err && in != INVALID_HANDLE_VALUE && out != in && err != out);
    CHECK_EQ(GetFileType(out), FILE_TYPE_CHAR);
    CHECK_EQ(GetFileType(in), FILE_TYPE_CHAR);
    DWORD mode = 7;
    SetLastError(0);
    CHECK(!GetConsoleMode(out, &mode));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    DWORD put = 0;
    CHECK(WriteFile(out, "s4 crt_k32: stdout line\r\n", 25, &put, NULL));
    CHECK_EQ(put, 25);
    CHECK(WriteFile(err, "s4 crt_k32: ", 12, &put, NULL));
    CHECK(WriteConsoleA(err, "stderr ", 7, &put, NULL));
    CHECK_EQ(put, 7);
    CHECK(WriteConsoleW(err, L"line \u00e9\n", 7, &put, NULL));
    CHECK_EQ(put, 7);
    char buf[4];
    DWORD got = 9;
    CHECK(ReadFile(in, buf, 4, &got, NULL));   /* end of file */
    CHECK_EQ(got, 0);
    CHECK(!WriteFile(in, "x", 1, &put, NULL));
    CHECK(!WriteConsoleA(in, "x", 1, &put, NULL));
    CHECK(CloseHandle(out));   /* a no-op */
    CHECK_EQ(GetFileType(out), FILE_TYPE_CHAR);
    SetLastError(0);
    CHECK(GetStdHandle(5) == INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    /* SetStdHandle: a file handle as stdout */
    s4_write_file("C:\\FalloutNV\\crt_k32_std.txt", "");
    HANDLE f = CreateFileA("C:\\FalloutNV\\crt_k32_std.txt", GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(f != INVALID_HANDLE_VALUE);
    CHECK_EQ(GetFileType(f), FILE_TYPE_DISK);
    CHECK(SetStdHandle(STD_OUTPUT_HANDLE, f));
    CHECK(GetStdHandle(STD_OUTPUT_HANDLE) == f);
    CHECK(SetStdHandle(STD_OUTPUT_HANDLE, out));
    CHECK(GetStdHandle(STD_OUTPUT_HANDLE) == out);
    CHECK(!SetStdHandle(0, out));
    CloseHandle(f);
    SetLastError(0);
    CHECK_EQ(GetFileType(f), FILE_TYPE_UNKNOWN);   /* closed */
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    HANDLE ev = CreateEventA(NULL, FALSE, FALSE, NULL);
    CHECK_EQ(GetFileType(ev), FILE_TYPE_UNKNOWN);
    CHECK_EQ(GetLastError(), ERROR_SUCCESS);
    CloseHandle(ev);
    CHECK_EQ(SetHandleCount(20), 20);
}

TEST(crt_k32_create_file_w_and_modules)
{
    s4_write_file("C:\\FalloutNV\\crt_k32_w.txt", "wide");
    HANDLE h = CreateFileW(L"c:\\falloutnv\\CRT_K32_W.TXT", GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(h != INVALID_HANDLE_VALUE);
    char buf[8] = { 0 };
    DWORD got = 0;
    CHECK(ReadFile(h, buf, 8, &got, NULL));
    CHECK_EQ(got, 4);
    CHECK_STR(buf, "wide");
    CloseHandle(h);
    SetLastError(0);
    CHECK(CreateFileW(L"C:\\FalloutNV\\\u0416.txt", GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL) ==
          INVALID_HANDLE_VALUE);
    CHECK_EQ(GetLastError(), ERROR_FILE_NOT_FOUND);
    CHECK(GetModuleHandleW(NULL) == GetModuleHandleA(NULL));
    CHECK(GetModuleHandleW(L"FalloutNV.exe") == GetModuleHandleA(NULL));
    /* the CRT's _mtinit needs kernel32; its Fls* are absent (TLS fallback), its exports resolve */
    HMODULE k = GetModuleHandleW(L"KERNEL32.DLL");
    CHECK(k != NULL);
    CHECK(GetProcAddress(k, "FlsAlloc") == NULL);
    CHECK((void *)GetProcAddress(k, "TlsAlloc") == (void *)&TlsAlloc);
    CHECK((void *)GetProcAddress(k, "MultiByteToWideChar") == (void *)&MultiByteToWideChar);
    HMODULE u = LoadLibraryA("USER32.DLL");
    CHECK(u != NULL);
    CHECK((void *)GetProcAddress(u, "MessageBoxA") == (void *)&MessageBoxA);
    CHECK(GetProcAddress(u, "GetProcessWindowStation") == NULL);
    CHECK(GetModuleHandleW(L"mscoree.dll") == NULL);
}

static int g_filter_calls;
static LONG filter(struct _EXCEPTION_POINTERS *) { g_filter_calls++; return EXCEPTION_CONTINUE_SEARCH; }
static LONG filter2(struct _EXCEPTION_POINTERS *) { return -1; /* EXCEPTION_CONTINUE_EXECUTION */ }

TEST(crt_k32_process_and_startup)
{
    STARTUPINFOA si;
    memset(&si, 0x55, sizeof si);
    GetStartupInfoA(&si);
    CHECK_EQ(si.cb, sizeof si);
    CHECK(si.lpReserved2 == NULL);
    CHECK_EQ(si.cbReserved2, 0);
    CHECK_EQ(si.dwFlags, 0);
    CHECK(si.hStdOutput == NULL);
    CHECK(SetUnhandledExceptionFilter(filter) == NULL);
    CHECK(SetUnhandledExceptionFilter(filter2) == filter);
    CHECK(SetUnhandledExceptionFilter(filter) == filter2);
    char fake[16];
    g_filter_calls = 0;
    CHECK_EQ(UnhandledExceptionFilter((struct _EXCEPTION_POINTERS *)fake), EXCEPTION_EXECUTE_HANDLER);
    CHECK_EQ(g_filter_calls, 1);
    SetUnhandledExceptionFilter(filter2);
    CHECK_EQ(UnhandledExceptionFilter((struct _EXCEPTION_POINTERS *)fake), -1);
    CHECK(SetUnhandledExceptionFilter(NULL) == filter2);
    CHECK_EQ(UnhandledExceptionFilter((struct _EXCEPTION_POINTERS *)fake), EXCEPTION_EXECUTE_HANDLER);
    /* TerminateProcess: other handles fail; the current process exits with the code */
    SetLastError(0);
    CHECK(!TerminateProcess((HANDLE)(uintptr_t)0x1234, 3));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    fflush(NULL);
    pid_t pid = fork();
    if (pid == 0) {
        TerminateProcess(GetCurrentProcess(), 42);
        _exit(1);
    }
    int st = 0;
    CHECK(pid > 0 && waitpid(pid, &st, 0) == pid);
    CHECK(WIFEXITED(st) && WEXITSTATUS(st) == 42);
}

TEST(crt_k32_drives_user_class)
{
    CHECK_EQ(GetDriveTypeA(NULL), DRIVE_FIXED);
    CHECK_EQ(GetDriveTypeA("C:\\"), DRIVE_FIXED);
    CHECK_EQ(GetDriveTypeA("c:/"), DRIVE_FIXED);
    CHECK_EQ(GetDriveTypeA("Q:\\"), DRIVE_NO_ROOT_DIR);
    CHECK_EQ(GetDriveTypeA("C:\\FalloutNV\\"), DRIVE_NO_ROOT_DIR);
    CHECK_EQ(GetDriveTypeA("foo"), DRIVE_NO_ROOT_DIR);
    char name[16];
    DWORD n = 3;
    SetLastError(0);
    CHECK(!GetUserNameA(name, &n));
    CHECK_EQ(GetLastError(), ERROR_INSUFFICIENT_BUFFER);
    CHECK_EQ(n, 7);
    CHECK(GetUserNameA(name, &n));
    CHECK_EQ(n, 7);
    CHECK_STR(name, "Player");
    CHECK_EQ(GetClassLongA(NULL, -12 /* GCL_HCURSOR */), 0);
}

TEST(crt_k32_environment)
{
    char *e = GetEnvironmentStrings();
    CHECK(e != NULL);
    size_t base_total = 0;
    int base_count = 0;
    for (char *p = e; *p; p += strlen(p) + 1, base_count++)   /* the Windows session's own variables (test_procenv.cpp) */
        base_total += strlen(p) + 1;
    CHECK(base_count > 0);
    CHECK(FreeEnvironmentStringsA(e));
    char buf[16];
    SetLastError(0);
    CHECK_EQ(GetEnvironmentVariableA("FNVNX_K32", buf, 16), 0);
    CHECK_EQ(GetLastError(), ERROR_ENVVAR_NOT_FOUND);
    CHECK(SetEnvironmentVariableA("FNVNX_K32", "on\xe9"));
    CHECK(SetEnvironmentVariableA("OTHER", "2"));
    CHECK_EQ(GetEnvironmentVariableA("fnvnx_k32", buf, 16), 3);
    CHECK_STR(buf, "on\xe9");
    CHECK_EQ(GetEnvironmentVariableA("FNVNX_K32", buf, 3), 4);   /* the size needed, NUL included */
    CHECK_EQ(GetEnvironmentVariableA("FNVNX_K32", NULL, 0), 4);
    CHECK(!SetEnvironmentVariableA("A=B", "x"));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    e = GetEnvironmentStrings();
    size_t total = 0;
    int count = 0;
    for (char *p = e; *p; p += strlen(p) + 1, count++)
        total += strlen(p) + 1;
    CHECK_EQ(count, base_count + 2);
    CHECK_EQ(total, base_total + strlen("FNVNX_K32=on\xe9") + 1 + strlen("OTHER=2") + 1);
    FreeEnvironmentStringsA(e);
    WCHAR *w = GetEnvironmentStringsW();
    CHECK(w != NULL);
    if (w) {
        const WCHAR *last = w, *prev = w;   /* the two set above are the last two, in the order set */
        for (const WCHAR *q = w; *q; q += w16len(q) + 1) {
            prev = last;
            last = q;
        }
        CHECK_EQ(w16len(prev), 13);
        CHECK_EQ(prev[12], 0xE9);
        CHECK_EQ(w16len(last), 7);
        /* the CRT sizes the block by WideCharToMultiByte up to the double NUL */
        const WCHAR *q = w;
        while (*q)
            q += w16len(q) + 1;
        CHECK_EQ(WideCharToMultiByte(CP_ACP, 0, w, (int)(q - w + 1), NULL, 0, NULL, NULL), (int)total + 1);
    }
    CHECK(FreeEnvironmentStringsW(w));
    CHECK(SetEnvironmentVariableA("FNVNX_K32", NULL));
    CHECK(SetEnvironmentVariableA("OTHER", NULL));
    CHECK(!SetEnvironmentVariableA("NEVER_SET", NULL));   /* deleting what is not there fails */
    CHECK_EQ(GetLastError(), ERROR_ENVVAR_NOT_FOUND);
    CHECK_EQ(GetEnvironmentVariableA("FNVNX_K32", buf, 16), 0);
    CHECK_EQ(GetLastError(), ERROR_ENVVAR_NOT_FOUND);
    e = GetEnvironmentStrings();
    int n = 0;
    for (char *p = e; e && *p; p += strlen(p) + 1)
        n++;
    CHECK_EQ(n, base_count);
    FreeEnvironmentStringsA(e);
}

TEST(crt_k32_exports)
{
    static const char *const k32[] = {
        "GetDriveTypeA", "MultiByteToWideChar", "WideCharToMultiByte", "TerminateProcess",
        "UnhandledExceptionFilter", "SetUnhandledExceptionFilter", "GetStartupInfoA", "GetModuleHandleW",
        "LCMapStringA", "LCMapStringW", "GetCPInfo", "GetStdHandle", "GetACP", "GetOEMCP", "IsValidCodePage",
        "SetEnvironmentVariableA", "CompareStringW", "SetHandleCount", "GetFileType", "GetConsoleCP",
        "CompareStringA", "CreateFileW", "WriteConsoleW", "GetConsoleOutputCP", "WriteConsoleA", "SetStdHandle",
        "GetTimeZoneInformation", "GetLocaleInfoW", "GetStringTypeW", "GetStringTypeA", "IsValidLocale",
        "GetConsoleMode", "FreeEnvironmentStringsA", "GetEnvironmentStrings", "FreeEnvironmentStringsW",
        "GetEnvironmentStringsW", "EnumSystemLocalesA", "GetUserDefaultLCID", "GetLocaleInfoA",
    };
    for (const char *n : k32)
        CHECK(fnvnx_win32_lookup_import("KERNEL32.dll", n, FNVNX_IMPORT_BY_NAME) != NULL);
    CHECK(fnvnx_win32_lookup_export("ADVAPI32.dll", "GetUserNameA") == (void *)&GetUserNameA);
    CHECK(fnvnx_win32_lookup_export("USER32.dll", "GetClassLongA") == (void *)&GetClassLongA);
    CHECK(fnvnx_win32_lookup_export("KERNEL32.dll", "GetLocaleInfoA") == (void *)&GetLocaleInfoA);
    CHECK(fnvnx_win32_lookup_export("KERNEL32.dll", "NoSuchExport") == NULL);   /* no resolver loop */
}
