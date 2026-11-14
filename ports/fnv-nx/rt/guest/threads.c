/* fnv-nx ILP32 guest: threads, TLS per thread, locks, condition variables, newlib/pthread hooks.
 *
 * Threads are host (libnx) threads created through FnvnxHostApi; each runs guest_thread_entry() first, which
 * gives it its own static TLS block (TPIDR_EL0) and newlib reentrancy struct. Blocking is built on the host's
 * address wait/wake (svcWaitForAddress / svcSignalToAddress), so locks and condvars are 32-bit words in guest
 * memory and zero-initialised storage is valid.
 *
 * Provides the devkitPro libsysbase hooks (__syscall_lock_*, __syscall_cond_*, __syscall_thread_*,
 * __syscall_tls_*, __syscall_getreent), so newlib's own locks, pthread_* and therefore libstdc++ (gthr-posix:
 * std::mutex, std::thread, thread-safe statics) work, plus a small C API in fnvnx/guest.h.
 */
#include <errno.h>
#include <malloc.h>
#include <reent.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/iosupport.h>
#include <sys/lock.h>

#include "fnvnx/guest.h"
#include "fnvnx/hostapi.h"

#define HIDDEN __attribute__((visibility("hidden")))
extern char __tdata_lma[] HIDDEN, __tdata_lma_end[] HIDDEN;
extern char __tls_start[] HIDDEN, __tls_tcb[] HIDDEN, __tls_end[] HIDDEN;
extern const uint32_t __tls_align HIDDEN;

/* ---- futex-style wait/wake --------------------------------------------------------------------------- */
int fnvnx_wait(volatile uint32_t *addr, uint32_t expected, uint64_t timeout_ns) {
    return (int)fnvnx_hcall3(fnvnx_host->wait_on_address, FNVNX_ADDR(addr), expected, timeout_ns);
}
void fnvnx_wake(volatile uint32_t *addr, uint32_t count) {
    fnvnx_hcall2(fnvnx_host->wake_address, FNVNX_ADDR(addr), count == UINT32_MAX ? UINT64_MAX : count);
}

/* ---- thread records ----------------------------------------------------------------------------------- */
struct __pthread_t {               /* newlib's opaque pthread_t is a pointer to this */
    void *(*fn)(void *);
    void *arg, *ret;
    uint64_t host_id;
    char *tls_block;               /* malloc'd static TLS block (NULL for the main thread) */
    uint32_t tid;
    volatile uint32_t done;
    uint32_t detached;
};

static __thread struct __pthread_t *t_self;
static __thread struct _reent t_reent;
static __thread int t_reent_ready;
static struct __pthread_t g_main_thread = { .tid = 1 };
static volatile uint32_t g_next_tid = 1;

static struct __pthread_t *self(void) {
    return t_self ? t_self : &g_main_thread;
}
uint32_t fnvnx_thread_id(void) { return self()->tid; }

struct _reent *__syscall_getreent(void) {
    if (!t_self) return _impure_ptr;           /* main thread */
    if (!t_reent_ready) { _REENT_INIT_PTR(&t_reent); t_reent_ready = 1; }
    return &t_reent;
}

static char *alloc_tls_block(void) {
    size_t align = __tls_align < 8 ? 8 : __tls_align;
    size_t size = (size_t)(__tls_end - __tls_tcb);
    char *b = memalign(align, size ? size : 8);
    if (!b) return NULL;
    memset(b, 0, size);
    memcpy(b + (__tls_start - __tls_tcb), __tdata_lma, (size_t)(__tdata_lma_end - __tdata_lma));
    return b;
}

/* First guest code on a new host thread (called by the host as entry(arg), arg = record). */
static uint64_t guest_thread_entry(uint64_t rec_addr) {
    struct __pthread_t *t = (struct __pthread_t *)(uintptr_t)rec_addr;
    __asm__ volatile("msr tpidr_el0, %0" :: "r"((uint64_t)(uintptr_t)t->tls_block) : "memory");
    t_self = t;
    t->ret = t->fn(t->arg);
    _reclaim_reent(&t_reent);
    __atomic_store_n(&t->done, 1, __ATOMIC_RELEASE);
    return 0;
}

int fnvnx_thread_create(struct __pthread_t **out, void *(*fn)(void *), void *arg, size_t stack_size, int prio,
                        int cpu) {
    struct __pthread_t *t = calloc(1, sizeof *t);
    if (!t) return ENOMEM;
    t->fn = fn; t->arg = arg;
    t->tid = __atomic_add_fetch(&g_next_tid, 1, __ATOMIC_RELAXED);
    if (!(t->tls_block = alloc_tls_block())) { free(t); return ENOMEM; }
    t->host_id = fnvnx_hcall5(fnvnx_host->thread_create, FNVNX_ADDR(guest_thread_entry), FNVNX_ADDR(t),
                              stack_size ? stack_size : 0x40000, (uint64_t)(int64_t)prio, (uint64_t)(int64_t)cpu);
    if (!t->host_id) { free(t->tls_block); free(t); return EAGAIN; }
    *out = t;
    return 0;
}
uint64_t fnvnx_thread_host_id(struct __pthread_t *t) { return t ? t->host_id : 0; }
int fnvnx_thread_start(struct __pthread_t *t) {
    return fnvnx_hcall1(fnvnx_host->thread_start, t->host_id) ? EAGAIN : 0;
}
void *fnvnx_thread_join(struct __pthread_t *t) {
    fnvnx_hcall1(fnvnx_host->thread_join, t->host_id);
    void *ret = t->ret;
    fnvnx_hcall1(fnvnx_host->thread_free, t->host_id);
    free(t->tls_block);
    free(t);
    return ret;
}
void fnvnx_thread_exit(void *ret) {
    struct __pthread_t *t = self();
    if (t == &g_main_thread) exit(0);
    t->ret = ret;
    _reclaim_reent(&t_reent);
    __atomic_store_n(&t->done, 1, __ATOMIC_RELEASE);
    fnvnx_hcall0(fnvnx_host->thread_exit);
    for (;;) {}   /* the host's thread_exit does not return */
}
void fnvnx_yield(void) { fnvnx_hcall0(fnvnx_host->thread_yield); }
/* libsysbase's weak sched_yield returns ENOSYS; std::this_thread::yield and spin loops need a real one. */
int sched_yield(void) { fnvnx_yield(); return 0; }

/* newlib pthread hooks */
int __syscall_thread_create(struct __pthread_t **thread, void *(*func)(void *), void *arg, void *stack_addr,
                            size_t stack_size) {
    if (stack_addr) return EINVAL;               /* caller-provided stacks are not supported */
    int rc = fnvnx_thread_create(thread, func, arg, stack_size, 0, -1);
    return rc ? rc : fnvnx_thread_start(*thread);
}
void *__syscall_thread_join(struct __pthread_t *thread) { return fnvnx_thread_join(thread); }
int __syscall_thread_detach(struct __pthread_t *thread) {
    thread->detached = 1;                        /* record leaks by design: host thread freed, record kept */
    fnvnx_hcall1(fnvnx_host->thread_free, thread->host_id);
    return 0;
}
void __syscall_thread_exit(void *value) { fnvnx_thread_exit(value); }
struct __pthread_t *__syscall_thread_self(void) { return self(); }

/* ---- locks ---------------------------------------------------------------------------------------------
 * Normal: 0 free, 1 locked, 2 locked with waiters (Drepper's futex mutex).
 * Recursive: lock word = owner tid | WAITERS, counter = depth. */
#define WAITERS 0x80000000u

void fnvnx_lock(volatile uint32_t *l) {
    uint32_t c = 0;
    if (__atomic_compare_exchange_n(l, &c, 1, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) return;
    if (c != 2) c = __atomic_exchange_n(l, 2, __ATOMIC_ACQUIRE);
    while (c != 0) {
        fnvnx_wait(l, 2, UINT64_MAX);
        c = __atomic_exchange_n(l, 2, __ATOMIC_ACQUIRE);
    }
}
int fnvnx_trylock(volatile uint32_t *l) {
    uint32_t c = 0;
    return __atomic_compare_exchange_n(l, &c, 1, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED);
}
void fnvnx_unlock(volatile uint32_t *l) {
    if (__atomic_exchange_n(l, 0, __ATOMIC_RELEASE) == 2) fnvnx_wake(l, 1);
}

static void rlock(_LOCK_RECURSIVE_T *l) {
    uint32_t me = fnvnx_thread_id();
    volatile uint32_t *w = &l->lock;
    if ((*w & ~WAITERS) == me) { l->counter++; return; }
    uint32_t c = 0;
    if (!__atomic_compare_exchange_n(w, &c, me, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) {
        for (;;) {
            c = __atomic_load_n(w, __ATOMIC_RELAXED);
            if (c == 0) {
                if (__atomic_compare_exchange_n(w, &c, me | WAITERS, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) break;
                continue;
            }
            if (!(c & WAITERS) &&
                !__atomic_compare_exchange_n(w, &c, c | WAITERS, 0, __ATOMIC_RELAXED, __ATOMIC_RELAXED))
                continue;
            fnvnx_wait(w, c | WAITERS, UINT64_MAX);
        }
    }
    l->counter = 1;
}
static int rtrylock(_LOCK_RECURSIVE_T *l) {
    uint32_t me = fnvnx_thread_id(), c = 0;
    if ((l->lock & ~WAITERS) == me) { l->counter++; return 1; }
    if (!__atomic_compare_exchange_n(&l->lock, &c, me, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) return 0;
    l->counter = 1;
    return 1;
}
static void runlock(_LOCK_RECURSIVE_T *l) {
    if (--l->counter) return;
    if (__atomic_exchange_n(&l->lock, 0, __ATOMIC_RELEASE) & WAITERS) fnvnx_wake(&l->lock, 1);
}

void __syscall_lock_acquire(_LOCK_T *l) { fnvnx_lock(l); }
int __syscall_lock_try_acquire(_LOCK_T *l) { return fnvnx_trylock(l) ? 0 : 1; }
void __syscall_lock_release(_LOCK_T *l) { fnvnx_unlock(l); }
void __syscall_lock_acquire_recursive(_LOCK_RECURSIVE_T *l) { rlock(l); }
int __syscall_lock_try_acquire_recursive(_LOCK_RECURSIVE_T *l) { return rtrylock(l) ? 0 : 1; }
void __syscall_lock_release_recursive(_LOCK_RECURSIVE_T *l) { runlock(l); }

/* ---- condition variables: a sequence word ---------------------------------------------------------------- */
int fnvnx_cond_wait(volatile uint32_t *c, volatile uint32_t *l, uint64_t timeout_ns) {
    uint32_t seq = __atomic_load_n(c, __ATOMIC_RELAXED);
    fnvnx_unlock(l);
    int r = fnvnx_wait(c, seq, timeout_ns);
    fnvnx_lock(l);
    return r == 1 ? 1 : 0;
}
void fnvnx_cond_signal(volatile uint32_t *c) { __atomic_add_fetch(c, 1, __ATOMIC_RELEASE); fnvnx_wake(c, 1); }
void fnvnx_cond_broadcast(volatile uint32_t *c) {
    __atomic_add_fetch(c, 1, __ATOMIC_RELEASE);
    fnvnx_wake(c, UINT32_MAX);
}

int __syscall_cond_signal(_COND_T *c) { fnvnx_cond_signal(c); return 0; }
int __syscall_cond_broadcast(_COND_T *c) { fnvnx_cond_broadcast(c); return 0; }
int __syscall_cond_wait(_COND_T *c, _LOCK_T *l, uint64_t timeout_ns) {
    return fnvnx_cond_wait(c, l, timeout_ns) ? ETIMEDOUT : 0;
}
int __syscall_cond_wait_recursive(_COND_T *c, _LOCK_RECURSIVE_T *l, uint64_t timeout_ns) {
    uint32_t seq = __atomic_load_n(c, __ATOMIC_RELAXED), depth = l->counter;
    l->counter = 1;
    runlock(l);
    int r = fnvnx_wait(c, seq, timeout_ns);
    rlock(l);
    l->counter = depth;
    return r == 1 ? ETIMEDOUT : 0;
}

/* ---- pthread keys ------------------------------------------------------------------------------------- */
#define NKEYS 64
static void (*volatile g_key_dtor[NKEYS])(void *);
static volatile uint32_t g_key_used[NKEYS];
static __thread const void *t_key_val[NKEYS];

int __syscall_tls_create(uint32_t *key, void (*destructor)(void *)) {
    for (uint32_t i = 0; i < NKEYS; i++) {
        uint32_t z = 0;
        if (__atomic_compare_exchange_n(&g_key_used[i], &z, 1, 0, __ATOMIC_ACQ_REL, __ATOMIC_RELAXED)) {
            g_key_dtor[i] = destructor;
            *key = i;
            return 0;
        }
    }
    return EAGAIN;
}
int __syscall_tls_set(uint32_t key, const void *value) {
    if (key >= NKEYS) return EINVAL;
    t_key_val[key] = value;
    return 0;
}
void *__syscall_tls_get(uint32_t key) { return key < NKEYS ? (void *)t_key_val[key] : NULL; }
int __syscall_tls_delete(uint32_t key) {
    if (key >= NKEYS) return EINVAL;
    g_key_used[key] = 0;
    return 0;
}
