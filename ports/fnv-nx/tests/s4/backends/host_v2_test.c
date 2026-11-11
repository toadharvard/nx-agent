/* host_v2_test.c - the FnvnxHostApi version 2 slots on libnx (toolchain/host/host_v2.inc, with host_ids.inc, host_vm.inc for the shared
 * results) executed under qemu-aarch64 with libnx's thread and address-arbiter calls stubbed and the file calls real (glibc stands in for
 * newlib: the same POSIX names). Checks the invalid-argument answers hostapi.h states: ids that name nothing, a thread that was never
 * started, a freed id, the table bound, address-arbiter results, directory ids and the zero capacity, -errno answers of the file slots.
 * Test names (hostapi.h table): thread_slots, wait_wake, dir_slots, fs_errors (and cpu_count). */
#include "kdouble.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/time.h>
#include "fnvnx/hostapi.h"

static void logf_(const char *fmt, ...) { (void)fmt; }
static int s_tz_rc;
Result timeToCalendarTimeWithMyRule(u64 ts, TimeCalendarTime *c, TimeCalendarAdditionalInfo *i)
{
    (void)ts; memset(c, 0, sizeof *c); memset(i, 0, sizeof *i);
    i->offset = -12345; i->DST = 1; memcpy(i->timezoneName, "ABC\0\0\0\0\0", 8);
    return s_tz_rc;
}
static Handle g_own_process = OWN_HANDLE;
struct __pthread_t;
int __real___syscall_thread_create(struct __pthread_t **t, void *(*fn)(void *), void *arg, void *stack, size_t size) { (void)t; (void)fn; (void)arg; (void)stack; (void)size; return 1; }
#include "../../../toolchain/host/placement.h"
static struct { int core3, boost_loading; } g_cfg = {0, 1};

/* ---- stubs */
static int s_started, s_closed, s_waited, s_prio_calls, s_mask_calls;
static Handle s_next_handle = 0x100, s_prio_handle, s_mask_handle;
static Result s_thread_create_rc, s_wait_rc, s_signal_rc;
static u64 s_wait_addr, s_wake_addr; static s32 s_wait_val, s_wake_count; static s64 s_wait_to;
Result threadCreate(Thread *t, ThreadFunc entry, void *arg, void *stack, size_t sz, int prio, int cpu)
{
    (void)entry; (void)arg; (void)stack; (void)sz; (void)prio; (void)cpu;
    if (s_thread_create_rc) return s_thread_create_rc;
    memset(t, 0, sizeof *t);
    t->handle = s_next_handle++;
    return 0;
}
Result threadStart(Thread *t) { (void)t; s_started++; return 0; }
Result threadClose(Thread *t) { (void)t; s_closed++; return 0; }
Result threadWaitForExit(Thread *t) { (void)t; s_waited++; return 0; }
void threadExit(void) { for (;;) {} }
Result svcSetThreadCoreMask(Handle h, s32 ideal, u32 mask) { (void)ideal; (void)mask; s_mask_handle = h; s_mask_calls++; return 0; }
Result svcGetThreadCoreMask(s32 *ideal, u64 *mask, Handle h) { (void)h; *ideal = 0; *mask = 7; return 0; }
Result svcSetThreadPriority(Handle h, u32 p) { (void)p; s_prio_handle = h; s_prio_calls++; return 0; }
void svcSleepThread(s64 ns) { (void)ns; }
Result svcWaitForAddress(void *a, u32 t, s64 v, s64 to) { (void)t; s_wait_addr = (u64)(uintptr_t)a; s_wait_val = (s32)v; s_wait_to = to; return s_wait_rc; }
Result svcSignalToAddress(void *a, u32 t, s32 v, s32 c) { (void)t; (void)v; s_wake_addr = (u64)(uintptr_t)a; s_wake_count = c; return s_signal_rc; }
Result svcGetInfo(u64 *o, u32 t, Handle h, u64 s) { (void)t; (void)h; (void)s; *o = 0; return 0; }
u64 randomGet64(void) { return 4; }
#include "../../../toolchain/host/host_ids.inc"
#include "../../../toolchain/host/host_vm.inc"
#undef free
#undef memalign
#include "../../../toolchain/host/host_tz.inc"
#include "../../../toolchain/host/host_v2.inc"

int main(void)
{
    kdouble_init();
    /* ---- tz_at: tz_offset_minutes is off_s / 60 of TZ_AT now (C division: -12345 / 60 = -205); 0 when TZ_AT does not answer 0 */
    T((s64)h_tz_offset_minutes() == -205);
    s_tz_rc = MAKERESULT(Module_Libnx, LibnxError_NotFound);
    T(h_tz_offset_minutes() == 0);
    s_tz_rc = 0;
    /* ---- thread_slots: create hands out a table handle, never the HostThread's address */
    u64 id = h_thread_create(0x1000, 0, 0x10000, 0, (u64)-1);
    T(id && (id & 0xFFFF) && !(id >> 63) && host_id_get(HOST_ID_THREAD, id) != NULL && (u64)(uintptr_t)host_id_get(HOST_ID_THREAD, id) != id);
    T(h_thread_create(0x1000, 0, 0x10000, 0, (u64)-1) != id);
    u64 bad[] = {0x12345678ull, ~0ull, 1, id ^ (1ull << 20), id ^ 0x30000};   /* made up; wrong generation */
    for (unsigned i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        T(h_thread_start(bad[i]) == FNVNX_HE_NOTFOUND && h_thread_join(bad[i]) == FNVNX_HE_NOTFOUND && h_thread_free(bad[i]) == FNVNX_HE_NOTFOUND);
        T(h_thread_set_priority(bad[i], 0) == FNVNX_HE_NOTFOUND && h_thread_set_cpu(bad[i], 1) == FNVNX_HE_NOTFOUND);
    }
    T(h_thread_start(0) == FNVNX_HE_NOTFOUND && h_thread_join(0) == FNVNX_HE_NOTFOUND && h_thread_free(0) == FNVNX_HE_NOTFOUND);   /* 0 is no id for these */
    T(h_thread_join(id) == FNVNX_HE_BADINPUT && s_waited == 0);                                  /* never started: it would never end */
    T(h_thread_set_priority(id, 0) == 0 && s_prio_handle == 0x100 && h_thread_set_priority(0, 0) == 0 && s_prio_handle == CUR_THREAD_HANDLE);
    T(h_thread_set_cpu(id, 1) == 0 && s_mask_handle == 0x100 && h_thread_set_cpu(0, ~0ull) == 0);   /* a negative spec: nothing to do, 0 */
    T(h_thread_start(id) == 0 && s_started == 1);
    T(h_thread_start(id) == FNVNX_HE_BADINPUT && s_started == 1 && (((HostThread *)host_id_get(HOST_ID_THREAD, id))->state & HT_STARTED));   /* already started: refused, still started */
    T(h_thread_join(id) == 0 && s_waited == 1);
    T(h_thread_free(id) == 0 && s_closed == 0);                       /* started, not finished: detached, its own end queues it for the reaper */
    T(h_thread_free(id) == FNVNX_HE_NOTFOUND && h_thread_join(id) == FNVNX_HE_NOTFOUND && h_thread_start(id) == FNVNX_HE_NOTFOUND);   /* the id died */
    {   /* never started: freed at once */
        u64 u = h_thread_create(0x1000, 0, 0x10000, 0, (u64)-1);
        int c = s_closed;
        T(u && h_thread_free(u) == 0 && s_closed == c + 1 && h_thread_free(u) == FNVNX_HE_NOTFOUND);
    }
    s_thread_create_rc = 7;
    T(h_thread_create(0x1000, 0, 0x10000, 0, (u64)-1) == 0);          /* the kernel refused: 0 */
    s_thread_create_rc = 0;
    {   /* the id table is bounded: the thread that cannot get an id is closed again and not leaked */
        static u64 ids[FNVNX_ID_MAX];
        int n = 0, closed = s_closed;
        u64 x;
        while ((x = h_thread_create(0x1000, 0, 0x10000, 0, (u64)-1)) != 0 && n < FNVNX_ID_MAX) ids[n++] = x;
        T(n >= 1000 && x == 0 && s_closed == closed + 1);
        for (int i = 0; i < n; i++) h_thread_free(ids[i]);
        T(h_thread_create(0x1000, 0, 0x10000, 0, (u64)-1) != 0);
    }

    /* ---- wait_wake: 0 woken, 1 timed out, 2 value differed; any other kernel result unchanged; wake returns the kernel's result */
    s_wait_rc = 0;
    T(h_wait_on_address(0x1000, 5, 100) == 0 && s_wait_addr == 0x1000 && s_wait_val == 5 && s_wait_to == 100);
    T(h_wait_on_address(0x1000, 5, ~0ull) == 0 && s_wait_to == -1);                      /* above INT64_MAX: forever */
    s_wait_rc = KERNELRESULT(TimedOut);     T(h_wait_on_address(0x1000, 5, 1) == 1);
    s_wait_rc = KERNELRESULT(InvalidState); T(h_wait_on_address(0x1000, 5, 1) == 2);   /* the value differed */
    s_wait_rc = FNVNX_HE_ADDR;                     T(h_wait_on_address(0x1001, 5, 1) == FNVNX_HE_ADDR);   /* misaligned: the kernel's result, not "differed" */
    s_wait_rc = FNVNX_HE_RANGE;                    T(h_wait_on_address(0x10, 5, 1) == FNVNX_HE_RANGE);
    T(h_wake_address(0x2000, 3) == 0 && s_wake_count == 3 && h_wake_address(0x2000, ~0ull) == 0 && s_wake_count == -1 && h_wake_address(0x2000, 0x7FFFFFFF) == 0 && s_wake_count == 0x7FFFFFFF);
    s_signal_rc = FNVNX_HE_ADDR;
    T(h_wake_address(0x2001, 1) == FNVNX_HE_ADDR);
    s_signal_rc = 0;

    /* ---- dir_slots: ids are handles; an id that names no open directory reads as the end; cap 0 consumes nothing; a long name is cut */
    char dir[] = "/tmp/hostv2dirXXXXXX";
    T(mkdtemp(dir) != NULL);
    char p[512];
    snprintf(p, sizeof p, "%s/abcdefghij", dir);
    int fd = open(p, O_CREAT | O_WRONLY, 0600);
    close(fd);
    u64 d = h_fs_dir_open((u64)(uintptr_t)dir);
    T(d && (d & 0xFFFF) && !(d >> 63) && d != (u64)(uintptr_t)host_id_get(HOST_ID_DIR, d));
    char buf[64];
    T(h_fs_dir_next(d, (u64)(uintptr_t)buf, 0) == 0);                                        /* cap 0: the end, nothing consumed */
    memset(buf, 'x', sizeof buf);
    T(h_fs_dir_next(d, (u64)(uintptr_t)buf, 5) == 4 && !strcmp(buf, "abcd"));                /* cut to cap - 1 */
    T(h_fs_dir_next(d, (u64)(uintptr_t)buf, sizeof buf) == 0);                                /* the end */
    T(h_fs_dir_next(0x12345678, (u64)(uintptr_t)buf, sizeof buf) == 0 && h_fs_dir_next(~0ull, (u64)(uintptr_t)buf, 8) == 0);
    T(h_fs_dir_next((u64)(uintptr_t)host_id_get(HOST_ID_DIR, d), (u64)(uintptr_t)buf, sizeof buf) == 0);   /* the DIR pointer is no id */
    T(h_fs_dir_close(d) == 0 && h_fs_dir_next(d, (u64)(uintptr_t)buf, 8) == 0 && h_fs_dir_close(d) == 0);   /* closed: an unknown id */
    T(h_fs_dir_close(0) == 0 && h_fs_dir_close(~0ull) == 0);
    T(h_fs_dir_open((u64)(uintptr_t)"/nonexistent/dir") == 0 && h_fs_dir_open((u64)(uintptr_t)p) == 0);   /* no such path, not a directory */
    {
        static u64 ds[FNVNX_ID_MAX];
        int n = 0;
        u64 x;
        while ((x = h_fs_dir_open((u64)(uintptr_t)dir)) != 0 && n < FNVNX_ID_MAX) ds[n++] = x;
        T(n >= 1000 && x == 0);                                                              /* the id table is full: 0, the DIR is closed again */
        for (int i = 0; i < n; i++) h_fs_dir_close(ds[i]);
        d = h_fs_dir_open((u64)(uintptr_t)dir);
        T(d != 0);
        h_fs_dir_close(d);
    }

    /* cpu_count: the cores placement lets a thread use */
    T(h_cpu_count() == 3);
    g_cfg.core3 = 1;
    T(h_cpu_count() == 4);
    g_cfg.core3 = 0;

    /* ---- fs_errors: the file slots answer -errno as the header says */
    u64 nof = h_fs_open((u64)(uintptr_t)"/nonexistent/file", FNVNX_O_READ);
    T(nof == (u64)-ENOENT);
    T(h_fs_close(9999) == (u64)-EBADF && h_fs_read(9999, (u64)(uintptr_t)buf, 4) == (u64)-EBADF && h_fs_seek(9999, 0, 0) == (u64)-EBADF);
    T(h_fs_fstat(9999, (u64)(uintptr_t)buf) == (u64)-EBADF && h_fs_truncate(9999, 0) == (u64)-EBADF && h_fs_sync(9999) == (u64)-EBADF);
    T(h_fs_rmdir((u64)(uintptr_t)"/nonexistent/x") == (u64)-ENOENT && h_fs_remove((u64)(uintptr_t)"/nonexistent/x") == (u64)-ENOENT);
    remove(p);
    rmdir(dir);
    printf("host_v2: %s\n", g_fail ? "FAIL" : "ok");
    return g_fail != 0;
}
