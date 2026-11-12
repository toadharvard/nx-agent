/* host_v2.inc - FnvnxHostApi version 2 on libnx: threads, address wait/wake, time, files.
 * Included by host.c. Every parameter and result is u64; negative results are -errno. */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/time.h>
#include <unistd.h>

/* ---- threads (HostThread: host_ids.inc; a thread id is a table handle) */
/* Detached threads. Win32 code closes a thread handle while the thread runs (CreateThread + CloseHandle), and a
 * thread often drops the last reference itself (platform/win32 thread_trampoline), so thread_free can come before
 * the end or from the thread itself. libnx's threadClose refuses a running thread (tls_array set: BadInput) and
 * unmaps the stack of an ended one, so freeing the record there would leave threadExit writing freed memory (and
 * leak the stack and handle). A thread whose record was freed early queues itself here as its last act; the next
 * thread_create/thread_free waits for it (it is inside threadExit) and closes it. tests/s15/mtqemu checks the
 * protocol on Linux (linhost.c does the same with its slots). */
static HostThread *g_zombies;
static Mutex g_zombie_lock;
static void reap_zombies(void) {
    mutexLock(&g_zombie_lock);
    HostThread *z = g_zombies;
    g_zombies = NULL;
    mutexUnlock(&g_zombie_lock);
    while (z) {
        HostThread *n = z->next_zombie;
        threadWaitForExit(&z->t);
        threadClose(&z->t);
        free(z);
        z = n;
    }
}

static void host_thread_main(void *p) {
    HostThread *h = (HostThread *)p;
    ((u64 (*)(u64))h->entry)(h->arg);
    if (__atomic_fetch_or(&h->state, HT_FINISHED, __ATOMIC_ACQ_REL) & HT_DETACHED) {
        mutexLock(&g_zombie_lock);
        h->next_zombie = g_zombies;
        g_zombies = h;
        mutexUnlock(&g_zombie_lock);
    }
}

#define win32_to_horizon_prio fnvnx_win32_to_horizon_prio

/* Thread placement (placement.h): libnx's threadCreate(..., -2) would put every thread on the process's ideal core 0
 * with a one-core mask; instead each thread gets its role's ideal core, mask and priority. */
static unsigned g_thread_seq;
static void place_log(const char *what, Handle th, int role, int ideal, uint32_t mask, int prio, Result rc) {
    logf_("fnvnx host: %s thread %#x: role %s, core %d, mask %#x, priority %#x%s\n", what, th,
          role >= 0 ? fnvnx_placement[role].role : "explicit", ideal, mask, prio, R_FAILED(rc) ? " (FAILED)" : "");
}

static u64 h_thread_create(u64 entry, u64 arg, u64 stack, u64 prio, u64 cpu) {
    reap_zombies();
    HostThread *h = calloc(1, sizeof *h);
    if (!h) return 0;
    h->entry = entry;
    h->arg = arg;
    stack = (stack + 0xFFF) & ~0xFFFull;
    int ideal, hprio;
    uint32_t mask;
    int role = fnvnx_place_ex(cpu, __atomic_fetch_add(&g_thread_seq, 1, __ATOMIC_RELAXED), (s64)prio, g_cfg.core3,
                              &ideal, &mask, &hprio);
    if (R_FAILED(threadCreate(&h->t, host_thread_main, h, NULL, stack ? stack : 0x40000, hprio, ideal))) {
        free(h);
        return 0;
    }
    u64 id = host_id_new(HOST_ID_THREAD, h);
    if (!id) {   /* id table full: the thread was never started, it is closed again */
        threadClose(&h->t);
        free(h);
        return 0;
    }
    Result rc = svcSetThreadCoreMask(h->t.handle, ideal, mask);   /* before it starts */
    place_log("guest", h->t.handle, role, ideal, mask, hprio, rc);
    return id;
}

/* Host-side pthreads (libnx newlib: priority 0x3B, the process's ideal core, all cores) take the render-worker
 * row: in the Nine host the long-lived one is Gallium Nine's CSMT worker. Active when the host links with
 * -Wl,--wrap=__syscall_thread_create (toolchain/package/build.sh and render/nine/switch_host.sh). */
struct __pthread_t;
int __real___syscall_thread_create(struct __pthread_t **t, void *(*fn)(void *), void *arg, void *stack, size_t size);
int __wrap___syscall_thread_create(struct __pthread_t **t, void *(*fn)(void *), void *arg, void *stack, size_t size) {
    int rc = __real___syscall_thread_create(t, fn, arg, stack, size);
    if (rc == 0 && *t) {
        Handle th = ((Thread *)*t)->handle;   /* libnx's struct __pthread_t starts with its Thread */
        int ideal, prio;
        uint32_t mask;
        int role = fnvnx_place(FNVNX_THREAD_ROLE(FNVNX_ROLE_RENDER_WORKER), 0, 0, &ideal, &mask, &prio);
        Result r = svcSetThreadCoreMask(th, ideal, mask);
        if (R_SUCCEEDED(r)) r = svcSetThreadPriority(th, prio);
        place_log("host pthread", th, role, ideal, mask, prio, r);
    }
    return rc;
}
static u64 h_thread_start(u64 id) {
    HostThread *h = host_id_get(HOST_ID_THREAD, id);
    if (!h) return FNVNX_HE_NOTFOUND;
    __atomic_fetch_or(&h->state, HT_STARTED, __ATOMIC_RELEASE);   /* before it runs: its end sees the bit */
    Result rc = threadStart(&h->t);
    if (R_FAILED(rc)) __atomic_fetch_and(&h->state, ~HT_STARTED, __ATOMIC_RELEASE);
    return rc;
}
static u64 h_thread_join(u64 id) {
    HostThread *h = host_id_get(HOST_ID_THREAD, id);
    if (!h) return FNVNX_HE_NOTFOUND;
    if (!(__atomic_load_n(&h->state, __ATOMIC_ACQUIRE) & HT_STARTED)) return FNVNX_HE_BADINPUT;   /* never started: it would wait forever */
    return threadWaitForExit(&h->t);
}
static u64 h_thread_free(u64 id) {   /* after join, or a detach: also of a running thread, also by itself; the id dies at once */
    HostThread *h = host_id_del(HOST_ID_THREAD, id);
    if (!h) return FNVNX_HE_NOTFOUND;
    reap_zombies();
    u32 old = __atomic_fetch_or(&h->state, HT_DETACHED, __ATOMIC_ACQ_REL);
    if ((old & HT_STARTED) && !(old & HT_FINISHED))
        return 0;   /* still running: host_thread_main queues it for reap_zombies */
    if (old & HT_STARTED)
        threadWaitForExit(&h->t);   /* finished: it is at most inside threadExit */
    threadClose(&h->t);
    free(h);
    return 0;
}
static u64 h_thread_exit(void) { threadExit(); }
static u64 h_thread_yield(void) { svcSleepThread(0); return 0; }
static u64 h_thread_set_priority(u64 id, u64 prio) {   /* id: any guest thread, also one not started yet */
    HostThread *h = id ? host_id_get(HOST_ID_THREAD, id) : NULL;
    if (id && !h) return FNVNX_HE_NOTFOUND;
    return svcSetThreadPriority(id ? h->t.handle : CUR_THREAD_HANDLE, win32_to_horizon_prio((s64)prio));
}
/* L5 ADDITION (THREAD_SELF, hostapi.h "L5 ADDITIONS"): a record for the calling thread, which thread_create did not make (the main
 * thread: libnx's threadGetSelf carries its real handle), under a table id like thread_create's. Never started by thread_start, never
 * freed by the guest: only FNVNX_PLAT_THREAD_ACTIVITY uses its id. 0 when the id table is full. */
static u64 h_thread_self(void) {
    HostThread *h = calloc(1, sizeof *h);
    if (!h) return 0;
    h->t.handle = threadGetSelf()->handle;
    h->state = HT_STARTED;
    u64 id = host_id_new(HOST_ID_THREAD, h);
    if (!id) free(h);
    return id;
}
static u64 h_thread_set_cpu(u64 id, u64 spec) {
    HostThread *h = id ? host_id_get(HOST_ID_THREAD, id) : NULL;
    if (id && !h) return FNVNX_HE_NOTFOUND;
    Handle th = id ? h->t.handle : CUR_THREAD_HANDLE;
    if ((spec >> 40) & 0x7FFFFFull) return 0;   /* -1 and other negative values: nothing to do */
    s32 cur_ideal = 0;
    u64 cur_mask = FNVNX_APP_CORES;
    if (spec >> 63) svcGetThreadCoreMask(&cur_ideal, &cur_mask, th);
    int ideal;
    uint32_t mask;
    fnvnx_cpu_spec(spec, cur_ideal, (uint32_t)cur_mask, &ideal, &mask);
    return svcSetThreadCoreMask(th, ideal, mask);
}

/* ---- address wait / wake */
static u64 h_wait_on_address(u64 addr, u64 expected, u64 timeout_ns) {
    s64 to = timeout_ns > (u64)INT64_MAX ? -1 : (s64)timeout_ns;
    Result rc = svcWaitForAddress((void *)addr, ArbitrationType_WaitIfEqual, (s32)(u32)expected, to);
    if (R_SUCCEEDED(rc)) return 0;
    if (R_VALUE(rc) == KERNELRESULT(TimedOut)) return 1;
    if (R_VALUE(rc) == KERNELRESULT(InvalidState)) return 2;   /* value differed */
    return rc;   /* any other kernel result unchanged, e.g. 0xCC01 addr not 4-aligned, 0xD401 not readable guest memory */
}
static u64 h_wake_address(u64 addr, u64 count) {
    return svcSignalToAddress((void *)addr, SignalType_Signal, 0, count > 0x7FFFFFFF ? -1 : (s32)count);
}

/* ---- time, misc */
static u64 h_wall_time_us(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (u64)((s64)tv.tv_sec * 1000000 + tv.tv_usec);
}
static u64 h_random64(void) { return randomGet64(); }
static u64 h_memory_info(u64 out) {
    FnvnxMemInfo *m = (FnvnxMemInfo *)out;
    u64 total = 0, used = 0;
    svcGetInfo(&total, InfoType_TotalMemorySize, CUR_PROCESS_HANDLE, 0);
    svcGetInfo(&used, InfoType_UsedMemorySize, CUR_PROCESS_HANDLE, 0);
    m->total_phys = total;
    m->avail_phys = total > used ? total - used : 0;
    return 0;
}
static u64 h_cpu_count(void) {   /* the cores placement.h lets a guest thread run on: application cores 0-2, plus core 3 with the opt-in */
    return (u64)__builtin_popcount(g_cfg.core3 ? FNVNX_APP_CORES_CORE3 : FNVNX_APP_CORES);
}

/* ---- files (newlib POSIX over libnx fsdev; paths like "sdmc:/...") */
#define NEG_ERRNO ((u64)(s64)-errno)
static u64 h_fs_open(u64 path, u64 f) {
    int fl = (f & FNVNX_O_READ) && (f & FNVNX_O_WRITE) ? O_RDWR : (f & FNVNX_O_WRITE) ? O_WRONLY : O_RDONLY;
    if (f & FNVNX_O_CREATE) fl |= O_CREAT;
    if (f & FNVNX_O_EXCL) fl |= O_EXCL;
    if (f & FNVNX_O_TRUNC) fl |= O_TRUNC;
    if (f & FNVNX_O_APPEND) fl |= O_APPEND;
    int fd = open((const char *)path, fl, 0666);
    return fd < 0 ? NEG_ERRNO : (u64)fd;
}
static u64 h_fs_read(u64 fd, u64 buf, u64 n) { ssize_t r = read((int)fd, (void *)buf, n); return r < 0 ? NEG_ERRNO : (u64)r; }
static u64 h_fs_write(u64 fd, u64 buf, u64 n) { ssize_t r = write((int)fd, (const void *)buf, n); return r < 0 ? NEG_ERRNO : (u64)r; }
static u64 h_fs_seek(u64 fd, u64 off, u64 whence) {
    off_t r = lseek((int)fd, (off_t)(s64)off, (int)whence);
    return r < 0 ? NEG_ERRNO : (u64)r;
}
static u64 h_fs_pread(u64 fd, u64 buf, u64 n, u64 off) {   /* newlib devoptab has no pread: seek + read */
    off_t cur = lseek((int)fd, 0, SEEK_CUR);
    if (cur < 0 || lseek((int)fd, (off_t)off, SEEK_SET) < 0) return NEG_ERRNO;
    ssize_t r = read((int)fd, (void *)buf, n);
    u64 res = r < 0 ? NEG_ERRNO : (u64)r;
    lseek((int)fd, cur, SEEK_SET);
    return res;
}
static u64 h_fs_pwrite(u64 fd, u64 buf, u64 n, u64 off) {
    off_t cur = lseek((int)fd, 0, SEEK_CUR);
    if (cur < 0 || lseek((int)fd, (off_t)off, SEEK_SET) < 0) return NEG_ERRNO;
    ssize_t r = write((int)fd, (const void *)buf, n);
    u64 res = r < 0 ? NEG_ERRNO : (u64)r;
    lseek((int)fd, cur, SEEK_SET);
    return res;
}
static u64 h_fs_truncate(u64 fd, u64 size) { return ftruncate((int)fd, (off_t)size) < 0 ? NEG_ERRNO : 0; }
static u64 h_fs_sync(u64 fd) { return fsync((int)fd) < 0 ? NEG_ERRNO : 0; }
static void fill_stat(const struct stat *st, FnvnxStat *o) {
    o->is_dir = S_ISDIR(st->st_mode);
    o->read_only = !(st->st_mode & 0200);
    o->size = (u64)st->st_size;
    o->mtime_us = (s64)st->st_mtime * 1000000;
    o->atime_us = (s64)st->st_atime * 1000000;
    o->ctime_us = (s64)st->st_ctime * 1000000;
}
static u64 h_fs_fstat(u64 fd, u64 out) {
    struct stat st;
    if (fstat((int)fd, &st) < 0) return NEG_ERRNO;
    fill_stat(&st, (FnvnxStat *)out);
    return 0;
}
static u64 h_fs_close(u64 fd) { return close((int)fd) < 0 ? NEG_ERRNO : 0; }
static u64 h_fs_stat(u64 path, u64 out) {
    struct stat st;
    if (stat((const char *)path, &st) < 0) return NEG_ERRNO;
    fill_stat(&st, (FnvnxStat *)out);
    return 0;
}
static u64 h_fs_mkdir(u64 path) { return mkdir((const char *)path, 0777) < 0 ? NEG_ERRNO : 0; }
static u64 h_fs_rmdir(u64 path) { return rmdir((const char *)path) < 0 ? NEG_ERRNO : 0; }
static u64 h_fs_remove(u64 path) { return unlink((const char *)path) < 0 ? NEG_ERRNO : 0; }
static u64 h_fs_rename(u64 a, u64 b) { return rename((const char *)a, (const char *)b) < 0 ? NEG_ERRNO : 0; }
static u64 h_fs_dir_open(u64 path) {
    DIR *d = opendir((const char *)path);
    u64 id = d ? host_id_new(HOST_ID_DIR, d) : 0;
    if (d && !id) closedir(d);   /* id table full */
    return id;
}
static u64 h_fs_dir_next(u64 id, u64 buf, u64 cap) {
    DIR *d = host_id_get(HOST_ID_DIR, id);
    struct dirent *e;
    if (!d || !cap) return 0;   /* not a directory id, or no room for even the NUL: the end, nothing consumed */
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        size_t n = strlen(e->d_name);
        if (n + 1 > cap) n = cap - 1;
        memcpy((char *)buf, e->d_name, n);
        ((char *)buf)[n] = 0;
        return n;
    }
    return 0;
}
static u64 h_fs_dir_close(u64 id) {
    DIR *d = host_id_del(HOST_ID_DIR, id);
    if (d) closedir(d);   /* a made-up or already closed id: nothing to close */
    return 0;
}

#define FNVNX_HOSTAPI_V2_SLOTS                                                                             \
    .thread_create = (u64)h_thread_create, .thread_start = (u64)h_thread_start,                          \
    .thread_join = (u64)h_thread_join, .thread_free = (u64)h_thread_free, .thread_exit = (u64)h_thread_exit, \
    .thread_yield = (u64)h_thread_yield, .thread_set_priority = (u64)h_thread_set_priority,              \
    .thread_set_cpu = (u64)h_thread_set_cpu, .wait_on_address = (u64)h_wait_on_address,                  \
    .wake_address = (u64)h_wake_address, .wall_time_us = (u64)h_wall_time_us,                            \
    .random64 = (u64)h_random64,                          \
    .memory_info = (u64)h_memory_info, .cpu_count = (u64)h_cpu_count, .fs_open = (u64)h_fs_open,          \
    .fs_read = (u64)h_fs_read, .fs_write = (u64)h_fs_write, .fs_pread = (u64)h_fs_pread,                 \
    .fs_pwrite = (u64)h_fs_pwrite, .fs_seek = (u64)h_fs_seek, .fs_truncate = (u64)h_fs_truncate,          \
    .fs_sync = (u64)h_fs_sync, .fs_fstat = (u64)h_fs_fstat, .fs_close = (u64)h_fs_close,                  \
    .fs_stat = (u64)h_fs_stat, .fs_mkdir = (u64)h_fs_mkdir, .fs_rmdir = (u64)h_fs_rmdir,                  \
    .fs_remove = (u64)h_fs_remove, .fs_rename = (u64)h_fs_rename, .fs_dir_open = (u64)h_fs_dir_open,      \
    .fs_dir_next = (u64)h_fs_dir_next, .fs_dir_close = (u64)h_fs_dir_close,
