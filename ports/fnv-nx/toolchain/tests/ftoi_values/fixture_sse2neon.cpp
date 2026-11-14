#include <fnvnx_compat.h>
extern "C" __attribute__((noinline)) __m64 ps2pi(__m128 a) { return _mm_cvt_ps2pi(a); }
extern "C" __attribute__((noinline)) __m64 ps_pi32(__m128 a) { return _mm_cvtps_pi32(a); }
extern "C" __attribute__((noinline)) __m64 ps_pi16(__m128 a) { return _mm_cvtps_pi16(a); }
extern "C" __attribute__((noinline)) __m64 ps_pi8(__m128 a) { return _mm_cvtps_pi8(a); }
extern "C" __attribute__((noinline)) int ss2si(__m128 a) { return _mm_cvt_ss2si(a); }
extern "C" __attribute__((noinline)) int ss_si32(__m128 a) { return _mm_cvtss_si32(a); }
