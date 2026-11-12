/* The one definition of the layout facts that C code outside the lifted headers (rt.c, compiled without the guest registers fixed and without fnvl.h)
   needs about struct cpu. rt/fnvl.h includes this file and static_asserts every value against the struct, so a change of the struct that
   moves a field is a build error in every unit that includes fnvl.h, never a silent mismatch. */
#ifndef FNVL_ABI_H
#define FNVL_ABI_H
#define FNVL_CPU_RA_OFF 248u     /* offsetof(struct cpu, ra): the address of a missed dispatch travels here (rt/rt.c fnvl_dispatch) */
/* The reference CPU's cpuid leaf 1 (rt/cpumodel.c answers it from here; rt/seh.cc derives the FXSAVE MXCSR_MASK from it). EDX bit 24 FXSR, bit 25 SSE, bit 26 SSE2; ECX 0: no SSE3, no AVX. */
#define FNVL_CPUID1_EAX 0x000c06f2u
#define FNVL_CPUID1_EBX 0x00010800u
#define FNVL_CPUID1_ECX 0x00000000u
#define FNVL_CPUID1_EDX 0x078bfbffu
/* The reference CPU's TSC: nominal 2.1 GHz (the pinned host's "@ 2.10GHz"), so ticks = ns * FNVL_TSC_NUM / FNVL_TSC_DEN. rt/cpumodel.c (fnvl_rdtsc) and test/instest.py read these two lines: one definition. */
#define FNVL_TSC_NUM 21u
#define FNVL_TSC_DEN 10u
#endif
