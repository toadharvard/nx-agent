/* The one definition of the layout facts that C code outside the lifted headers (rt.c, compiled without the guest registers fixed and without fnvl.h)
   needs about struct cpu. rt/fnvl.h includes this file and static_asserts every value against the struct, so a change of the struct that
   moves a field is a build error in every unit that includes fnvl.h, never a silent mismatch. */
#ifndef FNVL_ABI_H
#define FNVL_ABI_H
#define FNVL_CPU_RA_OFF 248u     /* offsetof(struct cpu, ra): the address of a missed dispatch travels here (rt/rt.c fnvl_dispatch) */
/* The reference CPU's cpuid leaf 1 (rt/cpumodel.c answers it from here; rt/seh.cc derives the FXSAVE MXCSR_MASK from it). EDX bit 24 FXSR, bit 25 SSE, bit 26 SSE2; ECX 0: no SSE3, no AVX. EDX bit 28 (HTT) is set: the declared machine has FNVNX_NPROC processors and rt/cpumodel.c's leaf 1 EBX[23:16] (valid only when HTT is set, Intel SDM Vol 2A, CPUID leaf 1) derives from hostabi/include/fnvnx/placement.h. */
/* Highest basic cpuid leaf the declared CPU reports (rt/cpumodel.c leaf 0 EAX). Below 7: no structured-feature leaf, see rt/fnvl_x87.h. */
#define FNVL_MAX_BASIC 4u
#define FNVL_CPUID1_EAX 0x000c06f2u
/* The reference CPU's identity (verify/cpuref.json pins the same three numbers; lift/tools/tests/test_cpuref.py compares them). rt/cpumodel.c static_asserts that leaf 1 EAX decodes to them
   by the SDM Vol 2A CPUID leaf 1 formula: family = EAX[11:8] (+ EAX[27:20] when that is 0xF); model = EAX[7:4] | EAX[19:16] << 4 (extended model counts when family is 6 or 0xF); stepping = EAX[3:0]. */
#define FNVL_CPUREF_FAMILY 6u
#define FNVL_CPUREF_MODEL 207u
#define FNVL_CPUREF_STEPPING 2u
#define FNVL_CPUID1_ECX 0x00000000u
#define FNVL_CPUID1_EDX 0x178bfbffu
#endif
