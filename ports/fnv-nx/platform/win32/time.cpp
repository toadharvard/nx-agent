/* time.cpp - tick counts, the performance counter, wall-clock time and FILETIME/SYSTEMTIME conversion.
 *
 * QueryPerformanceCounter/Frequency read os_ticks(): on Horizon that is CNTVCT_EL0 at CNTFRQ_EL0 (19.2 MHz),
 * the same counter S8's __rdtsc replacement reads, so code that calibrates rdtsc against QPC (Havok's
 * hkStopwatch) gets an exact ratio. On the host it is a 10 MHz monotonic counter. */
#include "w32.h"
#include <stdlib.h>
#include <string.h>

extern "C" BOOL QueryPerformanceCounter(LARGE_INTEGER *c)
{
    if (!c) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    c->QuadPart = (LONGLONG)os_ticks();
    return TRUE;
}

extern "C" BOOL QueryPerformanceFrequency(LARGE_INTEGER *f)
{
    if (!f) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    f->QuadPart = (LONGLONG)os_tick_freq();
    return TRUE;
}

static uint64_t g_boot_ns; /* GetTickCount starts at an arbitrary value on Windows too; keep it small */

extern "C" DWORD GetTickCount(void) { return W32_DW((DWORD)((os_monotonic_ns() - g_boot_ns) / 1000000ull)); }
extern "C" DWORD timeGetTime(void) { return GetTickCount(); }
/* timer resolution: the device range is 1..1000000 ms (timeGetDevCaps); timeBeginPeriod(p) adds one reference to
 * period p, timeEndPeriod(p) removes one of the same p (TIMERR_NOCANDO when there is none); the effective
 * resolution is the smallest period with references, else the default 15.6 ms. */
enum { TIMERR_NOERROR_ = 0, TIMERR_NOCANDO_ = 97, TIMERR_STRUCT_ = 129, TIME_PERIOD_MIN = 1, TIME_PERIOD_MAX = 1000000 };
struct tperiod { UINT ms, refs; };
static os_lock_t g_tp_lock;
static tperiod *g_tp;
static size_t g_tp_n;

extern "C" UINT timeGetDevCaps(void *ptc, UINT cbtc)
{
    if (!ptc)
        return 11; /* MMSYSERR_INVALPARAM */
    if (cbtc < 2 * sizeof(UINT))
        return TIMERR_STRUCT_;
    UINT c[2] = {TIME_PERIOD_MIN, TIME_PERIOD_MAX};
    memcpy(ptc, c, sizeof c);
    return TIMERR_NOERROR_;
}

extern "C" UINT timeBeginPeriod(UINT p)
{
    if (p < TIME_PERIOD_MIN || p > TIME_PERIOD_MAX)
        return TIMERR_NOCANDO_;
    UINT rc = TIMERR_NOERROR_;
    os_lock(&g_tp_lock);
    size_t i = 0;
    while (i < g_tp_n && g_tp[i].ms != p) i++;
    if (i < g_tp_n)
        g_tp[i].refs++;
    else {
        tperiod *n = (tperiod *)realloc(g_tp, (g_tp_n + 1) * sizeof *n);
        if (n) { g_tp = n; g_tp[g_tp_n++] = {p, 1}; }
        else rc = TIMERR_NOCANDO_;
    }
    os_unlock(&g_tp_lock);
    return rc;
}

extern "C" UINT timeEndPeriod(UINT p)
{
    UINT rc = TIMERR_NOCANDO_;
    os_lock(&g_tp_lock);
    for (size_t i = 0; i < g_tp_n; i++)
        if (g_tp[i].ms == p) {
            if (--g_tp[i].refs == 0)
                g_tp[i] = g_tp[--g_tp_n];
            rc = TIMERR_NOERROR_;
            break;
        }
    os_unlock(&g_tp_lock);
    return rc;
}

/* effective sleep/timer granularity in microseconds, for Sleep-style waits */
extern "C" unsigned fnvnx_timer_resolution_us(void)
{
    unsigned best = 15600;
    os_lock(&g_tp_lock);
    for (size_t i = 0; i < g_tp_n; i++)
        if (g_tp[i].ms * 1000u < best)
            best = g_tp[i].ms * 1000u;
    os_unlock(&g_tp_lock);
    return best;
}

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

static int days_in_month(int64_t y, unsigned m)
{
    static const unsigned char n[12] = { 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 };
    bool leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    return n[m - 1] + (m == 2 && leap);
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
    if (st->wMonth < 1 || st->wMonth > 12 || st->wDay < 1 || st->wHour > 23 ||
        st->wMinute > 59 || st->wSecond > 59 || st->wMilliseconds > 999 || st->wYear < 1601 || st->wYear > 30827 ||
        st->wDay > days_in_month(st->wYear, st->wMonth)) {
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

/* a TIME_ZONE_INFORMATION transition date as local seconds since 1970 in `year`: wYear != 0 is an absolute date,
 * else wDay 1..5 is the n-th (5 = last) wDayOfWeek of wMonth */
static int64_t tz_transition(int64_t year, const SYSTEMTIME *r)
{
    int day = r->wDay;
    if (!r->wYear) {
        int64_t d1 = days_from_civil(year, r->wMonth, 1);
        int dow1 = (int)((d1 % 7 + 7 + 4) % 7);
        day = 1 + (r->wDayOfWeek - dow1 + 7) % 7 + (day - 1) * 7;
        while (day > days_in_month(year, r->wMonth))
            day -= 7;
    }
    return days_from_civil(year, r->wMonth, (unsigned)day) * 86400 + r->wHour * 3600 + r->wMinute * 60 + r->wSecond;
}

extern "C" BOOL SystemTimeToTzSpecificLocalTime(LPTIME_ZONE_INFORMATION tz, const SYSTEMTIME *utc, LPSYSTEMTIME local)
{
    FILETIME ft;
    if (!utc || !local || !SystemTimeToFileTime(utc, &ft))
        return FALSE;
    int64_t off_min;   /* minutes to add to UTC */
    if (!tz) {
        off_min = os_tz_offset_minutes();   /* the current zone, as GetLocalTime applies it */
    } else {
        int64_t usecs = ((int64_t)w32_ft64(&ft) - FT_UNIX_EPOCH) / 10000000;
        int64_t std_local = usecs - (int64_t)tz->Bias * 60;
        off_min = -((int64_t)tz->Bias + tz->StandardBias);
        if (tz->StandardDate.wMonth && tz->DaylightDate.wMonth) {
            int y;
            unsigned m, d;
            civil_from_days(std_local >= 0 ? std_local / 86400 : -((-std_local + 86399) / 86400), &y, &m, &d);
            int64_t begin = tz_transition(y, &tz->DaylightDate);  /* standard wall time */
            int64_t end = tz_transition(y, &tz->StandardDate);    /* daylight wall time */
            int64_t dst_local = usecs - ((int64_t)tz->Bias + tz->DaylightBias) * 60;
            bool dst = begin < end ? (std_local >= begin && dst_local < end) : (std_local >= begin || dst_local < end);
            if (dst)
                off_min = -((int64_t)tz->Bias + tz->DaylightBias);
        }
    }
    ft_to_st((ULONGLONG)((int64_t)w32_ft64(&ft) + off_min * 600000000ll), local);
    return TRUE;
}

extern "C" LONG CompareFileTime(const FILETIME *a, const FILETIME *b)
{
    ULONGLONG x = w32_ft64(a), y = w32_ft64(b);
    return x < y ? -1 : x > y ? 1 : 0;
}

__attribute__((constructor(FNVNX_INIT_CLOCK))) static void time_init(void) { g_boot_ns = os_monotonic_ns() - 60000000000ull; }
