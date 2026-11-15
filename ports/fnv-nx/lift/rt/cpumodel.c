/* The ONE reference CPU model (README, "One reference CPU model"): cpuid and rdtsc as every lifted program sees them. Plain C (ILP32 AArch64 or host).
   Host hook, declared here and provided by the platform: unsigned long long fnvl_host_ticks_ns(void)  -- a monotonic nanosecond clock.
   The identity (vendor, family, model, stepping, brand) is verify/cpuref.json; the raw leaves of the pinned host and the declared-machine masks (max leaves, features cleared, with their reasons) are
   verify/cpuid_model.json; tools/gen_cpuref.py generates fnvl_cpuref.h (leaf 1 EAX / ECX / EDX) and cpumodel_tab.inc (the tables) from them. This file holds only the rules the platform owns: the processor count
   fields (leaf 1 EBX, leaf 4 EAX), the SDM rule for a leaf above the maximum, and the TSC. A leaf above the max of its range returns the data of the highest basic leaf (Intel SDM, CPUID: "if a value entered for
   CPUID.EAX is higher than the maximum input value for basic or extended function ... the data for the highest basic information leaf is returned"), i.e. leaf 4 with the given subleaf. */
#include "fnvl_abi.h"
#include "fnvnx/placement.h"   /* FNVNX_NPROC: the one declaration of the declared machine's processor count (L5, hostabi/include) */
/* Leaf 1 EBX: [31:24] initial APIC id 0 (the boot thread's; per-thread ids are the platform's), [23:16] maximum addressable IDs for logical processors in the package
   = FNVNX_NPROC (3 cores x 1 thread; valid because EDX bit 28 HTT is set), [15:8] CLFLUSH line size in 8-byte units (8 = 64 bytes), [7:0] brand index 0. */
#define FNVL_CPUID1_EBX (0x00000800u | (FNVNX_NPROC << 16))
/* Leaf 4 EAX: [31:26] cores per package - 1 = FNVNX_NPROC - 1; [25:14] logical processors sharing the cache - 1: 0 for the per-core L1 and L2 (one thread per core), FNVNX_NPROC - 1 for the shared L3. */
#define FNVL_CPUID4_CORES ((FNVNX_NPROC - 1u) << 26)
#define FNVL_CPUID4_SHARE_L3 ((FNVNX_NPROC - 1u) << 14)
#if defined(__cplusplus)
#define FNVL_SA static_assert
#else
#define FNVL_SA _Static_assert
#endif
FNVL_SA(FNVNX_NPROC >= 1u && FNVNX_NPROC <= 0xffu, "leaf 1 EBX[23:16] holds 8 bits");
FNVL_SA(FNVNX_NPROC - 1u < 64u && FNVNX_NPROC - 1u < 4096u, "leaf 4 EAX[31:26] holds 6 bits, EAX[25:14] 12 bits");
FNVL_SA((FNVL_CPUID1_EDX >> 28 & 1u) == 1u, "HTT (leaf 1 EDX bit 28) is set: EBX[23:16] is a valid count only then (SDM)");
FNVL_SA((FNVL_CPUID1_EBX >> 16 & 0xffu) == FNVNX_NPROC && (FNVL_CPUID1_EBX & 0xffff0000u) >> 16 == FNVNX_NPROC, "EBX layout");
#define FNVL_EAX_FAMILY(e) ((((e) >> 8) & 0xfu) + ((((e) >> 8) & 0xfu) == 0xfu ? (((e) >> 20) & 0xffu) : 0u))
#define FNVL_EAX_MODEL(e) ((((e) >> 4) & 0xfu) | ((((e) >> 8) & 0xfu) == 6u || (((e) >> 8) & 0xfu) == 0xfu ? (((e) >> 16) & 0xfu) << 4 : 0u))
FNVL_SA(FNVL_EAX_FAMILY(FNVL_CPUID1_EAX) == FNVL_CPUREF_FAMILY, "leaf 1 EAX family is the reference CPU's (SDM Vol 2A, CPUID leaf 1)");
FNVL_SA(FNVL_EAX_MODEL(FNVL_CPUID1_EAX) == FNVL_CPUREF_MODEL, "leaf 1 EAX model (with extended model) is the reference CPU's");
FNVL_SA((FNVL_CPUID1_EAX & 0xfu) == FNVL_CPUREF_STEPPING, "leaf 1 EAX stepping is the reference CPU's");
typedef unsigned int u32; typedef unsigned long long u64;
unsigned long long fnvl_host_ticks_ns(void);
void fnvl_cpuid(u32 leaf, u32 sub, u32 o[4]) {
#include "cpumodel_tab.inc"      /* L0 L2 L4 E1 BRAND: generated from verify/cpuref.json and verify/cpuid_model.json (tools/gen_cpuref.py) */
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
