/* os_guest.c - platform/os/os.h for the ILP32 guest (replaces os_horizon.c in the game build).
 *
 * os_horizon.c calls libnx directly, which the ILP32 guest cannot link (libnx is LP64, docs/TOOLCHAIN.md §2).
 * This backend implements the same contract on the guest runtime (toolchain/guest/threads.c, fs.c) and
 * FnvnxHostApi: threads are host threads with guest TLS, locks/condvars are the runtime's futex words, files
 * go to the host's newlib/fsdev. Paths are host paths ("sdmc:/...").
 */
#include "../os/os_clock.h"
#include "../os/os_world.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <malloc.h>

#include "fnvnx/guest.h"
#include "fnvnx/hostapi.h"
#include "fnvnx/sdlayout.h"

static int64_t hs(uint64_t v) { return (int64_t)v; }

void os_debug_print(const char *s) {
    size_t n = strlen(s);
    fnvnx_hcall2(fnvnx_host->output_debug_string, FNVNX_ADDR(s), n);
    fnvnx_hcall2(fnvnx_host->console_write, FNVNX_ADDR(s), n);
    if (n == 0 || s[n - 1] != '\n') fnvnx_hcall2(fnvnx_host->console_write, FNVNX_ADDR("\n"), 1);
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
/* the game's own locks (os.h): the same word, the same runtime lock */
void os_glock(os_lock_t *l) { fnvnx_lock(&l->word); }
int os_gtrylock(os_lock_t *l) { return fnvnx_trylock(&l->word); }
void os_gunlock(os_lock_t *l) { fnvnx_unlock(&l->word); }
int os_cond_timedwait(os_cond_t *c, os_lock_t *l, uint64_t timeout_ns) {
    return fnvnx_cond_wait(&c->word, &l->word, timeout_ns);
}
void os_cond_wait(os_cond_t *c, os_lock_t *l) { os_cond_timedwait(c, l, OS_WAIT_FOREVER); }
void os_cond_signal(os_cond_t *c) { fnvnx_cond_signal(&c->word); }
void os_cond_broadcast(os_cond_t *c) { fnvnx_cond_broadcast(&c->word); }

/* ---- threads */
struct os_thread {
    struct __pthread_t *t;
    os_thread_fn fn;
    void *arg;
    uint64_t hid;                  /* the host thread id: stays valid until the host thread is freed */
    int refs;                      /* the owner's reference, and the running thread's from os_thread_start until its end */
    os_wslot slot;                 /* the world registry (os_world.c) */
};
static __thread os_thread *g_me;   /* the platform-made thread this is (NULL: the adopted main thread) */

static void thread_unref(os_thread *t) {
    if (__atomic_sub_fetch(&t->refs, 1, __ATOMIC_ACQ_REL) == 0) free(t);
}
/* the thread's last act: leave the registry (under the world lock, so never during a stop), then drop its reference */
static void thread_done(os_thread *t) {
    os_world_unregister(&t->slot);
    g_me = NULL;
    thread_unref(t);
}

static void *trampoline(void *p) {
    os_thread *t = (os_thread *)p;
    g_me = t;
    os_world_register(&t->slot, t);   /* blocks while the world is stopped: no guest code starts during a stop */
    t->fn(t->arg);
    thread_done(t);
    return NULL;
}

os_thread *os_thread_create(os_thread_fn fn, void *arg, size_t stack_size, int prio, int cpu) {
    os_thread *t = calloc(1, sizeof *t);
    if (!t) return NULL;
    t->fn = fn;
    t->arg = arg;
    size_t s = stack_size ? stack_size : 1024 * 1024;
    if (s < 0x10000) s = 0x10000;
    t->refs = 1;
    if (fnvnx_thread_create(&t->t, trampoline, t, s, prio, cpu) != 0) { free(t); return NULL; }
    t->hid = fnvnx_thread_host_id(t->t);
    return t;
}
int os_thread_start(os_thread *t) {
    __atomic_add_fetch(&t->refs, 1, __ATOMIC_ACQ_REL);
    if (fnvnx_thread_start(t->t)) { __atomic_sub_fetch(&t->refs, 1, __ATOMIC_ACQ_REL); return -1; }
    return 0;
}
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
    thread_unref(t);
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
/* the calling thread cannot be paused by the kernel: thread.cpp blocks it instead (SuspendThread on itself). */
/* The pause primitive (os_world.h): the host's svcSetThreadActivity through FNVNX_PLAT_THREAD_ACTIVITY, a boolean per thread
 * (os_world.c combines the user and world reasons). The host call returns only when the thread is stopped: it waits
 * for svcGetThreadContext3 to succeed, which the kernel allows only for a paused, descheduled thread (host_thread_activity.inc;
 * the guarantee is cited there). The calling thread cannot be paused: -1. */
os_wslot *os_thread_wslot(os_thread *t) { return &t->slot; }
int os_raw_pause(os_thread *t) {
    if (t == g_me || !t->hid) return -1;
    uint64_t arg[2] = {t->hid, 1u};
    return fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, FNVNX_PLAT_THREAD_ACTIVITY, FNVNX_ADDR(arg), 2) ? -1 : 0;
}
void os_raw_resume(os_thread *t) {
    uint64_t arg[2] = {t->hid, 0u};
    fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, FNVNX_PLAT_THREAD_ACTIVITY, FNVNX_ADDR(arg), 2);
}
/* the main thread: the host registers a record for the calling thread (FNVNX_PLAT_THREAD_SELF) and returns its id */
os_thread *os_raw_adopt_self(void) {
    uint64_t arg[1] = {0};
    uint64_t id = fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, FNVNX_PLAT_THREAD_SELF, FNVNX_ADDR(arg), 0);
    if (!id) return NULL;
    os_thread *t = calloc(1, sizeof *t);
    if (!t) return NULL;
    t->hid = id;
    t->refs = 1;
    return t;
}
void os_thread_exit(void) {
    if (g_me) thread_done(g_me);
    fnvnx_thread_exit(NULL);
}
/* the host's platform services (toolchain/host/host.c platform_ext): appletSetCpuBoostMode, unless the config
 * (sdmc:/switch/fnv/fnvnx.ini boost_loading=0) turns it off */
_Static_assert(OS_ROLE_AUDIO == FNVNX_ROLE_AUDIO, "platform/os/os.h OS_ROLE_AUDIO = hostapi.h FNVNX_ROLE_AUDIO");
void os_cpu_boost(int fast_load) {
    if (!fnvnx_host->ext_call) return;
    uint64_t arg[1] = {fast_load ? 1u : 0u};
    fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, FNVNX_PLAT_CPU_BOOST, FNVNX_ADDR(arg), 1);
}
/* the host's error applet (toolchain/host/host_message.inc) */
void os_show_message(const char *caption, const char *text) {
    if (!fnvnx_host->ext_call) return;
    uint64_t arg[2] = {FNVNX_ADDR(caption ? caption : ""), FNVNX_ADDR(text ? text : "")};
    fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, FNVNX_PLAT_MESSAGE, FNVNX_ADDR(arg), 2);
}
/* the system software keyboard: host service FNVNX_PLAT_TEXT_ENTRY (hostapi.h), whose result is this function's: 1 entered, 0 cancelled,
 * negative = -FNVNX_HE_BADINPUT or -(the applet's Horizon result) */
int os_text_entry(unsigned max_len, const uint16_t *init, size_t init_n, uint16_t *out, size_t *n) {
    uint32_t cnt = 0;
    uint64_t arg[5] = {max_len, init ? FNVNX_ADDR(init) : 0u, init ? init_n : 0u, out ? FNVNX_ADDR(out) : 0u, n ? FNVNX_ADDR(&cnt) : 0u};
    int64_t r = (int64_t)fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, FNVNX_PLAT_TEXT_ENTRY, FNVNX_ADDR(arg), 5);
    if (r == 1) *n = cnt;
    return (int)r;
}
/* the console's operation mode: host service FNVNX_PLAT_DISPLAY_DOCKED (hostapi.h), which every host implements
 * (toolchain/host/host.c platform_ext: appletGetOperationMode() == AppletOperationMode_Console -> 1 docked, 0
 * handheld) */
int os_display_docked(void) {
    return fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, FNVNX_PLAT_DISPLAY_DOCKED, 0, 0) == 1;
}
void os_yield(void) { fnvnx_yield(); }
void os_sleep_ns(uint64_t ns) { fnvnx_hcall1(fnvnx_host->sleep_ns, ns); }

/* ---- time */
uint64_t os_ticks(void) { return fnvnx_hcall0(fnvnx_host->system_tick); }
uint64_t os_tick_freq(void) { return fnvnx_hcall0(fnvnx_host->system_tick_freq); }
uint64_t os_monotonic_ns(void) {
    uint64_t t = os_ticks(), f = os_tick_freq();
    return (t / f) * 1000000000ull + (t % f) * 1000000000ull / f;
}
uint64_t os_cycle_counter(void) {
    uint64_t v;
    __asm__ volatile("mrs %0, cntpct_el0" : "=r"(v));   /* CNTVCT_EL0 traps at EL0 on Horizon (NX1 check 3) */
    return v;
}
int64_t os_wall_time_us(void) { return hs(fnvnx_hcall0(fnvnx_host->wall_time_us)); }
/* the console's time zone: the host's TZ_AT service (hostapi.h) gives offset, daylight flag and abbreviation at an instant; an older
 * host without it answers FNVNX_EXT_NO_MODULE (no zone data: UTC for the offset, os_tz_rule fails) */
static int tzr_at(int64_t utc_s, int32_t *off_s, int *is_dst, char abbr[8]) {
    FnvnxTzAt o;
    uint64_t arg[2] = {(uint64_t)utc_s, FNVNX_ADDR(&o)};
    if (!fnvnx_host->ext_call || fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, FNVNX_PLAT_TZ_AT, FNVNX_ADDR(arg), 2)) return -1;
    *off_s = o.off_s;
    *is_dst = o.dst != 0;
    memcpy(abbr, o.abbr, 8);
    abbr[7] = 0;
    return 0;
}
int32_t os_zone_offset_minutes(void) {
    int32_t off;
    int dst;
    char n[8];
    return tzr_at(os_wall_time_us() / 1000000, &off, &dst, n) ? 0 : off / 60;
}
#include "../os/os_tzrule.inc"

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
/* FAT32/exFAT on the SD card fold case, but that is a property of Horizon's fs, not checked on hardware yet (NX1):
 * probe it once with the guest's own file. guest.nso found under its exact name but not as GUEST.NSO -> case-sensitive,
 * and path.cpp resolves every component against the directory (slower, correct). Without guest.nso under the root
 * (the emulator's sdmc tree) folds is 1: the case folding of the console's FAT, ASM-004. */
int os_fs_case_insensitive(void) {
    static int folds = -1;
    if (folds < 0) {
        os_stat_t st;
        folds = os_stat(FNVNX_SD_GUEST, &st) != 0 || os_stat(FNVNX_SD_DIR "/GUEST.NSO", &st) == 0;
    }
    return folds;
}

/* ---- virtual memory: the host's VM service (hostapi.h FNVNX_PLAT_VM_*, toolchain/host/host_vm.inc). Reservations are
 * address space only, commits map zeroed memory, decommits give it back, fixed addresses are honoured. The unified
 * host always serves it; there is no other source of pages. */
static uint64_t vm_ext(uint32_t fn, uint32_t n, uint64_t a0, uint64_t a1) {
    uint64_t arg[2] = {a0, a1};
    return fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, fn, FNVNX_ADDR(arg), n);
}
size_t os_vm_page_size(void) { return 0x1000; }
void *os_vm_reserve(void *hint, size_t size) {
    return (void *)(uintptr_t)vm_ext(FNVNX_PLAT_VM_RESERVE, 2, FNVNX_ADDR(hint), size);
}
int os_vm_commit(void *addr, size_t size) {
    return vm_ext(FNVNX_PLAT_VM_COMMIT, 2, FNVNX_ADDR(addr), size) ? -1 : 0;
}
int os_vm_decommit(void *addr, size_t size) {
    return vm_ext(FNVNX_PLAT_VM_DECOMMIT, 2, FNVNX_ADDR(addr), size) ? -1 : 0;
}
/* FNVNX_PLAT_VM_PROTECT (hostapi.h). The host's pages are never executable (the game's code is native, lifted code
 * that no page of the guest's data supplies): OS_VM_EXEC is the one os_prot bit with no counterpart there and is
 * dropped; readable and writable map 1:1 onto FNVNX_VM_PROT_R / _W. */
int os_vm_protect(void *addr, size_t size, int os_prot) {
    uint64_t arg[3] = {FNVNX_ADDR(addr), size,
                       (uint64_t)(((os_prot & OS_VM_READ) ? FNVNX_VM_PROT_R : 0) | ((os_prot & OS_VM_WRITE) ? FNVNX_VM_PROT_W : 0))};
    return fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, FNVNX_PLAT_VM_PROTECT, FNVNX_ADDR(arg), 3) ? -1 : 0;
}
int os_vm_release(void *addr, size_t size) {
    (void)size;
    return vm_ext(FNVNX_PLAT_VM_RELEASE, 1, FNVNX_ADDR(addr), 0) ? -1 : 0;
}

/* One SD directory for everything of the port (fnvnx/sdlayout.h): fnv.nro, guest.nso and the logs, the game's
 * C:/FalloutNV itself (FalloutNV.exe and Data/, read by boot.cpp too), user/ (C:/Users/Player: My Games/FalloutNV,
 * saves). */
const char *os_default_root(void) { return FNVNX_SD_DIR; }
