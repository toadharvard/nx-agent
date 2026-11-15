/* thread.cpp - threads, thread ids, last error, TlsAlloc slots, the image's implicit TLS and the fake TEB
 * behind __readfsdword. */
#include "w32.h"
#include "fnvnx_boundary.h"
#include "fnvl_host.h"

#include <stdio.h>
#include <stdlib.h>

/* ---- per-thread block */
static volatile DWORD g_next_tid = 0x1000;

/* The block lives in the thread's own TLS storage: it exists for the life of the thread, needs no allocation and so
 * cannot fail. g_self_live separates "no block yet" (the tid is assigned at first use) from a released block. */
/* The dispatch word (declared in shim/include/fnvl_host.h): one per host thread, zero from the thread's creation (a __thread object is
 * zero-initialised for every thread), initial-exec so the access is a TP-relative load/store with no call. */
extern "C" { __thread unsigned int fnvnx_call_fp __attribute__((tls_model("initial-exec"))); }

static __thread struct w32_thread g_self;
static __thread int g_self_live;

struct w32_thread *w32_self(void)
{
    if (!g_self_live) {
        g_self.tid = __atomic_fetch_add(&g_next_tid, 4, __ATOMIC_RELAXED); /* multiples of 4, like Windows */
        g_self_live = 1;
    }
    return &g_self;
}

/* releases the calling thread's block (threads created by CreateThread, when they end) */
static void self_release(void)
{
    if (!g_self_live)
        return;
    struct w32_thread *t = &g_self;
    w32_wg_forget(t);
    free(t->tls_ext);
    if (!t->implicit_tls_borrowed)
        free(t->implicit_tls[0]);
    memset(t, 0, sizeof *t);
    g_self_live = 0;
}

extern "C" DWORD GetLastError(void) { return w32_self()->last_error; }
extern "C" void SetLastError(DWORD e) { w32_self()->last_error = e; }
extern "C" DWORD GetCurrentThreadId(void) { return w32_self()->tid; }
extern "C" DWORD GetCurrentProcessId(void) { return os_process_id(); }

/* ---- TlsAlloc: a global bitmap of 1088 slots, values in the per-thread block.
 * Zero-initialisation in every thread without stopping any: each slot has a global generation, bumped by every
 * TlsAlloc of it; a thread's cell for the slot is valid only while the thread's recorded generation (kept in
 * tls_ext after the values) equals the global one, otherwise the slot reads as 0 and the first TlsSetValue
 * re-zeroes and stamps it. Fresh threads record 0, and generations of allocated slots are >= 1. */
static os_lock_t g_tls_lock;
static uint32_t g_tls_used[W32_TLS_SLOTS / 32];
static uint32_t g_tls_gen[W32_TLS_SLOTS];
#define TLS_OUT_OF_INDEXES_ ((DWORD)0xFFFFFFFF)
#ifndef ERROR_NOACCESS
#define ERROR_NOACCESS 998L
#endif
#define TLS_EXT_VALUES (W32_TLS_SLOTS - 64)

static bool tls_is_used(DWORD i)
{
    return (__atomic_load_n(&g_tls_used[i / 32], __ATOMIC_ACQUIRE) >> (i % 32)) & 1u;
}

/* the calling thread's cell for allocated slot i; NULL: reads as 0 (not stamped yet, or no memory when creating) */
static void **tls_cell(struct w32_thread *t, DWORD i, bool create)
{
    if (!t->tls_ext) {
        if (!create || !(t->tls_ext = (void **)calloc(TLS_EXT_VALUES + W32_TLS_SLOTS, sizeof(void *))))
            return NULL;
    }
    void **cell = i < 64 ? &t->tls[i] : &t->tls_ext[i - 64];
    uintptr_t *stamp = (uintptr_t *)&t->tls_ext[TLS_EXT_VALUES + i];
    uint32_t g = __atomic_load_n(&g_tls_gen[i], __ATOMIC_ACQUIRE);
    if (*stamp != g) {
        if (!create)
            return NULL;
        *cell = NULL;
        *stamp = g;
    }
    return cell;
}

extern "C" DWORD TlsAlloc(void)
{
    os_lock(&g_tls_lock);
    for (DWORD i = 0; i < W32_TLS_SLOTS; i++)
        if (!(g_tls_used[i / 32] & (1u << (i % 32)))) {
            __atomic_add_fetch(&g_tls_gen[i], 1, __ATOMIC_RELEASE);
            __atomic_or_fetch(&g_tls_used[i / 32], 1u << (i % 32), __ATOMIC_RELEASE);
            os_unlock(&g_tls_lock);
            return i;
        }
    os_unlock(&g_tls_lock);
    SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    return TLS_OUT_OF_INDEXES_;
}

extern "C" BOOL TlsFree(DWORD i)
{
    bool was_used = false;
    if (i < W32_TLS_SLOTS) {
        os_lock(&g_tls_lock);
        was_used = tls_is_used(i);
        __atomic_and_fetch(&g_tls_used[i / 32], ~(1u << (i % 32)), __ATOMIC_RELEASE);
        os_unlock(&g_tls_lock);
    }
    if (!was_used) {   /* out of range, or a slot that is not allocated */
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    return TRUE;
}

extern "C" LPVOID TlsGetValue(DWORD i)
{
    struct w32_thread *t = w32_self();
    if (i >= W32_TLS_SLOTS) {
        t->last_error = ERROR_INVALID_PARAMETER;
        return NULL;
    }
    t->last_error = ERROR_SUCCESS;
    if (!tls_is_used(i))
        return NULL;
    void **cell = tls_cell(t, i, false);
    return cell ? *cell : NULL;
}

extern "C" BOOL TlsSetValue(DWORD i, LPVOID v)
{
    struct w32_thread *t = w32_self();
    if (i >= W32_TLS_SLOTS || !tls_is_used(i)) {
        t->last_error = ERROR_INVALID_PARAMETER;
        return FALSE;
    }
    void **cell = tls_cell(t, i, true);
    if (!cell) {
        t->last_error = ERROR_NOT_ENOUGH_MEMORY;
        return FALSE;
    }
    *cell = v;
    return TRUE;
}

/* ---- implicit TLS of the image: __declspec(thread) data of FalloutNV.exe is reached as
 * ((char**)__readfsdword(0x2c))[_tls_index] + offset. The loader (S3) passes the .tls template; every thread
 * gets a copy on first access. _tls_index lives in the data image; the loader must store 0 there. */
extern "C" { void *(*fnvnx_image_tls_hook)(void) = nullptr; }

static const void *g_tls_init;
static size_t g_tls_init_size, g_tls_total;

/* The block of the calling thread (the main thread: the one that sets the template). FALSE and ERROR_NOT_ENOUGH_MEMORY
 * when the block cannot be allocated. A thread that CreateThread starts gets its block from CreateThread. */
static void *tls_block_new(void)
{
    if (!g_tls_total)
        return NULL;
    void *b = calloc(1, g_tls_total);
    if (b && g_tls_init_size)
        memcpy(b, g_tls_init, g_tls_init_size);
    return b;
}

extern "C" BOOL fnvnx_tls_set_template(const void *init_data, size_t init_size, size_t total_size)
{
    g_tls_init = init_data;
    g_tls_init_size = init_size;
    g_tls_total = total_size < init_size ? init_size : total_size;
    struct w32_thread *t = w32_self();
    if (t->implicit_tls[0] || fnvnx_image_tls_hook)
        return TRUE;
    t->implicit_tls[0] = tls_block_new();
    if (!t->implicit_tls[0] && g_tls_total) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return FALSE;
    }
    return TRUE;
}

/* Set by the game layer (toolchain/game/boot.cpp): the calling thread's native block of the image's TLS
 * (__fnvnx_tls_image, where S2 binds every __declspec(thread) variable). fs:[0x2c][0] then is that same block,
 * so an explicit __readfsdword(0x2c) access and a __declspec(thread) access see one variable, not two copies. */

extern "C" void *fnvnx_tls_block(void)
{
    struct w32_thread *t = w32_self();
    if (!t->implicit_tls[0] && fnvnx_image_tls_hook) {
        t->implicit_tls[0] = fnvnx_image_tls_hook();
        t->implicit_tls_borrowed = 1;
    }
    return t->implicit_tls[0];
}

extern "C" void **fnvnx_tls_array(void)
{
    fnvnx_tls_block();
    return w32_self()->implicit_tls;
}

/* ---- the TEB (x86 fs: segment) for the decomp's explicit __readfsdword reads. The TEB is a 0x1000-byte block; the
 * fields this layer maintains are served from the thread block, every other field holds zero (the layer gives them no
 * meaning: no debugger, no GDI batch, no OLE, no stack-limit checks), as in a thread that never set them. A read
 * past the block is an access violation. The read is composed bytewise so an unaligned offset sees the same image. */
static unsigned char g_fake_peb[0x1000];

static uint32_t teb_field(struct w32_thread *t, unsigned off)
{
    switch (off) {
    case 0x00: return 0xFFFFFFFFul;                               /* SEH chain: end of list */
    case 0x18: return (uint32_t)(uintptr_t)t;                     /* NT_TIB.Self (opaque) */
    case 0x20: return os_process_id();                            /* ClientId.UniqueProcess */
    case 0x24: return t->tid;                                     /* ClientId.UniqueThread */
    case 0x2c: return (uint32_t)(uintptr_t)fnvnx_tls_array();     /* ThreadLocalStoragePointer */
    case 0x30: return (uint32_t)(uintptr_t)g_fake_peb;            /* PEB (all zero: BeingDebugged = 0) */
    case 0x34: return t->last_error;                              /* LastErrorValue */
    default:
        if (off >= 0xE10 && off < 0xE10 + 64 * 4) {               /* TlsSlots[64] */
            DWORD i = (off - 0xE10) / 4;
            void **cell = tls_is_used(i) ? tls_cell(t, i, false) : NULL;
            return (uint32_t)(uintptr_t)(cell ? *cell : NULL);
        }
        return 0;
    }
}

extern "C" unsigned long __readfsdword(unsigned long off)
{
    if (off > 0x1000 - 4) {
        w32_raise(STATUS_ACCESS_VIOLATION, 0, 0, NULL);
        return 0;
    }
    struct w32_thread *t = w32_self();
    uint32_t v = 0;
    for (unsigned i = 0; i < 4; i++) {
        unsigned o = (unsigned)off + i;
        v |= ((teb_field(t, o & ~3u) >> (8 * (o & 3))) & 0xFF) << (8 * i);
    }
    return v;
}

/* ---- thread objects */
#define STACK_SIZE_PARAM_IS_A_RESERVATION_ 0x00010000

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
    uint64_t clk_parent;           /* the creator's clock stamp at CreateThread (fnvnx_clock.h) */
    void *tls_block;               /* the image's TLS block of the new thread, until the thread adopts it */
    fnvnx_bnd_thread *gt;          /* the game thread context (fnvnx_boundary.h); NULL once released */
    os_cond_t resumed;             /* a thread that suspended itself waits here until its count is 0 */
};

int w32_thread_signaled(struct w32_obj *o) { return ((struct w32_thread_obj *)o)->done; }
DWORD w32_thread_obj_tid(struct w32_obj *o) { return ((struct w32_thread_obj *)o)->tid; }

static void thread_destroy(struct w32_obj *o)
{
    struct w32_thread_obj *t = (struct w32_thread_obj *)o;
    os_thread_free(t->os); /* detaches a thread that still runs */
    free(t->tls_block);
    if (t->gt)   /* never ran (closed while suspended, or never started) */
        fnvnx_bnd_thread_release(t->gt, 0);
}

static void thread_finish(struct w32_thread_obj *to, DWORD code)
{
    os_lock(&w32_wait_lock);
    to->exit_code = code;
    to->done = 1;
    w32_mutexes_abandon_locked(to->tid);
    w32_obj_signal_locked(&to->o);
    os_unlock(&w32_wait_lock);
}

/* the game thread is over (its start routine returned, or ExitThread): the modules hear of it and the context goes back */
static void thread_end(struct w32_thread_obj *to)
{
    fnvnx_bnd_thread *g = to->gt;
    to->gt = NULL;
    if (g)
        fnvnx_bnd_thread_release(g, 1);
}

static void thread_trampoline(void *p)
{
    struct w32_thread_obj *to = (struct w32_thread_obj *)p;
    struct w32_thread *self = w32_self();
    /* adopt the id handed out by CreateThread */
    clk_thread_start(to->clk_parent);
    self->tid = to->tid;
    self->self_obj = &to->o;
    if (to->tls_block) {
        self->implicit_tls[0] = to->tls_block;
        to->tls_block = NULL;
    }
    self->boost = to->boost;
    if (self->boost)
        os_cpu_boost(1);
    DWORD code = W32_DW(fnvnx_bnd_thread_run(to->gt, (fnvnx_gaddr)(uintptr_t)to->arg));
    w32_fls_thread_exit();   /* before the modules hear of the detach, as the reference does */
    thread_end(to);
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
    if (flags & ~(DWORD)(CREATE_SUSPENDED | STACK_SIZE_PARAM_IS_A_RESERVATION_)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    struct w32_thread_obj *t =
        (struct w32_thread_obj *)w32_obj_new(W32_THREAD, sizeof(struct w32_thread_obj), thread_destroy);
    if (!t)
        return NULL;
    t->fn = fn;
    t->arg = arg;
    t->tid = __atomic_fetch_add(&g_next_tid, 4, __ATOMIC_RELAXED);
    t->exit_code = STILL_ACTIVE;
    t->cpu = -1;
    t->clk_parent = clk_stamp();
    if (!fnvnx_image_tls_hook && g_tls_total) {
        t->tls_block = tls_block_new();
        if (!t->tls_block) {
            w32_obj_unref(&t->o);
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return NULL;
        }
    }
    /* Windows' default is 1 MiB reserve; never go below 256 KiB (the engine's task threads recurse) */
    size_t s = stack ? (size_t)stack : 1024 * 1024;
    if (s < 256 * 1024)
        s = 256 * 1024;
    t->gt = fnvnx_bnd_thread_create((fnvnx_gaddr)(uintptr_t)fn, s);
    if (!t->gt) {
        w32_obj_unref(&t->o);
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return NULL;
    }
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

/* handle -> thread object. NULL with *bad == 0: the pseudo handle of a thread CreateThread did not start (the
 * main thread); NULL with *bad == 1: invalid handle (ERROR_INVALID_HANDLE set) */
static struct w32_thread_obj *thread_resolve(HANDLE h, int *bad)
{
    *bad = 0;
    if (h == W32_PSEUDO_THREAD)
        return (struct w32_thread_obj *)w32_self()->self_obj;
    struct w32_thread_obj *t = (struct w32_thread_obj *)w32_obj_get(h, W32_THREAD);
    *bad = t == NULL;
    return t;
}
static struct w32_thread_obj *thread_of(HANDLE h)
{
    int bad;
    return thread_resolve(h, &bad);
}

extern "C" DWORD ResumeThread(HANDLE h)
{
    int bad;
    struct w32_thread_obj *t = thread_resolve(h, &bad);
    if (!t)
        return bad ? (DWORD)-1 : 0;   /* the main thread is never suspended: previous count 0 */
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
    int bad;
    struct w32_thread_obj *t = thread_resolve(h, &bad);
    if (!t)
        return bad ? (DWORD)-1 : 0;   /* the main thread: no suspension state, previous count 0 */
    os_lock(&w32_wait_lock);
    int prev = t->suspend_count++;
    int running = t->started && !t->done;
    if (running && w32_self()->self_obj == &t->o) {
        /* the calling thread: no kernel pauses itself (Horizon refuses), so it waits until ResumeThread from
         * another thread brings the count back to 0, which is what the caller observes on Windows */
        while (t->suspend_count > 0)
            os_cond_wait(&t->resumed, &w32_wait_lock);
        os_unlock(&w32_wait_lock);
        return (DWORD)prev;
    }
    os_unlock(&w32_wait_lock);
    if (prev == 0 && running && os_thread_suspend(t->os)) {
        static int warned;
        if (!warned++)
            W32Log().s("SuspendThread: this backend cannot pause a running thread").emit();
    }
    return (DWORD)prev;
}

extern "C" BOOL GetExitCodeThread(HANDLE h, LPDWORD code)
{
    int bad;
    struct w32_thread_obj *t = thread_resolve(h, &bad);
    if (bad)
        return FALSE;                    /* ERROR_INVALID_HANDLE set by the lookup */
    if (!code) {                         /* Windows probes the output in the kernel */
        SetLastError(ERROR_NOACCESS);
        return FALSE;
    }
    if (!t) {   /* a thread the game did not start (the main thread): running */
        *code = STILL_ACTIVE;
        return TRUE;
    }
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
    w32_fls_thread_exit();
    if (to) {
        thread_end(to);
        thread_finish(to, code);
        self->self_obj = NULL;
        w32_obj_unref(&to->o);
        self_release();
    }
    os_thread_exit();
}

/* priority/ideal processor of threads CreateThread did not start (per host thread) */
static __thread int g_main_prio;
static __thread int g_main_cpu = -1;

extern "C" BOOL SetThreadPriority(HANDLE h, int prio)
{
    int bad;
    struct w32_thread_obj *t = thread_resolve(h, &bad);
    if (bad)
        return FALSE;
    if (!(prio == -15 || (prio >= -2 && prio <= 2) || prio == 15)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    if (os_thread_set_priority(t ? t->os : NULL, prio)) {
        SetLastError(ERROR_ACCESS_DENIED);
        return FALSE;
    }
    if (t)
        t->prio = prio;
    else
        g_main_prio = prio;
    return TRUE;
}

extern "C" int GetThreadPriority(HANDLE h)
{
    int bad;
    struct w32_thread_obj *t = thread_resolve(h, &bad);
    if (bad)
        return 0x7FFFFFFF;   /* THREAD_PRIORITY_ERROR_RETURN */
    return t ? t->prio : g_main_prio;
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
    int bad;
    struct w32_thread_obj *t = thread_resolve(h, &bad);
    if (bad)
        return (DWORD)-1;
    int cur = t ? t->cpu : g_main_cpu;
    DWORD prev = cur >= 0 ? (DWORD)cur : 0;
    if (cpu == 32)   /* MAXIMUM_PROCESSORS: a query */
        return prev;
    if (cpu >= (DWORD)os_cpu_count()) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return (DWORD)-1;
    }
    if (t)
        t->cpu = (int)cpu;
    else
        g_main_cpu = (int)cpu;
    os_thread_set_affinity(t ? t->os : NULL, (int)cpu, 0); /* a preference: the mask stays */
    return prev;
}

extern "C" void Sleep(DWORD ms)
{
    ms = W32_DW(ms);
    if (ms == INFINITE)
        for (;;)
            clk_rt_sleep_ns(3600ull * 1000000000ull);   /* never returns: no time passes for the thread */
    else
        clk_sleep_ns((uint64_t)ms * 1000000ull);   /* Sleep(0) yields; the virtual clock advances by ms (0: one quantum) */
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

/* Exceptions raised by platform code. The lifter runtime installs the SEH dispatcher over the guest's fs:[0]
 * chain as the backend. Without one no guest frame exists, so no handler does: the documented sequence of a
 * process without a handler - the top-level filter of SetUnhandledExceptionFilter decides; CONTINUE_EXECUTION
 * returns to the raiser if the exception is continuable (else STATUS_NONCONTINUABLE_EXCEPTION is raised, which is
 * unhandled in turn); otherwise the process ends with the exception code as its exit status. */
#define W32_EXCEPTION_NONCONTINUABLE 1u
#define W32_EXCEPTION_MAXIMUM_PARAMETERS 15u
#define W32_STATUS_NONCONTINUABLE_EXCEPTION 0xC0000025u

static fnvnx_raise_fn g_raise_backend;

extern "C" void fnvnx_win32_set_raise_backend(fnvnx_raise_fn fn) { __atomic_store_n(&g_raise_backend, fn, __ATOMIC_SEQ_CST); }

struct w32_exception_record {
    DWORD ExceptionCode, ExceptionFlags;
    struct w32_exception_record *ExceptionRecord;
    PVOID ExceptionAddress;
    DWORD NumberParameters;
    ULONG_PTR ExceptionInformation[W32_EXCEPTION_MAXIMUM_PARAMETERS];
};
struct w32_exception_pointers { struct w32_exception_record *rec; void *ctx; };

/* one console line per unhandled exception: what was raised, where (the address in the exe or a module) and on which
 * thread - the exit code alone does not say */
void w32_log_unhandled(const void *ep_, const char *tail)
{
    const struct w32_exception_pointers *ep = (const struct w32_exception_pointers *)ep_;
    W32Log log;
    log.s("UnhandledExceptionFilter: thread ").u((unsigned)GetCurrentThreadId());
    if (ep && ep->rec) {
        const struct w32_exception_record *r = ep->rec;
        log.s(" code ").h(W32_DW(r->ExceptionCode)).s(" flags ").h(W32_DW(r->ExceptionFlags)).s(" address ").p(r->ExceptionAddress);
        DWORD np = W32_DW(r->NumberParameters) < W32_EXCEPTION_MAXIMUM_PARAMETERS ? W32_DW(r->NumberParameters) : W32_EXCEPTION_MAXIMUM_PARAMETERS;
        for (DWORD i = 0; i < np; i++)
            log.s(i ? ", " : " info ").h((uintptr_t)r->ExceptionInformation[i]);
    } else {
        log.s(" (no exception record)");
    }
    log.s(tail).emit();
}

extern "C" void w32_raise(DWORD code, DWORD flags, DWORD nargs, const ULONG_PTR *args)
{
    flags = W32_DW(flags) & W32_EXCEPTION_NONCONTINUABLE;
    nargs = W32_DW(nargs);
    if (!args || nargs > W32_EXCEPTION_MAXIMUM_PARAMETERS)
        nargs = args ? W32_EXCEPTION_MAXIMUM_PARAMETERS : 0;
    fnvnx_raise_fn be = __atomic_load_n(&g_raise_backend, __ATOMIC_SEQ_CST);
    if (be) {
        uintptr_t a[W32_EXCEPTION_MAXIMUM_PARAMETERS];
        for (DWORD i = 0; i < nargs; i++)
            a[i] = (uintptr_t)args[i];
        be((uint32_t)W32_DW(code), (uint32_t)flags, (uint32_t)nargs, a);
        return;
    }
    struct w32_exception_record rec;
    memset(&rec, 0, sizeof rec);
    rec.ExceptionCode = W32_DW(code);
    rec.ExceptionFlags = flags;
    rec.ExceptionAddress = __builtin_return_address(0);
    rec.NumberParameters = nargs;
    for (DWORD i = 0; i < nargs; i++)
        rec.ExceptionInformation[i] = args[i];
    struct w32_exception_record outer;
    for (int round = 0;; round++) {
        unsigned char context[1024] = { 0 };   /* no guest registers exist here: an empty context */
        struct w32_exception_pointers ep = { &rec, context };
        LONG r = UnhandledExceptionFilter((struct _EXCEPTION_POINTERS *)&ep);
        if (r != (LONG)-1) /* not EXCEPTION_CONTINUE_EXECUTION */
            ExitProcess((UINT)W32_DW(rec.ExceptionCode));
        if (!(rec.ExceptionFlags & W32_EXCEPTION_NONCONTINUABLE))
            return;
        if (round) /* the filter continued STATUS_NONCONTINUABLE_EXCEPTION itself: nothing is left to resume */
            ExitProcess((UINT)W32_DW(rec.ExceptionCode));
        outer = rec;
        memset(&rec, 0, sizeof rec);
        rec.ExceptionCode = W32_STATUS_NONCONTINUABLE_EXCEPTION;
        rec.ExceptionFlags = W32_EXCEPTION_NONCONTINUABLE;
        rec.ExceptionRecord = &outer;
        rec.ExceptionAddress = outer.ExceptionAddress;
    }
}

extern "C" void RaiseException(DWORD code, DWORD flags, DWORD nargs, const ULONG_PTR *args) { w32_raise(code, flags, nargs, args); }
