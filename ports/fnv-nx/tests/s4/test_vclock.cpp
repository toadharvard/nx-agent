/* test_vclock.cpp - the virtual clocks of the verification mode (definition VC1, platform/include/fnvnx_clock.h,
 * platform/clock/clock_virtual.cpp). Compiled into both test binaries: every case returns at once unless the linked clock says
 * "virtual", so the default binary (the real clock) runs them as no-ops and `make -C tests/s4 vclock` runs them for real.
 *
 * Expected values are computed by a model of VC1 inside the test (a counter T in nanoseconds advanced by the rules of the
 * definition), never read back from the platform. */
#include "s4_test.h"
#include "../../platform/win32/w32.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <time.h>
#include <unistd.h>

extern "C" void *fnvnx_win32_lookup_export(const char *dll, const char *name);
extern "C" int fnvnx_post_thread_message(DWORD tid, UINT m, WPARAM w, LPARAM l);
extern "C" DWORD GetCurrentThreadId(void);

namespace {
const uint64_t Q = 100;                          /* ns */
const uint64_t EPOCH = 129383136000000000ull;    /* FILETIME of 2011-01-01T00:00:00Z */

bool virt() { return !strcmp(clk_name(), "virtual"); }
uint64_t now_ns() { return clk_peek_ns(); }     /* this thread's t, no advance */
uint64_t ft_of(const FILETIME &f) { return ((uint64_t)(uint32_t)f.dwHighDateTime << 32) | (uint32_t)f.dwLowDateTime; }
double real_s()
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}
char *(*strdate_fn())(char *) { return (char *(*)(char *))fnvnx_win32_lookup_export("msvcrt.dll", "_strdate"); }
char *(*strtime_fn())(char *) { return (char *(*)(char *))fnvnx_win32_lookup_export("msvcrt.dll", "_strtime"); }

/* one observation log: every value a scenario reads, as text */
struct Log {
    std::string s;
    void add(const char *what, uint64_t v) { char b[96]; snprintf(b, sizeof b, "%s=%llu\n", what, (unsigned long long)v); s += b; }
    void add(const char *what, const char *v) { s += what; s += "="; s += v; s += "\n"; }
};
}   // namespace

/* ---- one thread: a mixed sequence of reads (each API's units, then +q) */
TEST(vc_reads_units_and_quantum)
{
    if (!virt()) return;
    clk_reset();
    uint64_t T = 0;
    LARGE_INTEGER f, c;
    QueryPerformanceFrequency(&f);
    CHECK_EQ(f.QuadPart, 10000000);
    CHECK_EQ(now_ns(), T);                                   /* the main thread starts at 0; QPF reads nothing */
    QueryPerformanceCounter(&c); CHECK_EQ(c.QuadPart, (long long)(T / Q)); T += Q;
    QueryPerformanceCounter(&c); CHECK_EQ(c.QuadPart, (long long)(T / Q)); T += Q;
    CHECK_EQ(GetTickCount(), (DWORD)(T / 1000000)); T += Q;
    FILETIME ft;
    GetSystemTimeAsFileTime(&ft); CHECK_EQ(ft_of(ft), EPOCH + T / Q); T += Q;
    CHECK_EQ(now_ns(), T);
    Sleep(1234);                                             /* t += ms * 10^6 */
    T += 1234ull * 1000000;
    CHECK_EQ(now_ns(), T);
    CHECK_EQ(timeGetTime(), (DWORD)(T / 1000000)); T += Q;   /* timeGetTime is GetTickCount */
    QueryPerformanceCounter(&c); CHECK_EQ(c.QuadPart, (long long)(T / Q)); T += Q;
    GetSystemTimeAsFileTime(&ft); CHECK_EQ(ft_of(ft), EPOCH + T / Q); T += Q;
    Sleep(0);                                                /* one quantum */
    T += Q;
    CHECK_EQ(now_ns(), T);
    /* 1000 reads in a row: QueryPerformanceCounter counts one unit per read */
    QueryPerformanceCounter(&c);
    long long first = c.QuadPart;
    for (int i = 1; i <= 1000; i++) {
        QueryPerformanceCounter(&c);
        CHECK_EQ(c.QuadPart, first + i);
    }
    /* msvcrt time(): seconds since 1970 of the same clock */
    clk_reset();
    Sleep(90061001);                                         /* 1 d 1 h 1 min 1 s 1 ms */
    long long (*t64)(long long *) = (long long (*)(long long *))fnvnx_win32_lookup_export("msvcrt.dll", "_time64");
    if (t64) CHECK_EQ(t64(NULL), (long long)((EPOCH - 116444736000000000ull) / 10000000 + 90061));
}

/* ---- the calendar of the epoch, UTC, no daylight time */
TEST(vc_calendar_and_zone)
{
    if (!virt()) return;
    clk_reset();
    uint64_t T = 0;
    Sleep(90061001); T += 90061001ull * 1000000;             /* 2011-01-02 01:01:01.001 */
    SYSTEMTIME st, lt;
    GetSystemTime(&st); T += Q;
    CHECK_EQ(st.wYear, 2011); CHECK_EQ(st.wMonth, 1); CHECK_EQ(st.wDay, 2); CHECK_EQ(st.wDayOfWeek, 0);   /* a Sunday */
    CHECK_EQ(st.wHour, 1); CHECK_EQ(st.wMinute, 1); CHECK_EQ(st.wSecond, 1); CHECK_EQ(st.wMilliseconds, 1);
    GetLocalTime(&lt); T += Q;
    CHECK(!memcmp(&st, &lt, sizeof st));                      /* the local time is UTC */
    CHECK_EQ(now_ns(), T);
    char d[16], t[16];
    strdate_fn()(d); T += Q;
    strtime_fn()(t); T += Q;
    CHECK_STR(d, "01/02/11");
    CHECK_STR(t, "01:01:01");
    CHECK_EQ(now_ns(), T);
    /* GetDateFormatA / GetTimeFormatA read the clock only for a NULL SYSTEMTIME */
    char out[64];
    GetDateFormatA(0x0409, 0, NULL, "yyyy-MM-dd", out, sizeof out); T += Q;
    CHECK_STR(out, "2011-01-02");
    GetTimeFormatA(0x0409, 0, NULL, "HH:mm:ss", out, sizeof out); T += Q;
    CHECK_STR(out, "01:01:01");
    CHECK_EQ(now_ns(), T);
    GetDateFormatA(0x0409, 0, &st, "yyyy-MM-dd", out, sizeof out);
    GetTimeFormatA(0x0409, 0, &st, "HH:mm:ss", out, sizeof out);
    CHECK_EQ(now_ns(), T);                                    /* an explicit SYSTEMTIME: no read */
    /* the zone: UTC, no daylight time, and none of these reads the clock */
    TIME_ZONE_INFORMATION tz;
    CHECK_EQ(GetTimeZoneInformation(&tz), 0);                 /* TIME_ZONE_ID_UNKNOWN: no daylight time */
    CHECK_EQ(tz.Bias, 0); CHECK_EQ(tz.StandardBias, 0); CHECK_EQ(tz.DaylightBias, 0);
    CHECK_EQ(tz.StandardDate.wMonth, 0); CHECK_EQ(tz.DaylightDate.wMonth, 0);
    FILETIME a, b;
    SystemTimeToFileTime(&st, &a);
    FileTimeToLocalFileTime(&a, &b);
    CHECK_EQ(ft_of(a), ft_of(b));
    SYSTEMTIME loc;
    CHECK(SystemTimeToTzSpecificLocalTime(NULL, &st, &loc) && !memcmp(&st, &loc, sizeof st));
    CHECK(SystemTimeToTzSpecificLocalTime(&tz, &st, &loc) && !memcmp(&st, &loc, sizeof st));
    CHECK_EQ(now_ns(), T);
}

/* ---- sleeping takes no real time */
TEST(vc_sleep_is_virtual)
{
    if (!virt()) return;
    clk_reset();
    double r0 = real_s();
    Sleep(10000);                                             /* ten seconds of virtual time */
    Sleep(3600 * 1000);                                       /* an hour */
    CHECK_EQ(now_ns(), (10000ull + 3600000ull) * 1000000);
    CHECK(real_s() - r0 < 1.0);
    CHECK_EQ(GetTickCount(), 3610000u);
}

/* ---- thread start: a thread begins at its creator's t at the moment of creation */
static volatile uint64_t g_child_t0, g_child_qpc, g_child_end;
static DWORD child_reads(LPVOID)
{
    g_child_t0 = now_ns();
    LARGE_INTEGER c;
    QueryPerformanceCounter(&c);
    g_child_qpc = (uint64_t)c.QuadPart;
    g_child_end = now_ns();
    return 0;
}
TEST(vc_thread_inherits_creator_t)
{
    if (!virt()) return;
    clk_reset();
    Sleep(7);                                                 /* t = 7 ms */
    HANDLE h = CreateThread(NULL, 0, child_reads, NULL, CREATE_SUSPENDED, NULL);
    Sleep(50);                                                /* the creator goes on; the child starts at the creation instant */
    ResumeThread(h);
    CHECK_EQ(WaitForSingleObject(h, 5000), WAIT_OBJECT_0);
    CHECK_EQ(g_child_t0, 7000000);
    CHECK_EQ(g_child_qpc, 70000);
    CHECK_EQ(g_child_end, 7000100);
    CHECK_EQ(now_ns(), 57000000);                             /* the child's exit (7.0001 ms) is earlier than the creator's t */
    CloseHandle(h);
}

/* ---- waits: success takes max(t, the signaller's t); a timeout adds the timeout; a failed zero wait adds q */
static HANDLE g_ev, g_ev2;
static volatile int g_delay_us, g_adv_ms;
static DWORD signaller(LPVOID)
{
    if (g_delay_us) usleep((useconds_t)g_delay_us);           /* real scheduling is not part of the definition */
    Sleep((DWORD)g_adv_ms);                                   /* the signaller advances its clock by a known amount */
    SetEvent(g_ev);
    return 0;
}
TEST(vc_wait_success_takes_signaller_t)
{
    if (!virt()) return;
    g_ev = CreateEventA(NULL, FALSE, FALSE, NULL);
    for (int i = 0; i < 100; i++) {
        clk_reset();
        g_adv_ms = 1 + i % 7;
        g_delay_us = (i % 3) * 400;                           /* the signaller runs early, late or at once */
        HANDLE h = CreateThread(NULL, 0, signaller, NULL, 0, NULL);
        if (i % 2) usleep(200);                               /* the waiter starts waiting early or late */
        CHECK_EQ(WaitForSingleObject(g_ev, 10000), WAIT_OBJECT_0);
        CHECK_EQ(now_ns(), (uint64_t)g_adv_ms * 1000000);     /* t = max(0, the signaller's t) */
        WaitForSingleObject(h, 10000);
        CloseHandle(h);
    }
    /* a waiter that is ahead keeps its own t */
    clk_reset();
    Sleep(500);
    g_adv_ms = 3; g_delay_us = 0;
    HANDLE h = CreateThread(NULL, 0, signaller, NULL, 0, NULL);   /* starts at 500 ms, signals at 503 ms */
    WaitForSingleObject(g_ev, 10000);
    CHECK_EQ(now_ns(), 503000000);
    WaitForSingleObject(h, 10000);
    CloseHandle(h);
    CloseHandle(g_ev);
}

TEST(vc_wait_timeout_and_zero)
{
    if (!virt()) return;
    HANDLE e = CreateEventA(NULL, TRUE, FALSE, NULL), s = CreateSemaphoreA(NULL, 0, 4, NULL), m = CreateMutexA(NULL, TRUE, NULL);
    clk_reset();
    uint64_t T = 0;
    CHECK_EQ(WaitForSingleObject(e, 30), WAIT_TIMEOUT);       /* real blocking for 30 ms, virtual +30 ms */
    T += 30000000;
    CHECK_EQ(now_ns(), T);
    CHECK_EQ(WaitForSingleObject(e, 0), WAIT_TIMEOUT);        /* a zero wait that does not succeed: + q */
    T += Q;
    CHECK_EQ(now_ns(), T);
    HANDLE both[2] = { e, s };
    CHECK_EQ(WaitForMultipleObjects(2, both, TRUE, 0), WAIT_TIMEOUT);
    T += Q;
    CHECK_EQ(now_ns(), T);
    CHECK_EQ(WaitForMultipleObjects(2, both, FALSE, 20), WAIT_TIMEOUT);
    T += 20000000;
    CHECK_EQ(now_ns(), T);
    /* a wait that succeeds at once takes the stamp of the last signal: here the creation / signal by this thread itself */
    SetEvent(e);                                              /* signalled at t = T */
    CHECK_EQ(WaitForSingleObject(e, 0), WAIT_OBJECT_0);
    CHECK_EQ(now_ns(), T);                                    /* max(T, T): a success adds nothing */
    ReleaseMutex(m);
    Sleep(5);
    T += 5000000;
    CHECK_EQ(WaitForSingleObject(m, 0), WAIT_OBJECT_0);
    CHECK_EQ(now_ns(), T);
    CloseHandle(e); CloseHandle(s); CloseHandle(m);
}

/* every signalable object carries the t of its last signal: event, semaphore, mutex release, thread exit; wait-all takes the latest */
struct SigJob { HANDLE h; int kind; DWORD ms; };
static DWORD sig_job(LPVOID p)
{
    SigJob *j = (SigJob *)p;
    Sleep(j->ms);
    if (j->kind == 0) SetEvent(j->h);
    else if (j->kind == 1) ReleaseSemaphore(j->h, 1, NULL);
    else if (j->kind == 2) ReleaseMutex(j->h);
    return 0;
}
static HANDLE g_mx;
static volatile uint64_t g_mx_t;
static DWORD mutex_waiter_body(LPVOID)
{
    CHECK_EQ(now_ns(), 0);                                    /* created at t = 0 */
    CHECK_EQ(WaitForSingleObject(g_mx, 10000), WAIT_OBJECT_0);
    g_mx_t = now_ns();                                        /* took the release's stamp */
    ReleaseMutex(g_mx);
    return 0;
}
/* the CHECKs of this procedure end it, not the process: the harness guard (s4_test.h), the case fails at its join */
static DWORD mutex_waiter(LPVOID)
{
    return s4_thread_guard([&]() -> DWORD { return mutex_waiter_body(nullptr); }, (DWORD)0xFA11);
}
TEST(vc_signal_stamps_of_every_object)
{
    if (!virt()) return;
    clk_reset();
    HANDLE ev = CreateEventA(NULL, FALSE, FALSE, NULL), sem = CreateSemaphoreA(NULL, 0, 4, NULL), mx = CreateMutexA(NULL, FALSE, NULL);
    SigJob a = { ev, 0, 11 }, b = { sem, 1, 17 };
    HANDLE ta = CreateThread(NULL, 0, sig_job, &a, 0, NULL), tb = CreateThread(NULL, 0, sig_job, &b, 0, NULL);
    HANDLE both[2] = { ev, sem };
    CHECK_EQ(WaitForMultipleObjects(2, both, TRUE, 10000), WAIT_OBJECT_0);
    CHECK_EQ(now_ns(), 17000000);                             /* wait-all: the latest of the signals */
    CHECK_EQ(WaitForSingleObject(ta, 10000), WAIT_OBJECT_0);  /* thread exit carries the thread's t at its end (11 ms, earlier) */
    CHECK_EQ(now_ns(), 17000000);
    WaitForSingleObject(tb, 10000);
    CloseHandle(ta); CloseHandle(tb);
    /* a mutex release: the thread that waits for the mutex takes the releaser's t, whether it was already waiting or not */
    CloseHandle(mx);
    for (int i = 0; i < 20; i++) {
        clk_reset();
        g_mx = CreateMutexA(NULL, TRUE, NULL);                /* owned by this thread */
        HANDLE tc = CreateThread(NULL, 0, mutex_waiter, NULL, 0, NULL);
        if (i % 2) usleep(300);
        Sleep(40);
        ReleaseMutex(g_mx);                                   /* signalled at 40 ms */
        WaitForSingleObject(tc, 10000);
        CHECK_EQ(g_mx_t, 40000000);
        CloseHandle(tc);
        CloseHandle(g_mx);
    }
    CloseHandle(ev); CloseHandle(sem);
}

/* ---- messages: a posted message carries the poster's t; the receiver takes it on retrieval, MSG.time = t in ms */
static DWORD g_main_tid;
static DWORD poster(LPVOID)
{
    Sleep(3);                                                 /* 3 ms */
    fnvnx_post_thread_message(g_main_tid, 0x500, 1, 2);
    Sleep(4);                                                 /* the poster goes on: later messages carry later stamps */
    fnvnx_post_thread_message(g_main_tid, 0x501, 3, 4);
    return 0;
}
TEST(vc_message_time_across_threads)
{
    if (!virt()) return;
    clk_reset();
    g_main_tid = GetCurrentThreadId();
    MSG m;
    PeekMessageA(&m, NULL, 0, 0, PM_REMOVE);                  /* creates this thread's queue (and empties it) */
    clk_reset();
    HANDLE h = CreateThread(NULL, 0, poster, NULL, 0, NULL);
    while (!PeekMessageA(&m, NULL, 0x500, 0x500, PM_NOREMOVE))   /* real polling: no clock call of this thread in between */
        usleep(100);
    CHECK_EQ(now_ns(), 3000000);                              /* the peek retrieved it: t = max(0, 3 ms) */
    CHECK_EQ(m.time, 3);
    CHECK(PeekMessageA(&m, NULL, 0x500, 0x500, PM_REMOVE));
    CHECK_EQ(m.message, 0x500); CHECK_EQ(m.wParam, 1); CHECK_EQ(m.lParam, 2); CHECK_EQ(m.time, 3);
    while (!PeekMessageA(&m, NULL, 0x501, 0x501, PM_REMOVE))
        usleep(100);
    CHECK_EQ(m.time, 7);                                      /* posted at 3 + 4 ms */
    CHECK_EQ(now_ns(), 7000000);
    WaitForSingleObject(h, 10000);
    CloseHandle(h);
}

/* ---- file times: the platform stamps the calling thread's t (no advance) and reads it back */
TEST(vc_file_times)
{
    if (!virt()) return;
    clk_reset();
    Sleep(5);
    uint64_t T = now_ns();                                    /* 5 ms */
    HANDLE f = CreateFileA("C:\\vc_stamp.txt", GENERIC_READ | GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    CHECK(f != INVALID_HANDLE_VALUE);
    CHECK_EQ(now_ns(), T);                                    /* stamping advances nothing */
    FILETIME c, a, w;
    CHECK(GetFileTime(f, &c, &a, &w));
    CHECK_EQ(ft_of(c), EPOCH + T / Q); CHECK_EQ(ft_of(a), EPOCH + T / Q); CHECK_EQ(ft_of(w), EPOCH + T / Q);
    Sleep(20);
    uint64_t T2 = now_ns();
    DWORD n;
    CHECK(WriteFile(f, "abc", 3, &n, NULL));
    CHECK(GetFileTime(f, &c, &a, &w));
    CHECK_EQ(ft_of(c), EPOCH + T / Q); CHECK_EQ(ft_of(w), EPOCH + T2 / Q);
    Sleep(20);
    uint64_t T3 = now_ns();
    CHECK(CloseHandle(f));                                    /* a written file: the close stamps the write time */
    WIN32_FIND_DATAA fd;
    HANDLE fh = FindFirstFileA("C:\\vc_stamp.txt", &fd);
    CHECK(fh != INVALID_HANDLE_VALUE);
    CHECK_EQ(ft_of(fd.ftCreationTime), EPOCH + T / Q);
    CHECK_EQ(ft_of(fd.ftLastWriteTime), EPOCH + T3 / Q);
    FindClose(fh);
    Sleep(10);
    uint64_t T4 = now_ns();
    CHECK(CopyFileA("C:\\vc_stamp.txt", "C:\\vc_copy.txt", FALSE));
    fh = FindFirstFileA("C:\\vc_copy.txt", &fd);
    CHECK_EQ(ft_of(fd.ftCreationTime), EPOCH + T4 / Q); CHECK_EQ(ft_of(fd.ftLastWriteTime), EPOCH + T4 / Q);
    FindClose(fh);
    CHECK(MoveFileA("C:\\vc_copy.txt", "C:\\vc_moved.txt"));   /* a rename keeps the times */
    Sleep(10);
    fh = FindFirstFileA("C:\\vc_moved.txt", &fd);
    CHECK(fh != INVALID_HANDLE_VALUE);
    CHECK_EQ(ft_of(fd.ftCreationTime), EPOCH + T4 / Q);
    FindClose(fh);
    CHECK(CreateDirectoryA("C:\\vc_dir", NULL));
    uint64_t T5 = now_ns();
    fh = FindFirstFileA("C:\\vc_dir", &fd);
    CHECK(fh != INVALID_HANDLE_VALUE);
    CHECK_EQ(ft_of(fd.ftCreationTime), EPOCH + T5 / Q);
    FindClose(fh);
    DeleteFileA("C:\\vc_stamp.txt"); DeleteFileA("C:\\vc_moved.txt"); RemoveDirectoryA("C:\\vc_dir");
    /* a deleted and re-created file is new */
    Sleep(10);
    uint64_t T6 = now_ns();
    f = CreateFileA("C:\\vc_stamp.txt", GENERIC_WRITE, 0, NULL, CREATE_NEW, 0, NULL);
    GetFileTime(f, &c, NULL, &w);
    CHECK_EQ(ft_of(c), EPOCH + T6 / Q);
    CloseHandle(f);
    DeleteFileA("C:\\vc_stamp.txt");
}

/* ---- determinism: a scenario that mixes all of the above, run twice in one process (the clock re-initialised) and in two
 * processes, writes byte-identical logs of every observed time value */
static Log *g_log;
static HANDLE g_dev, g_dsem;
static DWORD scen_worker(LPVOID p)
{
    int id = (int)(intptr_t)p;
    LARGE_INTEGER c;
    QueryPerformanceCounter(&c);
    g_log->add(id == 1 ? "w1.qpc" : "w2.qpc", (uint64_t)c.QuadPart);   /* the two workers write distinct lines; order fixed below */
    Sleep((DWORD)(id * 13));
    if (id == 1) SetEvent(g_dev); else ReleaseSemaphore(g_dsem, 1, NULL);
    return 0;
}
static void scenario(Log &log)
{
    clk_reset();
    g_log = &log;
    LARGE_INTEGER c;
    FILETIME ft;
    SYSTEMTIME st;
    QueryPerformanceCounter(&c); log.add("qpc", (uint64_t)c.QuadPart);
    log.add("tick", GetTickCount());
    GetSystemTimeAsFileTime(&ft); log.add("ft", ft_of(ft));
    Sleep(250);
    GetSystemTime(&st);
    log.add("st.ms", st.wMilliseconds); log.add("st.s", st.wSecond); log.add("st.min", st.wMinute);
    GetLocalTime(&st); log.add("lt.ms", st.wMilliseconds);
    char buf[32];
    strdate_fn()(buf); log.add("strdate", buf);
    strtime_fn()(buf); log.add("strtime", buf);
    g_dev = CreateEventA(NULL, FALSE, FALSE, NULL);
    g_dsem = CreateSemaphoreA(NULL, 0, 2, NULL);
    /* worker 1 first (waited for before worker 2 starts: the log lines of the two do not race) */
    HANDLE w1 = CreateThread(NULL, 0, scen_worker, (void *)(intptr_t)1, 0, NULL);
    WaitForSingleObject(g_dev, 10000); log.add("after.w1.event", now_ns());
    WaitForSingleObject(w1, 10000); log.add("after.w1.exit", now_ns());
    Sleep(100);
    HANDLE w2 = CreateThread(NULL, 0, scen_worker, (void *)(intptr_t)2, 0, NULL);
    WaitForSingleObject(g_dsem, 10000); log.add("after.w2.sem", now_ns());
    WaitForSingleObject(w2, 10000);
    log.add("wait.timeout", WaitForSingleObject(g_dev, 15)); log.add("after.timeout", now_ns());
    log.add("wait.zero", WaitForSingleObject(g_dev, 0)); log.add("after.zero", now_ns());
    HANDLE f = CreateFileA("C:\\vc_det.bin", GENERIC_READ | GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    DWORD n;
    FILETIME c3, w3;
    GetFileTime(f, &c3, NULL, &w3); log.add("file.c", ft_of(c3)); log.add("file.w", ft_of(w3));
    Sleep(77);
    WriteFile(f, "xyz", 3, &n, NULL);
    GetFileTime(f, &c3, NULL, &w3); log.add("file.c2", ft_of(c3)); log.add("file.w2", ft_of(w3));
    CloseHandle(f);
    DeleteFileA("C:\\vc_det.bin");
    log.add("end", now_ns());
    CloseHandle(w1); CloseHandle(w2); CloseHandle(g_dev); CloseHandle(g_dsem);
}
static std::string hex_of(const std::string &s)
{
    std::string h;
    char b[4];
    for (unsigned char ch : s) { snprintf(b, sizeof b, "%02x", ch); h += b; }
    return h;
}
TEST(vc_determinism_in_process)
{
    if (!virt()) return;
    Log a, b;
    scenario(a);
    scenario(b);                                              /* the clock re-initialised in the same process */
    CHECK(a.s.size() > 200);
    CHECK(a.s == b.s);
    for (int i = 0; i < 20; i++) {
        Log c;
        scenario(c);
        CHECK(c.s == a.s);
    }
}
/* the second process: this same binary runs the one case below and prints its log in hex on one line */
TEST(vc_determinism_child)
{
    if (!virt() || !getenv("FNVNX_VC_CHILD")) return;
    Log a;
    scenario(a);
    printf("VCLOG %s\n", hex_of(a.s).c_str());
}
TEST(vc_determinism_two_processes)
{
    if (!virt() || getenv("FNVNX_VC_CHILD")) return;
    std::string mine;
    {
        Log a;
        scenario(a);
        mine = hex_of(a.s);
    }
    char exe[600];
    ssize_t en = readlink("/proc/self/exe", exe, sizeof exe - 1);
    CHECK(en > 0);
    if (en <= 0) return;
    exe[en] = 0;
    for (int run = 0; run < 2; run++) {
        char cmd[2400];
        snprintf(cmd, sizeof cmd, "mkdir -p '%s-vcchild%d/game' && FNVNX_VC_CHILD=1 FNVNX_TEST_ONLY=vc_determinism_child FNVNX_ROOT='%s-vcchild%d' '%s' 2>/dev/null",
                 s4_root(), run, s4_root(), run, exe);
        FILE *p = popen(cmd, "r");
        CHECK(p != NULL);
        std::string line;
        char buf[4096];
        while (p && fgets(buf, sizeof buf, p)) {
            if (!strncmp(buf, "VCLOG ", 6)) { line = buf + 6; while (!line.empty() && (line.back() == '\n')) line.pop_back(); }
        }
        if (p) pclose(p);
        CHECK(!line.empty());
        CHECK(line == mine);                                  /* byte-identical logs of another process */
    }
}
