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

#ifdef __cplusplus
extern "C" {
#endif

/* ---- process */
void os_init(void);                         /* idempotent; called by the runtime before first use */
void os_debug_print(const char *s);         /* one line to the debug log (stderr / svcOutputDebugString) */
void os_fatal(const char *msg) __attribute__((noreturn));
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
/* Creates a thread that does not run until os_thread_start. cpu < 0: any core. */
os_thread *os_thread_create(os_thread_fn fn, void *arg, size_t stack_size, int prio, int cpu);
int os_thread_start(os_thread *t);
int os_thread_join(os_thread *t);           /* waits for exit; does not free */
void os_thread_free(os_thread *t);          /* after join, or to detach a running thread */
int os_thread_set_priority(os_thread *t, int prio);   /* t == NULL: calling thread */
int os_thread_set_cpu(os_thread *t, int cpu);         /* t == NULL: calling thread; best effort */
int os_thread_suspend(os_thread *t);        /* -1 where unsupported */
int os_thread_resume(os_thread *t);
void os_thread_exit(void) __attribute__((noreturn));
void os_yield(void);
void os_sleep_ns(uint64_t ns);

/* One pointer of native thread-local storage for the runtime's per-thread block. */
void *os_tls_get(void);
void os_tls_set(void *p);

/* ---- time */
uint64_t os_ticks(void);                    /* monotonic */
uint64_t os_tick_freq(void);                /* ticks per second */
uint64_t os_monotonic_ns(void);
uint64_t os_cycle_counter(void);            /* cheap free-running counter (rdtsc stand-in) */
int64_t os_wall_time_us(void);              /* microseconds since 1970-01-01 UTC */
int32_t os_tz_offset_minutes(void);         /* local = UTC + offset */

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

/* ---- host directories the path mapper mounts (see docs/RUNTIME.md "Paths") */
const char *os_default_root(void);          /* e.g. "sdmc:/fnv-nx" ; host: $FNVNX_ROOT or "./fnvnx-root" */

#ifdef __cplusplus
}
#endif
#endif
