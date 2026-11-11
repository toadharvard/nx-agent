/* test_k32_contract_tz.cpp - GetTimeZoneInformation and the local-time conversions on one rule (task W20 group 6).
 * ENV L2: the zone comes from the platform's time zone data; the host backend reads it from TZ, so the tests run the
 * same checks in zones that differ in hemisphere, rule form and daylight time. The oracle for the converted times is
 * the C library's own conversion (localtime_r), independent of the rule the platform derives. */
#include "s4_test.h"

#include <stdlib.h>
#include <time.h>

#include "../../platform/win32/w32.h"

struct tz_zone {
    const char *tz;                 /* POSIX TZ string */
    LONG bias, dbias;               /* Bias, DaylightBias */
    bool dst;
    WORD sm, sw, sh, dm, dw, dh;    /* StandardDate month/week/hour, DaylightDate month/week/hour (recurring zones) */
    WORD swd, dwd;                  /* their DayOfWeek */
    const char *sname, *dname;
};

static void set_zone(const char *tz)
{
    if (tz)
        setenv("TZ", tz, 1);
    else
        unsetenv("TZ");
    tzset();
}

static int64_t libc_offset(int64_t utc)
{
    time_t t = (time_t)utc;
    struct tm lt;
    localtime_r(&t, &lt);
    return lt.tm_gmtoff;
}

static void check_conversions(const char *what)
{
    /* every 6 hours over three years: the converted time is UTC + the C library's offset at that instant */
    int bad = 0, n = 0;
    for (int64_t utc = 1735689600 /* 2025-01-01 */; utc < 1735689600 + 3 * 365 * 86400; utc += 6 * 3600 + 17 * 60) {
        SYSTEMTIME su, sl;
        FILETIME fu;
        w32_set_ft(&fu, w32_unix_us_to_filetime(utc * 1000000));
        FileTimeToSystemTime(&fu, &su);
        if (!SystemTimeToTzSpecificLocalTime(NULL, &su, &sl)) {
            bad++;
            continue;
        }
        FILETIME fl;
        SystemTimeToFileTime(&sl, &fl);
        int64_t got = ((int64_t)w32_ft64(&fl) - (int64_t)w32_ft64(&fu)) / 10000000;
        n++;
        if (got != libc_offset(utc)) {
            if (bad < 3)
                fprintf(stderr, "  %s: at %lld local-utc %lld want %lld\n", what, (long long)utc, (long long)got, (long long)libc_offset(utc));
            bad++;
        }
    }
    s4_checks++;
    if (bad) {
        s4_failures++;
        fprintf(stderr, "  FAIL %s: %d of %d conversions differ\n", what, bad, n);
    }
}

TEST(k32c_timezone_rules)
{
    static const tz_zone zones[] = {
        { "UTC0", 0, 0, false, 0, 0, 0, 0, 0, 0, 0, 0, "UTC", "UTC" },
        { "JST-9", -540, 0, false, 0, 0, 0, 0, 0, 0, 0, 0, "JST", "JST" },
        { "EST5EDT,M3.2.0,M11.1.0", 300, -60, true, 11, 1, 2, 3, 2, 2, 0, 0, "EST", "EDT" },
        { "CET-1CEST,M3.5.0/2,M10.5.0/3", -60, -60, true, 10, 5, 3, 3, 5, 2, 0, 0, "CET", "CEST" },
        { "AEST-10AEDT,M10.1.0,M4.1.0/3", -600, -60, true, 4, 1, 3, 10, 1, 2, 0, 0, "AEST", "AEDT" },
        { "IST-5:30", -330, 0, false, 0, 0, 0, 0, 0, 0, 0, 0, "IST", "IST" },
        { "NST3:30NDT,M3.2.0/0:01,M11.1.0/0:01", 210, -60, true, 11, 1, 0, 3, 2, 0, 0, 0, "NST", "NDT" },
    };
    for (size_t i = 0; i < sizeof zones / sizeof *zones; i++) {
        const tz_zone &z = zones[i];
        set_zone(z.tz);
        TIME_ZONE_INFORMATION tz;
        memset(&tz, 0xAA, sizeof tz);
        DWORD id = GetTimeZoneInformation(&tz);
        s4_checks++;
        if (tz.Bias != z.bias || tz.StandardBias != 0 || tz.DaylightBias != (z.dst ? z.dbias : 0)) {
            s4_failures++;
            fprintf(stderr, "  FAIL %s: Bias %ld StdBias %ld DstBias %ld\n", z.tz, (long)tz.Bias, (long)tz.StandardBias, (long)tz.DaylightBias);
        }
        char sn[40] = "", dn[40] = "";
        for (int k = 0; tz.StandardName[k] && k < 32; k++) sn[k] = (char)tz.StandardName[k];
        for (int k = 0; tz.DaylightName[k] && k < 32; k++) dn[k] = (char)tz.DaylightName[k];
        CHECK_STR(sn, z.sname);
        CHECK_STR(dn, z.dname);
        if (!z.dst) {
            CHECK_EQ(id, TIME_ZONE_ID_UNKNOWN);
            CHECK_EQ(tz.StandardDate.wMonth, 0);
            CHECK_EQ(tz.DaylightDate.wMonth, 0);
        } else {
            CHECK_EQ(tz.StandardDate.wYear, 0);
            CHECK_EQ(tz.StandardDate.wMonth, z.sm);
            CHECK_EQ(tz.StandardDate.wDay, z.sw);
            CHECK_EQ(tz.StandardDate.wHour, z.sh);
            CHECK_EQ(tz.StandardDate.wDayOfWeek, z.swd);
            CHECK_EQ(tz.DaylightDate.wYear, 0);
            CHECK_EQ(tz.DaylightDate.wMonth, z.dm);
            CHECK_EQ(tz.DaylightDate.wDay, z.dw);
            CHECK_EQ(tz.DaylightDate.wHour, z.dh);
            CHECK_EQ(tz.DaylightDate.wDayOfWeek, z.dwd);
            /* the return value is the state now: daylight iff the zone's present offset is the daylight one */
            bool dst_now = os_tz_offset_minutes() == -(tz.Bias + tz.DaylightBias);
            CHECK_EQ(id, dst_now ? 2 : 1);
        }
        check_conversions(z.tz);
        /* GetLocalTime and FileTimeToLocalFileTime apply the present offset */
        SYSTEMTIME a, b;
        GetSystemTime(&a);
        GetLocalTime(&b);
        FILETIME fa, fb, fl;
        SystemTimeToFileTime(&a, &fa);
        SystemTimeToFileTime(&b, &fb);
        int64_t d = ((int64_t)w32_ft64(&fb) - (int64_t)w32_ft64(&fa)) / 600000000;
        CHECK(d == os_tz_offset_minutes() || d == os_tz_offset_minutes() + 1 || d == os_tz_offset_minutes() - 1);   /* the clock moved */
        FileTimeToLocalFileTime(&fa, &fl);
        CHECK_EQ((int64_t)(w32_ft64(&fl) - w32_ft64(&fa)), (int64_t)os_tz_offset_minutes() * 600000000ll);
    }
    CHECK_EQ(GetTimeZoneInformation(NULL), 0xFFFFFFFFu);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    set_zone(NULL);
}

TEST(k32c_timezone_absolute_dates)
{
    /* Julian-day rules are fixed calendar dates: no recurring weekday rule reproduces them, so the dates are absolute
     * (wYear set) and the conversion agrees with the C library in the year of the rule */
    set_zone("XST5XDT,J100/2,J300/2");
    TIME_ZONE_INFORMATION tz;
    DWORD id = GetTimeZoneInformation(&tz);
    CHECK(id == 1 || id == 2);
    CHECK(tz.StandardDate.wYear != 0);
    CHECK(tz.DaylightDate.wYear != 0);
    CHECK_EQ(tz.Bias, 300);
    CHECK_EQ(tz.DaylightBias, -60);
    int bad = 0;
    time_t now = time(NULL);
    struct tm nt;
    gmtime_r(&now, &nt);
    int64_t y0 = (int64_t)(now - (int64_t)nt.tm_yday * 86400 - nt.tm_hour * 3600 - nt.tm_min * 60 - nt.tm_sec);
    for (int64_t utc = y0; utc < y0 + 360 * 86400; utc += 86400 / 2 + 61) {
        SYSTEMTIME su, sl;
        FILETIME fu, fl;
        w32_set_ft(&fu, w32_unix_us_to_filetime(utc * 1000000));
        FileTimeToSystemTime(&fu, &su);
        SystemTimeToTzSpecificLocalTime(NULL, &su, &sl);
        SystemTimeToFileTime(&sl, &fl);
        if (((int64_t)w32_ft64(&fl) - (int64_t)w32_ft64(&fu)) / 10000000 != libc_offset(utc))
            bad++;
    }
    CHECK_EQ(bad, 0);
    set_zone(NULL);
}
