/* os_posix.c - host backend of os.h (Linux; futex locks). Used for the host tests and as a reference for
 * os_horizon.c. Builds for x86-64 LP64 and for aarch64 ILP32 (syntax only, no ILP32 libc exists on hosts). */
#define _GNU_SOURCE
#include "os.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
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
void os_init(void) {}

void os_debug_print(const char *s)
{
    size_t n = strlen(s);
    fwrite(s, 1, n, stderr);
    if (n == 0 || s[n - 1] != '\n')
        fputc('\n', stderr);
}

void os_fatal(const char *msg)
{
    fprintf(stderr, "fnvnx fatal: %s\n", msg);
    abort();
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

/* ---- locks: 0 free, 1 locked, 2 locked with waiters (Drepper, "Futexes are tricky", mutex 3) */
void os_lock(os_lock_t *l)
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

int os_trylock(os_lock_t *l)
{
    uint32_t c = 0;
    return __atomic_compare_exchange_n(&l->word, &c, 1, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED);
}

void os_unlock(os_lock_t *l)
{
    if (__atomic_fetch_sub(&l->word, 1, __ATOMIC_RELEASE) != 1) {
        __atomic_store_n(&l->word, 0, __ATOMIC_RELEASE);
        futex_wake(&l->word, 1);
    }
}

/* ---- condvars: sequence counter */
int os_cond_wait(os_cond_t *c, os_lock_t *l, uint64_t timeout_ns)
{
    uint32_t seq = __atomic_load_n(&c->word, __ATOMIC_ACQUIRE);
    uint64_t start = timeout_ns == OS_WAIT_FOREVER ? 0 : os_monotonic_ns();
    os_unlock(l);
    if (timeout_ns == OS_WAIT_FOREVER) {
        futex_wait(&c->word, seq, NULL);
    } else {
        struct timespec ts = { (time_t)(timeout_ns / 1000000000ull), (long)(timeout_ns % 1000000000ull) };
        futex_wait(&c->word, seq, &ts);
    }
    /* futex_wait gives no reliable "timed out" result through spurious wakes, measure instead */
    int timed_out = timeout_ns != OS_WAIT_FOREVER && __atomic_load_n(&c->word, __ATOMIC_ACQUIRE) == seq &&
                    os_monotonic_ns() - start >= timeout_ns;
    os_lock(l);
    return timed_out;
}

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
};

static void *thread_entry(void *p)
{
    os_thread *t = (os_thread *)p;
    if (t->cpu >= 0)
        os_thread_set_cpu(NULL, t->cpu);
    t->fn(t->arg);
    return NULL;
}

os_thread *os_thread_create(os_thread_fn fn, void *arg, size_t stack_size, int prio, int cpu)
{
    os_thread *t = (os_thread *)calloc(1, sizeof *t);
    if (!t)
        return NULL;
    t->fn = fn; t->arg = arg; t->stack = stack_size; t->prio = prio; t->cpu = cpu;
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
    int r = pthread_create(&t->th, &a, thread_entry, t);
    pthread_attr_destroy(&a);
    if (r)
        return -1;
    t->started = 1;
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
    free(t);
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

int os_thread_suspend(os_thread *t) { (void)t; return -1; }
int os_thread_resume(os_thread *t) { (void)t; return -1; }
void os_thread_exit(void) { pthread_exit(NULL); }
void os_cpu_boost(int fast_load) { (void)fast_load; }   /* no CPU boost on a PC */
void os_yield(void) { sched_yield(); }

void os_sleep_ns(uint64_t ns)
{
    struct timespec ts = { (time_t)(ns / 1000000000ull), (long)(ns % 1000000000ull) };
    while (nanosleep(&ts, &ts) && errno == EINTR) {
    }
}

static __thread void *g_tls;
void *os_tls_get(void) { return g_tls; }
void os_tls_set(void *p) { g_tls = p; }

/* ---- time. Host ticks are 100 ns (10 MHz, the usual Windows QPC rate). */
uint64_t os_monotonic_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}
uint64_t os_ticks(void) { return os_monotonic_ns() / 100; }
uint64_t os_tick_freq(void) { return 10000000ull; }

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

int os_fs_case_insensitive(void) { return 0; }

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

const char *os_game_dir(void)
{
    const char *g = getenv("FNVNX_GAME_DIR");
    return g && *g ? g : NULL;
}

const char *os_default_root(void)
{
    const char *r = getenv("FNVNX_ROOT");
    return r && *r ? r : "./fnvnx-root";
}
