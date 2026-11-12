/* sync.cpp - critical sections, Interlocked*, events, mutexes, semaphores and the wait functions.
 *
 * Critical sections live entirely inside the game's 24-byte CRITICAL_SECTION (32-bit SDK layout): the lock
 * word is the first 32 bits of LockSemaphore, OwningThread holds the owner's thread id, RecursionCount the
 * depth. All-zero storage is a valid unlocked section, so sections the game zero-fills without calling
 * InitializeCriticalSection still work. They are recursive like Windows'.
 *
 * Waitable objects share one lock (w32_wait_lock). A waiting thread links itself to every object it waits on
 * and sleeps on its own condition variable; signalling an object wakes exactly the threads linked to it.
 */
#define FNVNX_W32_IMPL 1   /* the out-of-line Interlocked* (fnvnx_win32.h has inline copies for everyone else) */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>
#include <strings.h>

os_lock_t w32_wait_lock;

static_assert(sizeof(void *) != 4 || sizeof(CRITICAL_SECTION) == 24, "CRITICAL_SECTION must keep the 32-bit SDK layout");
static_assert(sizeof(((CRITICAL_SECTION *)0)->LockSemaphore) >= sizeof(os_lock_t), "lock word must fit LockSemaphore");

/* ---- critical sections: the game's own locks, so os_glock/os_gtrylock/os_gunlock (not counted, os.h) */
static inline os_lock_t *cs_lock(LPCRITICAL_SECTION cs) { return (os_lock_t *)(void *)&cs->LockSemaphore; }
static inline HANDLE tid_handle(DWORD tid) { return (HANDLE)(uintptr_t)tid; }

extern "C" void InitializeCriticalSection(LPCRITICAL_SECTION cs)
{
    memset(cs, 0, sizeof *cs);
    cs->LockCount = -1;
}

extern "C" BOOL InitializeCriticalSectionAndSpinCount(LPCRITICAL_SECTION cs, DWORD spin)
{
    InitializeCriticalSection(cs);
    cs->SpinCount = spin & 0x00FFFFFF;
    return TRUE;
}

/* Before sleeping on a held section, spin up to its SpinCount (InitializeCriticalSectionAndSpinCount; capped at
 * W32_CS_SPIN_MAX): a section another core holds for a few hundred cycles is cheaper to wait for than through
 * svcWaitForAddress and a reschedule. Sections without a spin count, and single-core systems, do not spin. */
#define W32_CS_SPIN_MAX 4000u
static inline void cpu_relax(void)
{
#if defined(__aarch64__)
    __asm__ volatile("yield" ::: "memory");
#elif defined(__x86_64__) || defined(__i386__)
    __asm__ volatile("pause" ::: "memory");
#endif
}

static bool cs_spin_acquire(LPCRITICAL_SECTION cs)
{
    DWORD spin = cs->SpinCount & 0x00FFFFFF;
    if (!spin || os_cpu_count() < 2)
        return false;
    if (spin > W32_CS_SPIN_MAX)
        spin = W32_CS_SPIN_MAX;
    os_lock_t *l = cs_lock(cs);
    for (DWORD i = 0; i < spin; i++) {
        if (__atomic_load_n(&l->word, __ATOMIC_SEQ_CST) == 0 && os_gtrylock(l))   /* 0: unlocked */
            return true;
        cpu_relax();
    }
    return false;
}

extern "C" void EnterCriticalSection(LPCRITICAL_SECTION cs)
{
    struct w32_thread *self = w32_self();
    DWORD me = self->tid;
    if (__atomic_load_n(&cs->OwningThread, __ATOMIC_SEQ_CST) == tid_handle(me)) {
        cs->RecursionCount++;
        return;
    }
    if (!os_gtrylock(cs_lock(cs)) && !cs_spin_acquire(cs)) {
        if (__builtin_expect(w32_wg_on, 0)) {   /* the wait-graph watchdog (waitgraph.cpp) */
            w32_wg_cs_begin(self, cs, (uint32_t)(uintptr_t)__builtin_return_address(0));
            os_glock(cs_lock(cs));
            w32_wg_end(self);
        } else {
            os_glock(cs_lock(cs));
        }
    }
    __atomic_store_n(&cs->OwningThread, tid_handle(me), __ATOMIC_SEQ_CST);
    cs->RecursionCount = 1;
    cs->LockCount = 0;
}

extern "C" BOOL TryEnterCriticalSection(LPCRITICAL_SECTION cs)
{
    DWORD me = w32_self()->tid;
    if (__atomic_load_n(&cs->OwningThread, __ATOMIC_SEQ_CST) == tid_handle(me)) {
        cs->RecursionCount++;
        return TRUE;
    }
    if (!os_gtrylock(cs_lock(cs)))
        return FALSE;
    __atomic_store_n(&cs->OwningThread, tid_handle(me), __ATOMIC_SEQ_CST);
    cs->RecursionCount = 1;
    cs->LockCount = 0;
    return TRUE;
}

extern "C" void LeaveCriticalSection(LPCRITICAL_SECTION cs)
{
    if (cs->RecursionCount <= 0) {
        W32Log().s("LeaveCriticalSection on a section that is not held (").p(cs).s(")").emit();
        return;
    }
    if (--cs->RecursionCount == 0) {
        __atomic_store_n(&cs->OwningThread, (HANDLE)0, __ATOMIC_SEQ_CST);
        cs->LockCount = -1;
        os_gunlock(cs_lock(cs));
    }
}

extern "C" void DeleteCriticalSection(LPCRITICAL_SECTION cs) { memset(cs, 0, sizeof *cs); }

/* ---- Interlocked*: full barriers, as on x86. A lock-prefixed instruction orders every earlier access before every
 * later one, store -> load included. A SEQ_CST ldaxr/stlxr loop alone does not: a later plain load can be satisfied
 * before the stlxr (and an earlier plain store) is visible. The trailing dmb ish makes the operation fully ordered,
 * as GCC's __sync builtins and Linux's fully ordered atomics do (S12-ordering, docs/ORDERING.md). */
#define W32_FULL(expr) ({ __typeof__(expr) r_ = (expr); __atomic_thread_fence(__ATOMIC_SEQ_CST); r_; })
/* A lock-prefixed instruction is atomic on a misaligned operand too (a split lock); ldaxr/stlxr fault on one
 * (ATOMIC-UNALIGNED). Such operands go through one global spin lock: atomic against every other Interlocked call,
 * as on x86, and fully ordered. */
static unsigned char g_split_lock;
template <class T, class F> static T w32_split(volatile T *p, F f)
{
    while (__atomic_exchange_n(&g_split_lock, 1, __ATOMIC_SEQ_CST)) {}
    T old;
    memcpy(&old, (const void *)p, sizeof old);
    T nv = f(old);
    memcpy((void *)p, &nv, sizeof nv);
    __atomic_store_n(&g_split_lock, 0, __ATOMIC_SEQ_CST);
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    return old;
}
#define W32_MISALIGNED(p) __builtin_expect(((uintptr_t)(p) & (sizeof(*(p)) - 1)) != 0, 0)

/* misaligned operands of the inline Interlocked* of fnvnx_win32.h */
extern "C" LONG fnvnx_interlocked_slow(int op, volatile LONG *p, LONG a, LONG b)
{
    switch (op) {
    case 0: return w32_split(p, [](LONG v) { return v + 1; }) + 1;
    case 1: return w32_split(p, [](LONG v) { return v - 1; }) - 1;
    case 2: return w32_split(p, [a](LONG) { return a; });
    case 3: return w32_split(p, [a](LONG o) { return o + a; });
    default: return w32_split(p, [a, b](LONG o) { return o == b ? a : o; });
    }
}

extern "C" LONG InterlockedIncrement(volatile LONG *p)
{
    if (W32_MISALIGNED(p)) return w32_split(p, [](LONG v) { return v + 1; }) + 1;
    return W32_FULL(__atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST));
}
extern "C" LONG InterlockedDecrement(volatile LONG *p)
{
    if (W32_MISALIGNED(p)) return w32_split(p, [](LONG v) { return v - 1; }) - 1;
    return W32_FULL(__atomic_sub_fetch(p, 1, __ATOMIC_SEQ_CST));
}
extern "C" LONG InterlockedExchange(volatile LONG *p, LONG v)
{
    if (W32_MISALIGNED(p)) return w32_split(p, [v](LONG) { return v; });
    return W32_FULL(__atomic_exchange_n(p, v, __ATOMIC_SEQ_CST));
}
extern "C" LONG InterlockedExchangeAdd(volatile LONG *p, LONG v)
{
    if (W32_MISALIGNED(p)) return w32_split(p, [v](LONG o) { return o + v; });
    return W32_FULL(__atomic_fetch_add(p, v, __ATOMIC_SEQ_CST));
}

extern "C" LONG InterlockedCompareExchange(volatile LONG *p, LONG xchg, LONG cmp)
{
    if (W32_MISALIGNED(p)) return w32_split(p, [xchg, cmp](LONG o) { return o == cmp ? xchg : o; });
    __atomic_compare_exchange_n(p, &cmp, xchg, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    return cmp; /* the initial value either way */
}

extern "C" PVOID InterlockedCompareExchangePointer(PVOID volatile *p, PVOID xchg, PVOID cmp)
{
    if (W32_MISALIGNED(p)) return w32_split(p, [xchg, cmp](PVOID o) { return o == cmp ? xchg : o; });
    __atomic_compare_exchange_n(p, &cmp, xchg, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    return cmp;
}

extern "C" PVOID InterlockedExchangePointer(PVOID volatile *p, PVOID v)
{
    if (W32_MISALIGNED(p)) return w32_split(p, [v](PVOID) { return v; });
    return W32_FULL(__atomic_exchange_n(p, v, __ATOMIC_SEQ_CST));
}

/* ---- waitable objects */
struct w32_event { struct w32_obj o; int manual, state; char *name; };
struct w32_mutex {
    struct w32_obj o;
    DWORD owner;
    int count;
    int abandoned;                 /* the owner exited without releasing: the next acquire reports WAIT_ABANDONED */
    char *name;
    struct w32_mutex *prev, *next; /* g_mutexes, guarded by w32_wait_lock */
};
static struct w32_mutex *g_mutexes;
struct w32_sem { struct w32_obj o; LONG count, max; char *name; };

/* named objects share one namespace, as in a Windows session */
namespace { struct named { struct named *next; char *name; struct w32_obj *o; }; }   // TU-local type: internal linkage (filemap.cpp has its own)
static struct named *g_named;

static void unname(struct w32_obj *o)
{
    os_lock(&w32_wait_lock);
    for (struct named **p = &g_named; *p; p = &(*p)->next)
        if ((*p)->o == o) {
            struct named *n = *p;
            *p = n->next;
            free(n->name);
            free(n);
            break;
        }
    os_unlock(&w32_wait_lock);
}

static void obj_destroy(struct w32_obj *o) { unname(o); }

static void mutex_destroy(struct w32_obj *o)
{
    struct w32_mutex *m = (struct w32_mutex *)o;
    os_lock(&w32_wait_lock);
    if (m->prev)
        m->prev->next = m->next;
    else if (g_mutexes == m)
        g_mutexes = m->next;
    if (m->next)
        m->next->prev = m->prev;
    os_unlock(&w32_wait_lock);
    unname(o);
}

/* A thread that ends while it owns mutexes abandons them: they become free and the next waiter that acquires one
 * gets WAIT_ABANDONED_0 + i. Called by the thread's exit with w32_wait_lock held. */
void w32_mutexes_abandon_locked(DWORD tid)
{
    for (struct w32_mutex *m = g_mutexes; m; m = m->next)
        if (m->owner == tid && m->count > 0) {
            m->owner = 0;
            m->count = 0;
            m->abandoned = 1;
            w32_obj_signal_locked(&m->o);
        }
}

/* returns an existing object of that name (new reference) or NULL; registers o under name otherwise */
static struct w32_obj *find_named(const char *name, int type)
{
    for (struct named *n = g_named; n; n = n->next)
        if (!strcmp(n->name, name)) {
            if (n->o->type != type)
                return (struct w32_obj *)-1;
            w32_obj_ref(n->o);
            return n->o;
        }
    return NULL;
}

#ifndef ERROR_FILENAME_EXCED_RANGE
#define ERROR_FILENAME_EXCED_RANGE 206
#endif
#ifndef ERROR_PATH_NOT_FOUND
#define ERROR_PATH_NOT_FOUND 3
#endif

/* Namespace key: a name is at most MAX_PATH-1 characters; "Local\\x" is the same object as "x" (the session
 * namespace), "Global\\x" is another; after the prefix a backslash is not allowed. */
static bool key_of(const char *name, char *key /* MAX_PATH + 8 */)
{
    size_t n = strlen(name);
    if (n >= MAX_PATH) {
        SetLastError(ERROR_FILENAME_EXCED_RANGE);
        return false;
    }
    const char *rest = name;
    bool global = false;
    if (!strncasecmp(name, "Local\\", 6))
        rest = name + 6;
    else if (!strncasecmp(name, "Global\\", 7)) {
        rest = name + 7;
        global = true;
    }
    if (!*rest || strchr(rest, '\\')) {
        SetLastError(ERROR_PATH_NOT_FOUND);
        return false;
    }
    snprintf(key, MAX_PATH + 8, "%s%s", global ? "Global\\" : "", rest);   /* Local\x is x; Global\x stays distinct */
    return true;
}

/* takes over the caller's reference to fresh; on failure it is dropped */
static HANDLE create_named(const char *name, int type, struct w32_obj *fresh)
{
    if (!name || !*name) {
        HANDLE h = w32_handle_open(fresh);
        if (h)
            SetLastError(ERROR_SUCCESS);
        return h;
    }
    char key[MAX_PATH + 8];
    if (!key_of(name, key)) {
        w32_obj_unref(fresh);
        return NULL;
    }
    struct named *n = (struct named *)malloc(sizeof *n);   /* before taking the lock: a failure leaves no half state */
    char *copy = strdup(key);
    if (!n || !copy) {
        free(n);
        free(copy);
        w32_obj_unref(fresh);
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return NULL;
    }
    os_lock(&w32_wait_lock);
    struct w32_obj *ex = find_named(key, type);
    if (!ex) {
        n->name = copy;
        n->o = fresh;
        n->next = g_named;
        g_named = n;
    }
    os_unlock(&w32_wait_lock);
    if (ex) {
        free(n);
        free(copy);
    }
    if (ex == (struct w32_obj *)-1) {
        w32_obj_unref(fresh);
        SetLastError(ERROR_INVALID_HANDLE);
        return NULL;
    }
    HANDLE h = w32_handle_open(ex ? ex : fresh);
    if (ex)
        w32_obj_unref(fresh);
    if (h)
        SetLastError(ex ? ERROR_ALREADY_EXISTS : ERROR_SUCCESS);
    return h;
}

extern "C" HANDLE CreateEventA(void *sa, BOOL manual, BOOL initial, LPCSTR name)
{
    W32_UNUSED(sa);
    struct w32_event *e = (struct w32_event *)w32_obj_new(W32_EVENT, sizeof *e, obj_destroy);
    if (!e)
        return NULL;
    e->manual = manual != 0;
    e->state = initial != 0;
    return create_named(name, W32_EVENT, &e->o);
}

extern "C" HANDLE CreateMutexA(void *sa, BOOL owned, LPCSTR name)
{
    W32_UNUSED(sa);
    struct w32_mutex *m = (struct w32_mutex *)w32_obj_new(W32_MUTEX, sizeof *m, mutex_destroy);
    if (!m)
        return NULL;
    os_lock(&w32_wait_lock);
    m->next = g_mutexes;
    if (g_mutexes)
        g_mutexes->prev = m;
    g_mutexes = m;
    os_unlock(&w32_wait_lock);
    if (owned) { /* only takes effect for a new mutex: an existing one is returned unowned */
        m->owner = w32_self()->tid;
        m->count = 1;
    }
    w32_obj_ref(&m->o);
    HANDLE h = create_named(name, W32_MUTEX, &m->o);
    if (h && GetLastError() == ERROR_ALREADY_EXISTS) {
        m->owner = 0;
        m->count = 0;
    }
    w32_obj_unref(&m->o);
    return h;
}

extern "C" HANDLE CreateSemaphoreA(void *sa, LONG initial, LONG max, LPCSTR name)
{
    W32_UNUSED(sa);
    if (max <= 0 || initial < 0 || initial > max) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    struct w32_sem *s = (struct w32_sem *)w32_obj_new(W32_SEMAPHORE, sizeof *s, obj_destroy);
    if (!s)
        return NULL;
    s->count = initial;
    s->max = max;
    return create_named(name, W32_SEMAPHORE, &s->o);
}

void w32_obj_signal_locked(struct w32_obj *o)
{
    o->clk_t = clk_stamp();
    for (struct w32_link *l = o->waiters.next; l != &o->waiters; l = l->next)
        os_cond_signal(&l->t->waiter.cond);
}

extern "C" BOOL SetEvent(HANDLE h)
{
    struct w32_event *e = (struct w32_event *)w32_obj_get(h, W32_EVENT);
    if (!e)
        return FALSE;
    os_lock(&w32_wait_lock);
    e->state = 1;
    w32_obj_signal_locked(&e->o);
    os_unlock(&w32_wait_lock);
    return TRUE;
}

extern "C" BOOL ResetEvent(HANDLE h)
{
    struct w32_event *e = (struct w32_event *)w32_obj_get(h, W32_EVENT);
    if (!e)
        return FALSE;
    os_lock(&w32_wait_lock);
    e->state = 0;
    os_unlock(&w32_wait_lock);
    return TRUE;
}

DWORD w32_wg_mutex_owner_locked(struct w32_obj *o)
{
    struct w32_mutex *m = (struct w32_mutex *)o;
    return m->count > 0 ? m->owner : 0;
}

extern "C" BOOL ReleaseMutex(HANDLE h)
{
    struct w32_mutex *m = (struct w32_mutex *)w32_obj_get(h, W32_MUTEX);
    if (!m)
        return FALSE;
    DWORD me = w32_self()->tid;
    os_lock(&w32_wait_lock);
    if (m->owner != me || m->count <= 0) {
        os_unlock(&w32_wait_lock);
        SetLastError(ERROR_NOT_OWNER);
        return FALSE;
    }
    if (--m->count == 0) {
        m->owner = 0;
        w32_obj_signal_locked(&m->o);
    }
    os_unlock(&w32_wait_lock);
    return TRUE;
}

extern "C" BOOL ReleaseSemaphore(HANDLE h, LONG n, LPLONG prev)
{
    struct w32_sem *s = (struct w32_sem *)w32_obj_get(h, W32_SEMAPHORE);
    if (!s)
        return FALSE;
    if (n <= 0) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    os_lock(&w32_wait_lock);
    if (s->count > s->max - n) {
        os_unlock(&w32_wait_lock);
        SetLastError(ERROR_TOO_MANY_POSTS);
        return FALSE;
    }
    if (prev)
        *prev = s->count;
    s->count += n;
    w32_obj_signal_locked(&s->o);
    os_unlock(&w32_wait_lock);
    return TRUE;
}

static int signaled(struct w32_obj *o, DWORD me)
{
    switch (o->type) {
    case W32_EVENT: return ((struct w32_event *)o)->state;
    case W32_MUTEX: { struct w32_mutex *m = (struct w32_mutex *)o; return m->owner == 0 || m->owner == me; }
    case W32_SEMAPHORE: return ((struct w32_sem *)o)->count > 0;
    case W32_THREAD: return w32_thread_signaled(o);
    case W32_FILE: return 1; /* I/O is synchronous: a file handle is always signaled */
    default: return 0;      /* W32_PSEUDO: the running process / thread; never signaled */
    }
}

/* takes the object; returns 1 if it was an abandoned mutex (WAIT_ABANDONED_0 + i) */
static int acquire(struct w32_obj *o, DWORD me)
{
    switch (o->type) {
    case W32_EVENT: if (!((struct w32_event *)o)->manual) ((struct w32_event *)o)->state = 0; break;
    case W32_MUTEX: {
        struct w32_mutex *m = (struct w32_mutex *)o;
        int ab = m->abandoned;
        m->abandoned = 0;
        m->owner = me;
        m->count++;
        return ab;
    }
    case W32_SEMAPHORE: ((struct w32_sem *)o)->count--; break;
    default: break;
    }
    return 0;
}

static DWORD wait_multiple(DWORD n, const HANDLE *hs, BOOL all, DWORD ms, uint32_t pc);

/* The pseudo handles GetCurrentProcess() / GetCurrentThread() are waitable (SYNCHRONIZE) and not signaled while
 * they run; there are no standard handles (F5). One static, never-freed object stands for the
 * unsignaled pseudo handles; the others are rejected by type (W32_FIND, W32_HEAP carry no SYNCHRONIZE access). */
#define W32_PSEUDO_RUNNING 100
static struct w32_obj g_running_obj = { W32_MAGIC, W32_PSEUDO_RUNNING, 1 << 30, { &g_running_obj.waiters, &g_running_obj.waiters, NULL }, NULL };

static struct w32_obj *wait_target(HANDLE h)
{
    if (h == W32_PSEUDO_PROCESS || h == W32_PSEUDO_THREAD) {
        w32_obj_ref(&g_running_obj);
        return &g_running_obj;
    }
    struct w32_obj *o = w32_obj_get_ref(h, 0);
    if (o && o->type != W32_EVENT && o->type != W32_MUTEX && o->type != W32_SEMAPHORE && o->type != W32_THREAD &&
        o->type != W32_FILE) {
        w32_obj_unref(o);
        SetLastError(ERROR_INVALID_HANDLE);
        return NULL;
    }
    return o;
}

/* The loading-screen thread (OS_THREAD_BOOST) waits without a timeout only when the loading screen is done
 * (LoadingMenuThread::ThreadProc parks on its semaphore): the CPU boost is off for such a wait.
 * pc: the game's call site, for the wait-graph watchdog. */
static DWORD wait_entry(DWORD n, const HANDLE *hs, BOOL all, DWORD ms, uint32_t pc)
{
    struct w32_thread *self = w32_self();
    int unboost = self->boost && W32_DW(ms) == INFINITE;
    if (unboost)
        os_cpu_boost(0);
    DWORD r = wait_multiple(n, hs, all, ms, pc);
    if (unboost)
        os_cpu_boost(1);
    return r;
}

extern "C" DWORD WaitForMultipleObjects(DWORD n, const HANDLE *hs, BOOL all, DWORD ms)
{
    return wait_entry(n, hs, all, ms, (uint32_t)(uintptr_t)__builtin_return_address(0));
}

static DWORD wait_multiple(DWORD n, const HANDLE *hs, BOOL all, DWORD ms, uint32_t pc)
{
    n = W32_DW(n);
    ms = W32_DW(ms);
    if (n == 0 || n > MAXIMUM_WAIT_OBJECTS || !hs) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return WAIT_FAILED;
    }
    if (all)   /* the same handle twice cannot be acquired twice */
        for (DWORD i = 1; i < n; i++)
            for (DWORD j = 0; j < i; j++)
                if (hs[i] == hs[j]) {
                    SetLastError(ERROR_INVALID_PARAMETER);
                    return WAIT_FAILED;
                }
    struct w32_thread *self = w32_self();
    struct w32_obj *objs[MAXIMUM_WAIT_OBJECTS];
    struct w32_link links[MAXIMUM_WAIT_OBJECTS];
    for (DWORD i = 0; i < n; i++) {
        objs[i] = wait_target(hs[i]);   /* referenced under the handle lock: a CloseHandle cannot free it */
        if (!objs[i]) {
            for (DWORD j = 0; j < i; j++)
                w32_obj_unref(objs[j]);
            return WAIT_FAILED;
        }
    }
    uint64_t deadline = ms == INFINITE ? 0 : clk_rt_deadline((uint64_t)ms * 1000000ull);
    uint64_t sig_t = 0;            /* the latest signal time of what was acquired (fnvnx_clock.h: the wait takes it) */
    int idled = 0;
    DWORD res = WAIT_TIMEOUT;
    int wg = __builtin_expect(w32_wg_on, 0) && ms != 0, recorded = 0;
    if (wg)
        w32_wg_prepare(self);
    os_lock(&w32_wait_lock);
    for (;;) {
        if (all) {
            DWORD k = 0;
            while (k < n && signaled(objs[k], self->tid))
                k++;
            if (k == n) {
                res = WAIT_OBJECT_0;
                for (DWORD i = 0; i < n; i++) {
                    if (acquire(objs[i], self->tid) && res == WAIT_OBJECT_0)
                        res = WAIT_ABANDONED + i;
                    if (objs[i]->clk_t > sig_t) sig_t = objs[i]->clk_t;
                }
                break;
            }
        } else {
            DWORD k = 0;
            while (k < n && !signaled(objs[k], self->tid))
                k++;
            if (k < n) {
                res = (acquire(objs[k], self->tid) ? WAIT_ABANDONED : WAIT_OBJECT_0) + k;
                sig_t = objs[k]->clk_t;
                break;
            }
        }
        uint64_t left = OS_WAIT_FOREVER;
        if (ms != INFINITE) {
            if (ms == 0 || !clk_rt_left(deadline, &left))
                break;
        }
        if (!idled) {   /* about to block: virtual time may have work for this wait (the audio device), outside the lock */
            idled = 1;
            os_unlock(&w32_wait_lock);
            int worked = clk_wait_idle((const void *const *)hs, n, ms == INFINITE ? UINT64_MAX : (uint64_t)ms * 1000000ull);
            os_lock(&w32_wait_lock);
            if (worked) idled = 0;
            continue;
        }
        idled = 0;
        if (wg && !recorded) {   /* recorded and cleared under w32_wait_lock while objs[] are referenced */
            w32_wg_obj_begin(self, objs, n, all, ms, pc);
            recorded = 1;
        }
        for (DWORD i = 0; i < n; i++) {
            links[i].t = self;
            links[i].next = objs[i]->waiters.next;
            links[i].prev = &objs[i]->waiters;
            objs[i]->waiters.next->prev = &links[i];
            objs[i]->waiters.next = &links[i];
        }
        clk_rt_cond_timedwait(&self->waiter.cond, &w32_wait_lock, left);
        for (DWORD i = 0; i < n; i++) {
            links[i].prev->next = links[i].next;
            links[i].next->prev = links[i].prev;
        }
    }
    if (recorded)
        w32_wg_end(self);
    os_unlock(&w32_wait_lock);
    for (DWORD i = 0; i < n; i++)
        w32_obj_unref(objs[i]);
    if (res == WAIT_TIMEOUT)
        clk_wait_timeout(ms == INFINITE ? 0 : (uint64_t)ms * 1000000ull);   /* the thread's time passes by the timeout */
    else
        clk_merge(sig_t);                                                    /* a success takes the signaller's time */
    return res;
}

extern "C" DWORD WaitForSingleObject(HANDLE h, DWORD ms)
{
    return wait_entry(1, &h, FALSE, ms, (uint32_t)(uintptr_t)__builtin_return_address(0));
}
