/* time.cpp - tick counts, the performance counter, wall-clock time and FILETIME/SYSTEMTIME conversion.
 *
 * QueryPerformanceCounter/Frequency read the clock interface (fnvnx_clock.h): the real clock on Horizon that is CNTVCT_EL0 at CNTFRQ_EL0 (19.2 MHz),
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
    c->QuadPart = (LONGLONG)clk_read_ticks();
    return TRUE;
}

extern "C" BOOL QueryPerformanceFrequency(LARGE_INTEGER *f)
{
    if (!f) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    f->QuadPart = (LONGLONG)clk_tick_freq();
    return TRUE;
}

extern "C" DWORD GetTickCount(void) { return W32_DW((DWORD)clk_read_tick_ms()); }
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

/* ---- FILETIME <-> SYSTEMTIME: Wine 9.0's integer arithmetic, line for line (it is the declared machine's behaviour
 * for every input, in or out of the documented domain). TIME_FIELDS members are CSHORT (int16): a SYSTEMTIME field
 * above 32767 is negative there, which the range checks reject.
 * wine-9.0:dlls/ntdll/time.c:RtlTimeToTimeFields, wine-9.0:dlls/ntdll/time.c:RtlTimeFieldsToTime. */
struct tfields { int16_t Year, Month, Day, Hour, Minute, Second, Milliseconds, Weekday; };

/* wine-9.0:dlls/ntdll/time.c:RtlTimeToTimeFields (C truncating division, as there; the caller rejects a negative time) */
static void time_to_fields(int64_t t, tfields *tf)
{
    tf->Milliseconds = (int16_t)((t % 10000000) / 10000);
    int64_t Time = t / 10000000;
    int64_t Days = Time / 86400;
    int SecondsInDay = (int)(Time % 86400);
    tf->Hour = (int16_t)(SecondsInDay / 3600);
    SecondsInDay %= 3600;
    tf->Minute = (int16_t)(SecondsInDay / 60);
    tf->Second = (int16_t)(SecondsInDay % 60);
    tf->Weekday = (int16_t)((1 + Days) % 7);
    int64_t cleaps = (3 * ((4 * Days + 1227) / (365 * 400 + 97)) + 3) / 4;
    Days += 28188 + cleaps;
    int64_t years = (20 * Days - 2442) / (5 * (365 * 4 + 1));
    int64_t yearday = Days - (years * (365 * 4 + 1)) / 4;
    int64_t months = (64 * yearday) / 1959;
    if (months < 14) {
        tf->Month = (int16_t)(months - 1);
        tf->Year = (int16_t)(years + 1524);
    } else {
        tf->Month = (int16_t)(months - 13);
        tf->Year = (int16_t)(years + 1525);
    }
    tf->Day = (int16_t)(yearday - (1959 * months) / 64);
}

static void fields_to_st(const tfields *tf, SYSTEMTIME *st)
{
    st->wYear = (WORD)tf->Year;
    st->wMonth = (WORD)tf->Month;
    st->wDay = (WORD)tf->Day;
    st->wHour = (WORD)tf->Hour;
    st->wMinute = (WORD)tf->Minute;
    st->wSecond = (WORD)tf->Second;
    st->wMilliseconds = (WORD)tf->Milliseconds;
    st->wDayOfWeek = (WORD)tf->Weekday;
}

/* wine-9.0:dlls/kernelbase/file.c:FileTimeToSystemTime: a negative FILETIME is ERROR_INVALID_PARAMETER */
static BOOL ft_to_st(int64_t ft, SYSTEMTIME *st)
{
    if (ft < 0) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    tfields tf;
    time_to_fields(ft, &tf);
    fields_to_st(&tf, st);
    return TRUE;
}

/* wine-9.0:dlls/ntdll/time.c:RtlTimeFieldsToTime, with two documented-Windows corrections (Win32 as documented, ASM-003): Wine indexes MonthLengths[Month == 2 || IsLeapYear(Year)], which gives February 29 days in every year, and accepts years
 * up to 32767 with the 64-bit tick count wrapping; Windows and the SYSTEMTIME documentation reject both (the domain is
 * 1601..30827, February 29 only in a leap year), so they return FALSE + ERROR_INVALID_PARAMETER here. */
static bool st_to_time(const SYSTEMTIME *st, int64_t *out)
{
    static const int MonthLengths[2][12] = {
        { 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 },
        { 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 } };
    int16_t Year = (int16_t)st->wYear, Month = (int16_t)st->wMonth, Day = (int16_t)st->wDay, Hour = (int16_t)st->wHour,
            Minute = (int16_t)st->wMinute, Second = (int16_t)st->wSecond, Ms = (int16_t)st->wMilliseconds;
    bool leap = Year % 4 == 0 && (Year % 100 != 0 || Year % 400 == 0);
    if (Ms < 0 || Ms > 999 || Second < 0 || Second > 59 || Minute < 0 || Minute > 59 || Hour < 0 || Hour > 23 ||
        Month < 1 || Month > 12 || Day < 1 || Day > MonthLengths[Month == 2 && leap][Month - 1] || Year < 1601 || Year > 30827)
        return false;
    int month, year;
    if (Month < 3) {
        month = Month + 13;
        year = Year - 1;
    } else {
        month = Month + 1;
        year = Year;
    }
    int cleaps = (3 * (year / 100) + 3) / 4;
    int day = (36525 * year) / 100 - cleaps + (1959 * month) / 64 + Day - 584817;
    uint64_t t = (((((uint64_t)(int64_t)day * 24 + Hour) * 60 + Minute) * 60 + Second) * 1000 + Ms) * 10000;
    *out = (int64_t)t;
    return true;
}

/* wine-9.0:dlls/kernelbase/file.c:SystemTimeToFileTime */
extern "C" BOOL SystemTimeToFileTime(const SYSTEMTIME *st, LPFILETIME ft)
{
    int64_t t;
    if (!st_to_time(st, &t)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    w32_set_ft(ft, (ULONGLONG)t);
    return TRUE;
}

extern "C" BOOL FileTimeToSystemTime(const FILETIME *ft, LPSYSTEMTIME st) { return ft_to_st((int64_t)w32_ft64(ft), st); }

/* wine-9.0:dlls/kernelbase/file.c:GetSystemTimeAsFileTime */
extern "C" void GetSystemTimeAsFileTime(LPFILETIME ft) { w32_set_ft(ft, (ULONGLONG)clk_read_filetime()); }

/* wine-9.0:dlls/kernelbase/file.c:GetSystemTime (the result of FileTimeToSystemTime is not used there) */
extern "C" void GetSystemTime(LPSYSTEMTIME st) { ft_to_st(clk_read_filetime(), st); }

/* the local time of a UTC FILETIME: wine-9.0:dlls/ntdll/time.c:RtlSystemTimeToLocalTime = utc - TimeZoneBias, the
 * bias of the declared machine's zone now (TZ_AT through clk_zone_offset_minutes: east positive, so minus the bias is
 * plus the offset); 64-bit wrapping */
static int64_t ft_utc_to_local(int64_t utc) { return (int64_t)((uint64_t)utc + (uint64_t)((int64_t)clk_zone_offset_minutes() * 600000000ll)); }

/* wine-9.0:dlls/kernelbase/file.c:GetLocalTime */
extern "C" void GetLocalTime(LPSYSTEMTIME st) { ft_to_st(ft_utc_to_local(clk_read_filetime()), st); }

/* wine-9.0:dlls/kernelbase/file.c:FileTimeToLocalFileTime (always TRUE: RtlSystemTimeToLocalTime is STATUS_SUCCESS) */
extern "C" BOOL FileTimeToLocalFileTime(const FILETIME *in, LPFILETIME out)
{
    w32_set_ft(out, (ULONGLONG)ft_utc_to_local((int64_t)w32_ft64(in)));
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

/* is utc (seconds since 1970) in daylight time under the rule of tz? (the one rule, applied to the year of that instant:
 * the historical rule is the present rule, as Windows 7 applies a TIME_ZONE_INFORMATION without dynamic DST data) */
static bool tz_is_dst(const TIME_ZONE_INFORMATION *tz, int64_t utc)
{
    if (!tz->StandardDate.wMonth || !tz->DaylightDate.wMonth)
        return false;
    int64_t std_local = utc - (int64_t)tz->Bias * 60;
    int y;
    unsigned m, d;
    civil_from_days(std_local >= 0 ? std_local / 86400 : -((-std_local + 86399) / 86400), &y, &m, &d);
    int64_t begin = tz_transition(y, &tz->DaylightDate);  /* standard wall time */
    int64_t end = tz_transition(y, &tz->StandardDate);    /* daylight wall time */
    int64_t dst_local = utc - ((int64_t)tz->Bias + tz->DaylightBias) * 60;
    return begin < end ? (std_local >= begin && dst_local < end) : (std_local >= begin || dst_local < end);
}

static void tz_date(SYSTEMTIME *st, const clk_tzdate_t *d)
{
    memset(st, 0, sizeof *st);
    st->wYear = d->year;
    st->wMonth = d->month;
    st->wDayOfWeek = d->wday;
    st->wDay = d->week;
    st->wHour = d->hour;
    st->wMinute = d->minute;
}

/* The active zone (ENV L2: the Switch time service; the OS layer derives its rule): Bias in minutes west of UTC,
 * StandardDate = when standard time begins (daylight wall time), DaylightDate = when daylight time begins (standard wall
 * time), DaylightBias the daylight offset's difference to standard; the names are the zone's abbreviations. Without
 * daylight time the dates are zero (TIME_ZONE_ID_UNKNOWN). */
static bool tz_current(TIME_ZONE_INFORMATION *tz)
{
    clk_tzrule_t r;
    memset(tz, 0, sizeof *tz);
    if (clk_tz_rule(&r))
        return false;
    tz->Bias = -(LONG)r.std_off_min;
    for (int i = 0; i < 8 && r.std_name[i]; i++)
        tz->StandardName[i] = (WCHAR)(unsigned char)r.std_name[i];
    for (int i = 0; i < 8 && r.dst_name[i]; i++)
        tz->DaylightName[i] = (WCHAR)(unsigned char)r.dst_name[i];
    if (r.has_dst) {
        tz->DaylightBias = -(LONG)(r.dst_off_min - r.std_off_min);
        tz_date(&tz->StandardDate, &r.dst_end);
        tz_date(&tz->DaylightDate, &r.dst_begin);
    }
    return true;
}

extern "C" DWORD GetTimeZoneInformation(LPTIME_ZONE_INFORMATION tz)
{
    if (!tz) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0xFFFFFFFFu;   /* TIME_ZONE_ID_INVALID */
    }
    if (!tz_current(tz)) {
        SetLastError(ERROR_INVALID_FUNCTION);   /* the platform has no time zone data */
        return 0xFFFFFFFFu;
    }
    if (!tz->StandardDate.wMonth)
        return TIME_ZONE_ID_UNKNOWN;
    return tz_is_dst(tz, (clk_peek_filetime() - FT_UNIX_EPOCH) / 10000000) ? 2 /* TIME_ZONE_ID_DAYLIGHT */ : 1 /* TIME_ZONE_ID_STANDARD */;
}

#ifndef TIME_ZONE_ID_STANDARD
#define TIME_ZONE_ID_STANDARD 1
#endif
#ifndef TIME_ZONE_ID_DAYLIGHT
#define TIME_ZONE_ID_DAYLIGHT 2
#endif

/* wine-9.0:dlls/kernelbase/locale.c:compare_tzdate */
static int compare_tzdate(const tfields *tf, const SYSTEMTIME *compare)
{
    static const int month_lengths[12] = { 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 };
    int first, last, limit, dayinsecs;

    if (tf->Month < (int)compare->wMonth) return -1;
    if (tf->Month > (int)compare->wMonth) return 1;
    if (!compare->wYear) {
        first = (6 + compare->wDayOfWeek - tf->Weekday + tf->Day) % 7 + 1;
        last = month_lengths[tf->Month - 1] + (tf->Month == 2 && (!(tf->Year % 4) && (tf->Year % 100 || !(tf->Year % 400))));
        limit = first + 7 * (compare->wDay - 1);
        if (limit > last) limit -= 7;
    } else
        limit = compare->wDay;
    limit = ((limit * 24 + compare->wHour) * 60 + compare->wMinute) * 60;
    dayinsecs = ((tf->Day * 24 + tf->Hour) * 60 + tf->Minute) * 60 + tf->Second;
    return dayinsecs - limit;
}

/* wine-9.0:dlls/kernelbase/locale.c:get_timezone_id (is_local = FALSE: `time` is UTC) */
static DWORD get_timezone_id(const TIME_ZONE_INFORMATION *info, int64_t time)
{
    int year;
    bool before_standard_date, after_daylight_date;
    int64_t t2;
    tfields tf;

    if (!info->DaylightDate.wMonth) return TIME_ZONE_ID_UNKNOWN;
    if (info->StandardDate.wMonth == 0 ||
        (info->StandardDate.wYear == 0 && (info->StandardDate.wDay < 1 || info->StandardDate.wDay > 5 ||
                                           info->DaylightDate.wDay < 1 || info->DaylightDate.wDay > 5))) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0xFFFFFFFFu;   /* TIME_ZONE_ID_INVALID */
    }
    time = (int64_t)((uint64_t)time - (uint64_t)((int64_t)info->Bias * 600000000ll));
    time_to_fields(time, &tf);
    year = tf.Year;
    t2 = (int64_t)((uint64_t)time - (uint64_t)((int64_t)info->DaylightBias * 600000000ll));
    time_to_fields(t2, &tf);
    if (tf.Year == year)
        before_standard_date = compare_tzdate(&tf, &info->StandardDate) < 0;
    else
        before_standard_date = tf.Year < year;
    t2 = (int64_t)((uint64_t)time - (uint64_t)((int64_t)info->StandardBias * 600000000ll));
    time_to_fields(t2, &tf);
    if (tf.Year == year)
        after_daylight_date = compare_tzdate(&tf, &info->DaylightDate) >= 0;
    else
        after_daylight_date = tf.Year > year;
    if (info->DaylightDate.wMonth < info->StandardDate.wMonth) {   /* Northern hemisphere */
        if (before_standard_date && after_daylight_date) return TIME_ZONE_ID_DAYLIGHT;
    } else if (before_standard_date || after_daylight_date)         /* Down south */
        return TIME_ZONE_ID_DAYLIGHT;
    return TIME_ZONE_ID_STANDARD;
}

/* wine-9.0:dlls/kernelbase/locale.c:SystemTimeToTzSpecificLocalTime. A NULL zone is the declared machine's current
 * zone (RtlQueryTimeZoneInformation there). */
extern "C" BOOL SystemTimeToTzSpecificLocalTime(LPTIME_ZONE_INFORMATION tz, const SYSTEMTIME *utc, LPSYSTEMTIME local)
{
    int64_t ft;
    TIME_ZONE_INFORMATION cur;
    if (!utc || !local)
        return FALSE;
    if (!tz) {
        if (!tz_current(&cur)) {
            SetLastError(ERROR_INVALID_FUNCTION);   /* the platform has no time zone data */
            return FALSE;
        }
        tz = &cur;
    }
    if (!st_to_time(utc, &ft)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    int64_t bias_min;
    switch (get_timezone_id(tz, ft)) {
    case TIME_ZONE_ID_UNKNOWN: bias_min = tz->Bias; break;
    case TIME_ZONE_ID_STANDARD: bias_min = (int64_t)tz->Bias + tz->StandardBias; break;
    case TIME_ZONE_ID_DAYLIGHT: bias_min = (int64_t)tz->Bias + tz->DaylightBias; break;
    default: return FALSE;
    }
    ft = (int64_t)((uint64_t)ft - (uint64_t)(bias_min * 600000000ll));
    return ft_to_st(ft, local);
}

/* wine-9.0:dlls/kernelbase/file.c:CompareFileTime (NULL on either side is -1; the parts compare unsigned, high first) */
extern "C" LONG CompareFileTime(const FILETIME *a, const FILETIME *b)
{
    if (!a || !b)
        return -1;
    ULONGLONG x = w32_ft64(a), y = w32_ft64(b);
    return x < y ? -1 : x > y ? 1 : 0;
}

