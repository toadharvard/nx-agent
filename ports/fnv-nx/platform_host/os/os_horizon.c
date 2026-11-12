/* os_horizon.c - Horizon OS (Nintendo Switch) backend of os.h, on libnx.
 *
 * Not buildable yet (stream S5 owns the devkitA64/ILP32 toolchain and the libnx boundary); written against the
 * libnx API at the pinned revision. Requirements on the build (S5):
 *   - files: fsdev mounted ("sdmc:") before the first file call; newlib POSIX calls go through the devoptab;
 *   - virtual memory: svcMapPhysicalMemory needs a non-zero system resource size in the NPDM, and the reserved
 *     ranges are carved out of the alias region (svcGetInfo InfoType_AliasRegionAddress/Size);
 *   - the address-space type must keep the alias region inside the 32-bit range used by ILP32 pointers.
 */
#include "../../platform/os/os_clock.h"
#include "../../platform/os/os_world.h"

/* os_cpu_count, os_vm_page_size, os_default_root, os_fs_case_insensitive: manifest/env_const.tsv (os_default_root = FNVNX_SD_DIR,
 * toolchain/include/fnvnx/sdlayout.h) */
#include "../../platform/os/env_const_horizon.gen.inc"

#include <switch/types.h>
#include <switch/result.h>
#include <switch/arm/counter.h>
#include <switch/kernel/condvar.h>
#include <switch/kernel/mutex.h>
#include <switch/kernel/random.h>
#include <switch/kernel/svc.h>
#include <switch/kernel/thread.h>
#include <switch/services/applet.h>
#include <switch/services/time.h>
#include <switch/applets/error.h>

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

void os_debug_print(const char *s)
{
    size_t n = strlen(s);
    svcOutputDebugString(s, n);
    fwrite(s, 1, n, stderr);
    if (n == 0 || s[n - 1] != '\n')
        fputc('\n', stderr);
}

void os_exit_process(int code)
{
    fflush(NULL);
    exit(code);
}


uint32_t os_process_id(void)
{
    u64 pid = 0;
    svcGetProcessId(&pid, CUR_PROCESS_HANDLE);
    return (uint32_t)pid;
}

void os_memory_info(uint64_t *total, uint64_t *avail)
{
    u64 t = 0, u = 0;
    svcGetInfo(&t, InfoType_TotalMemorySize, CUR_PROCESS_HANDLE, 0);
    svcGetInfo(&u, InfoType_UsedMemorySize, CUR_PROCESS_HANDLE, 0);
    *total = t;
    *avail = t > u ? t - u : 0;
}

uint32_t os_random32(void) { return (uint32_t)randomGet64(); }

/* ---- locks / condvars: libnx Mutex (_LOCK_T, one u32: owner handle | waiters bit; 0 = free) and CondVar
 * (u32). Both match os.h's "zero is valid" contract. */
_Static_assert(sizeof(Mutex) == sizeof(os_lock_t), "libnx Mutex must be one 32-bit word");
_Static_assert(sizeof(CondVar) == sizeof(os_cond_t), "libnx CondVar must be one 32-bit word");

void os_lock(os_lock_t *l) { mutexLock((Mutex *)&l->word); }
int os_trylock(os_lock_t *l) { return mutexTryLock((Mutex *)&l->word) ? 1 : 0; }
void os_unlock(os_lock_t *l) { mutexUnlock((Mutex *)&l->word); }
/* the game's own locks (os.h): the same word, the same mutex */
void os_glock(os_lock_t *l) { mutexLock((Mutex *)&l->word); }
int os_gtrylock(os_lock_t *l) { return mutexTryLock((Mutex *)&l->word) ? 1 : 0; }
void os_gunlock(os_lock_t *l) { mutexUnlock((Mutex *)&l->word); }

int os_cond_timedwait(os_cond_t *c, os_lock_t *l, uint64_t timeout_ns)
{
    Result rc = condvarWaitTimeout((CondVar *)&c->word, (Mutex *)&l->word,
                                   timeout_ns == OS_WAIT_FOREVER ? UINT64_MAX : timeout_ns);
    return R_VALUE(rc) == KERNELRESULT(TimedOut) ? 1 : 0;
}

void os_cond_wait(os_cond_t *c, os_lock_t *l) { os_cond_timedwait(c, l, OS_WAIT_FOREVER); }
void os_cond_signal(os_cond_t *c) { condvarWakeOne((CondVar *)&c->word); }
void os_cond_broadcast(os_cond_t *c) { condvarWakeAll((CondVar *)&c->word); }

/* ---- threads */
struct os_thread {
    Thread th;
    os_thread_fn fn;
    void *arg;
    int created, started, joined;
    u64 tid;                       /* kernel thread id, set by the thread itself on entry */
    struct os_thread *zombie_next; /* g_zombies */
    os_wslot slot;                 /* the world registry (os_world.c); the record outlives the thread (zombies, join) */
};
static __thread os_thread *t_self; /* the platform-made thread this is (NULL: the adopted main thread) */

/* A thread whose last reference goes away on the thread itself (CreateThread, CloseHandle, then the thread ends)
 * cannot wait for its own exit in os_thread_free: that would never return and the thread would leak for good.
 * Such a thread is parked here and closed by a later os_thread_create / os_thread_free, once it has exited. */
static os_lock_t g_zombie_lock;
static os_thread *g_zombies;

static void zombies_reap(void)
{
    os_thread *dead = NULL;
    os_lock(&g_zombie_lock);
    for (os_thread **p = &g_zombies; *p;) {
        os_thread *z = *p;
        s32 idx;
        if (R_SUCCEEDED(svcWaitSynchronization(&idx, &z->th.handle, 1, 0))) { /* a thread handle signals at exit */
            *p = z->zombie_next;
            z->zombie_next = dead;
            dead = z;
        } else {
            p = &z->zombie_next;
        }
    }
    os_unlock(&g_zombie_lock);
    while (dead) {
        os_thread *z = dead;
        dead = z->zombie_next;
        threadClose(&z->th);
        free(z);
    }
}

/* Win32 priority (-15..15, higher = more urgent) -> Horizon (0x18..0x3B for applications, lower = more urgent).
 * 0x2C is the default priority of the main thread. */
static int map_prio(int p)
{
    int h = 0x2C - p;
    return h < 0x18 ? 0x18 : h > 0x3B ? 0x3B : h;
}

static void thread_entry(void *p)
{
    os_thread *t = (os_thread *)p;
    u64 id = 0;
    svcGetThreadId(&id, CUR_THREAD_HANDLE);
    __atomic_store_n(&t->tid, id, __ATOMIC_RELEASE);
    t_self = t;
    os_world_register(&t->slot, t);   /* blocks while the world is stopped: no guest code starts during a stop */
    t->fn(t->arg);
    os_world_unregister(&t->slot);
}

os_thread *os_thread_create(os_thread_fn fn, void *arg, size_t stack_size, int prio, int cpu)
{
    zombies_reap();
    os_thread *t = (os_thread *)calloc(1, sizeof *t);
    if (!t)
        return NULL;
    t->fn = fn;
    t->arg = arg;
    size_t s = stack_size ? stack_size : 1024 * 1024;
    s = (s + 0xFFF) & ~(size_t)0xFFF;
    if (s < 0x10000)
        s = 0x10000;
    /* cpuid -2: the process' default core */
    if (R_FAILED(threadCreate(&t->th, thread_entry, t, NULL, s, map_prio(prio), cpu < 0 || cpu >= 0x100 ? -2 : cpu % 3))) {
        free(t);
        return NULL;
    }
    t->created = 1;
    return t;
}

int os_thread_start(os_thread *t)
{
    if (R_FAILED(threadStart(&t->th)))
        return -1;
    t->started = 1;
    return 0;
}

int os_thread_join(os_thread *t)
{
    if (t->joined)
        return 0;
    if (!t->started || R_FAILED(threadWaitForExit(&t->th)))
        return -1;
    t->joined = 1;
    return 0;
}

void os_thread_free(os_thread *t)
{
    if (!t)
        return;
    zombies_reap();
    if (t->started && !t->joined) {
        u64 me = 0;
        svcGetThreadId(&me, CUR_THREAD_HANDLE);
        if (__atomic_load_n(&t->tid, __ATOMIC_ACQUIRE) == me) {   /* freed by the thread itself: see g_zombies */
            os_lock(&g_zombie_lock);
            t->zombie_next = g_zombies;
            g_zombies = t;
            os_unlock(&g_zombie_lock);
            return;
        }
        /* libnx frees the stack in threadClose, which needs the thread to have exited */
        threadWaitForExit(&t->th);
    }
    if (t->created)
        threadClose(&t->th);
    free(t);
}

static Handle handle_of(os_thread *t) { return t ? t->th.handle : CUR_THREAD_HANDLE; }

int os_thread_set_priority(os_thread *t, int prio)
{
    return R_FAILED(svcSetThreadPriority(handle_of(t), (u32)map_prio(prio))) ? -1 : 0;
}

int os_thread_set_cpu(os_thread *t, int cpu)
{
    if (cpu < 0 || cpu >= 0x100)
        return 0;
    cpu %= 3;
    return R_FAILED(svcSetThreadCoreMask(handle_of(t), cpu, 1u << cpu)) ? -1 : 0;
}

int os_thread_set_affinity(os_thread *t, int ideal, unsigned mask)
{
    s32 cur = 0;
    u64 cur_mask = 7;
    svcGetThreadCoreMask(&cur, &cur_mask, handle_of(t));
    u32 m = mask ? (mask & 7) : (u32)cur_mask;
    s32 id = ideal >= 0 ? ideal % 3 : (m >> cur & 1) ? cur : (m & 1 ? 0 : m & 2 ? 1 : 2);
    m |= 1u << id;
    return R_FAILED(svcSetThreadCoreMask(handle_of(t), id, m)) ? -1 : 0;
}

os_wslot *os_thread_wslot(os_thread *t) { return &t->slot; }

/* The pause primitive of Horizon: svcSetThreadActivity(handle, Paused) / (Runnable), a boolean (os_world.c combines the
 * user and world reasons). Synchronous guarantee, from libnx's own header (switch/kernel/svc.h): svcGetThreadContext3
 * "dumps the registers of a thread paused by svcSetThreadActivity", and the kernel gives that dump only for a thread
 * that is suspended, i.e. no longer executing (it fails for a thread that is merely requested to pause).
 * svcSetThreadActivity itself returns once the pause is requested, so the primitive waits until svcGetThreadContext3
 * succeeds: then the thread is stopped. A thread requested to pause always reaches the suspended state (the kernel
 * interrupts the core it runs on, or it is already waiting), so the loop ends; it yields between tries.
 * Assumption (the header's contract): GetThreadContext3 succeeds only for a paused, descheduled thread.
 * The calling thread cannot pause itself (the kernel refuses): -1. */
static int is_self(os_thread *t)
{
    u64 me = 0;
    svcGetThreadId(&me, CUR_THREAD_HANDLE);
    return __atomic_load_n(&t->tid, __ATOMIC_ACQUIRE) == me;
}

int os_raw_pause(os_thread *t)
{
    if (!t->started || t->joined || is_self(t))
        return -1;
    if (R_FAILED(svcSetThreadActivity(t->th.handle, ThreadActivity_Paused)))
        return -1;
    ThreadContext ctx;
    while (R_FAILED(svcGetThreadContext3(&ctx, t->th.handle)))
        svcSleepThread(0);
    return 0;
}

void os_raw_resume(os_thread *t) { svcSetThreadActivity(t->th.handle, ThreadActivity_Runnable); }

/* the main thread: libnx's record of it carries its real handle (envGetMainThreadHandle) */
os_thread *os_raw_adopt_self(void)
{
    os_thread *t = (os_thread *)calloc(1, sizeof *t);
    if (!t)
        return NULL;
    t->th.handle = threadGetSelf()->handle;
    u64 id = 0;
    svcGetThreadId(&id, CUR_THREAD_HANDLE);
    t->tid = id;
    t->started = 1;
    return t;
}

/* ExitThread: leave the registry first (thread_entry's own unregister is not reached) */
void os_thread_exit(void)
{
    if (t_self)
        os_world_unregister(&t_self->slot);
    threadExit();
}
void os_cpu_boost(int fast_load) {
    appletSetCpuBoostMode(fast_load ? ApmCpuBoostMode_FastLoad : ApmCpuBoostMode_Normal);
}

void os_show_message(const char *caption, const char *text)
{
    char dlg[512], full[2048];
    snprintf(dlg, sizeof dlg, "%s\n%.*s", caption ? caption : "", (int)strcspn(text ? text : "", "\r\n"), text ? text : "");
    snprintf(full, sizeof full, "%s\n\n%s", caption ? caption : "", text ? text : "");
    ErrorApplicationConfig c;
    if (R_SUCCEEDED(errorApplicationCreate(&c, dlg, full)))
        errorApplicationShow(&c);
}

int os_display_docked(void) { return appletGetOperationMode() == AppletOperationMode_Console; }

void os_yield(void) { svcSleepThread(0); } /* 0 = yield to threads of the same priority */

void os_sleep_ns(uint64_t ns) { svcSleepThread(ns ? (s64)ns : 0); }

/* ---- time: the ARM generic timer (CNTPCT_EL0 / CNTFRQ_EL0, 19.2 MHz; CNTVCT_EL0 traps at EL0 on Horizon). QueryPerformanceCounter, __rdtsc
 * (shim/include/fnvnx_asm.h) and os_cycle_counter all read the same counter so calibrations line up. */
uint64_t os_ticks(void) { return armGetSystemTick(); }
uint64_t os_tick_freq(void) { return armGetSystemTickFreq(); }
uint64_t os_monotonic_ns(void) { return armTicksToNs(armGetSystemTick()); }
uint64_t os_cycle_counter(void) { return armGetSystemTick(); }

int64_t os_wall_time_us(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL); /* libnx: time service (timeInitialize in the app's init) */
    return (int64_t)tv.tv_sec * 1000000 + tv.tv_usec;
}


/* the zone of the console's time service (the user's setting): offset, daylight flag and abbreviation at an instant */
static int tzr_at(int64_t utc_s, int32_t *off_s, int *is_dst, char abbr[8])
{
    TimeCalendarTime cal;
    TimeCalendarAdditionalInfo info;
    if (R_FAILED(timeToCalendarTimeWithMyRule((u64)utc_s, &cal, &info)))
        return -1;
    *off_s = info.offset;
    *is_dst = info.DST != 0;
    memcpy(abbr, info.timezoneName, 8);
    abbr[7] = 0;
    return 0;
}

int32_t os_tz_offset_minutes(void)
{
    int32_t off;
    int dst;
    char n[8];
    return tzr_at((int64_t)(os_wall_time_us() / 1000000), &off, &dst, n) ? 0 : off / 60;
}
#include "../../platform/os/os_tzrule.inc"

/* ---- files: newlib POSIX on the fsdev devoptab */
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

/* newlib's off_t is as wide as the build's long (32 bits on ILP32): refuse an offset it would wrap rather than seek
 * to the wrong place (BSAs stay below 2 GB; a larger position is an error, not a modulo) */
static int off_fits(uint64_t off) { return (uint64_t)(off_t)off == off && (off_t)off >= 0; }

int os_file_open(const char *path, int flags)
{
    int o;
    if ((flags & OS_O_READ) && (flags & OS_O_WRITE)) o = O_RDWR;
    else if (flags & OS_O_WRITE) o = O_WRONLY;
    else o = O_RDONLY;
    if (flags & OS_O_CREATE) o |= O_CREAT;
    if (flags & OS_O_EXCL) o |= O_EXCL;
    if (flags & OS_O_TRUNC) o |= O_TRUNC;
    if (flags & OS_O_APPEND) o |= O_APPEND;
    int fd = open(path, o, 0666);
    return fd < 0 ? neg_errno() : fd;
}

int64_t os_file_read(int fd, void *b, size_t n) { ssize_t r = read(fd, b, n); return r < 0 ? neg_errno() : r; }
int64_t os_file_write(int fd, const void *b, size_t n) { ssize_t r = write(fd, b, n); return r < 0 ? neg_errno() : r; }

/* fsdev has no positional I/O: emulate under one lock (the position is restored afterwards). */
static os_lock_t g_pio_lock;

int64_t os_file_pread(int fd, void *b, size_t n, uint64_t off)
{
    if (!off_fits(off))
        return -OS_EINVAL;
    os_lock(&g_pio_lock);
    off_t cur = lseek(fd, 0, SEEK_CUR);
    ssize_t r = lseek(fd, (off_t)off, SEEK_SET) < 0 ? -1 : read(fd, b, n);
    int64_t res = r < 0 ? neg_errno() : r;
    lseek(fd, cur, SEEK_SET);
    os_unlock(&g_pio_lock);
    return res;
}

int64_t os_file_pwrite(int fd, const void *b, size_t n, uint64_t off)
{
    if (!off_fits(off))
        return -OS_EINVAL;
    os_lock(&g_pio_lock);
    off_t cur = lseek(fd, 0, SEEK_CUR);
    ssize_t r = lseek(fd, (off_t)off, SEEK_SET) < 0 ? -1 : write(fd, b, n);
    int64_t res = r < 0 ? neg_errno() : r;
    lseek(fd, cur, SEEK_SET);
    os_unlock(&g_pio_lock);
    return res;
}

int64_t os_file_seek(int fd, int64_t off, int whence)
{
    int w = whence == OS_SEEK_CUR ? SEEK_CUR : whence == OS_SEEK_END ? SEEK_END : SEEK_SET;
    if ((int64_t)(off_t)off != off)
        return -OS_EINVAL;
    off_t r = lseek(fd, (off_t)off, w);
    return r < 0 ? neg_errno() : (int64_t)r;
}

int os_file_truncate(int fd, uint64_t size)
{
    return !off_fits(size) ? -OS_EINVAL : ftruncate(fd, (off_t)size) ? neg_errno() : 0;
}
int os_file_sync(int fd) { return fsync(fd) ? neg_errno() : 0; }
int os_file_close(int fd) { return close(fd) ? neg_errno() : 0; }

static void fill_stat(const struct stat *s, os_stat_t *o)
{
    o->is_dir = S_ISDIR(s->st_mode);
    o->read_only = !(s->st_mode & S_IWUSR);
    o->size = (uint64_t)s->st_size;
    o->mtime_us = (int64_t)s->st_mtime * 1000000;
    o->atime_us = (int64_t)s->st_atime * 1000000;
    o->ctime_us = (int64_t)s->st_ctime * 1000000;
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


/* ---- virtual memory: reservations are carved from the alias region (first fit over a small table),
 * commit/decommit map/unmap physical memory there (pages come back zero-filled from the kernel). */
#define VM_MAX 512
static struct { uintptr_t base, size; } g_vm[VM_MAX];
static int g_vm_n;
static uintptr_t g_alias_base, g_alias_end;
static os_lock_t g_vm_lock;


static int vm_init(void)
{
    if (g_alias_end)
        return 0;
    u64 b = 0, s = 0;
    if (R_FAILED(svcGetInfo(&b, InfoType_AliasRegionAddress, CUR_PROCESS_HANDLE, 0)) ||
        R_FAILED(svcGetInfo(&s, InfoType_AliasRegionSize, CUR_PROCESS_HANDLE, 0)) || !s)
        return -1;
    if (b + s > 0x100000000ull)
        s = b < 0x100000000ull ? 0x100000000ull - b : 0; /* ILP32 pointers reach 4 GiB */
    g_alias_base = (uintptr_t)b;
    g_alias_end = (uintptr_t)(b + s);
    return s ? 0 : -1;
}

static int vm_overlaps(uintptr_t b, uintptr_t s)
{
    for (int i = 0; i < g_vm_n; i++)
        if (b < g_vm[i].base + g_vm[i].size && g_vm[i].base < b + s)
            return 1;
    return 0;
}

void *os_vm_reserve(void *hint, size_t size)
{
    size = (size + 0xFFFF) & ~(size_t)0xFFFF;
    void *res = NULL;
    os_lock(&g_vm_lock);
    if (vm_init() == 0 && g_vm_n < VM_MAX) {
        uintptr_t at = 0;
        if (hint) {
            uintptr_t h = (uintptr_t)hint;
            if (h >= g_alias_base && h + size <= g_alias_end && !vm_overlaps(h, size))
                at = h;
        } else {
            for (uintptr_t c = g_alias_base; c + size <= g_alias_end && c + size > c;) {
                int moved = 0;
                for (int i = 0; i < g_vm_n; i++)
                    if (c < g_vm[i].base + g_vm[i].size && g_vm[i].base < c + size) {
                        c = (g_vm[i].base + g_vm[i].size + 0xFFFF) & ~(uintptr_t)0xFFFF;
                        moved = 1;
                    }
                if (!moved) {
                    at = c;
                    break;
                }
            }
        }
        if (at) {
            g_vm[g_vm_n].base = at;
            g_vm[g_vm_n].size = size;
            g_vm_n++;
            res = (void *)at;
        }
    }
    os_unlock(&g_vm_lock);
    return res;
}

int os_vm_commit(void *addr, size_t size)
{
    return R_FAILED(svcMapPhysicalMemory(addr, (u64)size)) ? -OS_ENOMEM : 0;
}

int os_vm_decommit(void *addr, size_t size)
{
    return R_FAILED(svcUnmapPhysicalMemory(addr, (u64)size)) ? -OS_EINVAL : 0;
}

int os_vm_release(void *addr, size_t size)
{
    int r = -OS_EINVAL;
    os_lock(&g_vm_lock);
    for (int i = 0; i < g_vm_n; i++)
        if (g_vm[i].base == (uintptr_t)addr) {
            /* unmapping a range that is only partly mapped is allowed by the kernel */
            svcUnmapPhysicalMemory(addr, (u64)(size ? size : g_vm[i].size));
            g_vm[i] = g_vm[--g_vm_n];
            r = 0;
            break;
        }
    os_unlock(&g_vm_lock);
    return r;
}

/* committed pages are kernel-mapped physical memory: their permission is svcSetMemoryPermission's. The kernel
 * grants R and RW (and no W without R); execute needs a code mapping and is the kernel's answer (an error).
 * os_shm_* (file mappings) has no Horizon backend yet: svcCreateSharedMemory / svcMapSharedMemory mapped
 * into the alias region is the mechanism; reported to the orchestrator. */
int os_vm_protect(void *addr, size_t size, int os_prot)
{
    u32 perm = (os_prot & OS_VM_READ ? Perm_R : 0) | (os_prot & OS_VM_WRITE ? Perm_W : 0) | (os_prot & OS_VM_EXEC ? Perm_X : 0);
    return R_FAILED(svcSetMemoryPermission(addr, (u64)size, perm)) ? -OS_EINVAL : 0;
}

