/* fnvl_host.h - the platform hooks the lifted runtime calls (lifter rule R9 and the runtime's clock and TLS).
 * The one declaration of these four symbols, for C and C++. Defined by the platform:
 *   fnvl_host_tls_slot, fnvl_host_ticks_ns   platform/boundary/host_hooks.cpp
 *   fnvl_host_world_stop, fnvl_host_world_resume   platform/os/os_world.c (the contract and its proof are there)
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

#ifdef __cplusplus
}
#endif
#endif
