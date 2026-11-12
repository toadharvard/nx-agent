/* os_posix.c - host backend of os.h (Linux; futex locks). Used for the host tests and as a reference for
 * os_horizon.c. Builds for x86-64 LP64 and for aarch64 ILP32 (syntax only, no ILP32 libc exists on hosts). */
#define _GNU_SOURCE
#include "../../platform/os/os_clock.h"
#include "../../platform/os/os_shm.h"
#include "fnvnx/hostapi.h"

/* os_tick_freq, os_fs_case_insensitive: manifest/env_const.tsv */
#include "../../platform/os/env_const_posix.gen.inc"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include "../../platform/os/os_world.h"
#include <semaphore.h>
#include <signal.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>
#ifdef __linux__
#include <linux/futex.h>
#include <sys/syscall.h>
#endif

/* ---- process */
void os_debug_print(const char *s)
{
    size_t n = strlen(s);
    fwrite(s, 1, n, stderr);
    if (n == 0 || s[n - 1] != '\n')
        fputc('\n', stderr);
}

void os_exit_process(int code)
{
    fflush(NULL);
    _exit(code);
}

unsigned os_cpu_count(void)
{
    long n = sysconf(_SC_NPROCESSORS_ONLN);
    return n > 0 ? (unsigned)n : 1;
}

uint32_t os_process_id(void) { return (uint32_t)getpid(); }

void os_memory_info(uint64_t *total, uint64_t *avail)
{
    long pages = sysconf(_SC_PHYS_PAGES), avp = sysconf(_SC_AVPHYS_PAGES), ps = sysconf(_SC_PAGESIZE);
    *total = pages > 0 ? (uint64_t)pages * (uint64_t)ps : 0;
    *avail = avp > 0 ? (uint64_t)avp * (uint64_t)ps : 0;
}

uint32_t os_random32(void)
{
    static uint64_t s;
    uint64_t x = __atomic_load_n(&s, __ATOMIC_RELAXED);
    if (!x)
        x = os_monotonic_ns() ^ ((uint64_t)getpid() << 32) ^ 0x9E3779B97F4A7C15ull;
    x ^= x << 13; x ^= x >> 7; x ^= x << 17;
    __atomic_store_n(&s, x, __ATOMIC_RELAXED);
    return (uint32_t)(x >> 16);
}

/* ---- futex helpers */
static void futex_wait(volatile uint32_t *addr, uint32_t val, const struct timespec *rel)
{
#ifdef __linux__
    syscall(SYS_futex, (uint32_t *)addr, FUTEX_WAIT_PRIVATE, val, rel, NULL, 0);
#else
    (void)addr; (void)val; (void)rel;
    sched_yield();
#endif
}

static void futex_wake(volatile uint32_t *addr, int n)
{
#ifdef __linux__
    syscall(SYS_futex, (uint32_t *)addr, FUTEX_WAKE_PRIVATE, n, NULL, NULL, 0);
#else
    (void)addr; (void)n;
#endif
}

/* ---- locks: 0 free, 1 locked, 2 locked with waiters (Drepper, "Futexes are tricky", mutex 3)
 * The host test processes define fnvnx_lockcount_note (tests/s4/host_boundary.cpp): os_lock/os_trylock/os_unlock report
 * every platform lock taken and released by the calling thread (the lock, the address the acquiring caller returns to),
 * which the test boundary checks at each call into the game. The product's backends (os_horizon.c, os_guest.c) do not
 * count. The game's own locks (os_glock family) are not reported. */
extern void fnvnx_lockcount_note(int delta, const void *lock, const void *site);

static void lock_raw(os_lock_t *l)
{
    uint32_t c = 0;
    if (__atomic_compare_exchange_n(&l->word, &c, 1, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED))
        return;
    for (int spin = 0; spin < 100; spin++) {
        c = 0;
        if (__atomic_load_n(&l->word, __ATOMIC_RELAXED) == 0 &&
            __atomic_compare_exchange_n(&l->word, &c, 1, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED))
            return;
    }
    if (c != 2)
        c = __atomic_exchange_n(&l->word, 2, __ATOMIC_ACQUIRE);
    while (c != 0) {
        futex_wait(&l->word, 2, NULL);
        c = __atomic_exchange_n(&l->word, 2, __ATOMIC_ACQUIRE);
    }
}

static int trylock_raw(os_lock_t *l)
{
    uint32_t c = 0;
    return __atomic_compare_exchange_n(&l->word, &c, 1, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED);
}

static void unlock_raw(os_lock_t *l)
{
    if (__atomic_fetch_sub(&l->word, 1, __ATOMIC_RELEASE) != 1) {
        __atomic_store_n(&l->word, 0, __ATOMIC_RELEASE);
        futex_wake(&l->word, 1);
    }
}

void os_lock(os_lock_t *l)
{
    lock_raw(l);
    fnvnx_lockcount_note(1, l, __builtin_return_address(0));
}

int os_trylock(os_lock_t *l)
{
    if (!trylock_raw(l))
        return 0;
    fnvnx_lockcount_note(1, l, __builtin_return_address(0));
    return 1;
}

void os_unlock(os_lock_t *l)
{
    fnvnx_lockcount_note(-1, l, 0);
    unlock_raw(l);
}

void os_glock(os_lock_t *l) { lock_raw(l); }
int os_gtrylock(os_lock_t *l) { return trylock_raw(l); }
void os_gunlock(os_lock_t *l) { unlock_raw(l); }

/* ---- condvars: sequence counter. The wait drops the lock and takes it again before it returns: for the count the
 * thread holds the lock throughout (no guest call can happen on it while it waits). */
int os_cond_timedwait(os_cond_t *c, os_lock_t *l, uint64_t timeout_ns)
{
    uint32_t seq = __atomic_load_n(&c->word, __ATOMIC_ACQUIRE);
    uint64_t start = timeout_ns == OS_WAIT_FOREVER ? 0 : os_monotonic_ns();
    unlock_raw(l);
    if (timeout_ns == OS_WAIT_FOREVER) {
        futex_wait(&c->word, seq, NULL);
    } else {
        struct timespec ts = { (time_t)(timeout_ns / 1000000000ull), (long)(timeout_ns % 1000000000ull) };
        futex_wait(&c->word, seq, &ts);
    }
    /* futex_wait gives no reliable "timed out" result through spurious wakes, measure instead */
    int timed_out = timeout_ns != OS_WAIT_FOREVER && __atomic_load_n(&c->word, __ATOMIC_ACQUIRE) == seq &&
                    os_monotonic_ns() - start >= timeout_ns;
    lock_raw(l);
    return timed_out;
}

void os_cond_wait(os_cond_t *c, os_lock_t *l) { os_cond_timedwait(c, l, OS_WAIT_FOREVER); }

void os_cond_signal(os_cond_t *c)
{
    __atomic_fetch_add(&c->word, 1, __ATOMIC_RELEASE);
    futex_wake(&c->word, 1);
}

void os_cond_broadcast(os_cond_t *c)
{
    __atomic_fetch_add(&c->word, 1, __ATOMIC_RELEASE);
    futex_wake(&c->word, 0x7fffffff);
}

/* ---- threads */
struct os_thread {
    pthread_t th;
    os_thread_fn fn;
    void *arg;
    size_t stack;
    int prio, cpu;
    int started, joined;
    os_lock_t lock;
    os_cond_t cond;
    volatile uint32_t ps;          /* the pause word, see ps_park */
    int refs;                      /* the owner's reference, and the running thread's from os_thread_start until its end */
    os_wslot slot;                 /* the world registry (os_world.c) */
};

/* The pause primitive (one boolean per thread: os_world.c combines the two reasons, user SuspendThread and world stop,
 * under the world lock, so this layer only ever sees "paused" or "running").
 *   os_raw_pause: sets PS_REQ, sends SIGUSR2 and returns only when the target has acknowledged by setting PS_PARKED
 *   inside the handler (ps_park), where it waits on the futex word until PS_REQ is cleared. A parked thread executes
 *   nothing but the futex wait of the handler: that is the synchronous guarantee of the POSIX backend.
 *   Stale-acknowledgement proof: the handler leaves ps_park only by a compare-and-swap that clears PS_PARKED from a value
 *   observed without PS_REQ. If a new request (fetch_or PS_REQ) lands after that observation the CAS fails and the thread
 *   stays parked; if it lands before it, the thread has already left and the signal sent after the request parks it
 *   afresh. So when the requester sees PS_PARKED with its own PS_REQ set, the target is inside the handler and cannot
 *   return to the interrupted code before the request is cleared.
 * A thread that has not set t_self yet (the very start of thread_entry) ignores the signal; thread_entry checks the word
 * itself after setting t_self, and the request is set before the signal is sent, so no request is lost. */
#define PS_REQ 1u
#define PS_PARKED 2u
static __thread os_thread *t_self;
static void ps_park(os_thread *t)
{
    for (;;) {
        uint32_t v = __atomic_load_n(&t->ps, __ATOMIC_ACQUIRE);
        if (!(v & PS_REQ)) {
            if (!(v & PS_PARKED) || __atomic_compare_exchange_n(&t->ps, &v, v & ~PS_PARKED, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
                return;
            continue;
        }
        if (!(v & PS_PARKED)) {
            if (!__atomic_compare_exchange_n(&t->ps, &v, v | PS_PARKED, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
                continue;
            futex_wake(&t->ps, 0x7fffffff);
            v |= PS_PARKED;
        }
        futex_wait(&t->ps, v, NULL);
    }
}
static void pause_handler(int sig)
{
    (void)sig;
    os_thread *t = t_self;
    if (!t)
        return;
    int e = errno;
    ps_park(t);
    errno = e;
}
static pthread_once_t g_pause_once = PTHREAD_ONCE_INIT;
static void pause_install(void)
{
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = pause_handler;
    sa.sa_flags = SA_RESTART;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGUSR2, &sa, NULL);
}

os_wslot *os_thread_wslot(os_thread *t) { return &t->slot; }

int os_raw_pause(os_thread *t)
{
    if (!t->started || t->joined || t == t_self)
        return -1;
    __atomic_fetch_or(&t->ps, PS_REQ, __ATOMIC_ACQ_REL);
    if (pthread_kill(t->th, SIGUSR2)) {
        __atomic_fetch_and(&t->ps, ~PS_REQ, __ATOMIC_ACQ_REL);
        return -1;
    }
    for (;;) {
        uint32_t v = __atomic_load_n(&t->ps, __ATOMIC_ACQUIRE);
        if (v & PS_PARKED)
            return 0;
        futex_wait(&t->ps, v, NULL);
    }
}
void os_raw_resume(os_thread *t)
{
    __atomic_fetch_and(&t->ps, ~PS_REQ, __ATOMIC_ACQ_REL);
    futex_wake(&t->ps, 0x7fffffff);
}

static void thread_unref(os_thread *t)
{
    if (__atomic_sub_fetch(&t->refs, 1, __ATOMIC_ACQ_REL) == 0)
        free(t);
}

/* the thread's last act: leave the registry (under the world lock, so never during a stop), then drop the thread's
 * reference. After os_world_unregister the thread executes only this exit code. */
static void thread_done(os_thread *t)
{
    os_world_unregister(&t->slot);
    t_self = NULL;
    thread_unref(t);
}

static void *thread_entry(void *p)
{
    os_thread *t = (os_thread *)p;
    t->th = pthread_self();
    t_self = t;
    ps_park(t);                    /* a pause requested before t_self existed */
    os_world_register(&t->slot, t);   /* blocks while the world is stopped: no guest code starts during a stop */
    if (t->cpu >= 0)
        os_thread_set_cpu(NULL, t->cpu);
    t->fn(t->arg);
    thread_done(t);
    return NULL;
}

os_thread *os_thread_create(os_thread_fn fn, void *arg, size_t stack_size, int prio, int cpu)
{
    os_thread *t = (os_thread *)calloc(1, sizeof *t);
    if (!t)
        return NULL;
    t->fn = fn; t->arg = arg; t->stack = stack_size; t->prio = prio; t->cpu = cpu;
    t->refs = 1;
    pthread_once(&g_pause_once, pause_install);
    return t;
}

/* the process's first thread (and any thread the platform did not create) joins the world registry: os_world.c calls
 * this once, on that thread, before any guest code runs */
os_thread *os_raw_adopt_self(void)
{
    os_thread *t = (os_thread *)calloc(1, sizeof *t);
    if (!t)
        return NULL;
    t->th = pthread_self();
    t->started = 1;
    t->refs = 1;
    t->cpu = -1;
    pthread_once(&g_pause_once, pause_install);
    t_self = t;
    return t;
}

int os_thread_start(os_thread *t)
{
    pthread_attr_t a;
    pthread_attr_init(&a);
    if (t->stack) {
        size_t s = t->stack < 65536 ? 65536 : t->stack;
        pthread_attr_setstacksize(&a, (s + 4095) & ~(size_t)4095);
    }
    t->started = 1;                /* before the thread exists: it may be registered, and paused, at once */
    __atomic_add_fetch(&t->refs, 1, __ATOMIC_ACQ_REL);
    int r = pthread_create(&t->th, &a, thread_entry, t);
    pthread_attr_destroy(&a);
    if (r) {
        t->started = 0;
        __atomic_sub_fetch(&t->refs, 1, __ATOMIC_ACQ_REL);
        return -1;
    }
    return 0;
}

int os_thread_join(os_thread *t)
{
    if (!t->started || t->joined)
        return t->joined ? 0 : -1;
    if (pthread_join(t->th, NULL))
        return -1;
    t->joined = 1;
    return 0;
}

void os_thread_free(os_thread *t)
{
    if (!t)
        return;
    if (t->started && !t->joined)
        pthread_detach(t->th);
    thread_unref(t);
}

int os_thread_set_priority(os_thread *t, int prio)
{
    if (t)
        t->prio = prio;
    return 0; /* non-realtime host scheduling: priorities are advisory only */
}

int os_thread_set_cpu(os_thread *t, int cpu)
{
#ifdef __linux__
    if (cpu < 0 || cpu >= 0x100)   /* OS_THREAD_ROLE: no placement table on this backend */
        return 0;
    if (t && !t->started) {
        t->cpu = cpu;
        return 0;
    }
    cpu_set_t s;
    CPU_ZERO(&s);
    CPU_SET(cpu % (int)os_cpu_count(), &s);
    return pthread_setaffinity_np(t ? t->th : pthread_self(), sizeof s, &s) ? -1 : 0;
#else
    (void)t; (void)cpu;
    return 0;
#endif
}

int os_thread_set_affinity(os_thread *t, int ideal, unsigned mask)
{
    (void)ideal;
#ifdef __linux__
    if (!mask)
        return 0;
    if (t && !t->started) {   /* applied at start, like os_thread_set_cpu */
        for (int c = 0; c < 32; c++)
            if (mask >> c & 1) { t->cpu = c; break; }
        return 0;
    }
    cpu_set_t s;
    CPU_ZERO(&s);
    for (int c = 0; c < 32; c++)
        if (mask >> c & 1)
            CPU_SET(c % (int)os_cpu_count(), &s);
    return pthread_setaffinity_np(t ? t->th : pthread_self(), sizeof s, &s) ? -1 : 0;
#else
    (void)t; (void)mask;
    return 0;
#endif
}

void os_thread_exit(void)
{
    os_thread *t = t_self;
    if (t)
        thread_done(t);
    pthread_exit(NULL);
}
void os_cpu_boost(int fast_load) { (void)fast_load; }   /* no CPU boost on a PC */
void os_show_message(const char *caption, const char *text) { (void)caption; (void)text; }   /* logged by the caller */

/* ---- text entry: the scripted user of the host tests */
#define TS_MAX 32
static struct { uint16_t *text; size_t n; int cancel; } g_ts[TS_MAX];
static unsigned g_ts_head, g_ts_count, g_ts_calls;
static uint16_t g_ts_init[FNVNX_TEXT_ENTRY_MAX];
static size_t g_ts_init_n = (size_t)-1;
static os_lock_t g_ts_lock;
static void ts_put(const uint16_t *t, size_t n, int cancel)
{
    os_lock(&g_ts_lock);
    if (g_ts_count < TS_MAX) {
        unsigned i = (g_ts_head + g_ts_count++) % TS_MAX;
        g_ts[i].text = n ? (uint16_t *)malloc(n * 2) : NULL;
        if (n) memcpy(g_ts[i].text, t, n * 2);
        g_ts[i].n = n;
        g_ts[i].cancel = cancel;
    }
    os_unlock(&g_ts_lock);
}
void os_text_script_push(const uint16_t *t, size_t n) { ts_put(t, n, 0); }
void os_text_script_cancel(void) { ts_put(NULL, 0, 1); }
unsigned os_text_script_calls(void) { return g_ts_calls; }
size_t os_text_script_init(uint16_t *buf, size_t cap)
{
    os_lock(&g_ts_lock);
    size_t n = g_ts_init_n;
    if (n != (size_t)-1) memcpy(buf, g_ts_init, (n < cap ? n : cap) * 2);
    os_unlock(&g_ts_lock);
    return n;
}
void os_text_script_reset(void)
{
    os_lock(&g_ts_lock);
    while (g_ts_count) { free(g_ts[g_ts_head].text); g_ts_head = (g_ts_head + 1) % TS_MAX; g_ts_count--; }
    g_ts_calls = 0;
    g_ts_init_n = (size_t)-1;
    os_unlock(&g_ts_lock);
}
int os_text_entry(unsigned max_len, const uint16_t *init, size_t init_n, uint16_t *out, size_t *n)
{
    if (max_len == 0 || max_len > FNVNX_TEXT_ENTRY_MAX || !out || !n) return -(int)FNVNX_HE_BADINPUT;
    os_lock(&g_ts_lock);
    g_ts_calls++;
    g_ts_init_n = init ? init_n : (size_t)-1;
    if (init) memcpy(g_ts_init, init, (init_n < FNVNX_TEXT_ENTRY_MAX ? init_n : FNVNX_TEXT_ENTRY_MAX) * 2);
    if (!g_ts_count) { os_unlock(&g_ts_lock); return 0; }   /* an empty script is a user who closes the applet */
    uint16_t *t = g_ts[g_ts_head].text;
    size_t len = g_ts[g_ts_head].n;
    int cancel = g_ts[g_ts_head].cancel;
    g_ts_head = (g_ts_head + 1) % TS_MAX;
    g_ts_count--;
    os_unlock(&g_ts_lock);
    if (!cancel) {
        if (len > max_len) len = max_len;   /* the applet's field takes no more units */
        if (len) memcpy(out, t, len * 2);
        out[len] = 0;
        *n = len;
    }
    free(t);
    return cancel ? 0 : 1;
}
/* the console's dock state of the host tests: handheld until a test says otherwise */
static volatile int g_posix_docked;
int os_display_docked(void) { return __atomic_load_n(&g_posix_docked, __ATOMIC_ACQUIRE); }
void os_posix_set_docked(int docked) { __atomic_store_n(&g_posix_docked, docked != 0, __ATOMIC_RELEASE); }
void os_yield(void) { sched_yield(); }

void os_sleep_ns(uint64_t ns)
{
    struct timespec ts = { (time_t)(ns / 1000000000ull), (long)(ns % 1000000000ull) };
    while (nanosleep(&ts, &ts) && errno == EINTR) {
    }
}

/* ---- time. Host ticks are 100 ns (10 MHz, the usual Windows QPC rate). */
uint64_t os_monotonic_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}
uint64_t os_ticks(void) { return os_monotonic_ns() / 100; }

uint64_t os_cycle_counter(void)
{
#if defined(__x86_64__) || defined(__i386__)
    return __builtin_ia32_rdtsc();
#elif defined(__aarch64__)
    uint64_t v;
    __asm__ __volatile__("mrs %0, cntvct_el0" : "=r"(v));
    return v;
#else
    return os_monotonic_ns();
#endif
}

int64_t os_wall_time_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

int32_t os_tz_offset_minutes(void)
{
    time_t now = time(NULL);
    struct tm lt;
    localtime_r(&now, &lt);
    return (int32_t)(lt.tm_gmtoff / 60);
}

static int tzr_at(int64_t utc_s, int32_t *off_s, int *is_dst, char abbr[8])
{
    time_t t = (time_t)utc_s;
    struct tm lt;
    if (!localtime_r(&t, &lt))
        return -1;
    *off_s = (int32_t)lt.tm_gmtoff;
    *is_dst = lt.tm_isdst > 0;
    snprintf(abbr, 8, "%s", lt.tm_zone ? lt.tm_zone : "");
    return 0;
}
#include "../../platform/os/os_tzrule.inc"

/* ---- files */
static int neg_errno(void)
{
    switch (errno) {
    case ENOENT: return -OS_ENOENT;
    case EACCES: case EPERM: case EROFS: return -OS_EACCES;
    case EEXIST: return -OS_EEXIST;
    case ENOTDIR: return -OS_ENOTDIR;
    case EISDIR: return -OS_EISDIR;
    case ENOSPC: return -OS_ENOSPC;
    case ENOTEMPTY: return -OS_ENOTEMPTY;
    case ENOMEM: return -OS_ENOMEM;
    case EINVAL: return -OS_EINVAL;
    default: return -OS_EIO;
    }
}

int os_file_open(const char *path, int flags)
{
    int o = 0;
    if ((flags & OS_O_READ) && (flags & OS_O_WRITE)) o = O_RDWR;
    else if (flags & OS_O_WRITE) o = O_WRONLY;
    else o = O_RDONLY;
    if (flags & OS_O_CREATE) o |= O_CREAT;
    if (flags & OS_O_EXCL) o |= O_EXCL;
    if (flags & OS_O_TRUNC) o |= O_TRUNC;
    if (flags & OS_O_APPEND) o |= O_APPEND;
    int fd = open(path, o | O_CLOEXEC, 0666);
    if (fd < 0)
        return neg_errno();
    struct stat st;
    if (fstat(fd, &st) == 0 && S_ISDIR(st.st_mode) && (flags & OS_O_WRITE)) {
        close(fd);
        return -OS_EISDIR;
    }
    return fd;
}

int64_t os_file_read(int fd, void *b, size_t n) { ssize_t r = read(fd, b, n); return r < 0 ? neg_errno() : r; }
int64_t os_file_write(int fd, const void *b, size_t n) { ssize_t r = write(fd, b, n); return r < 0 ? neg_errno() : r; }
int64_t os_file_pread(int fd, void *b, size_t n, uint64_t off) { ssize_t r = pread(fd, b, n, (off_t)off); return r < 0 ? neg_errno() : r; }
int64_t os_file_pwrite(int fd, const void *b, size_t n, uint64_t off) { ssize_t r = pwrite(fd, b, n, (off_t)off); return r < 0 ? neg_errno() : r; }

int64_t os_file_seek(int fd, int64_t off, int whence)
{
    int w = whence == OS_SEEK_CUR ? SEEK_CUR : whence == OS_SEEK_END ? SEEK_END : SEEK_SET;
    off_t r = lseek(fd, (off_t)off, w);
    return r < 0 ? neg_errno() : (int64_t)r;
}

int os_file_truncate(int fd, uint64_t size) { return ftruncate(fd, (off_t)size) ? neg_errno() : 0; }
int os_file_sync(int fd) { return fsync(fd) ? neg_errno() : 0; }
int os_file_close(int fd) { return close(fd) ? neg_errno() : 0; }

static void fill_stat(const struct stat *s, os_stat_t *o)
{
    o->is_dir = S_ISDIR(s->st_mode);
    o->read_only = !(s->st_mode & S_IWUSR);
    o->size = (uint64_t)s->st_size;
    o->mtime_us = (int64_t)s->st_mtim.tv_sec * 1000000 + s->st_mtim.tv_nsec / 1000;
    o->atime_us = (int64_t)s->st_atim.tv_sec * 1000000 + s->st_atim.tv_nsec / 1000;
    o->ctime_us = (int64_t)s->st_ctim.tv_sec * 1000000 + s->st_ctim.tv_nsec / 1000;
}

int os_file_fstat(int fd, os_stat_t *o)
{
    struct stat s;
    if (fstat(fd, &s))
        return neg_errno();
    fill_stat(&s, o);
    return 0;
}

int os_stat(const char *path, os_stat_t *o)
{
    struct stat s;
    if (stat(path, &s))
        return neg_errno();
    fill_stat(&s, o);
    return 0;
}

int os_mkdir(const char *p) { return mkdir(p, 0777) ? neg_errno() : 0; }
int os_rmdir(const char *p) { return rmdir(p) ? neg_errno() : 0; }
int os_remove(const char *p) { return unlink(p) ? neg_errno() : 0; }
int os_rename(const char *a, const char *b) { return rename(a, b) ? neg_errno() : 0; }

struct os_dir { DIR *d; };

os_dir *os_dir_open(const char *path)
{
    DIR *d = opendir(path);
    if (!d)
        return NULL;
    os_dir *r = (os_dir *)malloc(sizeof *r);
    if (!r) {
        closedir(d);
        return NULL;
    }
    r->d = d;
    return r;
}

const char *os_dir_next(os_dir *d)
{
    struct dirent *e;
    while ((e = readdir(d->d))) {
        if (strcmp(e->d_name, ".") && strcmp(e->d_name, ".."))
            return e->d_name;
    }
    return NULL;
}

void os_dir_close(os_dir *d)
{
    if (d) {
        closedir(d->d);
        free(d);
    }
}


/* ---- virtual memory */
size_t os_vm_page_size(void) { return (size_t)sysconf(_SC_PAGESIZE); }

void *os_vm_reserve(void *hint, size_t size)
{
    int fl = MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE;
    if (!hint) { /* Windows hands out 64 KiB aligned reservations: over-map and trim */
        const size_t g = 0x10000;
        char *p = (char *)mmap(NULL, size + g, PROT_NONE, fl, -1, 0);
        if (p == MAP_FAILED)
            return NULL;
        char *a = (char *)(((uintptr_t)p + g - 1) & ~(uintptr_t)(g - 1));
        if (a > p)
            munmap(p, (size_t)(a - p));
        if (a + size < p + size + g)
            munmap(a + size, (size_t)(p + size + g - (a + size)));
        return a;
    }
#ifdef MAP_FIXED_NOREPLACE
    if (hint)
        fl |= MAP_FIXED_NOREPLACE;
#endif
    void *p = mmap(hint, size, PROT_NONE, fl, -1, 0);
    if (p == MAP_FAILED)
        return NULL;
    if (hint && p != hint) {
        munmap(p, size);
        return NULL;
    }
    return p;
}

int os_vm_commit(void *addr, size_t size)
{
    return mprotect(addr, size, PROT_READ | PROT_WRITE) ? -OS_ENOMEM : 0;
}

int os_vm_decommit(void *addr, size_t size)
{
    /* replace with fresh inaccessible pages: drops contents and backing */
    void *p = mmap(addr, size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE | MAP_FIXED, -1, 0);
    return p == MAP_FAILED ? -OS_EINVAL : 0;
}

int os_vm_release(void *addr, size_t size) { return munmap(addr, size) ? -OS_EINVAL : 0; }

static int posix_prot(int p)
{
    return (p & OS_VM_READ ? PROT_READ : 0) | (p & OS_VM_WRITE ? PROT_WRITE : 0) | (p & OS_VM_EXEC ? PROT_EXEC : 0);
}

int os_vm_protect(void *addr, size_t size, int os_prot)
{
    return mprotect(addr, size, posix_prot(os_prot)) ? -OS_EINVAL : 0;
}

struct os_shm { int fd; };

os_shm *os_shm_create(uint64_t size)
{
    int fd = (int)syscall(SYS_memfd_create, "fnvnx-shm", 1u /* MFD_CLOEXEC */);
    if (fd < 0)
        return NULL;
    if (size > (uint64_t)INT64_MAX || ftruncate(fd, (off_t)size)) {
        close(fd);
        return NULL;
    }
    os_shm *s = (os_shm *)malloc(sizeof *s);
    if (!s) {
        close(fd);
        return NULL;
    }
    s->fd = fd;
    return s;
}

void *os_shm_map(os_shm *s, uint64_t off, size_t size, void *hint, int os_prot, int priv)
{
    int fl = priv ? MAP_PRIVATE : MAP_SHARED;
#ifdef MAP_FIXED_NOREPLACE
    if (hint)
        fl |= MAP_FIXED_NOREPLACE;
#endif
    void *p = mmap(hint, size, posix_prot(os_prot), fl, s->fd, (off_t)off);
    if (p == MAP_FAILED)
        return NULL;
    if (hint && p != hint) {
        munmap(p, size);
        return NULL;
    }
    return p;
}

int os_shm_unmap(void *addr, size_t size) { return munmap(addr, size) ? -OS_EINVAL : 0; }

void os_shm_free(os_shm *s)
{
    if (s) {
        close(s->fd);
        free(s);
    }
}

const char *os_default_root(void)
{
    const char *r = getenv("FNVNX_ROOT");
    return r && *r ? r : "./fnvnx-root";
}
