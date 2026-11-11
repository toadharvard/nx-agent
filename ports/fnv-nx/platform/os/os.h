/* os.h - the small OS abstraction under the fnv-nx runtime (platform/win32, platform/crt).
 *
 * Two backends implement it: os_posix.c (Linux host, used by the tests) and os_horizon.c (libnx). Everything
 * Win32-shaped lives above this line; nothing here knows about Windows. Plain C, no allocation in the
 * lock/condvar paths.
 *
 * Conventions: functions returning int return 0 on success and a negative value on failure unless stated.
 * Times are nanoseconds unless the name says otherwise. Paths are host paths (already mapped).
 */
#ifndef FNVNX_OS_H
#define FNVNX_OS_H

#include <stddef.h>
#include <stdint.h>

/* ---- static-constructor priorities of the guest. toolchain/guest/guest.ld runs prioritized constructors first
 * (lowest number first; GCC reserves 0-100), then those of the runtime libraries (libstdc++, newlib), then the rest
 * in link order, which includes every game unit's. So the platform's constructors and the game boot
 * (toolchain/game/boot.cpp) come before any game static, which may use them (toolchain/game/init_order.py checks
 * the linked order). */
#define FNVNX_INIT_CLOCK 101    /* clock bases: clock(), GetTickCount */
#define FNVNX_INIT_MODULES 103  /* module and COM class registrations (audio, input) */
#define FNVNX_INIT_BOOT 110     /* the game boot: data image, memory manager (toolchain/game/boot.cpp) */

#ifdef __cplusplus
extern "C" {
#endif

/* ---- process */
void os_debug_print(const char *s);         /* one line to the debug log (stderr / svcOutputDebugString) */
void os_exit_process(int code) __attribute__((noreturn));
unsigned os_cpu_count(void);                /* cores the game may use */
uint32_t os_process_id(void);
void os_memory_info(uint64_t *total_phys, uint64_t *avail_phys);
uint32_t os_random32(void);                 /* non-cryptographic quality is enough */

/* ---- locks: one 32-bit word, all-zero = unlocked, so zeroed storage is a valid unlocked lock.
 * Not recursive (the Win32 layer builds recursion on top). Must not be moved while locked. */
typedef struct { volatile uint32_t word; } os_lock_t;
#define OS_LOCK_INIT { 0 }
void os_lock(os_lock_t *l);
int os_trylock(os_lock_t *l);               /* 1 if acquired */
void os_unlock(os_lock_t *l);
/* os_lock/os_trylock/os_unlock (and the lock a condition wait drops and re-takes) are the PLATFORM's locks: the rule
 * "no platform lock is held across a call into the game" applies to every one of them, and the host test backend
 * (os_posix.c) counts them per thread for the test boundary. A lock that implements a synchronisation object OWNED BY
 * THE GAME (a CRITICAL_SECTION: the game holds it across its own callbacks, legitimately) takes the same word through
 * os_glock/os_gtrylock/os_gunlock, which are not counted: the two families differ only in the count. */
void os_glock(os_lock_t *l);
int os_gtrylock(os_lock_t *l);              /* 1 if acquired */
void os_gunlock(os_lock_t *l);

/* ---- condition variables: one 32-bit word, all-zero = valid. Spurious wakeups allowed. */
typedef struct { volatile uint32_t word; } os_cond_t;
#define OS_COND_INIT { 0 }
#define OS_WAIT_FOREVER UINT64_MAX
int os_cond_wait(os_cond_t *c, os_lock_t *l, uint64_t timeout_ns); /* 0 woken, 1 timed out */
void os_cond_signal(os_cond_t *c);
void os_cond_broadcast(os_cond_t *c);

/* ---- threads */
typedef struct os_thread os_thread;
typedef void (*os_thread_fn)(void *arg);
#define OS_PRIO_NORMAL 0                    /* Win32 THREAD_PRIORITY_* scale: -15..15, 0 normal */
/* Creates a thread that does not run until os_thread_start. cpu < 0: any core; 0..63: that core;
 * OS_THREAD_ROLE(r): placement role r, which the backend maps to cores and a priority (the Switch guest: the
 * host's table, toolchain/host/placement.h; other backends: any core). */
#define OS_THREAD_ROLE(r) (0x100 | (r))
/* or'ed into fnvnx_thread_role_hook's result: the thread boosts the CPU while it runs (the loading screen): on at
 * its start and after each infinite wait, off before such a wait and at its end (platform/win32 thread.cpp,
 * sync.cpp). Not passed on to os_thread_create. */
#define OS_THREAD_BOOST 0x10000
#define OS_ROLE_AUDIO 6   /* FNVNX_ROLE_AUDIO (toolchain/include/fnvnx/hostapi.h): the platform's audio pump */
os_thread *os_thread_create(os_thread_fn fn, void *arg, size_t stack_size, int prio, int cpu);
/* CPU boost for loading: 1 = fast load (Switch: appletSetCpuBoostMode FastLoad, CPU 1785 MHz, GPU at its minimum),
 * 0 = normal. The host may refuse (platform config boost_loading=0); a no-op on other backends. */
void os_cpu_boost(int fast_load);
/* A system dialog with the caption, the text and an OK button, blocking until it is closed (MessageBoxA): the
 * Switch's error applet; a no-op where the platform has none (the caller logs the text). */
void os_show_message(const char *caption, const char *text);
/* The console's output mode: 1 = docked (the TV output, 1920x1080), 0 = handheld (the built-in panel, 1280x720).
 * Switch: appletGetOperationMode() == AppletOperationMode_Console. Host tests (os_posix.c): the state set by
 * os_posix_set_docked, handheld at start. */
int os_display_docked(void);
int os_thread_start(os_thread *t);
int os_thread_join(os_thread *t);           /* waits for exit; does not free */
void os_thread_free(os_thread *t);          /* after join, or to detach a running thread */
int os_thread_set_priority(os_thread *t, int prio);   /* t == NULL: calling thread */
int os_thread_set_cpu(os_thread *t, int cpu);         /* t == NULL: calling thread; best effort; that core only */
/* ideal core (< 0: keep) and affinity mask over the logical processors (0: keep); t == NULL: calling thread */
int os_thread_set_affinity(os_thread *t, int ideal, unsigned mask);
int os_thread_suspend(os_thread *t);        /* -1 where unsupported */
int os_thread_resume(os_thread *t);
void os_thread_exit(void) __attribute__((noreturn));
void os_yield(void);
void os_sleep_ns(uint64_t ns);

/* ---- time */
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

/* ---- files (host paths, '/' separated) */
#define OS_O_READ   0x01
#define OS_O_WRITE  0x02
#define OS_O_CREATE 0x04
#define OS_O_EXCL   0x08
#define OS_O_TRUNC  0x10
#define OS_O_APPEND 0x20
#define OS_SEEK_SET 0
#define OS_SEEK_CUR 1
#define OS_SEEK_END 2
typedef struct {
    int is_dir;
    int read_only;
    uint64_t size;
    int64_t mtime_us, atime_us, ctime_us;   /* since 1970 UTC */
} os_stat_t;
int os_file_open(const char *path, int flags);    /* >= 0 descriptor, < 0 error (-OS_E*) */
int64_t os_file_read(int fd, void *buf, size_t n);
int64_t os_file_write(int fd, const void *buf, size_t n);
int64_t os_file_pread(int fd, void *buf, size_t n, uint64_t off);
int64_t os_file_pwrite(int fd, const void *buf, size_t n, uint64_t off);
int64_t os_file_seek(int fd, int64_t off, int whence);
int os_file_truncate(int fd, uint64_t size);
int os_file_sync(int fd);
int os_file_fstat(int fd, os_stat_t *st);
int os_file_close(int fd);
int os_stat(const char *path, os_stat_t *st);
int os_mkdir(const char *path);
int os_rmdir(const char *path);
int os_remove(const char *path);
int os_rename(const char *from, const char *to);
/* Directory listing: returns entry names one by one (including neither "." nor ".."). */
typedef struct os_dir os_dir;
os_dir *os_dir_open(const char *path);
const char *os_dir_next(os_dir *d);         /* NULL at end */
void os_dir_close(os_dir *d);
int os_fs_case_insensitive(void);           /* 1 if the host file system folds case itself */
/* errors (negated in return values) */
#define OS_ENOENT  2
#define OS_EACCES  13
#define OS_EEXIST  17
#define OS_ENOTDIR 20
#define OS_EISDIR  21
#define OS_EINVAL  22
#define OS_ENOSPC  28
#define OS_ENOTEMPTY 39
#define OS_EIO     5
#define OS_ENOMEM  12

/* ---- virtual memory. Reservations are page-aligned address ranges with no backing; commit backs pages
 * with zeroed read/write memory, decommit drops the backing (contents lost), release frees the range. */
size_t os_vm_page_size(void);
void *os_vm_reserve(void *hint, size_t size);   /* hint NULL: anywhere; else exactly there or NULL */
int os_vm_commit(void *addr, size_t size);
int os_vm_decommit(void *addr, size_t size);
int os_vm_release(void *addr, size_t size);
/* Changes the access of committed pages (page-aligned range). os_prot: OS_VM_* bits; OS_VM_NONE makes the pages
 * inaccessible without dropping their contents. 0 or -OS_E*. */
#define OS_VM_NONE  0
#define OS_VM_READ  1
#define OS_VM_WRITE 2
#define OS_VM_EXEC  4
int os_vm_protect(void *addr, size_t size, int os_prot);

/* ---- host directories the path mapper mounts (see docs/RUNTIME.md "Paths") */
const char *os_default_root(void);          /* "sdmc:/switch/fnv" (sdlayout.h); host: $FNVNX_ROOT or "./fnvnx-root" */

#ifdef __cplusplus
}
#endif
#endif
