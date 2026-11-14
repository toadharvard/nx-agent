#include <arm_neon.h>
typedef float v4sf __attribute__((vector_size(16)));
typedef int v4si __attribute__((vector_size(16)));
typedef unsigned v4su __attribute__((vector_size(16)));
typedef double v2df __attribute__((vector_size(16)));
typedef long long v2di __attribute__((vector_size(16)));
typedef unsigned long long v2du __attribute__((vector_size(16)));
#define V(n, T, e, x) extern "C" T n(e a) { return x; }
// vector builtins, signed: every rounding (z n a m p), 4 / 2 / 2 lanes
V(tr4, int32x4_t, float32x4_t, vcvtq_s32_f32(a)) V(nr4, int32x4_t, float32x4_t, vcvtnq_s32_f32(a)) V(aw4, int32x4_t, float32x4_t, vcvtaq_s32_f32(a))
V(fl4, int32x4_t, float32x4_t, vcvtmq_s32_f32(a)) V(ce4, int32x4_t, float32x4_t, vcvtpq_s32_f32(a))
V(tr2, int32x2_t, float32x2_t, vcvt_s32_f32(a)) V(nr2, int32x2_t, float32x2_t, vcvtn_s32_f32(a))
V(trd, int64x2_t, float64x2_t, vcvtq_s64_f64(a)) V(nrd, int64x2_t, float64x2_t, vcvtnq_s64_f64(a)) V(awd, int64x2_t, float64x2_t, vcvtaq_s64_f64(a))
V(fld, int64x2_t, float64x2_t, vcvtmq_s64_f64(a)) V(ced, int64x2_t, float64x2_t, vcvtpq_s64_f64(a))
// vector builtins, unsigned
V(utr4, uint32x4_t, float32x4_t, vcvtq_u32_f32(a)) V(unr4, uint32x4_t, float32x4_t, vcvtnq_u32_f32(a)) V(uaw4, uint32x4_t, float32x4_t, vcvtaq_u32_f32(a))
V(ufl4, uint32x4_t, float32x4_t, vcvtmq_u32_f32(a)) V(uce4, uint32x4_t, float32x4_t, vcvtpq_u32_f32(a))
V(utr2, uint32x2_t, float32x2_t, vcvt_u32_f32(a))
V(utrd, uint64x2_t, float64x2_t, vcvtq_u64_f64(a)) V(unrd, uint64x2_t, float64x2_t, vcvtnq_u64_f64(a)) V(uawd, uint64x2_t, float64x2_t, vcvtaq_u64_f64(a))
V(ufld, uint64x2_t, float64x2_t, vcvtmq_u64_f64(a)) V(uced, uint64x2_t, float64x2_t, vcvtpq_u64_f64(a))
// fixed-point (fcvtz, a fraction-bits operand)
V(fx4, int32x4_t, float32x4_t, vcvtq_n_s32_f32(a, 8)) V(ufx4, uint32x4_t, float32x4_t, vcvtq_n_u32_f32(a, 5)) V(fxd, int64x2_t, float64x2_t, vcvtq_n_s64_f64(a, 10))
// scalars
extern "C" int tr1(float a) { return vcvts_s32_f32(a); }
extern "C" int nr1(float a) { return vcvtns_s32_f32(a); }
extern "C" unsigned utr1(float a) { return vcvts_u32_f32(a); }
extern "C" long long trd1(double a) { return vcvtd_s64_f64(a); }
extern "C" long long nrd1(double a) { return vcvtnd_s64_f64(a); }
extern "C" unsigned long long utrd1(double a) { return vcvtd_u64_f64(a); }
extern "C" int fx1(float a) { return vcvts_n_s32_f32(a, 4); }
extern "C" unsigned ufx1(float a) { return vcvts_n_u32_f32(a, 7); }
extern "C" long long fxd1(double a) { return vcvtd_n_s64_f64(a, 12); }
extern "C" long long fs64(float a) { return __builtin_aarch64_fix_truncsfdi(a); }
extern "C" int fd32(double a) { return __builtin_aarch64_fix_truncdfsi(a); }
// generic vector conversions: FIX_TRUNC_EXPR on vectors, __builtin_convertvector (IFN_VEC_CONVERT), unsigned lanes
V(cv4, v4si, v4sf, __builtin_convertvector(a, v4si)) V(ucv4, v4su, v4sf, __builtin_convertvector(a, v4su)) V(cvd, v2di, v2df, __builtin_convertvector(a, v2di))
V(ucvd, v2du, v2df, __builtin_convertvector(a, v2du))
// the BUILT_IN_NORMAL families (long is 32 bits: ILP32)
extern "C" long lrf(float a) { return __builtin_lrintf(a); }
extern "C" long lrd(double a) { return __builtin_lrint(a); }
extern "C" long long llrd(double a) { return __builtin_llrint(a); }
extern "C" long lrdn(double a) { return __builtin_lround(a); }
extern "C" long long llrf(float a) { return __builtin_llroundf(a); }
extern "C" long lcd(double a) { return __builtin_lceil(a); }
extern "C" long lfd(double a) { return __builtin_lfloor(a); }
extern "C" int ird(double a) { return __builtin_irint(a); }
extern "C" int16x4_t mp3(float32x4_t a, int32x4_t b) { return vqmovn_s32(vqaddq_s32(vcvtq_s32_f32(a), b)); }
