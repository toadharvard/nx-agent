/* fnvnx_clock.h - the platform's one clock interface.
 *
 * Every time source of platform/win32, crt, audio, input and boundary goes through these functions; the OS layer's own
 * clock primitives (platform/os/os_clock.h) are not visible to those directories, so a direct use does not compile.
 * Two implementations, chosen by which object is linked (no #ifdef, no run-time switch, no weak symbols):
 *
 *   platform/clock/clock_real.cpp     the product: the OS layer's monotonic, wall and zone clocks (today's behaviour)
 *   platform/clock/clock_virtual.cpp  the verification mode, definition VC1: a counter t per thread, nanoseconds; every
 *                                     read returns t in the API's units from a fixed epoch and THEN advances t by the
 *                                     quantum q = 100 ns; sleeps and timed-out waits advance t by their length; a
 *                                     successful wait, a message, a thread start take t from the other side; the audio
 *                                     device runs on virtual time; the zone is UTC. No observable value depends on real
 *                                     elapsed time (real blocking still happens where two threads need each other).
 *   platform/clock/clock_rt.cpp       real-time services of both: timers that no observable value depends on (watchdog
 *                                     dumps, polling back-off, the real blocking of a wait). Linked with either.
 *
 * "read" functions advance (virtual); "peek" functions do not. The real clock advances nothing. */
#ifndef FNVNX_CLOCK_H
#define FNVNX_CLOCK_H

#include <stddef.h>
#include <stdint.h>
#include "../os/os.h"

#ifdef __cplusplus
extern "C" {
#endif

const char *clk_name(void);                 /* "real" or "virtual": which clock is linked */

/* ---- reads of the API's units (read, then advance) */
uint64_t clk_read_ticks(void);              /* QueryPerformanceCounter */
uint64_t clk_tick_freq(void);               /* QueryPerformanceFrequency (virtual: 10 000 000) */
uint32_t clk_read_tick_ms(void);            /* GetTickCount, timeGetTime (32-bit wrap is the caller's) */
uint32_t clk_read_process_ms(void);         /* the C runtime's clock(): milliseconds since process start */
uint64_t clk_read_ns(void);                 /* the lifter runtime's rdtsc: nanoseconds of the monotonic clock */
int64_t clk_read_filetime(void);            /* wall clock as FILETIME (100 ns since 1601): GetSystemTime*, GetLocalTime, time() */

/* ---- peeks (no advance) */
uint32_t clk_peek_tick_ms(void);            /* the tick count, for timestamps of events */
uint64_t clk_peek_ns(void);                 /* monotonic nanoseconds, for deadlines inside platform state (key repeat) */
int64_t clk_peek_filetime(void);            /* wall clock as FILETIME: the instant a zone rule is applied at */

/* ---- the time zone: local = UTC + offset. Virtual: UTC, no daylight time. */
typedef struct { uint16_t year, month, wday, week, hour, minute; } clk_tzdate_t;
typedef struct {
    int32_t std_off_min, dst_off_min;
    int has_dst;
    clk_tzdate_t dst_begin, dst_end;
    char std_name[8], dst_name[8];
} clk_tzrule_t;                             /* the layout and meaning of os_tzrule_t (os_clock.h) */
int32_t clk_tz_offset_minutes(void);        /* the offset at the present instant */
int clk_tz_rule(clk_tzrule_t *r);           /* 0, or < 0 when the platform has no zone data */

/* ---- sleeping, waiting, and taking time from another thread */
void clk_sleep_ns(uint64_t ns);             /* Sleep: virtual t += ns, Sleep(0) (ns == 0) t += q; real: sleeps / yields */
void clk_wait_timeout(uint64_t ns);         /* a wait that did not succeed: virtual t += ns, ns == 0: t += q; real: nothing */
uint64_t clk_stamp(void);                   /* the calling thread's t (no advance), carried by what it signals, posts, creates */
void clk_merge(uint64_t other);             /* virtual: t = max(t, other); real: nothing */
void clk_thread_start(uint64_t parent);     /* the first call of a new thread, or a new activation of a service thread: virtual t = the stamp */
/* a posted or sent message: the poster's stamp, and on retrieval the receiver takes it and MSG.time is its t in ms. `stamp`
 * is what clk_msg_post returned, `time` the MSG.time the post wrote (the real clock keeps it). */
uint64_t clk_msg_post(void);
uint32_t clk_msg_retrieve(uint64_t stamp, uint32_t time);
/* a wait about to block with nothing signalled: virtual runs the audio device on the way (handles are the waited HANDLEs,
 * timeout_ns the wait's virtual timeout or UINT64_MAX): 1 = virtual time worked (re-evaluate at once), 0 = block for real */
int clk_wait_idle(const void *const *handles, unsigned n, uint64_t timeout_ns);

/* ---- file times written by the platform: virtual stamps t (no advance) and serves them back; real: the host file
 * system's. host is the host path (the file-sharing key's spelling). */
enum { CLK_FS_CREATE = 1, CLK_FS_WRITE = 2 };
void clk_file_stamp(const char *host, int what);          /* CREATE: creation, access and write time; WRITE: write time */
void clk_file_moved(const char *from, const char *to);
void clk_file_removed(const char *host);
int clk_file_times(const char *host, int64_t ft[3]);      /* 1 and creation/access/write as FILETIME; 0: the host file system's */

/* ---- devices driven by a period: the audio mixer's output backend and DirectShow's renderer pump ask for a pump:
 * render(ctx, frames) once per period of `period` frames at `rate`. Real: a thread (stack, priority, core as os_thread_create)
 * paced by the monotonic clock. Virtual: no thread; the pump runs in virtual time of whichever thread reads or advances its
 * clock (period n at start + n*period/rate, start = the starting thread's t), so cursors, notifications and completions are
 * functions of virtual time. after (may be NULL) runs after each virtual period, outside every clock lock. NULL: failed. */
typedef void (*clk_pump_fn)(void *ctx, uint32_t frames);
void *clk_pump_start(clk_pump_fn render, clk_pump_fn after, void *ctx, uint32_t rate, uint32_t period, size_t stack, int prio, int cpu);
void clk_pump_stop(void *pump);             /* returns when no period is running; frees the pump */
/* an event the device signals (DirectSound notification, waveOut CALLBACK_EVENT): a wait on it runs the device */
void clk_device_watch(const void *handle, int on);

/* ---- a run's start: the calling thread's t becomes 0 and the file-time stamps are forgotten (virtual); the real clock does
 * nothing. For a verification run that begins again in the same process, and for the tests of this interface. */
void clk_reset(void);

/* ---- real-time services: values that no observable result depends on (diagnostic timers, polling back-off, the real
 * blocking of a wait). The same in both clocks. */
uint64_t clk_rt_ns(void);
void clk_rt_sleep_ns(uint64_t ns);
int clk_rt_cond_timedwait(os_cond_t *c, os_lock_t *l, uint64_t timeout_ns);   /* 0 woken, 1 timed out */
uint64_t clk_rt_deadline(uint64_t timeout_ns);                                /* now + timeout */
int clk_rt_left(uint64_t deadline, uint64_t *left_ns);                        /* 1 and the time left, or 0 when over */

#ifdef __cplusplus
}
#endif
#endif
