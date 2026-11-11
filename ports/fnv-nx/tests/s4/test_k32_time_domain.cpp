/* test_k32_time_domain.cpp - FILETIME/SYSTEMTIME conversions over their whole domain and beyond it, as Wine 9.0 answers.
 * Sources: wine-9.0:dlls/ntdll/time.c:RtlTimeToTimeFields / RtlTimeFieldsToTime, wine-9.0:dlls/kernelbase/file.c:
 * FileTimeToSystemTime / SystemTimeToFileTime / FileTimeToLocalFileTime / CompareFileTime,
 * wine-9.0:dlls/kernelbase/locale.c:SystemTimeToTzSpecificLocalTime.
 * Oracles (not the code under test): the expected FILETIMEs and the weekdays come from a proleptic-Gregorian day count
 * from 1601 (a calendar oracle, checked on every in-domain case); the two cases where Wine 9.0 answers
 * beyond the documented domain (February 29 of every year; years 30828..32767 with the tick count wrapping) expect
 * Windows' documented FALSE + ERROR_INVALID_PARAMETER instead. The test defines no symbol of the platform. */
#include "s4_test.h"

#include <stdlib.h>
#include <time.h>

#include "../../platform/win32/w32.h"

namespace {
struct st_case { WORD y, mo, d, h, mi, s, ms; int ok; unsigned long long ft; };
const st_case st_cases[] = {
    /* in the documented domain 1601..30827 */
    { 1601, 1, 1, 0, 0, 0, 0, 1, 0x0000000000000000ULL },
    { 1601, 12, 31, 23, 59, 59, 999, 1, 0x00011ED178C698F0ULL },
    { 1604, 2, 29, 0, 0, 0, 0, 1, 0x00038AD130B38000ULL },
    { 1900, 3, 1, 0, 0, 0, 0, 1, 0x014F6598C43F8000ULL },
    { 2000, 2, 29, 0, 0, 0, 0, 1, 0x01BF8247EBCC8000ULL },
    { 2024, 2, 29, 12, 34, 56, 789, 1, 0x01DA6B0BB3E67C50ULL },
    { 1970, 1, 1, 0, 0, 0, 0, 1, 0x019DB1DED53E8000ULL },
    { 9999, 12, 31, 23, 59, 59, 999, 1, 0x24C85A5ED1C018F0ULL },
    { 30827, 12, 31, 23, 59, 59, 999, 1, 0x7FFF35F4F06C58F0ULL },
    /* above 30827: Wine accepts to year 32767 (the tick count wraps); Windows and the documentation reject, as here */
    { 30828, 1, 1, 0, 0, 0, 0, 0, 0 },
    { 30828, 9, 14, 2, 48, 5, 477, 0, 0 },
    { 30828, 9, 14, 2, 48, 5, 478, 0, 0 },
    { 30828, 9, 15, 0, 0, 0, 0, 0, 0 },
    { 32767, 12, 31, 23, 59, 59, 999, 0, 0 },
    /* Wine indexes the month table with (Month == 2 || leap), so it takes February 29 of every year; Windows rejects it, as here */
    { 1900, 2, 29, 0, 0, 0, 0, 0, 0 },
    { 2001, 2, 29, 0, 0, 0, 0, 0, 0 },
    { 2100, 2, 29, 0, 0, 0, 0, 0, 0 },
    /* invalid fields: FALSE, ERROR_INVALID_PARAMETER */
    { 1600, 12, 31, 23, 59, 59, 999, 0, 0 },
    { 0, 1, 1, 0, 0, 0, 0, 0, 0 },
    { 2024, 0, 1, 0, 0, 0, 0, 0, 0 },
    { 2024, 13, 1, 0, 0, 0, 0, 0, 0 },
    { 2024, 1, 0, 0, 0, 0, 0, 0, 0 },
    { 2024, 1, 32, 0, 0, 0, 0, 0, 0 },
    { 2024, 4, 31, 0, 0, 0, 0, 0, 0 },
    { 2024, 2, 30, 0, 0, 0, 0, 0, 0 },
    { 2024, 1, 1, 24, 0, 0, 0, 0, 0 },
    { 2024, 1, 1, 0, 60, 0, 0, 0, 0 },
    { 2024, 1, 1, 0, 0, 60, 0, 0, 0 },
    { 2024, 1, 1, 0, 0, 0, 1000, 0, 0 },
    { 32768, 1, 1, 0, 0, 0, 0, 0, 0 },      /* the field is a CSHORT there: negative */
    { 65535, 1, 1, 0, 0, 0, 0, 0, 0 },
    { 2024, 1, 65535, 0, 0, 0, 0, 0, 0 },
    { 2024, 1, 1, 0, 0, 0, 65535, 0, 0 },
};

struct ft_case { unsigned long long ft; WORD y, mo, d, h, mi, s, ms, wd; };
const ft_case ft_cases[] = {
    { 0x0000000000000000ULL, 1601, 1, 1, 0, 0, 0, 0, 1 },
    { 0x0000000000000001ULL, 1601, 1, 1, 0, 0, 0, 0, 1 },
    { 0x000000000000270FULL, 1601, 1, 1, 0, 0, 0, 0, 1 },
    { 0x0000000000002710ULL, 1601, 1, 1, 0, 0, 0, 1, 1 },
    { 0x000000000098967FULL, 1601, 1, 1, 0, 0, 0, 999, 1 },
    { 0x0000000000989680ULL, 1601, 1, 1, 0, 0, 1, 0, 1 },
    { 0x0000000100000000ULL, 1601, 1, 1, 0, 7, 9, 496, 1 },
    { 0x019DB1DED53E8000ULL, 1970, 1, 1, 0, 0, 0, 0, 4 },
    { 0x01D4C3A0F8B6F000ULL, 2019, 2, 13, 13, 35, 23, 550, 3 },
    { 0x7FFFFFFFFFFFE94FULL, 30828, 9, 14, 2, 48, 5, 476, 4 },
    { 0x7FFFFFFFFFFFE950ULL, 30828, 9, 14, 2, 48, 5, 477, 4 },
    { 0x7FFFFFFFFFFFFFFFULL, 30828, 9, 14, 2, 48, 5, 477, 4 },
};

SYSTEMTIME mk(const st_case &c)
{
    SYSTEMTIME s;
    memset(&s, 0xAA, sizeof s);
    s.wYear = c.y; s.wMonth = c.mo; s.wDay = c.d; s.wHour = c.h; s.wMinute = c.mi; s.wSecond = c.s; s.wMilliseconds = c.ms;
    s.wDayOfWeek = 7;   /* ignored on input */
    return s;
}
FILETIME mkft(unsigned long long v) { FILETIME f; w32_set_ft(&f, v); return f; }
}

TEST(k32t_systemtime_to_filetime_domain)
{
    for (size_t i = 0; i < sizeof st_cases / sizeof *st_cases; i++) {
        const st_case &c = st_cases[i];
        SYSTEMTIME s = mk(c);
        FILETIME f = mkft(0x5A5A5A5A5A5A5A5AULL);
        SetLastError(0);
        BOOL r = SystemTimeToFileTime(&s, &f);
        s4_checks++;
        if ((r != FALSE) != (c.ok != 0)) {
            s4_failures++;
            fprintf(stderr, "  FAIL case %zu (%u-%u-%u): result %d want %d\n", i, c.y, c.mo, c.d, (int)r, c.ok);
            continue;
        }
        if (c.ok)
            CHECK_EQ(w32_ft64(&f), c.ft);
        else {
            CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
            CHECK_EQ(w32_ft64(&f), 0x5A5A5A5A5A5A5A5AULL);   /* the output is not written on failure */
        }
    }
}

TEST(k32t_filetime_to_systemtime_domain)
{
    for (size_t i = 0; i < sizeof ft_cases / sizeof *ft_cases; i++) {
        const ft_case &c = ft_cases[i];
        FILETIME f = mkft(c.ft);
        SYSTEMTIME s;
        memset(&s, 0xAA, sizeof s);
        BOOL r = FileTimeToSystemTime(&f, &s);
        CHECK(r);
        s4_checks++;
        if (s.wYear != c.y || s.wMonth != c.mo || s.wDay != c.d || s.wHour != c.h || s.wMinute != c.mi || s.wSecond != c.s ||
            s.wMilliseconds != c.ms || s.wDayOfWeek != c.wd) {
            s4_failures++;
            fprintf(stderr, "  FAIL ft case %zu: %u-%u-%u %u:%u:%u.%u wd %u\n", i, s.wYear, s.wMonth, s.wDay, s.wHour, s.wMinute,
                    s.wSecond, s.wMilliseconds, s.wDayOfWeek);
        }
    }
    /* a FILETIME above 0x7FFFFFFFFFFFFFFF is negative as a LARGE_INTEGER: FALSE + ERROR_INVALID_PARAMETER, output untouched */
    static const unsigned long long bad[] = { 0x8000000000000000ULL, 0x8000000000000001ULL, 0xFFFFFFFFFFFFFFFFULL };
    for (size_t i = 0; i < sizeof bad / sizeof *bad; i++) {
        FILETIME f = mkft(bad[i]);
        SYSTEMTIME s;
        memset(&s, 0xAA, sizeof s);
        SetLastError(0);
        CHECK(!FileTimeToSystemTime(&f, &s));
        CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
        CHECK_EQ(s.wYear, 0xAAAA);
    }
}

TEST(k32t_roundtrip_both_ends)
{
    /* FILETIME -> SYSTEMTIME -> FILETIME is the identity on whole milliseconds over the domain, at both ends and in between */
    static const unsigned long long v[] = { 0ULL, 10000ULL, 0x00011ED178C698F0ULL, 0x019DB1DED53E8000ULL, 0x24C85A5ED1C018F0ULL,
                                            0x7FFF35F4F06C58F0ULL };
    for (size_t i = 0; i < sizeof v / sizeof *v; i++) {
        FILETIME f = mkft(v[i]), g;
        SYSTEMTIME s;
        CHECK(FileTimeToSystemTime(&f, &s));
        CHECK(SystemTimeToFileTime(&s, &g));
        CHECK_EQ(w32_ft64(&g), v[i]);
    }
    /* the largest FILETIME is in year 30828, outside SYSTEMTIME's documented domain: FileTimeToSystemTime gives it, the way back fails */
    FILETIME f = mkft(0x7FFFFFFFFFFFFFFFULL), g;
    SYSTEMTIME s;
    CHECK(FileTimeToSystemTime(&f, &s));
    SetLastError(0);
    CHECK(!SystemTimeToFileTime(&s, &g));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
}

TEST(k32t_compare_filetime)
{
    FILETIME a = mkft(0x0000000100000000ULL), b = mkft(0x00000000FFFFFFFFULL), c = mkft(0x0000000100000000ULL);
    CHECK_EQ(CompareFileTime(&a, &b), 1);
    CHECK_EQ(CompareFileTime(&b, &a), -1);
    CHECK_EQ(CompareFileTime(&a, &c), 0);
    FILETIME hi = mkft(0x8000000000000000ULL), lo = mkft(0x7FFFFFFFFFFFFFFFULL);   /* the parts compare unsigned */
    CHECK_EQ(CompareFileTime(&hi, &lo), 1);
    CHECK_EQ(CompareFileTime(&lo, &hi), -1);
    CHECK_EQ(CompareFileTime(NULL, &a), -1);   /* wine-9.0:CompareFileTime: NULL on either side is -1 */
    CHECK_EQ(CompareFileTime(&a, NULL), -1);
    CHECK_EQ(CompareFileTime(NULL, NULL), -1);
}

TEST(k32t_filetime_to_local_wrap)
{
    /* FileTimeToLocalFileTime is utc + the zone's offset and always TRUE (RtlSystemTimeToLocalTime is STATUS_SUCCESS), the
     * 64-bit sum wrapping; the declared machine's zone here is JST (+9 h) */
    setenv("TZ", "JST-9", 1);
    tzset();
    FILETIME in = mkft(0), out;
    CHECK(FileTimeToLocalFileTime(&in, &out));
    CHECK_EQ(w32_ft64(&out), 9ULL * 3600 * 10000000);
    in = mkft(0x7FFFFFFFFFFFFFFFULL);
    CHECK(FileTimeToLocalFileTime(&in, &out));
    CHECK_EQ(w32_ft64(&out), 0x7FFFFFFFFFFFFFFFULL + 9ULL * 3600 * 10000000);   /* wraps into the sign bit */
    SYSTEMTIME s;
    SetLastError(0);
    CHECK(!FileTimeToSystemTime(&out, &s));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    unsetenv("TZ");
    tzset();
}

namespace {
TIME_ZONE_INFORMATION mktz(LONG bias, LONG sbias, LONG dbias, WORD sm, WORD sw, WORD sh, WORD dm, WORD dw, WORD dh)
{
    TIME_ZONE_INFORMATION t;
    memset(&t, 0, sizeof t);
    t.Bias = bias; t.StandardBias = sbias; t.DaylightBias = dbias;
    t.StandardDate.wMonth = sm; t.StandardDate.wDay = sw; t.StandardDate.wHour = sh;   /* wDayOfWeek 0 = Sunday */
    t.DaylightDate.wMonth = dm; t.DaylightDate.wDay = dw; t.DaylightDate.wHour = dh;
    return t;
}
}

TEST(k32t_systemtime_to_tz_specific)
{
    SYSTEMTIME in, out;
    /* no daylight date (TIME_ZONE_ID_UNKNOWN): local = utc - Bias */
    TIME_ZONE_INFORMATION jst = mktz(-540, 0, 0, 0, 0, 0, 0, 0, 0);
    in = mk({ 1601, 1, 1, 0, 0, 0, 0, 1, 0 });
    CHECK(SystemTimeToTzSpecificLocalTime(&jst, &in, &out));
    CHECK_EQ(out.wYear, 1601); CHECK_EQ(out.wDay, 1); CHECK_EQ(out.wHour, 9); CHECK_EQ(out.wDayOfWeek, 1);
    /* before 1601 after the shift: the local FILETIME is negative, FileTimeToSystemTime fails */
    TIME_ZONE_INFORMATION est_flat = mktz(300, 0, 0, 0, 0, 0, 0, 0, 0);
    SetLastError(0);
    CHECK(!SystemTimeToTzSpecificLocalTime(&est_flat, &in, &out));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    /* past the end: 30828-09-14 02:48:05.477 + 9 h exceeds 0x7FFF...: FALSE */
    in = mk({ 30828, 9, 14, 2, 48, 5, 477, 1, 0 });
    SetLastError(0);
    CHECK(!SystemTimeToTzSpecificLocalTime(&jst, &in, &out));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    /* invalid input fields */
    in = mk({ 2024, 13, 1, 0, 0, 0, 0, 1, 0 });
    SetLastError(0);
    CHECK(!SystemTimeToTzSpecificLocalTime(&jst, &in, &out));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    /* a daylight date without a standard date, and a weekday-rule week outside 1..5: TIME_ZONE_ID_INVALID there, FALSE here */
    in = mk({ 2024, 6, 1, 0, 0, 0, 0, 1, 0 });
    TIME_ZONE_INFORMATION badz = mktz(300, 0, -60, 0, 0, 0, 3, 2, 2);
    SetLastError(0);
    CHECK(!SystemTimeToTzSpecificLocalTime(&badz, &in, &out));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    badz = mktz(300, 0, -60, 11, 0, 2, 3, 2, 2);
    SetLastError(0);
    CHECK(!SystemTimeToTzSpecificLocalTime(&badz, &in, &out));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    /* US Eastern: standard from the first Sunday of November 02:00, daylight from the second Sunday of March 02:00 */
    TIME_ZONE_INFORMATION est = mktz(300, 0, -60, 11, 1, 2, 3, 2, 2);
    in = mk({ 2024, 7, 1, 12, 0, 0, 0, 1, 0 });      /* summer: UTC-4 */
    CHECK(SystemTimeToTzSpecificLocalTime(&est, &in, &out));
    CHECK_EQ(out.wMonth, 7); CHECK_EQ(out.wDay, 1); CHECK_EQ(out.wHour, 8);
    in = mk({ 2024, 1, 15, 12, 0, 0, 0, 1, 0 });     /* winter: UTC-5 */
    CHECK(SystemTimeToTzSpecificLocalTime(&est, &in, &out));
    CHECK_EQ(out.wDay, 15); CHECK_EQ(out.wHour, 7);
    in = mk({ 2024, 12, 31, 3, 0, 0, 0, 1, 0 });     /* across midnight, winter: previous day 22:00 */
    CHECK(SystemTimeToTzSpecificLocalTime(&est, &in, &out));
    CHECK_EQ(out.wDay, 30); CHECK_EQ(out.wHour, 22);
}

TEST(k32t_getsystemtime_in_domain)
{
    SYSTEMTIME a;
    memset(&a, 0xAA, sizeof a);
    GetSystemTime(&a);
    CHECK(a.wYear >= 1601 && a.wYear <= 30827);
    CHECK(a.wMonth >= 1 && a.wMonth <= 12);
    CHECK(a.wDay >= 1 && a.wDay <= 31);
    CHECK(a.wHour < 24 && a.wMinute < 60 && a.wSecond < 60 && a.wMilliseconds < 1000 && a.wDayOfWeek < 7);
    FILETIME f;
    GetSystemTimeAsFileTime(&f);
    SYSTEMTIME b;
    CHECK(FileTimeToSystemTime(&f, &b));
    CHECK(b.wYear >= a.wYear);
}
