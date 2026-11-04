/* gstart.c - entry of the mtqemu ILP32 guest: the host calls  uint64_t _start(uint64_t api)  as it calls a real
 * guest module (toolchain/include/fnvnx/hostapi.h); main-thread TLS as toolchain/guest/runtime.c sets it up, then
 * the C++ initialisers and the test. Plus the two uninstrumented helpers the test times with. */
#include <stdint.h>
#include <stddef.h>

#include "fnvnx/hostapi.h"

void *memcpy(void *d, const void *s, size_t n);
extern char __tdata_lma[], __tdata_lma_end[], __tls_start[], __tls_tcb[];
extern void (*__init_array_start[])(void), (*__init_array_end[])(void);
const FnvnxHostApi *fnvnx_host;
int mtq_main(void);

__attribute__((section(".text.mtq_entry"))) uint64_t _start(uint64_t api)
{
    fnvnx_host = (const FnvnxHostApi *)(uintptr_t)api;
    if (fnvnx_host->magic != FNVNX_HOSTAPI_MAGIC) return 0xF0000002u;
    memcpy(__tls_start, __tdata_lma, (size_t)(__tdata_lma_end - __tdata_lma));
    __asm__ volatile("msr tpidr_el0, %0" :: "r"((uint64_t)(uintptr_t)__tls_tcb) : "memory");
    for (void (**f)(void) = __init_array_start; f < __init_array_end; f++) (*f)();
    return (uint64_t)mtq_main();
}

uint64_t mtq_ticks_ns(void) { return fnvnx_hcall0(fnvnx_host->system_tick); }

/* a fixed amount of integer work (an LCG), not instrumented */
uint64_t mtq_spin(uint64_t n)
{
    uint64_t x = n | 1;
    for (uint64_t i = 0; i < n; i++) { x = x * 6364136223846793005ull + 1442695040888963407ull; __asm__ volatile("" : "+r"(x)); }
    return x;
}
