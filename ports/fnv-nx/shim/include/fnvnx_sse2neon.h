/* fnvnx_sse2neon.h - sse2neon (SSE on NEON) with x86's results where sse2neon differs. Every unit gets it through
 * fnvnx_compat.h; the <xmmintrin.h> / <emmintrin.h> shims include it too.
 *
 * Float -> int32 conversions (_mm_cvt_ss2si / _mm_cvtss_si32, _mm_cvt_ps2pi / _mm_cvtps_pi32, _mm_cvtps_pi16, _mm_cvtps_pi8): x86 gives the integer
 * indefinite 0x80000000 for NaN and for anything outside the integer type, where sse2neon's vcvtnq saturates. fnvnx_ftoi rewrites every target
 * float -> int builtin they reach to x86's result by one in-range rule; the rounding stays the run-time FPCR mode (frinti), as x86 reads MXCSR.
 * Test: toolchain/tests/test_ftoi_sse2neon.py (the real compat headers and the pinned sse2neon).
 *
 * SSE2NEON_PRECISE_MINMAX = 1 (below). */
#ifndef FNVNX_SSE2NEON_H
#define FNVNX_SSE2NEON_H
/* min / max (_mm_min_ps / _mm_max_ps / _ss / _pd / _sd): x86 returns the second operand when either is NaN (and for
 * +0 / -0); NEON fmin / fmax propagate the NaN. sse2neon's precise form (compare + select) gives x86's result (EQ fuzz:
 * 0x00d1bad0 read NaN where the exe kept the other operand; orchestrator decision, the cost goes to D21). */
#ifndef SSE2NEON_PRECISE_MINMAX
#define SSE2NEON_PRECISE_MINMAX 1
#endif
#include <sse2neon.h>
#endif
