/* thread.cpp - threads, thread ids, last error, TlsAlloc slots, the image's implicit TLS and the fake TEB
 * behind __readfsdword. */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

/* ---- per-thread block */
static volatile DWORD g_next_tid = 0x1000;

struct w32_thread *w32_self(void)
{
    struct w32_thread *t = (struct w32_thread *)os_tls_get();
    if (t)
        return t;
    os_init();
    t = (struct w32_thread *)calloc(1, sizeof *t);
    if (!t)
        os_fatal("out of memory creating the per-thread block");
    t->tid = __atomic_fetch_add(&g_next_tid, 4, __ATOMIC_RELAXED); /* multiples of 4, like Windows */
    os_tls_set(t);
    return t;
}

/* frees the calling thread's block (threads created by CreateThread, when they end) */
static void self_release(void)
{
    struct w32_thread *t = (struct w32_thread *)os_tls_get();
    if (!t)
        return;
    os_tls_set(NULL);
    free(t->tls_ext);
    if (!t->implicit_tls_borrowed)
        free(t->implicit_tls[0]);
    free(t);
}

extern "C" DWORD GetLastError(void) { return w32_self()->last_error; }
extern "C" void SetLastError(DWORD e) { w32_self()->last_error = e; }
extern "C" DWORD GetCurrentThreadId(void) { return w32_self()->tid; }
extern "C" HANDLE GetCurrentThread(void) { return W32_PSEUDO_THREAD; }
extern "C" HANDLE GetCurrentProcess(void) { return W32_PSEUDO_PROCESS; }
extern "C" DWORD GetCurrentProcessId(void) { return os_process_id(); }

/* ---- TlsAlloc: a global bitmap of 1088 slots, values in the per-thread block */
static os_lock_t g_tls_lock;
static uint32_t g_tls_used[W32_TLS_SLOTS / 32];
#define TLS_OUT_OF_INDEXES_ ((DWORD)0xFFFFFFFF)

extern "C" DWORD TlsAlloc(void)
{
    os_lock(&g_tls_lock);
    for (DWORD i = 0; i < W32_TLS_SLOTS; i++)
        if (!(g_tls_used[i / 32] & (1u << (i % 32)))) {
            g_tls_used[i / 32] |= 1u << (i % 32);
            os_unlock(&g_tls_lock);
            /* Windows zeroes the slot in every thread; threads created later start at zero anyway */
            struct w32_thread *t = w32_self();
            if (i < 64)
                t->tls[i] = NULL;
            else if (t->tls_ext)
                t->tls_ext[i - 64] = NULL;
            return i;
        }
    os_unlock(&g_tls_lock);
    SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    return TLS_OUT_OF_INDEXES_;
}

extern "C" BOOL TlsFree(DWORD i)
{
    if (i >= W32_TLS_SLOTS) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    os_lock(&g_tls_lock);
    g_tls_used[i / 32] &= ~(1u << (i % 32));
    os_unlock(&g_tls_lock);
    return TRUE;
}

extern "C" LPVOID TlsGetValue(DWORD i)
{
    struct w32_thread *t = w32_self();
    if (i < 64) {
        t->last_error = ERROR_SUCCESS;
        return t->tls[i];
    }
    if (i >= W32_TLS_SLOTS) {
        t->last_error = ERROR_INVALID_PARAMETER;
        return NULL;
    }
    t->last_error = ERROR_SUCCESS;
    return t->tls_ext ? t->tls_ext[i - 64] : NULL;
}

extern "C" BOOL TlsSetValue(DWORD i, LPVOID v)
{
    struct w32_thread *t = w32_self();
    if (i < 64) {
        t->tls[i] = v;
        return TRUE;
    }
    if (i >= W32_TLS_SLOTS) {
        t->last_error = ERROR_INVALID_PARAMETER;
        return FALSE;
    }
    if (!t->tls_ext && !(t->tls_ext = (void **)calloc(W32_TLS_SLOTS - 64, sizeof(void *)))) {
        t->last_error = ERROR_NOT_ENOUGH_MEMORY;
        return FALSE;
    }
    t->tls_ext[i - 64] = v;
    return TRUE;
}

/* ---- implicit TLS of the image: __declspec(thread) data of FalloutNV.exe is reached as
 * ((char**)__readfsdword(0x2c))[_tls_index] + offset. The loader (S3) passes the .tls template; every thread
 * gets a copy on first access. _tls_index lives in the data image; the loader must store 0 there. */
static const void *g_tls_init;
static size_t g_tls_init_size, g_tls_total;

extern "C" void fnvnx_tls_set_template(const void *init_data, size_t init_size, size_t total_size)
{
    g_tls_init = init_data;
    g_tls_init_size = init_size;
    g_tls_total = total_size < init_size ? init_size : total_size;
}

/* Set by the game layer (toolchain/game/boot.cpp): the calling thread's native block of the image's TLS
 * (__fnvnx_tls_image, where S2 binds every __declspec(thread) variable). fs:[0x2c][0] then is that same block,
 * so an explicit __readfsdword(0x2c) access and a __declspec(thread) access see one variable, not two copies. */
extern "C" { void *(*fnvnx_image_tls_hook)(void) = nullptr; }

extern "C" void *fnvnx_tls_block(void)
{
    struct w32_thread *t = w32_self();
    if (!t->implicit_tls[0] && fnvnx_image_tls_hook) {
        t->implicit_tls[0] = fnvnx_image_tls_hook();
        t->implicit_tls_borrowed = 1;
    }
    if (!t->implicit_tls[0] && g_tls_total) {
        void *b = calloc(1, g_tls_total);
        if (!b)
            os_fatal("out of memory for the image TLS block");
        if (g_tls_init_size)
            memcpy(b, g_tls_init, g_tls_init_size);
        t->implicit_tls[0] = b;
    }
    return t->implicit_tls[0];
}

extern "C" void **fnvnx_tls_array(void)
{
    fnvnx_tls_block();
    return w32_self()->implicit_tls;
}

/* ---- fake TEB (x86 fs: segment) for the decomp's explicit __readfsdword reads.
 * Only offsets the decomp uses or that MSVC-era code commonly reads are served; others read 0. */
static unsigned char g_fake_peb[0x1000];

extern "C" unsigned long __readfsdword(unsigned long off)
{
    struct w32_thread *t = w32_self();
    switch (off) {
    case 0x00: return 0xFFFFFFFFul;                               /* SEH chain: end of list */
    case 0x18: return (unsigned long)(uintptr_t)t;                /* NT_TIB.Self (opaque) */
    case 0x20: return os_process_id();                            /* ClientId.UniqueProcess */
    case 0x24: return t->tid;                                     /* ClientId.UniqueThread */
    case 0x2c: return (unsigned long)(uintptr_t)fnvnx_tls_array(); /* ThreadLocalStoragePointer */
    case 0x30: return (unsigned long)(uintptr_t)g_fake_peb;       /* PEB (all zero: BeingDebugged = 0) */
    case 0x34: return t->last_error;                              /* LastErrorValue */
    default:
        if (off >= 0xE10 && off < 0xE10 + 64 * 4 && !(off & 3))  /* TlsSlots[64] */
            return (unsigned long)(uintptr_t)t->tls[(off - 0xE10) / 4];
        w32_log("__readfsdword(0x%lx): unsupported TEB offset, returning 0", off);
        return 0;
    }
}

/* ---- thread objects */

/* Set by the game layer (toolchain/game/boot.cpp): the placement role of a new thread from its start routine and
 * argument, OS_THREAD_ROLE(r) or -1 (default placement); see toolchain/host/placement.h. */
extern "C" { int (*fnvnx_thread_role_hook)(void *start, void *arg) = nullptr; }
struct w32_thread_obj {
    struct w32_obj o;
    os_thread *os;
    LPTHREAD_START_ROUTINE fn;
    LPVOID arg;
    DWORD tid;
    DWORD exit_code;
    int done;                      /* guarded by w32_wait_lock */
    int suspend_count;             /* > 0 before the first ResumeThread when CREATE_SUSPENDED */
    int started;
    int prio;
    int cpu;
    int boost;                     /* OS_THREAD_BOOST from the role hook */
    os_cond_t resumed;             /* a thread that suspended itself waits here until its count is 0 */
};

int w32_thread_signaled(struct w32_obj *o) { return ((struct w32_thread_obj *)o)->done; }

static void thread_destroy(struct w32_obj *o)
{
    struct w32_thread_obj *t = (struct w32_thread_obj *)o;
    os_thread_free(t->os); /* detaches a thread that still runs */
}

static void thread_finish(struct w32_thread_obj *to, DWORD code)
{
    os_lock(&w32_wait_lock);
    to->exit_code = code;
    to->done = 1;
    w32_obj_signal_locked(&to->o);
    os_unlock(&w32_wait_lock);
}

static void thread_trampoline(void *p)
{
    struct w32_thread_obj *to = (struct w32_thread_obj *)p;
    struct w32_thread *self = w32_self();
    /* adopt the id handed out by CreateThread */
    self->tid = to->tid;
    self->self_obj = &to->o;
    self->boost = to->boost;
    if (self->boost)
        os_cpu_boost(1);
    DWORD code = to->fn(to->arg);
    if (self->boost) {
        os_cpu_boost(0);
        self->boost = 0;
    }
    thread_finish(to, code);
    self->self_obj = NULL;
    w32_obj_unref(&to->o); /* the running thread's reference */
    self_release();
}

extern "C" HANDLE CreateThread(void *sa, SIZE_T stack, LPTHREAD_START_ROUTINE fn, LPVOID arg, DWORD flags,
                               LPDWORD tid_out)
{
    W32_UNUSED(sa);
    struct w32_thread_obj *t =
        (struct w32_thread_obj *)w32_obj_new(W32_THREAD, sizeof(struct w32_thread_obj), thread_destroy);
    if (!t)
        return NULL;
    t->fn = fn;
    t->arg = arg;
    t->tid = __atomic_fetch_add(&g_next_tid, 4, __ATOMIC_RELAXED);
    t->exit_code = STILL_ACTIVE;
    t->cpu = -1;
    /* Windows' default is 1 MiB reserve; never go below 256 KiB (the engine's task threads recurse) */
    size_t s = stack ? (size_t)stack : 1024 * 1024;
    if (s < 256 * 1024)
        s = 256 * 1024;
    int place = fnvnx_thread_role_hook ? fnvnx_thread_role_hook((void *)fn, arg) : -1;
    if (place >= 0 && (place & OS_THREAD_BOOST)) {
        t->boost = 1;
        place &= ~OS_THREAD_BOOST;
    }
    t->os = os_thread_create(thread_trampoline, t, s, OS_PRIO_NORMAL, place);
    if (!t->os) {
        w32_obj_unref(&t->o);
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return NULL;
    }
    w32_obj_ref(&t->o); /* reference held by the running thread */
    if (tid_out)
        *tid_out = t->tid;
    if (flags & CREATE_SUSPENDED) {
        t->suspend_count = 1;
    } else {
        t->started = 1;
        if (os_thread_start(t->os)) {
            w32_obj_unref(&t->o);
            w32_obj_unref(&t->o);
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return NULL;
        }
    }
    return w32_handle_open(&t->o);
}

static struct w32_thread_obj *thread_of(HANDLE h)
{
    if (h == W32_PSEUDO_THREAD) {
        struct w32_thread *self = w32_self();
        return self->self_obj ? (struct w32_thread_obj *)self->self_obj : NULL;
    }
    return (struct w32_thread_obj *)w32_obj_get(h, W32_THREAD);
}

extern "C" DWORD ResumeThread(HANDLE h)
{
    struct w32_thread_obj *t = thread_of(h);
    if (!t)
        return (DWORD)-1;
    os_lock(&w32_wait_lock);
    int prev = t->suspend_count;
    if (prev > 0)
        t->suspend_count--;
    int start = prev == 1 && !t->started;
    if (start)
        t->started = 1;
    if (prev == 1)
        os_cond_broadcast(&t->resumed);   /* a self-suspended thread goes on */
    os_unlock(&w32_wait_lock);
    if (start) {
        if (os_thread_start(t->os))
            return (DWORD)-1;
    } else if (prev == 1) {
        os_thread_resume(t->os);
    }
    return (DWORD)prev;
}

extern "C" DWORD SuspendThread(HANDLE h)
{
    struct w32_thread_obj *t = thread_of(h);
    if (!t)
        return (DWORD)-1;
    os_lock(&w32_wait_lock);
    int prev = t->suspend_count++;
    int running = t->started && !t->done;
    if (running && w32_self()->self_obj == &t->o) {
        /* the calling thread: no kernel pauses itself (Horizon refuses), so it waits until ResumeThread from
         * another thread brings the count back to 0, which is what the caller observes on Windows */
        while (t->suspend_count > 0)
            os_cond_wait(&t->resumed, &w32_wait_lock, OS_WAIT_FOREVER);
        os_unlock(&w32_wait_lock);
        return (DWORD)prev;
    }
    os_unlock(&w32_wait_lock);
    if (prev == 0 && running && os_thread_suspend(t->os)) {
        static int warned;
        if (!warned++)
            w32_log("SuspendThread: this backend cannot pause a running thread");
    }
    return (DWORD)prev;
}

extern "C" BOOL GetExitCodeThread(HANDLE h, LPDWORD code)
{
    struct w32_thread_obj *t = thread_of(h);
    if (!t)
        return FALSE;
    os_lock(&w32_wait_lock);
    *code = t->done ? t->exit_code : STILL_ACTIVE;
    os_unlock(&w32_wait_lock);
    return TRUE;
}

extern "C" void ExitThread(DWORD code)
{
    struct w32_thread *self = w32_self();
    struct w32_thread_obj *to = (struct w32_thread_obj *)self->self_obj;
    if (self->boost) {
        os_cpu_boost(0);
        self->boost = 0;
    }
    if (to) {
        thread_finish(to, code);
        self->self_obj = NULL;
        w32_obj_unref(&to->o);
        self_release();
    }
    os_thread_exit();
}

extern "C" BOOL SetThreadPriority(HANDLE h, int prio)
{
    if (h == W32_PSEUDO_THREAD)
        return os_thread_set_priority(NULL, prio) == 0;
    struct w32_thread_obj *t = thread_of(h);
    if (!t)
        return FALSE;
    t->prio = prio;
    return os_thread_set_priority(t->os, prio) == 0;
}

extern "C" int GetThreadPriority(HANDLE h)
{
    struct w32_thread_obj *t = h == W32_PSEUDO_THREAD ? NULL : thread_of(h);
    return t ? t->prio : THREAD_PRIORITY_NORMAL;
}

extern "C" BOOL SetThreadPriorityBoost(HANDLE h, BOOL disable)
{
    W32_UNUSED(h);
    W32_UNUSED(disable);
    return TRUE;
}

extern "C" DWORD_PTR SetThreadAffinityMask(HANDLE h, DWORD_PTR mask)
{
    DWORD_PTR all = ((DWORD_PTR)1 << os_cpu_count()) - 1;
    if (!(mask & all))
        return 0;
    if (h == W32_PSEUDO_THREAD)
        os_thread_set_affinity(NULL, -1, (unsigned)(mask & all));
    else if (struct w32_thread_obj *t = thread_of(h))
        os_thread_set_affinity(t->os, -1, (unsigned)(mask & all));
    else
        return 0;
    return all; /* previous mask: "all" */
}

extern "C" DWORD SetThreadIdealProcessor(HANDLE h, DWORD cpu)
{
    struct w32_thread_obj *t = h == W32_PSEUDO_THREAD ? NULL : thread_of(h);
    if (h != W32_PSEUDO_THREAD && !t)
        return (DWORD)-1;
    DWORD prev = t && t->cpu >= 0 ? (DWORD)t->cpu : 0;
    if (cpu == 0xFFFFFFFFu || cpu == (DWORD)-1) /* MAXIMUM_PROCESSORS query */
        return prev;
    int c = (int)(cpu % os_cpu_count());
    if (t)
        t->cpu = c;
    os_thread_set_affinity(t ? t->os : NULL, c, 0); /* a preference: the mask stays */
    return prev;
}

extern "C" void Sleep(DWORD ms)
{
    ms = W32_DW(ms);
    if (ms == 0)
        os_yield();
    else if (ms == INFINITE)
        for (;;)
            os_sleep_ns(3600ull * 1000000000ull);
    else
        os_sleep_ns((uint64_t)ms * 1000000ull);
}

extern "C" DWORD SleepEx(DWORD ms, BOOL alertable)
{
    W32_UNUSED(alertable);
    Sleep(ms);
    return 0;
}

extern "C" BOOL SwitchToThread(void)
{
    os_yield();
    return TRUE;
}

/* SetThreadName convention and other first-chance exceptions */
extern "C" void RaiseException(DWORD code, DWORD flags, DWORD nargs, const ULONG_PTR *args)
{
    W32_UNUSED(flags);
    if (W32_DW(code) == MS_VC_EXCEPTION && nargs >= 3 && args) {
        /* THREADNAME_INFO { DWORD dwType = 0x1000; LPCSTR szName; DWORD dwThreadID; DWORD dwFlags; } */
        const char *name = (const char *)(uintptr_t)args[1];
        struct w32_thread *t = w32_self();
        if (name && (W32_DW(args[2]) == 0xFFFFFFFFu || W32_DW(args[2]) == t->tid)) {
            strncpy(t->name, name, sizeof t->name - 1);
            t->name[sizeof t->name - 1] = 0;
        }
        return;
    }
    char msg[96];
    snprintf(msg, sizeof msg, "RaiseException(0x%08lx) - no SEH on this platform", (unsigned long)W32_DW(code));
    os_fatal(msg);
}

extern "C" void RtlUnwind(PVOID frame, PVOID ip, PVOID rec, PVOID ret)
{
    W32_UNUSED(frame);
    W32_UNUSED(ip);
    W32_UNUSED(rec);
    W32_UNUSED(ret);
    os_fatal("RtlUnwind: SEH unwinding is not supported");
}
