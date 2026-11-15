/* The ONE reference CPU model (README, "One reference CPU model"): cpuid and rdtsc as every lifted program sees them. Plain C (ILP32 AArch64 or host).
   Host hook, declared here and provided by the platform: unsigned long long fnvl_host_ticks_ns(void)  -- a monotonic nanosecond clock.

   Source of the table: the pinned reference host, `cpuid` run once for leaves 0..max and 0x80000000..max (tools: a 15-line host program over
   __cpuid_count). CPU identity of that host: GenuineIntel, family 6, model 207 (0xCF, Emerald Rapids Xeon), stepping 2, "Intel(R) Xeon(R) Processor @ 2.10GHz",
   microcode revision not exposed (virtual machine: /proc/cpuinfo has no microcode line).
   Masking applied to the raw host values (everything else is the host value verbatim):
   max basic leaf 0xb -> 4 (a 2010-era game asks leaves 0 1 2 4 and 0x80000000..4); max extended leaf 0x80000008 -> 0x80000004 (brand string).
   Leaf 1 EAX 000c06f2 kept (family 6, model 0xCF, stepping 2). EBX 00040800 -> 00010800: 1 logical processor (the host says 4), APIC id 0.
   Leaf 1 EDX 1f8bfbff -> 078bfbff: cleared SS (27) and HTT (28); kept FPU VME DE PSE TSC MSR PAE MCE CX8 APIC SEP MTRR PGE MCA CMOV PAT PSE36 CLFSH MMX FXSR SSE SSE2.
   Leaf 1 ECX fffa3203 -> 00000000: everything cleared. The lifter implements MMX/SSE/SSE2 only (fnvlift/f_sse.py has lddqu but none of the other SSE3 instructions:
   no haddps/movddup/addsubps/movshdup, so the SSE3 bit stays clear). Cleared: SSE3, PCLMULQDQ, SSSE3, FMA, CX16, PCID, SSE4.1, SSE4.2, x2APIC, MOVBE, POPCNT,
   TSC-deadline, AES, XSAVE, OSXSAVE, AVX, F16C, RDRAND, hypervisor.
   Leaf 4 (deterministic cache, subleaves 0..3 kept, others zero): EAX bits 31:26 (cores-1) and 25:14 (threads sharing-1) cleared: one core, one thread.
   Leaf 7 (structured features: AVX2 BMI1/2 AVX-512 ...), 0xb, 0xd, 0x1d, 0x1e, 0x1f: not reported (above the max leaf).
   Leaf 0x80000001: EDX 2c100800 -> 00100000 (NX only; SYSCALL/1GB pages/RDTSCP/long mode cleared), ECX 00000121 -> 0 (LAHF64, LZCNT/ABM, PREFETCHW cleared: not
   implemented or 64-bit-only). Leaves 0x80000005..8 above the reported max.
   A leaf above the max of its range returns the data of the highest basic leaf (Intel SDM, CPUID: "if a value entered for CPUID.EAX is higher than the maximum
   input value for basic or extended function ... the data for the highest basic information leaf is returned"), i.e. leaf 4 with the given subleaf.
   Leaves 0x80000002..4: the host's brand string "Intel(R) Xeon(R) Processor @ 2.10GHz", unchanged. */
#include "fnvl_abi.h"
typedef unsigned int u32; typedef unsigned long long u64;
unsigned long long fnvl_host_ticks_ns(void);
#define FNVL_MAX_BASIC 4u
#define FNVL_MAX_EXT 0x80000004u
void fnvl_cpuid(u32 leaf, u32 sub, u32 o[4]) {
    static const u32 L0[4] = { FNVL_MAX_BASIC, 0x756e6547, 0x49656e69, 0x6c65746e };   /* EAX max leaf, EBX "Genu", EDX "ineI", ECX "ntel" */
    static const u32 L2[4] = { 0x00feff01, 0x000000f0, 0, 0 };
    static const u32 L4[4][4] = {
        { 0x00000121, 0x02c0003f, 0x0000003f, 0 },       /* L1 data cache */
        { 0x00000122, 0x01c0003f, 0x0000003f, 0 },       /* L1 instruction cache */
        { 0x00000143, 0x03c0003f, 0x000007ff, 0 },       /* L2 */
        { 0x00000163, 0x04c0003f, 0x00033fff, 4 } };     /* L3 (host values with EAX[31:26] and EAX[25:14] cleared) */
    static const u32 E1[4] = { 0, 0, 0, 0x00100000 };
    static const u32 BRAND[3][4] = { { 0x65746e49, 0x2952286c, 0x6f655820, 0x2952286e }, { 0x6f725020, 0x73736563, 0x4020726f, 0x312e3220 }, { 0x7a484730, 0, 0, 0 } };
    u32 i;
    for (i = 0; i < 4; i++) o[i] = 0;
    if (leaf >= 0x80000000u && leaf <= FNVL_MAX_EXT) {
        if (leaf == 0x80000000u) o[0] = FNVL_MAX_EXT;
        else if (leaf == 0x80000001u) for (i = 0; i < 4; i++) o[i] = E1[i];
        else for (i = 0; i < 4; i++) o[i] = BRAND[leaf - 0x80000002u][i];
        return;
    }
    if (leaf > FNVL_MAX_BASIC) { leaf = FNVL_MAX_BASIC; }     /* SDM rule above (also for 0x80000005.. and 0x40000000..) */
    switch (leaf) {
    case 0: o[0] = L0[0]; o[1] = L0[1]; o[2] = L0[3]; o[3] = L0[2]; break;
    case 1: o[0] = FNVL_CPUID1_EAX; o[1] = FNVL_CPUID1_EBX; o[2] = FNVL_CPUID1_ECX; o[3] = FNVL_CPUID1_EDX; break;
    case 2: for (i = 0; i < 4; i++) o[i] = L2[i]; break;
    case 3: break;
    case 4: if (sub < 4) for (i = 0; i < 4; i++) o[i] = L4[sub][i]; break;
    }
}
/* nominal TSC frequency of the model: 2.1 GHz (the host's "@ 2.10GHz"): ticks = ns * 21 / 10 (no 64-bit overflow for 27 years of uptime) */
unsigned long long fnvl_rdtsc(void) {
    u64 ns = fnvl_host_ticks_ns();
    return ns / 10 * 21 + ns % 10 * 21 / 10;
}

/* msvcrt!_adjust_fdiv (a data export: the import slot holds the address of this object). The C runtime sets it to 1 at start-up on a CPU with the
   Pentium FDIV flaw and compiled code tests it before a division; the reference CPU divides correctly, so the value is 0 and nothing writes it. */
int fnvl_msvcrt_adjust_fdiv = 0;
