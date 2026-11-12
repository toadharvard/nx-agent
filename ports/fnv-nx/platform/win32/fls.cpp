/* fls.cpp - kernel32 FlsAlloc / FlsFree / FlsGetValue / FlsSetValue.
 *
 * The game never creates fibers (its import manifest has no CreateFiber / ConvertThreadToFiber), so by the documented
 * rule a thread that is not a fiber uses fiber local storage as thread local storage with a destruction callback.
 *   - 128 slots (Windows 7 FLS_MAXIMUM_AVAILABLE); index 0 is never handed out (the reference reserves it: ntdll
 *     RtlFlsAlloc "FLS index 0 is prohibited"), so 127 can be allocated; FlsAlloc then fails with
 *     FLS_OUT_OF_INDEXES and ERROR_NOT_ENOUGH_MEMORY. A slot allocated with a NULL callback has no callback.
 *   - values are per thread and start NULL. FlsGetValue succeeds with ERROR_SUCCESS for a NULL value; an index that
 *     is 0, out of range or not allocated fails with ERROR_INVALID_PARAMETER (FlsSetValue likewise).
 *   - FlsFree(i) calls the callback for the non-NULL value of EVERY thread, on the calling thread, in the order the
 *     threads first stored a value (reference: ntdll RtlFlsFree walks the FLS list in that order), clears those
 *     values and frees the slot. An unallocated index fails with ERROR_INVALID_PARAMETER.
 *   - when a thread ends (ExitThread, or its start routine returning) the callbacks of its non-NULL values run in
 *     ascending index order, once, BEFORE the modules hear of the thread detach (reference: ntdll LdrShutdownThread
 *     calls RtlProcessFlsData first); ExitProcess does the same for the calling thread before DLL_PROCESS_DETACH
 *     (LdrShutdownProcess). Values a callback stores meanwhile are dropped with the thread.
 * The callback is a game function: fnvnx_guestfn, called through the boundary with NO lock held (every callback run
 * takes its value under the lock, releases the lock, then calls). Cells are read by FlsGetValue without the lock;
 * every other access to a cell, and the slot table, is under g_fls_lock. A slot being freed stays reserved (busy)
 * until its last callback ran, so FlsAlloc cannot hand it out and a thread ending meanwhile still finds its callback. */
#include "w32.h"
#include "../include/fnvnx_guestfn.h"

#include <stdlib.h>

#define FLS_SLOTS 128u
#define FLS_OUT_OF_INDEXES_ ((DWORD)0xFFFFFFFF)

typedef fnvnx_guestfn<void(void *)> fls_callback;

struct fls_data {
    struct fls_data *next;   /* registration order: the order of first FlsSetValue */
    void *val[FLS_SLOTS];
};

static os_lock_t g_fls_lock;
static uint32_t g_fls_used[FLS_SLOTS / 32];   /* allocated; read without the lock by FlsGetValue */
static uint32_t g_fls_busy[FLS_SLOTS / 32];   /* being freed: reserved, callback still valid */
static uintptr_t g_fls_cb[FLS_SLOTS];         /* the guest callback of an allocated or busy slot; 0: none */
static struct fls_data *g_fls_head, *g_fls_tail;
static __thread struct fls_data *t_fls;

static bool bit(const uint32_t *m, DWORD i) { return (__atomic_load_n(&m[i / 32], __ATOMIC_ACQUIRE) >> (i % 32)) & 1u; }
static void bit_set(uint32_t *m, DWORD i) { __atomic_or_fetch(&m[i / 32], 1u << (i % 32), __ATOMIC_RELEASE); }
static void bit_clear(uint32_t *m, DWORD i) { __atomic_and_fetch(&m[i / 32], ~(1u << (i % 32)), __ATOMIC_RELEASE); }

extern "C" DWORD FlsAlloc(fls_callback cb)
{
    os_lock(&g_fls_lock);
    for (DWORD i = 1; i < FLS_SLOTS; i++)
        if (!bit(g_fls_used, i) && !bit(g_fls_busy, i)) {
            g_fls_cb[i] = cb.value();
            bit_set(g_fls_used, i);
            os_unlock(&g_fls_lock);
            return i;
        }
    os_unlock(&g_fls_lock);
    SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    return FLS_OUT_OF_INDEXES_;
}

extern "C" LPVOID FlsGetValue(DWORD i)
{
    struct w32_thread *t = w32_self();
    if (i == 0 || i >= FLS_SLOTS || !bit(g_fls_used, i)) {
        t->last_error = ERROR_INVALID_PARAMETER;
        return NULL;
    }
    t->last_error = ERROR_SUCCESS;
    struct fls_data *d = t_fls;
    return d ? __atomic_load_n(&d->val[i], __ATOMIC_ACQUIRE) : NULL;
}

extern "C" BOOL FlsSetValue(DWORD i, LPVOID v)
{
    if (i == 0 || i >= FLS_SLOTS) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    struct fls_data *fresh = NULL;
    if (!t_fls && !(fresh = (struct fls_data *)calloc(1, sizeof *fresh))) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return FALSE;
    }
    os_lock(&g_fls_lock);
    if (!bit(g_fls_used, i)) {
        os_unlock(&g_fls_lock);
        free(fresh);
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    if (fresh) {
        t_fls = fresh;
        if (g_fls_tail)
            g_fls_tail->next = fresh;
        else
            g_fls_head = fresh;
        g_fls_tail = fresh;
    }
    __atomic_store_n(&t_fls->val[i], v, __ATOMIC_RELEASE);
    os_unlock(&g_fls_lock);
    return TRUE;
}

/* the first thread (registration order) holding a value in slot i; the value is taken (cell cleared). NULL: none.
 * Caller holds g_fls_lock. */
static void *take_first(DWORD i)
{
    for (struct fls_data *d = g_fls_head; d; d = d->next) {
        void *v = __atomic_exchange_n(&d->val[i], (void *)NULL, __ATOMIC_ACQ_REL);
        if (v)
            return v;
    }
    return NULL;
}

extern "C" BOOL FlsFree(DWORD i)
{
    os_lock(&g_fls_lock);
    if (i == 0 || i >= FLS_SLOTS || !bit(g_fls_used, i)) {
        os_unlock(&g_fls_lock);
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    bit_set(g_fls_busy, i);
    bit_clear(g_fls_used, i);   /* Get/Set fail from here on; the callback stays in g_fls_cb until the last value is done */
    fls_callback cb((uintptr_t)g_fls_cb[i]);
    for (;;) {
        void *v = take_first(i);
        if (!v)
            break;
        os_unlock(&g_fls_lock);
        if (cb)
            cb(v);
        os_lock(&g_fls_lock);
    }
    g_fls_cb[i] = 0;
    bit_clear(g_fls_busy, i);
    os_unlock(&g_fls_lock);
    return TRUE;
}

/* The calling thread ends (thread.cpp ExitThread / thread start return, module.cpp ExitProcess): callbacks in ascending
 * index order for its non-NULL values, then its cells are released. */
void w32_fls_thread_exit(void)
{
    struct fls_data *d = t_fls;
    if (!d)
        return;
    for (DWORD i = 1; i < FLS_SLOTS; i++) {
        os_lock(&g_fls_lock);
        void *v = __atomic_exchange_n(&d->val[i], (void *)NULL, __ATOMIC_ACQ_REL);
        fls_callback cb(bit(g_fls_used, i) || bit(g_fls_busy, i) ? (uintptr_t)g_fls_cb[i] : 0);
        os_unlock(&g_fls_lock);
        if (v && cb)
            cb(v);
    }
    os_lock(&g_fls_lock);
    struct fls_data **pp = &g_fls_head, *prev = NULL;
    while (*pp != d) {
        prev = *pp;
        pp = &prev->next;
    }
    *pp = d->next;
    if (g_fls_tail == d)
        g_fls_tail = prev;
    t_fls = NULL;
    os_unlock(&g_fls_lock);
    free(d);
}
