/* fmath: the x87 transcendental and remainder forms of the reference CPU model (README "One reference CPU model", R8).
   Every function takes and returns binary64 values. The results of sin cos tan(fptan) atan2(fpatan) 2^x-1(f2xm1) y*log2(x)(fyl2x) y*log2(x+1)(fyl2xp1) are the
   CORRECTLY ROUNDED (nearest-even) values of CORE-MATH (cr_*, below) in a private host FP environment; fscale, fxtract and fprem/fprem1 are exact (integer arithmetic).
   The msvcrt functions below (acos asin atan cosh sinh tanh exp log pow) are the integer engine of rt/fmath.c. Rule and proof: rt/fmath.c.
   *fl (every function): the x87 exception bits IE 1, ZE 4, OE 8, UE 0x10, PE 0x20 are ORed in, and C1 0x200 (FSW positions): C1 = the inexact result was rounded
   up in magnitude. The caller ORs the exception bits into c->fex and assigns C1 (fnvl_x87.h FM_FL). */
#ifndef FNVL_FMATH_H
#define FNVL_FMATH_H
#ifndef FNVL_TYPES
#define FNVL_TYPES
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64;
#endif
#ifdef __cplusplus
extern "C" {
#endif
/* 1 when fsin/fcos/fsincos/fptan must set C2 and leave the stack unchanged: x finite and |x| >= 2^63 (SDM). Otherwise the functions below apply. */
u32 fnvl_m_trig_big(double x);
/* CORE-MATH (the pinned library, built by the runtime root: the symbols are NOT defined in lift/rt): correctly rounded binary64 functions, the one declaration of them. */
double cr_sin(double); double cr_cos(double); double cr_tan(double); double cr_exp2m1(double);
double cr_log2(double); double cr_log2p1(double); double cr_atan2(double, double);
/* kinds of fnvl_m_cr1 / fnvl_m_crl: the macros of rt/fnvl_x87.h paste FMK_ in front of the CORE-MATH function they are given */
enum { FMK_cr_sin, FMK_cr_cos, FMK_cr_tan, FMK_cr_exp2m1, FMK_cr_log2, FMK_cr_log2p1 };
double fnvl_m_cr1(double (*cr)(double), u32 k, double x, u32 *fl);            /* sin cos tan 2^x - 1 (f2xm1) */
double fnvl_m_cr2(double (*cr)(double, double), double y, double x, u32 *fl); /* fpatan: y = ST(1), x = ST(0) */
double fnvl_m_crl(double (*cr)(double), u32 k, double y, double x, u32 *fl);  /* fyl2x (cr_log2: y * log2(x)) and fyl2xp1 (cr_log2p1: y * log2(x + 1)) */
double fnvl_m_sin(double x, u32 *fl);
double fnvl_m_cos(double x, u32 *fl);
double fnvl_m_tan(double x, u32 *fl);
double fnvl_m_atan2(double y, double x, u32 *fl);
double fnvl_m_scale(double x, double e, u32 *fl);             /* x * 2^trunc(e) */
void fnvl_m_xtract(double x, double *expo, double *sig, u32 *fl);   /* fxtract: ST0 := expo, then push sig */
/* fprem (near = 0) and fprem1 (near = 1): a = ST(0), b = ST(1). Returns the new ST(0); *cf gets C0 0x100, C1 0x200, C2 0x400, C3 0x4000 (FSW positions). */
double fnvl_m_fprem(double a, double b, u32 near, u32 *cf, u32 *fl);
/* msvcrt math library (the 15 _CI* entries, rt/msvcrt_ci.cc): correctly rounded (nearest-even) binary64 results of the true functions, fmod and sqrt exact, C99 Annex F values for zero / infinite / NaN /
   out-of-domain operands (invalid -> the default NaN 0xFFF8..., NaN -> quiet NaN, log 0 and pow(0, y < 0) -> ZE and +-inf). *fl as above. The caller derives errno from operands, result and *fl (rt/msvcrt_ci.cc). */
double fnvl_m_acos(double x, u32 *fl);
double fnvl_m_asin(double x, u32 *fl);
double fnvl_m_atan(double x, u32 *fl);
double fnvl_m_cosh(double x, u32 *fl);
double fnvl_m_sinh(double x, u32 *fl);
double fnvl_m_tanh(double x, u32 *fl);
double fnvl_m_exp(double x, u32 *fl);
double fnvl_m_log(double x, u32 *fl);                          /* natural logarithm */
double fnvl_m_pow(double x, double y, u32 *fl);
double fnvl_m_fmod(double x, double y, u32 *fl);               /* exact: x - trunc(x/y) y */
double fnvl_m_sqrt(double x, u32 *fl);                         /* IEEE sqrt */
#ifdef __cplusplus
}
#endif
#endif
