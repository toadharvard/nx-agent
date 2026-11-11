/* os_clock.h - the OS layer's clock primitives: monotonic and wall time, sleeps, timed condition waits, the zone rule.
 *
 * Split from os.h so that platform code cannot read the clock behind the back of the clock interface: platform/win32,
 * crt, audio and input include os.h only; every time read, sleep and timed wait there goes through
 * platform/include/fnvnx_clock.h, which has two implementations (platform/clock/clock_real.cpp: these primitives;
 * clock_virtual.cpp: the virtual clocks of the verification mode). Only those two files, clock_rt.cpp (real-time
 * services that no observable value depends on) and the OS backends include this header. */
#ifndef FNVNX_OS_CLOCK_H
#define FNVNX_OS_CLOCK_H

#include "os.h"

#ifdef __cplusplus
extern "C" {
#endif

int os_cond_timedwait(os_cond_t *c, os_lock_t *l, uint64_t timeout_ns); /* 0 woken, 1 timed out; OS_WAIT_FOREVER: no timeout */
void os_sleep_ns(uint64_t ns);

uint64_t os_ticks(void);                    /* monotonic */
uint64_t os_tick_freq(void);                /* ticks per second */
uint64_t os_monotonic_ns(void);
uint64_t os_cycle_counter(void);            /* cheap free-running counter (rdtsc stand-in) */
int64_t os_wall_time_us(void);              /* microseconds since 1970-01-01 UTC */
int32_t os_tz_offset_minutes(void);         /* local = UTC + offset */
/* The zone's rule in the terms of a Windows TIME_ZONE_INFORMATION (derived from the platform's own time zone data). A date is
 * recurring (year == 0: `week` = 1..5 is the n-th, 5 the last, `wday` of `month`) or absolute (year != 0: `week` is the day of
 * the month); `hour`:`minute` is the wall time just before the transition. dst_begin is in standard wall time, dst_end in
 * daylight wall time. Without daylight time has_dst is 0 and both offsets are the present one. Offsets: local = UTC + offset.
 * Returns 0, or < 0 when the platform has no zone data. */
typedef struct { uint16_t year, month, wday, week, hour, minute; } os_tzdate_t;
typedef struct {
    int32_t std_off_min, dst_off_min;
    int has_dst;
    os_tzdate_t dst_begin, dst_end;
    char std_name[8], dst_name[8];       /* the zone's abbreviations */
} os_tzrule_t;
int os_tz_rule(os_tzrule_t *r);

#ifdef __cplusplus
}
#endif
#endif
