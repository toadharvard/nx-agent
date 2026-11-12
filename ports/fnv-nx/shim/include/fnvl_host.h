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

#ifdef __cplusplus
}
#endif
#endif
