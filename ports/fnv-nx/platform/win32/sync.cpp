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
#include "w32.h"

#include <stdlib.h>

os_lock_t w32_wait_lock;

static_assert(sizeof(void *) != 4 || sizeof(CRITICAL_SECTION) == 24, "CRITICAL_SECTION must keep the 32-bit SDK layout");
static_assert(sizeof(((CRITICAL_SECTION *)0)->LockSemaphore) >= sizeof(os_lock_t), "lock word must fit LockSemaphore");

/* ---- critical sections */
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
        if (__atomic_load_n(&l->word, __ATOMIC_RELAXED) == 0 && os_trylock(l))   /* 0: unlocked */
            return true;
        cpu_relax();
    }
    return false;
}

extern "C" void EnterCriticalSection(LPCRITICAL_SECTION cs)
{
    DWORD me = w32_self()->tid;
    if (__atomic_load_n(&cs->OwningThread, __ATOMIC_RELAXED) == tid_handle(me)) {
        cs->RecursionCount++;
        return;
    }
    if (!os_trylock(cs_lock(cs)) && !cs_spin_acquire(cs))
        os_lock(cs_lock(cs));
    __atomic_store_n(&cs->OwningThread, tid_handle(me), __ATOMIC_RELAXED);
    cs->RecursionCount = 1;
    cs->LockCount = 0;
}

extern "C" BOOL TryEnterCriticalSection(LPCRITICAL_SECTION cs)
{
    DWORD me = w32_self()->tid;
    if (__atomic_load_n(&cs->OwningThread, __ATOMIC_RELAXED) == tid_handle(me)) {
        cs->RecursionCount++;
        return TRUE;
    }
    if (!os_trylock(cs_lock(cs)))
        return FALSE;
    __atomic_store_n(&cs->OwningThread, tid_handle(me), __ATOMIC_RELAXED);
    cs->RecursionCount = 1;
    cs->LockCount = 0;
    return TRUE;
}

extern "C" void LeaveCriticalSection(LPCRITICAL_SECTION cs)
{
    if (cs->RecursionCount <= 0) {
        w32_log("LeaveCriticalSection on a section that is not held (%p)", (void *)cs);
        return;
    }
    if (--cs->RecursionCount == 0) {
        __atomic_store_n(&cs->OwningThread, (HANDLE)0, __ATOMIC_RELAXED);
        cs->LockCount = -1;
        os_unlock(cs_lock(cs));
    }
}

extern "C" void DeleteCriticalSection(LPCRITICAL_SECTION cs) { memset(cs, 0, sizeof *cs); }

/* ---- Interlocked*: full barriers, as on x86 */
extern "C" LONG InterlockedIncrement(volatile LONG *p) { return __atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST); }
extern "C" LONG InterlockedDecrement(volatile LONG *p) { return __atomic_sub_fetch(p, 1, __ATOMIC_SEQ_CST); }
extern "C" LONG InterlockedExchange(volatile LONG *p, LONG v) { return __atomic_exchange_n(p, v, __ATOMIC_SEQ_CST); }
extern "C" LONG InterlockedExchangeAdd(volatile LONG *p, LONG v) { return __atomic_fetch_add(p, v, __ATOMIC_SEQ_CST); }

extern "C" LONG InterlockedCompareExchange(volatile LONG *p, LONG xchg, LONG cmp)
{
    __atomic_compare_exchange_n(p, &cmp, xchg, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    return cmp; /* the initial value either way */
}

extern "C" PVOID InterlockedCompareExchangePointer(PVOID volatile *p, PVOID xchg, PVOID cmp)
{
    __atomic_compare_exchange_n(p, &cmp, xchg, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    return cmp;
}

extern "C" PVOID InterlockedExchangePointer(PVOID volatile *p, PVOID v) { return __atomic_exchange_n(p, v, __ATOMIC_SEQ_CST); }

/* ---- waitable objects */
struct w32_event { struct w32_obj o; int manual, state; char *name; };
struct w32_mutex { struct w32_obj o; DWORD owner; int count; char *name; };
struct w32_sem { struct w32_obj o; LONG count, max; char *name; };

/* named objects share one namespace, as in a Windows session */
struct named { struct named *next; char *name; struct w32_obj *o; };
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

static HANDLE create_named(const char *name, int type, struct w32_obj *fresh)
{
    if (!name || !*name) {
        HANDLE h = w32_handle_open(fresh);
        if (h)
            SetLastError(ERROR_SUCCESS);
        return h;
    }
    os_lock(&w32_wait_lock);
    struct w32_obj *ex = find_named(name, type);
    if (!ex) {
        struct named *n = (struct named *)malloc(sizeof *n);
        if (n) {
            n->name = strdup(name);
            n->o = fresh;
            n->next = g_named;
            g_named = n;
        }
    }
    os_unlock(&w32_wait_lock);
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
    struct w32_mutex *m = (struct w32_mutex *)w32_obj_new(W32_MUTEX, sizeof *m, obj_destroy);
    if (!m)
        return NULL;
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
    default: return 0;
    }
}

static void acquire(struct w32_obj *o, DWORD me)
{
    switch (o->type) {
    case W32_EVENT: if (!((struct w32_event *)o)->manual) ((struct w32_event *)o)->state = 0; break;
    case W32_MUTEX: { struct w32_mutex *m = (struct w32_mutex *)o; m->owner = me; m->count++; break; }
    case W32_SEMAPHORE: ((struct w32_sem *)o)->count--; break;
    default: break;
    }
}

static DWORD wait_multiple(DWORD n, const HANDLE *hs, BOOL all, DWORD ms);

/* The loading-screen thread (OS_THREAD_BOOST) waits without a timeout only when the loading screen is done
 * (LoadingMenuThread::ThreadProc parks on its semaphore): the CPU boost is off for such a wait. */
extern "C" DWORD WaitForMultipleObjects(DWORD n, const HANDLE *hs, BOOL all, DWORD ms)
{
    struct w32_thread *self = w32_self();
    int unboost = self->boost && W32_DW(ms) == INFINITE;
    if (unboost)
        os_cpu_boost(0);
    DWORD r = wait_multiple(n, hs, all, ms);
    if (unboost)
        os_cpu_boost(1);
    return r;
}

static DWORD wait_multiple(DWORD n, const HANDLE *hs, BOOL all, DWORD ms)
{
    n = W32_DW(n);
    ms = W32_DW(ms);
    if (n == 0 || n > MAXIMUM_WAIT_OBJECTS || !hs) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return WAIT_FAILED;
    }
    struct w32_thread *self = w32_self();
    struct w32_obj *objs[MAXIMUM_WAIT_OBJECTS];
    struct w32_link links[MAXIMUM_WAIT_OBJECTS];
    for (DWORD i = 0; i < n; i++) {
        objs[i] = w32_obj_get(hs[i], 0);
        if (!objs[i]) {
            for (DWORD j = 0; j < i; j++)
                w32_obj_unref(objs[j]);
            return WAIT_FAILED;
        }
        w32_obj_ref(objs[i]);
    }
    uint64_t deadline = ms == INFINITE ? 0 : os_monotonic_ns() + (uint64_t)ms * 1000000ull;
    DWORD res = WAIT_TIMEOUT;
    os_lock(&w32_wait_lock);
    for (;;) {
        if (all) {
            DWORD k = 0;
            while (k < n && signaled(objs[k], self->tid))
                k++;
            if (k == n) {
                for (DWORD i = 0; i < n; i++)
                    acquire(objs[i], self->tid);
                res = WAIT_OBJECT_0;
                break;
            }
        } else {
            DWORD k = 0;
            while (k < n && !signaled(objs[k], self->tid))
                k++;
            if (k < n) {
                acquire(objs[k], self->tid);
                res = WAIT_OBJECT_0 + k;
                break;
            }
        }
        uint64_t left = OS_WAIT_FOREVER;
        if (ms != INFINITE) {
            uint64_t now = os_monotonic_ns();
            if (ms == 0 || now >= deadline)
                break;
            left = deadline - now;
        }
        for (DWORD i = 0; i < n; i++) {
            links[i].t = self;
            links[i].next = objs[i]->waiters.next;
            links[i].prev = &objs[i]->waiters;
            objs[i]->waiters.next->prev = &links[i];
            objs[i]->waiters.next = &links[i];
        }
        os_cond_wait(&self->waiter.cond, &w32_wait_lock, left);
        for (DWORD i = 0; i < n; i++) {
            links[i].prev->next = links[i].next;
            links[i].next->prev = links[i].prev;
        }
    }
    os_unlock(&w32_wait_lock);
    for (DWORD i = 0; i < n; i++)
        w32_obj_unref(objs[i]);
    return res;
}

extern "C" DWORD WaitForSingleObject(HANDLE h, DWORD ms) { return WaitForMultipleObjects(1, &h, FALSE, ms); }
