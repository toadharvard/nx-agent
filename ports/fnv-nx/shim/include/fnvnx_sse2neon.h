/* fnvnx_sse2neon.h - sse2neon (SSE on NEON) with x86's results where sse2neon differs. Every unit gets it through
 * fnvnx_compat.h; the <xmmintrin.h> / <emmintrin.h> shims include it too.
 *
 * _mm_cvt_ss2si / _mm_cvtss_si32: x86 cvtss2si rounds by MXCSR and gives the integer indefinite 0x80000000 for NaN
 * and for anything outside int32; sse2neon's vcvtnq saturates (0x7fffffff for 2^31 and up and +inf, 0 for NaN;
 * V1, tests/v1/test_ftoi_fuzz.py `mm_cvtss`). The other float -> int32 conversions of sse2neon (cvtt*, cvtps_epi32,
 * cvtsd_si32) already return INT32_MIN there. A float is in range exactly when -2^31 <= f < 2^31 (the floats below
 * 2^31 end at 2147483520, which rounds to itself), so the check needs no rounding.
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
#ifdef __cplusplus
extern "C++" {
#endif
static inline int fnvnx_mm_cvt_ss2si(__m128 a)
{
    float f = vgetq_lane_f32(vreinterpretq_f32_m128(a), 0);
    if (!(f >= -2147483648.0f && f < 2147483648.0f)) return INT32_MIN;   /* NaN fails both */
    return (_mm_cvt_ss2si)(a);
}
#ifdef __cplusplus
}
#endif
#undef _mm_cvtss_si32
#define _mm_cvtss_si32(a) fnvnx_mm_cvt_ss2si(a)
#define _mm_cvt_ss2si(a) fnvnx_mm_cvt_ss2si(a)
#endif
