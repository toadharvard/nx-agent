/* time.cpp - tick counts, the performance counter, wall-clock time and FILETIME/SYSTEMTIME conversion.
 *
 * QueryPerformanceCounter/Frequency read os_ticks(): on Horizon that is CNTVCT_EL0 at CNTFRQ_EL0 (19.2 MHz),
 * the same counter S8's __rdtsc replacement reads, so code that calibrates rdtsc against QPC (Havok's
 * hkStopwatch) gets an exact ratio. On the host it is a 10 MHz monotonic counter. */
#include "w32.h"

extern "C" BOOL QueryPerformanceCounter(LARGE_INTEGER *c)
{
    c->QuadPart = (LONGLONG)os_ticks();
    return TRUE;
}

extern "C" BOOL QueryPerformanceFrequency(LARGE_INTEGER *f)
{
    f->QuadPart = (LONGLONG)os_tick_freq();
    return TRUE;
}

static uint64_t g_boot_ns; /* GetTickCount starts at an arbitrary value on Windows too; keep it small */

extern "C" DWORD GetTickCount(void) { return W32_DW((DWORD)((os_monotonic_ns() - g_boot_ns) / 1000000ull)); }
extern "C" DWORD timeGetTime(void) { return GetTickCount(); }
extern "C" UINT timeBeginPeriod(UINT p) { W32_UNUSED(p); return 0; }
extern "C" UINT timeEndPeriod(UINT p) { W32_UNUSED(p); return 0; }

/* civil date <-> days since 1970-01-01 (H. Hinnant's algorithms) */
static int64_t days_from_civil(int64_t y, unsigned m, unsigned d)
{
    y -= m <= 2;
    int64_t era = (y >= 0 ? y : y - 399) / 400;
    unsigned yoe = (unsigned)(y - era * 400);
    unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + (int64_t)doe - 719468;
}

static void civil_from_days(int64_t z, int *y, unsigned *m, unsigned *d)
{
    z += 719468;
    int64_t era = (z >= 0 ? z : z - 146096) / 146097;
    unsigned doe = (unsigned)(z - era * 146097);
    unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    int64_t yy = (int64_t)yoe + era * 400;
    unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    unsigned mp = (5 * doy + 2) / 153;
    *d = doy - (153 * mp + 2) / 5 + 1;
    *m = mp < 10 ? mp + 3 : mp - 9;
    *y = (int)(yy + (*m <= 2));
}

static const int64_t FT_UNIX_EPOCH = 116444736000000000ll; /* 1970-01-01 in FILETIME units */

static void ft_to_st(ULONGLONG ft, SYSTEMTIME *st)
{
    int64_t t = (int64_t)ft - FT_UNIX_EPOCH;          /* 100 ns since 1970 */
    int64_t secs = t >= 0 ? t / 10000000 : -((-t + 9999999) / 10000000);
    int64_t rem = t - secs * 10000000;
    int64_t days = secs >= 0 ? secs / 86400 : -((-secs + 86399) / 86400);
    int64_t sod = secs - days * 86400;
    int y;
    unsigned m, d;
    civil_from_days(days, &y, &m, &d);
    st->wYear = (WORD)y;
    st->wMonth = (WORD)m;
    st->wDay = (WORD)d;
    st->wDayOfWeek = (WORD)((days % 7 + 7 + 4) % 7); /* 1970-01-01 was a Thursday */
    st->wHour = (WORD)(sod / 3600);
    st->wMinute = (WORD)(sod / 60 % 60);
    st->wSecond = (WORD)(sod % 60);
    st->wMilliseconds = (WORD)(rem / 10000);
}

extern "C" BOOL FileTimeToSystemTime(const FILETIME *ft, LPSYSTEMTIME st)
{
    ULONGLONG v = w32_ft64(ft);
    if (v >> 63) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    ft_to_st(v, st);
    return TRUE;
}

extern "C" BOOL SystemTimeToFileTime(const SYSTEMTIME *st, LPFILETIME ft)
{
    if (st->wMonth < 1 || st->wMonth > 12 || st->wDay < 1 || st->wDay > 31 || st->wHour > 23 ||
        st->wMinute > 59 || st->wSecond > 59 || st->wMilliseconds > 999 || st->wYear < 1601) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    int64_t days = days_from_civil(st->wYear, st->wMonth, st->wDay);
    int64_t t = ((days * 86400 + st->wHour * 3600 + st->wMinute * 60 + st->wSecond) * 1000 + st->wMilliseconds) * 10000;
    w32_set_ft(ft, (ULONGLONG)(t + FT_UNIX_EPOCH));
    return TRUE;
}

extern "C" void GetSystemTimeAsFileTime(LPFILETIME ft) { w32_set_ft(ft, w32_unix_us_to_filetime(os_wall_time_us())); }

extern "C" void GetSystemTime(LPSYSTEMTIME st) { ft_to_st(w32_unix_us_to_filetime(os_wall_time_us()), st); }

extern "C" void GetLocalTime(LPSYSTEMTIME st)
{
    ft_to_st(w32_unix_us_to_filetime(os_wall_time_us() + (int64_t)os_tz_offset_minutes() * 60000000ll), st);
}

extern "C" BOOL FileTimeToLocalFileTime(const FILETIME *in, LPFILETIME out)
{
    w32_set_ft(out, w32_ft64(in) + (ULONGLONG)((int64_t)os_tz_offset_minutes() * 600000000ll));
    return TRUE;
}

extern "C" BOOL SystemTimeToTzSpecificLocalTime(LPTIME_ZONE_INFORMATION tz, const SYSTEMTIME *utc, LPSYSTEMTIME local)
{
    /* tz == NULL means the current zone; a specific zone is not supported (the game passes NULL) */
    W32_UNUSED(tz);
    FILETIME ft;
    if (!SystemTimeToFileTime(utc, &ft))
        return FALSE;
    ft_to_st(w32_ft64(&ft) + (ULONGLONG)((int64_t)os_tz_offset_minutes() * 600000000ll), local);
    return TRUE;
}

extern "C" LONG CompareFileTime(const FILETIME *a, const FILETIME *b)
{
    ULONGLONG x = w32_ft64(a), y = w32_ft64(b);
    return x < y ? -1 : x > y ? 1 : 0;
}

__attribute__((constructor(FNVNX_INIT_CLOCK))) static void time_init(void) { g_boot_ns = os_monotonic_ns() - 60000000000ull; }
