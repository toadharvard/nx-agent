/* os_guest.c - platform/os/os.h for the ILP32 guest (replaces os_horizon.c in the game build).
 *
 * os_horizon.c calls libnx directly, which the ILP32 guest cannot link (libnx is LP64, docs/TOOLCHAIN.md §2).
 * This backend implements the same contract on the guest runtime (toolchain/guest/threads.c, fs.c) and
 * FnvnxHostApi: threads are host threads with guest TLS, locks/condvars are the runtime's futex words, files
 * go to the host's newlib/fsdev. Paths are host paths ("sdmc:/...").
 */
#include "../../platform/os/os.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <malloc.h>

#include "fnvnx/guest.h"
#include "fnvnx/hostapi.h"

static int64_t hs(uint64_t v) { return (int64_t)v; }

void os_init(void) {}

void os_debug_print(const char *s) {
    size_t n = strlen(s);
    fnvnx_hcall2(fnvnx_host->output_debug_string, FNVNX_ADDR(s), n);
    fnvnx_hcall2(fnvnx_host->console_write, FNVNX_ADDR(s), n);
    if (n == 0 || s[n - 1] != '\n') fnvnx_hcall2(fnvnx_host->console_write, FNVNX_ADDR("\n"), 1);
}

void os_fatal(const char *msg) {
    os_debug_print("fnvnx fatal:");
    os_debug_print(msg);
    fnvnx_hcall1(fnvnx_host->exit, 0xFA);
    __builtin_unreachable();
}

void os_exit_process(int code) {
    fflush(NULL);
    exit(code);
}

unsigned os_cpu_count(void) { return (unsigned)fnvnx_hcall0(fnvnx_host->cpu_count); }
uint32_t os_process_id(void) { return 0x51; }   /* any stable non-zero id */

void os_memory_info(uint64_t *total, uint64_t *avail) {
    FnvnxMemInfo m = {0, 0};
    fnvnx_hcall1(fnvnx_host->memory_info, FNVNX_ADDR(&m));
    *total = m.total_phys;
    *avail = m.avail_phys;
}

uint32_t os_random32(void) { return (uint32_t)fnvnx_hcall0(fnvnx_host->random64); }

/* ---- locks / condvars: os_lock_t / os_cond_t are one u32, the runtime's lock/cond words */
void os_lock(os_lock_t *l) { fnvnx_lock(&l->word); }
int os_trylock(os_lock_t *l) { return fnvnx_trylock(&l->word); }
void os_unlock(os_lock_t *l) { fnvnx_unlock(&l->word); }
int os_cond_wait(os_cond_t *c, os_lock_t *l, uint64_t timeout_ns) {
    return fnvnx_cond_wait(&c->word, &l->word, timeout_ns);
}
void os_cond_signal(os_cond_t *c) { fnvnx_cond_signal(&c->word); }
void os_cond_broadcast(os_cond_t *c) { fnvnx_cond_broadcast(&c->word); }

/* ---- threads */
struct os_thread {
    struct __pthread_t *t;
    os_thread_fn fn;
    void *arg;
};

static void *trampoline(void *p) {
    os_thread *t = (os_thread *)p;
    t->fn(t->arg);
    return NULL;
}

os_thread *os_thread_create(os_thread_fn fn, void *arg, size_t stack_size, int prio, int cpu) {
    os_thread *t = calloc(1, sizeof *t);
    if (!t) return NULL;
    t->fn = fn;
    t->arg = arg;
    size_t s = stack_size ? stack_size : 1024 * 1024;
    if (s < 0x10000) s = 0x10000;
    if (fnvnx_thread_create(&t->t, trampoline, t, s, prio, cpu) != 0) { free(t); return NULL; }
    return t;
}
int os_thread_start(os_thread *t) { return fnvnx_thread_start(t->t) ? -1 : 0; }
int os_thread_join(os_thread *t) {
    if (!t->t) return -1;
    fnvnx_thread_join(t->t);
    t->t = NULL;
    return 0;
}
void os_thread_free(os_thread *t) {
    if (t->t) {          /* detach a running thread */
        extern int __syscall_thread_detach(struct __pthread_t *);
        __syscall_thread_detach(t->t);
    }
    free(t);
}
/* t: another thread (also one not started yet) through its host id; NULL: the calling thread (id 0) */
static int host_id_of(os_thread *t, uint64_t *id) {
    *id = t ? fnvnx_thread_host_id(t->t) : 0;
    return t && !*id ? -1 : 0;   /* joined: its host thread is gone */
}
int os_thread_set_priority(os_thread *t, int prio) {
    uint64_t id;
    if (host_id_of(t, &id)) return -1;
    return fnvnx_hcall2(fnvnx_host->thread_set_priority, id, (uint64_t)(int64_t)prio) ? -1 : 0;
}
int os_thread_set_cpu(os_thread *t, int cpu) {
    uint64_t id;
    if (host_id_of(t, &id)) return -1;
    if (cpu < 0) return 0;
    return fnvnx_hcall2(fnvnx_host->thread_set_cpu, id, (uint64_t)cpu) ? -1 : 0;
}
int os_thread_set_affinity(os_thread *t, int ideal, unsigned mask) {
    uint64_t id;
    if (host_id_of(t, &id)) return -1;
    uint64_t spec = FNVNX_CPU_SPEC(ideal < 0 ? FNVNX_CPU_KEEP : (unsigned)ideal, mask);
    return fnvnx_hcall2(fnvnx_host->thread_set_cpu, id, spec) ? -1 : 0;
}
int os_thread_suspend(os_thread *t) { (void)t; return -1; }
int os_thread_resume(os_thread *t) { (void)t; return -1; }
void os_thread_exit(void) { fnvnx_thread_exit(NULL); }
/* the host's platform services (toolchain/host/host.c platform_ext): appletSetCpuBoostMode, unless the config
 * (sdmc:/fnvnx/fnvnx.ini boost_loading=0) turns it off */
_Static_assert(OS_ROLE_AUDIO == FNVNX_ROLE_AUDIO, "platform/os/os.h OS_ROLE_AUDIO = hostapi.h FNVNX_ROLE_AUDIO");
void os_cpu_boost(int fast_load) {
    if (!fnvnx_host->ext_call) return;
    uint64_t arg[1] = {fast_load ? 1u : 0u};
    fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, FNVNX_PLAT_CPU_BOOST, FNVNX_ADDR(arg), 1);
}
void os_yield(void) { fnvnx_yield(); }
void os_sleep_ns(uint64_t ns) { fnvnx_hcall1(fnvnx_host->sleep_ns, ns); }

static __thread void *t_os_tls;
void *os_tls_get(void) { return t_os_tls; }
void os_tls_set(void *p) { t_os_tls = p; }

/* ---- time */
uint64_t os_ticks(void) { return fnvnx_hcall0(fnvnx_host->system_tick); }
uint64_t os_tick_freq(void) { return fnvnx_hcall0(fnvnx_host->system_tick_freq); }
uint64_t os_monotonic_ns(void) {
    uint64_t t = os_ticks(), f = os_tick_freq();
    return (t / f) * 1000000000ull + (t % f) * 1000000000ull / f;
}
uint64_t os_cycle_counter(void) {
    uint64_t v;
    __asm__ volatile("mrs %0, cntvct_el0" : "=r"(v));
    return v;
}
int64_t os_wall_time_us(void) { return hs(fnvnx_hcall0(fnvnx_host->wall_time_us)); }
int32_t os_tz_offset_minutes(void) { return (int32_t)hs(fnvnx_hcall0(fnvnx_host->tz_offset_minutes)); }

/* ---- files: host fs calls return -errno, os.h wants -OS_E* (same numbers) */
int os_file_open(const char *path, int flags) {
    return (int)hs(fnvnx_hcall2(fnvnx_host->fs_open, FNVNX_ADDR(path), (uint64_t)flags));
}
int64_t os_file_read(int fd, void *buf, size_t n) {
    return hs(fnvnx_hcall3(fnvnx_host->fs_read, (uint64_t)fd, FNVNX_ADDR(buf), n));
}
int64_t os_file_write(int fd, const void *buf, size_t n) {
    return hs(fnvnx_hcall3(fnvnx_host->fs_write, (uint64_t)fd, FNVNX_ADDR(buf), n));
}
int64_t os_file_pread(int fd, void *buf, size_t n, uint64_t off) {
    return hs(fnvnx_hcall4(fnvnx_host->fs_pread, (uint64_t)fd, FNVNX_ADDR(buf), n, off));
}
int64_t os_file_pwrite(int fd, const void *buf, size_t n, uint64_t off) {
    return hs(fnvnx_hcall4(fnvnx_host->fs_pwrite, (uint64_t)fd, FNVNX_ADDR(buf), n, off));
}
int64_t os_file_seek(int fd, int64_t off, int whence) {
    return hs(fnvnx_hcall3(fnvnx_host->fs_seek, (uint64_t)fd, (uint64_t)off, (uint64_t)whence));
}
int os_file_truncate(int fd, uint64_t size) { return (int)hs(fnvnx_hcall2(fnvnx_host->fs_truncate, (uint64_t)fd, size)); }
int os_file_sync(int fd) { return (int)hs(fnvnx_hcall1(fnvnx_host->fs_sync, (uint64_t)fd)); }
static void from_host(const FnvnxStat *h, os_stat_t *st) {
    st->is_dir = (int)h->is_dir;
    st->read_only = (int)h->read_only;
    st->size = h->size;
    st->mtime_us = h->mtime_us;
    st->atime_us = h->atime_us;
    st->ctime_us = h->ctime_us;
}
int os_file_fstat(int fd, os_stat_t *st) {
    FnvnxStat h;
    int r = (int)hs(fnvnx_hcall2(fnvnx_host->fs_fstat, (uint64_t)fd, FNVNX_ADDR(&h)));
    if (r == 0) from_host(&h, st);
    return r;
}
int os_file_close(int fd) { return (int)hs(fnvnx_hcall1(fnvnx_host->fs_close, (uint64_t)fd)); }
int os_stat(const char *path, os_stat_t *st) {
    FnvnxStat h;
    int r = (int)hs(fnvnx_hcall2(fnvnx_host->fs_stat, FNVNX_ADDR(path), FNVNX_ADDR(&h)));
    if (r == 0) from_host(&h, st);
    return r;
}
int os_mkdir(const char *p) { return (int)hs(fnvnx_hcall1(fnvnx_host->fs_mkdir, FNVNX_ADDR(p))); }
int os_rmdir(const char *p) { return (int)hs(fnvnx_hcall1(fnvnx_host->fs_rmdir, FNVNX_ADDR(p))); }
int os_remove(const char *p) { return (int)hs(fnvnx_hcall1(fnvnx_host->fs_remove, FNVNX_ADDR(p))); }
int os_rename(const char *a, const char *b) {
    return (int)hs(fnvnx_hcall2(fnvnx_host->fs_rename, FNVNX_ADDR(a), FNVNX_ADDR(b)));
}

struct os_dir {
    uint64_t h;
    char name[256];
};
os_dir *os_dir_open(const char *path) {
    uint64_t h = fnvnx_hcall1(fnvnx_host->fs_dir_open, FNVNX_ADDR(path));
    if (!h) return NULL;
    os_dir *d = calloc(1, sizeof *d);
    if (!d) { fnvnx_hcall1(fnvnx_host->fs_dir_close, h); return NULL; }
    d->h = h;
    return d;
}
const char *os_dir_next(os_dir *d) {
    return fnvnx_hcall3(fnvnx_host->fs_dir_next, d->h, FNVNX_ADDR(d->name), sizeof d->name) ? d->name : NULL;
}
void os_dir_close(os_dir *d) {
    fnvnx_hcall1(fnvnx_host->fs_dir_close, d->h);
    free(d);
}
int os_fs_case_insensitive(void) { return 1; }   /* FAT32/exFAT SD card */

/* ---- virtual memory: carved from the guest heap (committed and zeroed at reserve time).
 * A fixed hint is not supported (the S3 data window is mapped by the loader, not through this). */
size_t os_vm_page_size(void) { return 0x1000; }
void *os_vm_reserve(void *hint, size_t size) {
    if (hint) return NULL;
    void *p = memalign(0x1000, (size + 0xFFF) & ~(size_t)0xFFF);
    if (p) memset(p, 0, size);
    return p;
}
int os_vm_commit(void *addr, size_t size) { (void)addr; (void)size; return 0; }
int os_vm_decommit(void *addr, size_t size) { memset(addr, 0, size); return 0; }
int os_vm_release(void *addr, size_t size) { (void)size; free(addr); return 0; }

/* The port's SD directory: guest.nso, host.log and fnvnx.ini (host.c), user/ (C:/Users/Player: My Games/FalloutNV,
 * saves), drive_<x>/. The game install (C:/FalloutNV: FalloutNV.exe, Data/, the INIs) is os_game_dir(). */
const char *os_default_root(void) { return "sdmc:/fnvnx"; }

/* The game install on the SD card: sdmc:/switch/fnv (Data/ at sdmc:/switch/fnv/Data), or `game_dir=` in
 * sdmc:/fnvnx/fnvnx.ini (a path without "sdmc:" is taken on the SD card). Read once; the first caller is boot.cpp,
 * before any thread exists. */
#define FNVNX_GAME_DIR_DEFAULT "sdmc:/switch/fnv"
const char *os_game_dir(void) {
    static char dir[256];
    if (dir[0]) return dir;
    snprintf(dir, sizeof dir, "%s", FNVNX_GAME_DIR_DEFAULT);
    int fd = os_file_open("sdmc:/fnvnx/fnvnx.ini", OS_O_READ);
    if (fd < 0) return dir;
    char buf[2048];
    int64_t n = os_file_read(fd, buf, sizeof buf - 1);
    os_file_close(fd);
    buf[n > 0 ? n : 0] = 0;
    for (char *line = buf; line && *line;) {
        char *next = strchr(line, '\n');
        if (next) *next++ = 0;
        char *c = strpbrk(line, "#;\r");
        if (c) *c = 0;
        while (*line == ' ' || *line == '\t') line++;
        if (!strncmp(line, "game_dir", 8)) {
            char *v = line + 8;
            while (*v == ' ' || *v == '\t') v++;
            if (*v++ == '=') {
                while (*v == ' ' || *v == '\t') v++;
                size_t l = strlen(v);
                while (l && (v[l - 1] == ' ' || v[l - 1] == '\t' || v[l - 1] == '/')) v[--l] = 0;
                if (l)
                    snprintf(dir, sizeof dir, "%s%s", strncmp(v, "sdmc:", 5) ? (*v == '/' ? "sdmc:" : "sdmc:/") : "", v);
            }
        }
        line = next;
    }
    return dir;
}
