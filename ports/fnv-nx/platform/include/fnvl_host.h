/* fnvl_host.h - the platform hooks the lifted runtime calls (lifter rule R9 and the runtime's clock and TLS).
 * The one declaration of these four symbols, for C and C++. Defined by the platform:
 *   fnvl_host_tls_slot, fnvl_host_ticks_ns   platform/boundary/host_hooks.cpp
 *   fnvl_host_world_stop, fnvl_host_world_resume   platform/os/os_world.c (the contract and its proof are there)
 * The dispatch word: fnvnx_call_fp (unsigned int, the exe address of the indirect call in progress; the L2 dispatch plugin
 * stores it before each indirect call and the dispatcher bridge reads it): a thread-local variable, initial-exec TLS model (the
 * plugin's declaration uses the same model), zero in every thread from its creation. Defined by the platform: platform/win32/thread.cpp.
 * Every defining file and every user includes this header; nothing else declares them. */
#ifndef FNVL_HOST_H
#define FNVL_HOST_H

#ifdef __cplusplus
extern "C" {
#endif

struct cpu;

unsigned long long fnvl_host_ticks_ns(void);
struct cpu **fnvl_host_tls_slot(void);
void fnvl_host_world_stop(void);
void fnvl_host_world_resume(void);

extern __thread unsigned int fnvnx_call_fp __attribute__((tls_model("initial-exec")));

/* DISPATCH1 (toolchain/dispatch/fnvnx_dispatch.cc): the per-site record and the dispatcher's prototype. The plugin builds the same record
 * (`struct fnvnx_site { unsigned int fp; <native entry> entry; }`, size 8, alignment 8) for every indirect call site and never looks it up by
 * name: the call sequence reads the whole record with one 64-bit acquire load and calls `.entry` when `.fp` is the callee's exe address.
 * fnvnx_dispatch (the runtime's, lift/rt) returns the entry for the exe address and stores the record, both halves, with ONE 64-bit release
 * store. fnvnx_entry_fn is the generic native entry type (ENTRY1): calls through it are native calls, and the call sequence converts it to the
 * call site's own function-pointer type. The plugin's row of this prototype in .fnvnx.decltype is compared with the row of this header by the
 * layout gate (gate abi), so the two cannot drift. */
typedef void (*__attribute__((fnvnx_entry)) fnvnx_entry_fn)(void);
typedef struct fnvnx_site {
  unsigned int fp;
  fnvnx_entry_fn entry;
} __attribute__((aligned(8))) fnvnx_site_t;
/* one spelling of the assertion for both languages: C11 `_Static_assert`/`_Alignof`, C++ `static_assert`/`alignof` (g++ rejects the C keywords) */
#ifdef __cplusplus
#define FNVNX_ALIGNOF(t) alignof(t)
#define FNVNX_STATIC_ASSERT(c, m) static_assert(c, m)
#else
#define FNVNX_ALIGNOF(t) _Alignof(t)
#define FNVNX_STATIC_ASSERT(c, m) _Static_assert(c, m)
#endif
FNVNX_STATIC_ASSERT(sizeof(fnvnx_site_t) == 8 && FNVNX_ALIGNOF(fnvnx_site_t) == 8, "the site record is one 64-bit word");

fnvnx_entry_fn fnvnx_dispatch(void *fp, fnvnx_site_t *site, const void *descriptor);

#ifdef __cplusplus
}
#endif
#endif
