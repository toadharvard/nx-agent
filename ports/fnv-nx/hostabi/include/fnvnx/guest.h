/* fnv-nx ILP32 guest runtime C API (toolchain/guest). Only for guest code (-mabi=ilp32). */
#ifndef FNVNX_GUEST_H
#define FNVNX_GUEST_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

struct __pthread_t;

/* threads (each has its own static TLS block and newlib reent) */
uint64_t fnvnx_thread_host_id(struct __pthread_t *t);   /* the host's id, for thread_set_priority/_cpu */
int fnvnx_thread_create(struct __pthread_t **out, void *(*fn)(void *), void *arg, size_t stack_size, int prio,
                        int cpu);                  /* 0 or errno; not started */
int fnvnx_thread_start(struct __pthread_t *t);
void *fnvnx_thread_join(struct __pthread_t *t);    /* waits, frees the record, returns fn's result */
void fnvnx_thread_exit(void *ret) __attribute__((noreturn));
uint32_t fnvnx_thread_id(void);                    /* 1 = main thread */
void fnvnx_yield(void);

/* address wait/wake: 0 woken, 1 timed out, 2 *addr != expected */
int fnvnx_wait(volatile uint32_t *addr, uint32_t expected, uint64_t timeout_ns);
void fnvnx_wake(volatile uint32_t *addr, uint32_t count);   /* UINT32_MAX: all */

/* one-word lock (0 = unlocked) and condvar (0 = valid) */
void fnvnx_lock(volatile uint32_t *l);
int fnvnx_trylock(volatile uint32_t *l);           /* 1 if acquired */
void fnvnx_unlock(volatile uint32_t *l);
int fnvnx_cond_wait(volatile uint32_t *c, volatile uint32_t *l, uint64_t timeout_ns); /* 0 woken, 1 timeout */
void fnvnx_cond_signal(volatile uint32_t *c);
void fnvnx_cond_broadcast(volatile uint32_t *c);

/* host file system: "sdmc:/..." style host paths; also reachable through newlib (fopen, open, stat, ...) */
/* memory sections with shared views (hostapi.h SECTION_* services, toolchain/guest/section_guest.c). All return 0, or
 * -(FNVNX_SEC_E_* code), e.g. -0xD401; -0xFF when the host has no section service. Views are always read-write. */
int fnvnx_section_create(uint32_t size, uint32_t *id);   /* zeroed backing, size a 4 KiB multiple */
int fnvnx_section_map(uint32_t id, uint32_t offset, uint32_t size, uint32_t addr_hint, uint32_t *addr);   /* hint 0: host-chosen; else inside a VM reservation */
int fnvnx_section_unmap(uint32_t addr, uint32_t size);   /* exactly a view */
int fnvnx_section_free(uint32_t id);   /* deferred while views live */

#ifdef __cplusplus
}
#endif
#endif
