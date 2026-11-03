/* host_v2.inc - FnvnxHostApi version 2 on libnx: threads, address wait/wake, time, files.
 * Included by host.c. Every parameter and result is u64; negative results are -errno. */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/time.h>
#include <unistd.h>

/* ---- threads */
typedef struct HostThread {
    Thread t;
    u64 entry, arg;
} HostThread;

static void host_thread_main(void *p) {
    HostThread *h = (HostThread *)p;
    ((u64 (*)(u64))h->entry)(h->arg);
}

static int win32_to_horizon_prio(s64 prio) {   /* 0x2C is libnx's default; Win32 higher = more urgent */
    s64 p = 0x2C - prio;
    return (int)(p < 0x1C ? 0x1C : p > 0x3B ? 0x3B : p);
}

static u64 h_thread_create(u64 entry, u64 arg, u64 stack, u64 prio, u64 cpu) {
    HostThread *h = calloc(1, sizeof *h);
    if (!h) return 0;
    h->entry = entry;
    h->arg = arg;
    stack = (stack + 0xFFF) & ~0xFFFull;
    if (R_FAILED(threadCreate(&h->t, host_thread_main, h, NULL, stack ? stack : 0x40000,
                              win32_to_horizon_prio((s64)prio), (s64)cpu < 0 ? -2 : (int)cpu))) {
        free(h);
        return 0;
    }
    return (u64)h;
}
static u64 h_thread_start(u64 id) { return threadStart(&((HostThread *)id)->t); }
static u64 h_thread_join(u64 id) { return threadWaitForExit(&((HostThread *)id)->t); }
static u64 h_thread_free(u64 id) {
    HostThread *h = (HostThread *)id;
    threadClose(&h->t);   /* a still-running detached thread keeps running; its record leaks */
    free(h);
    return 0;
}
static u64 h_thread_exit(void) { threadExit(); }
static u64 h_thread_yield(void) { svcSleepThread(0); return 0; }
static u64 h_thread_set_priority(u64 id, u64 prio) {
    Handle th = id ? ((HostThread *)id)->t.handle : CUR_THREAD_HANDLE;
    return svcSetThreadPriority(th, win32_to_horizon_prio((s64)prio));
}
static u64 h_thread_set_cpu(u64 id, u64 cpu) {
    Handle th = id ? ((HostThread *)id)->t.handle : CUR_THREAD_HANDLE;
    return (s64)cpu < 0 ? 0 : svcSetThreadCoreMask(th, (s32)cpu, 1u << cpu);
}

/* ---- address wait / wake */
static u64 h_wait_on_address(u64 addr, u64 expected, u64 timeout_ns) {
    s64 to = timeout_ns > (u64)INT64_MAX ? -1 : (s64)timeout_ns;
    Result rc = svcWaitForAddress((void *)addr, ArbitrationType_WaitIfEqual, (s32)(u32)expected, to);
    if (R_SUCCEEDED(rc)) return 0;
    if (R_VALUE(rc) == KERNELRESULT(TimedOut)) return 1;
    return 2;   /* InvalidState: value differed */
}
static u64 h_wake_address(u64 addr, u64 count) {
    svcSignalToAddress((void *)addr, SignalType_Signal, 0, count > 0x7FFFFFFF ? -1 : (s32)count);
    return 0;
}

/* ---- time, misc */
static u64 h_wall_time_us(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (u64)((s64)tv.tv_sec * 1000000 + tv.tv_usec);
}
static u64 h_tz_offset_minutes(void) { return 0; }   /* TODO: time:u LocationName / CalendarAdditionalInfo */
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
static u64 h_cpu_count(void) { return 3; }   /* application cores 0-2 */

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
static u64 h_fs_dir_open(u64 path) { return (u64)opendir((const char *)path); }
static u64 h_fs_dir_next(u64 d, u64 buf, u64 cap) {
    struct dirent *e;
    while ((e = readdir((DIR *)d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        size_t n = strlen(e->d_name);
        if (n + 1 > cap) n = cap - 1;
        memcpy((char *)buf, e->d_name, n);
        ((char *)buf)[n] = 0;
        return n;
    }
    return 0;
}
static u64 h_fs_dir_close(u64 d) { closedir((DIR *)d); return 0; }

#define FNVNX_HOSTAPI_V2_SLOTS                                                                             \
    .thread_create = (u64)h_thread_create, .thread_start = (u64)h_thread_start,                          \
    .thread_join = (u64)h_thread_join, .thread_free = (u64)h_thread_free, .thread_exit = (u64)h_thread_exit, \
    .thread_yield = (u64)h_thread_yield, .thread_set_priority = (u64)h_thread_set_priority,              \
    .thread_set_cpu = (u64)h_thread_set_cpu, .wait_on_address = (u64)h_wait_on_address,                  \
    .wake_address = (u64)h_wake_address, .wall_time_us = (u64)h_wall_time_us,                            \
    .tz_offset_minutes = (u64)h_tz_offset_minutes, .random64 = (u64)h_random64,                          \
    .memory_info = (u64)h_memory_info, .cpu_count = (u64)h_cpu_count, .fs_open = (u64)h_fs_open,          \
    .fs_read = (u64)h_fs_read, .fs_write = (u64)h_fs_write, .fs_pread = (u64)h_fs_pread,                 \
    .fs_pwrite = (u64)h_fs_pwrite, .fs_seek = (u64)h_fs_seek, .fs_truncate = (u64)h_fs_truncate,          \
    .fs_sync = (u64)h_fs_sync, .fs_fstat = (u64)h_fs_fstat, .fs_close = (u64)h_fs_close,                  \
    .fs_stat = (u64)h_fs_stat, .fs_mkdir = (u64)h_fs_mkdir, .fs_rmdir = (u64)h_fs_rmdir,                  \
    .fs_remove = (u64)h_fs_remove, .fs_rename = (u64)h_fs_rename, .fs_dir_open = (u64)h_fs_dir_open,      \
    .fs_dir_next = (u64)h_fs_dir_next, .fs_dir_close = (u64)h_fs_dir_close,
