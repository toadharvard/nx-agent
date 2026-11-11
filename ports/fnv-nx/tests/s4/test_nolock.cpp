/* test_nolock.cpp - the rule "no platform lock is held across a call into the game" (platform/os/os.h: os_lock family;
 * platform/include/fnvnx_guestfn.h): the detector itself, and every kind of callout of the platform driven with a
 * callback that DOES NOT RETURN (a longjmp out to the test: the product's runtime may discard the native frames of an
 * import when a game exception unwinds past it, without unlocking anything). After each such call the thread must hold no
 * platform lock (fnvnx_bnd_locks_held() == 0: the host backend counts every os_lock/os_trylock), the same API must work
 * again from this thread and from another one, and the run-wide violation count (host_boundary.h) must be untouched.
 * Cases nlk_*. The audio / input callouts are in tests/s7/test_nolock_audio.cpp. */
#include "s4_test.h"

#include "fnvnx_boundary.h"
#include "fnvnx_guestfn.h"
#include "fnvnx_mmio.h"
#include "fnvnx_msvcrt.h"
#include "fnvnx_win32_ext.h"
#include "host_boundary.h"
#include "../../platform/os/os.h"
#include "../../platform/win32/w32.h"

#include <setjmp.h>
#include <stdint.h>
#include <string.h>
#include <sys/wait.h>
#include <atomic>
#include <thread>
#include <unistd.h>


namespace {

jmp_buf g_jb;
[[noreturn]] void escape() { longjmp(g_jb, 1); }
/* runs the statements; true when a callback escaped out of them */
#define ESCAPES(...) ([&]() -> bool { if (setjmp(g_jb)) return true; __VA_ARGS__; return false; }())

/* the state a "does not return" case must leave: no lock held here, none counted in the run */
bool clean() { return fnvnx_bnd_locks_held() == 0; }
void nop() {}

void drain()
{
    MSG m;
    while (PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)) DispatchMessageA(&m);
}

/* runs f on another thread while this one keeps serving its messages (what sends to its windows wait for) */
void on_other_thread(void (*f)(void))
{
    std::atomic<bool> done(false);
    std::thread t([&] { f(); done = true; });
    while (!done) {
        drain();
        usleep(500);
    }
    t.join();
}

/* ---- the detector */
TEST(nlk_detector_counts_platform_locks_and_not_the_games)
{
    fnvnx_bnd_mark mk = fnvnx_bnd_violation_mark();
    fnvnx_guestfn<void(void)> g((const void *)&nop);
    os_lock_t l = OS_LOCK_INIT, m = OS_LOCK_INIT;
    CHECK_EQ(fnvnx_bnd_locks_held(), 0);
    /* a platform lock held across a call is counted: every os_lock, a successful os_trylock */
    os_lock(&l);
    CHECK_EQ(fnvnx_bnd_locks_held(), 1);
    CHECK(!os_trylock(&l));                 /* not acquired: not counted */
    CHECK_EQ(fnvnx_bnd_locks_held(), 1);
    CHECK(os_trylock(&m));
    CHECK_EQ(fnvnx_bnd_locks_held(), 2);
    g();
    CHECK_EQ(fnvnx_bnd_violation_mark().violations, mk.violations + 1);
    os_unlock(&l);
    g();
    CHECK_EQ(fnvnx_bnd_violation_mark().violations, mk.violations + 2);   /* m still held */
    os_unlock(&m);
    CHECK_EQ(fnvnx_bnd_locks_held(), 0);
    g();
    CHECK_EQ(fnvnx_bnd_violation_mark().violations, mk.violations + 2);
    /* a condition wait drops and takes the lock again: held before and after */
    os_cond_t c = OS_COND_INIT;
    os_lock(&l);
    CHECK_EQ(os_cond_wait(&c, &l, 1000000ull), 1);
    CHECK_EQ(fnvnx_bnd_locks_held(), 1);
    os_unlock(&l);
    CHECK_EQ(fnvnx_bnd_locks_held(), 0);
    /* the game's own locks are not platform locks: a critical section / _lock / std mutex held across a callback is
     * legitimate, and the guard functions of the host process see it as nothing */
    CRITICAL_SECTION cs;
    InitializeCriticalSection(&cs);
    EnterCriticalSection(&cs);
    CHECK(TryEnterCriticalSection(&cs));
    CHECK_EQ(fnvnx_bnd_locks_held(), 0);
    g();
    LeaveCriticalSection(&cs);
    LeaveCriticalSection(&cs);
    DeleteCriticalSection(&cs);
    auto lock = (void (*)(int))fnvnx_win32_lookup_export("msvcrt.dll", "_lock");
    auto unlock = (void (*)(int))fnvnx_win32_lookup_export("msvcrt.dll", "_unlock");
    lock(13);
    CHECK_EQ(fnvnx_bnd_locks_held(), 0);
    g();
    unlock(13);
    os_glock(&l);
    CHECK_EQ(fnvnx_bnd_locks_held(), 0);
    g();
    os_gunlock(&l);
    CHECK_EQ(fnvnx_bnd_violation_mark().violations, mk.violations + 2);
    fnvnx_bnd_violation_forget(mk);
}

TEST(nlk_detector_sees_every_boundary_entry)
{
    /* the entries into the game other than guest_invoke (module load, thread run / detach, process detach) check as well:
     * a lock held across LoadLibraryA of a module that is not loaded yet is a violation */
    fnvnx_bnd_mark mk = fnvnx_bnd_violation_mark();
    g_bnd_loader = [](const char *, uint32_t *) -> uint32_t { return 126; };
    os_lock_t l = OS_LOCK_INIT;
    os_lock(&l);
    LoadLibraryA("nlk_none.dll");
    os_unlock(&l);
    CHECK_EQ(fnvnx_bnd_violation_mark().violations, mk.violations + 1);
    fnvnx_bnd_violation_forget(mk);
    LoadLibraryA("nlk_none.dll");
    CHECK_EQ(fnvnx_bnd_violation_mark().violations, mk.violations);
    g_bnd_loader = NULL;
}

/* ---- windows: window procedures, hooks, timers, enumeration */
LRESULT eproc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    if (m == 0x7701) escape();
    if (m == 0x500) return (LRESULT)(w + l);
    return DefWindowProcA(h, m, w, l);
}
HWND mkwin(const char *cls, HWND parent = NULL, DWORD style = 0x90000000u)
{
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = eproc;
    wc.lpszClassName = cls;
    RegisterClassA(&wc);
    return CreateWindowExA(0, cls, "t", style, 0, 0, 100, 100, parent, NULL, NULL, NULL);
}
HWND g_w;

TEST(nlk_escape_window_procedure)
{
    fnvnx_bnd_mark mk = fnvnx_bnd_violation_mark();
    HWND w = mkwin("NlkW");
    CHECK(w != NULL);
    drain();
    CHECK(ESCAPES(SendMessageA(w, 0x7701, 0, 0)));      /* the procedure never returns */
    CHECK(clean());
    CHECK_EQ(SendMessageA(w, 0x500, 3, 4), 7);          /* the window and the queue still work */
    PostMessageA(w, 0x7701, 0, 0);
    MSG m;
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_REMOVE));
    CHECK(ESCAPES(DispatchMessageA(&m)));               /* DispatchMessage reaches the procedure */
    CHECK(clean());
    PostMessageA(w, 0x500, 0, 0);
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_REMOVE));
    CHECK_EQ(DispatchMessageA(&m), 0);
    g_w = w;
    on_other_thread([] {                                // another thread uses the same window list and queues
        CHECK(IsWindow(g_w));
        PostMessageA(g_w, 0x500, 0, 0);
        HWND w2 = mkwin("NlkW");
        CHECK(w2 != NULL);
        CHECK_EQ(SendMessageA(w2, 0x500, 1, 1), 2);
        CHECK(DestroyWindow(w2));
    });
    drain();
    CHECK(DestroyWindow(w));
    drain();
    CHECK(clean());
    CHECK_EQ(fnvnx_bnd_violation_mark().violations, mk.violations);
}

LRESULT ehook(int code, WPARAM w, LPARAM l)
{
    MSG *m = (MSG *)l;
    if (code >= 0 && m && m->message == 0x7702) escape();
    return CallNextHookEx(NULL, code, w, l);
}
int g_nhook;
LRESULT okhook(int code, WPARAM w, LPARAM l)
{
    g_nhook++;
    return CallNextHookEx(NULL, code, w, l);
}

TEST(nlk_escape_hook_procedure)
{
    HWND w = mkwin("NlkH");
    drain();
    HHOOK h = SetWindowsHookExA(3 /* WH_GETMESSAGE */, ehook, (HINSTANCE)(uintptr_t)0x400000, GetCurrentThreadId());
    CHECK(h != NULL);
    PostMessageA(w, 0x7702, 0, 0);
    MSG m;
    CHECK(ESCAPES(PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)));   /* the hook procedure is called from message retrieval */
    CHECK(clean());
    /* the hook list is usable again: another hook, another thread, removal of the one that never returned */
    HHOOK h2 = SetWindowsHookExA(3, okhook, (HINSTANCE)(uintptr_t)0x400000, GetCurrentThreadId());
    CHECK(h2 != NULL);
    CHECK(UnhookWindowsHookEx(h));
    g_nhook = 0;
    PostMessageA(w, 0x500, 0, 0);
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_REMOVE));
    CHECK(g_nhook > 0);
    on_other_thread([] {
        HHOOK t = SetWindowsHookExA(3, okhook, (HINSTANCE)(uintptr_t)0x400000, GetCurrentThreadId());
        CHECK(t != NULL);
        CHECK(UnhookWindowsHookEx(t));
    });
    CHECK(UnhookWindowsHookEx(h2));
    CHECK(DestroyWindow(w));
    drain();
    CHECK(clean());
}

void etimer(HWND, UINT, UINT_PTR, DWORD) { escape(); }
int g_ticks;
void oktimer(HWND, UINT, UINT_PTR, DWORD) { g_ticks++; }

TEST(nlk_escape_timer_procedure)
{
    MSG m = {};
    m.message = 0x113;   /* WM_TIMER with a TIMERPROC in lParam: DispatchMessage calls it */
    m.wParam = 1;
    m.lParam = (LPARAM)(uintptr_t)&etimer;
    CHECK(ESCAPES(DispatchMessageA(&m)));
    CHECK(clean());
    g_ticks = 0;
    m.lParam = (LPARAM)(uintptr_t)&oktimer;
    DispatchMessageA(&m);
    CHECK_EQ(g_ticks, 1);
}

BOOL eenum(HWND, LPARAM) { escape(); }
BOOL okenum(HWND h, LPARAM l) { (*(int *)l)++; (void)h; return TRUE; }

TEST(nlk_escape_enum_child_windows)
{
    HWND p = mkwin("NlkP");
    HWND c1 = mkwin("NlkC", p, 0x50000000u), c2 = mkwin("NlkC", p, 0x50000000u);
    CHECK(c1 && c2);
    CHECK(ESCAPES(EnumChildWindows(p, eenum, 0)));
    CHECK(clean());
    static int n;
    n = 0;
    CHECK(EnumChildWindows(p, okenum, (LPARAM)&n));
    CHECK_EQ(n, 2);
    g_w = p;
    on_other_thread([] {                      // the window list is not locked for ever
        n = 0;
        CHECK(EnumChildWindows(g_w, okenum, (LPARAM)&n));
        CHECK_EQ(n, 2);
    });
    CHECK(DestroyWindow(p));
    drain();
    CHECK(clean());
}

/* ---- process: unhandled exception filter, locale enumeration, loader */
LONG efilter(struct _EXCEPTION_POINTERS *) { escape(); }
LONG okfilter(struct _EXCEPTION_POINTERS *) { return EXCEPTION_EXECUTE_HANDLER; }
BOOL elocale(LPSTR) { escape(); }
BOOL oklocale(LPSTR) { return TRUE; }

TEST(nlk_escape_filter_and_locale_callbacks)
{
    LPTOP_LEVEL_EXCEPTION_FILTER old = SetUnhandledExceptionFilter(efilter);
    char epb[64] = {};
    struct _EXCEPTION_POINTERS *ep = (struct _EXCEPTION_POINTERS *)epb;
    CHECK(ESCAPES(UnhandledExceptionFilter(ep)));
    CHECK(clean());
    SetUnhandledExceptionFilter(okfilter);
    CHECK_EQ(UnhandledExceptionFilter(ep), 1);
    on_other_thread([] {
        char b[64] = {};
        CHECK_EQ(UnhandledExceptionFilter((struct _EXCEPTION_POINTERS *)b), 1);
    });
    SetUnhandledExceptionFilter(old);
    CHECK(ESCAPES(EnumSystemLocalesA(elocale, 1)));
    CHECK(clean());
    CHECK(EnumSystemLocalesA(oklocale, 1));
}

TEST(nlk_escape_module_loader)
{
    g_bnd_loader = [](const char *, uint32_t *) -> uint32_t { escape(); };
    CHECK(ESCAPES(LoadLibraryA("nlk_escape.dll")));   /* the loader runs DllMain of the module */
    CHECK(clean());
    g_bnd_loader = [](const char *, uint32_t *) -> uint32_t { return 126; };
    CHECK(LoadLibraryA("nlk_escape.dll") == NULL);
    CHECK_EQ(GetLastError(), 126);
    on_other_thread([] { CHECK(LoadLibraryA("nlk_escape.dll") == NULL); });
    g_bnd_loader = NULL;
}

/* ---- the C runtime: qsort comparator, _initterm table, exit handlers */
void *X(const char *n) { return fnvnx_win32_lookup_export("msvcrt.dll", n); }
int ecmp(const void *, const void *) { escape(); }
int okcmp(const void *a, const void *b) { return *(const int *)a - *(const int *)b; }
void eterm() { escape(); }
int g_terms;
void okterm() { g_terms++; }
void eexit() { escape(); }
int g_exits;
void okexit() { g_exits++; }

TEST(nlk_escape_crt_callbacks)
{
    auto qs = (void (*)(void *, size_t, size_t, int (*)(const void *, const void *)))X("qsort");
    int v[8] = {5, 3, 8, 1, 9, 2, 7, 4};
    CHECK(ESCAPES(qs(v, 8, sizeof(int), ecmp)));
    CHECK(clean());
    qs(v, 8, sizeof(int), okcmp);
    CHECK(v[0] == 1 && v[7] == 9);
    on_other_thread([] {
        int u[3] = {3, 1, 2};
        auto q = (void (*)(void *, size_t, size_t, int (*)(const void *, const void *)))X("qsort");
        q(u, 3, sizeof(int), okcmp);
        CHECK(u[0] == 1 && u[2] == 3);
    });
    auto it = (void (*)(void (**)(), void (**)()))X("_initterm");
    void (*tab[3])() = {okterm, eterm, okterm};
    g_terms = 0;
    CHECK(ESCAPES(it(tab, tab + 3)));
    CHECK(clean());
    CHECK_EQ(g_terms, 1);
    void (*tab2[2])() = {okterm, okterm};
    it(tab2, tab2 + 2);
    CHECK_EQ(g_terms, 3);
    /* exit handlers: the handler is taken off the list before it runs, so an escape leaves the rest runnable */
    fnvnx_atexit(okexit);
    fnvnx_atexit(eexit);
    fnvnx_atexit(okexit);
    g_exits = 0;
    CHECK(ESCAPES(fnvnx_cexit()));
    CHECK(clean());
    CHECK_EQ(g_exits, 1);                  /* the handler registered last ran, the escaping one is gone */
    fnvnx_cexit();
    CHECK_EQ(g_exits, 2);
    fnvnx_cexit();
    CHECK_EQ(g_exits, 2);
    on_other_thread([] { fnvnx_atexit(okexit); fnvnx_cexit(); });
    CHECK_EQ(g_exits, 3);
}

/* ---- threads: the start routine of a game thread (and its thread-detach notification) is called with no platform lock held,
 * and one that ends without returning (ExitThread) leaves the thread table usable */
int g_th_held = -1;
DWORD tstart_exit(void *) { g_th_held = (int)fnvnx_bnd_locks_held(); ExitThread(7); return 0; }
DWORD tstart_ret(void *) { return 3; }

TEST(nlk_thread_start_and_exit_without_return)
{
    DWORD id = 0;
    HANDLE t = CreateThread(NULL, 0, tstart_exit, NULL, 0, &id);
    CHECK(t != NULL);
    CHECK_EQ(WaitForSingleObject(t, 5000), 0);
    DWORD code = 0;
    CHECK(GetExitCodeThread(t, &code));
    CHECK_EQ(code, 7);
    CHECK_EQ(g_th_held, 0);
    CloseHandle(t);
    HANDLE u = CreateThread(NULL, 0, tstart_ret, NULL, 0, &id);   /* the table still serves threads */
    CHECK(u != NULL);
    CHECK_EQ(WaitForSingleObject(u, 5000), 0);
    CHECK(GetExitCodeThread(u, &code));
    CHECK_EQ(code, 3);
    CloseHandle(u);
    CHECK(clean());
}

/* ---- mmio: the I/O procedure of the game (MMIOM_OPEN / READ / SEEK / CLOSE are calls into the game) */
int g_mm_mode;   /* 0 normal, 1 READ never returns */
LRESULT mmproc(LPSTR p, UINT msg, LPARAM l1, LPARAM l2)
{
    MMIOINFO *i = (MMIOINFO *)p;
    switch (msg) {
    case MMIOM_OPEN: i->lDiskOffset = 0; return 0;
    case MMIOM_CLOSE: return 0;
    case MMIOM_READ:
        if (g_mm_mode == 1) escape();
        memset((void *)l1, 'x', (size_t)l2);
        i->lDiskOffset += (LONG)l2;
        return (LRESULT)l2;
    case MMIOM_SEEK: i->lDiskOffset = (LONG)l1; return i->lDiskOffset;
    }
    return 0;
}
HMMIO mm_open()
{
    MMIOINFO in = {};
    in.fccIOProc = FNVNX_FOURCC('N', 'L', 'K', ' ');
    in.pIOProc = mmproc;
    return mmioOpenA((LPSTR) "nlk.bin", &in, MMIO_READ);
}

TEST(nlk_escape_mmio_procedure)
{
    HMMIO h = mm_open();
    CHECK(h != NULL);
    char b[8];
    g_mm_mode = 1;
    CHECK(ESCAPES(mmioRead(h, b, 8)));
    CHECK(clean());
    g_mm_mode = 0;
    CHECK_EQ(mmioRead(h, b, 8), 8);              /* the same handle, and a new one, still work */
    HMMIO h2 = mm_open();
    CHECK(h2 != NULL);
    CHECK_EQ(mmioRead(h2, b, 4), 4);
    on_other_thread([] {
        HMMIO h3 = mm_open();
        char c[4];
        CHECK(h3 != NULL);
        CHECK_EQ(mmioRead(h3, c, 4), 4);
        CHECK_EQ(mmioClose(h3, 0), 0);
    });
    CHECK_EQ(mmioClose(h2, 0), 0);
    CHECK_EQ(mmioClose(h, 0), 0);
    CHECK(clean());
}

/* ---- process end: DllMain(PROCESS_DETACH) of the modules is a call into the game too; observed in a child process, whose
 * detach hook (the host boundary's test double of the loader's) ends it with the verdict */
TEST(nlk_process_detach_checks_locks)
{
    for (int held = 0; held < 2; held++) {
        fflush(NULL);
        pid_t pid = fork();
        CHECK(pid >= 0);
        if (pid == 0) {
            os_lock_t l = OS_LOCK_INIT;
            if (held)
                os_lock(&l);   /* a platform lock held at ExitProcess: must be seen */
            g_bnd_process_detach_hook = [] { _exit(g_bnd_lock_violations ? 41 : 40); };
            ExitProcess(5);
            _exit(98);
        }
        int st = 0;
        CHECK_EQ(waitpid(pid, &st, 0), pid);
        CHECK(WIFEXITED(st));
        CHECK_EQ(WEXITSTATUS(st), held ? 41 : 40);
    }
}

}   // namespace
