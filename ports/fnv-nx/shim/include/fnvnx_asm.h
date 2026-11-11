/* fnvnx_asm.h - the time stamp counter read behind __rdtsc() (docs/ASM.md).
 *
 * Inline assembly of the decomp is not translated: prepare keeps it for cl and gives GCC an empty asm statement, the
 * function's body is lifted from the exe (rule ASM-Pi, tools/rules/s8_asm.py). What is left here is the intrinsic
 * __rdtsc(), which is a call, not inline assembly.
 *
 * No standard headers on purpose: this file is included into decomp units that declare their own CRT prototypes.
 */
#ifndef FNVNX_ASM_H
#define FNVNX_ASM_H

#define FNVNX_ASM_INLINE static inline __attribute__((always_inline, unused))

/* ------------------------------------------------------------------------------------------- time stamp counter
 * rdtsc: a free-running 64-bit tick counter. AArch64: the generic timer's physical count (CNTPCT_EL0, 19.2 MHz on the
 * Switch, the only count Horizon opens to EL0 - libnx armGetSystemTick() reads the same register). Every tick consumer in
 * the decomp either takes differences of these values or calibrates them against QueryPerformanceCounter (Havok
 * hkSystemClock), so only the rate changes. The "memory" clobber keeps the read ordered against surrounding memory
 * accesses like an MSVC __asm block (which the compiler does not move code across). */

FNVNX_ASM_INLINE unsigned long long fnvnx_rdtsc64(void)
{
#if defined(__aarch64__)
    unsigned long long v;
    /* CNTPCT_EL0, not CNTVCT_EL0: Horizon's kernel opens only the physical count to EL0 (Mesosphere writes
     * CNTKCTL_EL1 = EL0PCTEN, kern_k_hardware_timer.hpp), a CNTVCT_EL0 read traps; libnx's armGetSystemTick reads
     * the same register (NX1 check 3) */
    __asm__ __volatile__("mrs %0, cntpct_el0" : "=r"(v) : : "memory");
    return v;
#elif defined(__i386__) || defined(__x86_64__)
    unsigned long long v;
    __asm__ __volatile__("" : : : "memory");
    v = __builtin_ia32_rdtsc();
    __asm__ __volatile__("" : : : "memory");
    return v;
#else
#error "fnvnx_asm.h: no tick counter for this target"
#endif
}

/* __rdtsc(): the decomp's units declare it themselves, mostly `extern "C" unsigned __int64 __rdtsc(void);`, some
 * without the linkage; a declaration without linkage after this one takes the C linkage of this one. The platform
 * monotonic timer is the owner-decided meaning of rdtsc. */
#ifdef __cplusplus
extern "C" {
#endif
inline __attribute__((always_inline, unused)) unsigned long long __rdtsc(void) { return fnvnx_rdtsc64(); }
#ifdef __cplusplus
}
#endif

#endif
