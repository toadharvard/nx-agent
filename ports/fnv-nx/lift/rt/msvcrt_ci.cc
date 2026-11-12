/* msvcrt.dll _CI* intrinsics as guest entries of the lifter runtime. C++ (it includes fnvl.h: guest registers w19..w26, c in x27); compile with the lifted-code flags, -x c++ (the recipe lift/pack/build.json: flags of test/instest.py FLAGS):
     aarch64-linux-gnu-g++ -x c++ <FLAGS> -c msvcrt_ci.cc
   Bridge rows (manifest/bridge.tsv, bind = l3): msvcrt.dll _CIacos _CIasin _CIatan _CIatan2 _CIcos _CIcosh _CIexp _CIfmod _CIlog _CIpow _CIsin _CIsinh _CIsqrt _CItan _CItanh, cdecl, symbols fnvl_msvcrt_CI<name>.
   CALLING CONVENTION (the MSVC intrinsic helpers, as Wine 9.0 dlls/msvcrt/math.c CREATE_FPU_FUNC1 / CREATE_FPU_FUNC2 implement them): no stack arguments; the operands are on the x87 stack and are replaced by the
   result. One operand: ST0 -> ST0. Two operands: the wrapper pops ST0 into the SECOND argument and ST1 into the FIRST (`fstpl 8(%esp)` then `fstpl (%esp)`), calls the C function (arg1, arg2) and pushes the result:
   _CIatan2: ST1 = y, ST0 = x -> atan2(y, x); _CIpow: ST1 = x, ST0 = y -> pow(x, y); _CIfmod: ST1 = x, ST0 = y -> fmod(x, y). Deeper stack entries are untouched (Wine saves and restores them; here they simply stay).
   The operands are popped BEFORE the result is pushed, so an eight-deep stack works. An empty operand register gives the real indefinite and IE|SF through dyn_rd / dyn_pop (fnvl_x87.h).
   VALUES: every result is the correctly rounded binary64 value of the true function (fmod and sqrt exact), computed by rt/fmath.c; Annex F for the special operands. The x87 exception bits (IE ZE OE UE PE) it reports are ORed into
   c->fex and C1 is assigned (FM_FL), like the fyl2x / f2xm1 instruction handlers do.
   ERRORS (what Wine's msvcrt returns and sets; msvcrt's math error path with the default _matherr, which returns 0): the guest cannot install a user handler (no lifted module imports __setusermatherr or _matherr:
   manifest/imports.tsv), so the path reduces to "errno := EDOM / ERANGE" and the result unchanged. Rule (the class, from the operands, the result and the flags of fmath):
     EDOM   (33): the result is a NaN and no operand is a NaN (acos / asin outside [-1, 1], log / sqrt of a negative number, pow of a negative number to a non-integer power, fmod(x, 0), fmod(inf, y), sin / cos / tan of
                  an infinity), or the operand is a NaN of _CIasin / _CIatan / _CIexp / _CItanh / _CIsqrt (Wine's MSVCRT_asin, MSVCRT_atan, MSVCRT_exp, MSVCRT_tanh, MSVCRT_sqrt call math_error(_DOMAIN) for a NaN argument);
     ERANGE (34): the result is an infinity and every operand is finite (log 0, pow(0, y < 0), overflow of exp / cosh / sinh / pow), or the result is a zero that is the rounding of a non-zero value (UE reported: underflow of
                  exp / pow / atan2).
   The platform function used: `int *_errno(void)` (platform/crt/heap.c, bridge row msvcrt.dll _errno, bind lookup): the address of the calling thread's errno, written with the MSVC values EDOM = 33, ERANGE = 34 (the
   same numbers as the platform's newlib / glibc errno.h). Nothing else is called. */
#include "fnvl.h"
extern "C" void *fnvnx_win32_lookup_export(const char *dll, const char *name);      /* the platform's own table of its msvcrt exports: the same lookup the import entry of the row uses (resolved for every lookup row before the first guest instruction: fnvnx_lift_imports_init) */
enum { CI_EDOM = 33, CI_ERANGE = 34 };

static inline int ci_nan(double d) { return (d2u(d) & 0x7fffffffffffffffull) > 0x7ff0000000000000ull; }
static inline int ci_fin(double d) { return (d2u(d) & 0x7ff0000000000000ull) != 0x7ff0000000000000ull; }
static inline int ci_inf(double d) { return (d2u(d) & 0x7fffffffffffffffull) == 0x7ff0000000000000ull; }
/* errno of the result r of f(a) / f(a, b) (b = 0.0 for one operand: finite and not NaN), fl the fmath flags; nandom: Wine's NaN-operand domain error */
static void ci_errno(double r, double a, double b, u32 fl, int nandom) {
  u32 err = 0;
  if (ci_nan(r) && !ci_nan(a) && !ci_nan(b)) err = CI_EDOM;
  else if (nandom && ci_nan(a)) err = CI_EDOM;
  else if (ci_inf(r) && ci_fin(a) && ci_fin(b)) err = CI_ERANGE;
  else if ((d2u(r) & 0x7fffffffffffffffull) == 0 && (fl & 0x10u)) err = CI_ERANGE;
  if (err) { REGS(); *((int *(*)(void))fnvnx_win32_lookup_export("msvcrt.dll", "_errno"))() = (int)err; REGS(); }
}

#define CI1(NAME, FN, NANDOM) \
  extern "C" void fnvl_msvcrt_CI##NAME(void) { \
    u32 ra = M32(esp), f_ = 0; \
    double a_ = fv_d(dyn_rd(0)); dyn_pop(); \
    double r_ = FN(a_, &f_); \
    ci_errno(r_, a_, 0.0, f_, NANDOM); \
    dyn_push(fv_f64(r_)); FM_FL(f_); \
    c->ra = ra; esp += 4; }
#define CI2(NAME, FN) \
  extern "C" void fnvl_msvcrt_CI##NAME(void) { \
    u32 ra = M32(esp), f_ = 0; \
    double a_ = fv_d(dyn_rd(1)), b_ = fv_d(dyn_rd(0)); dyn_pop(); dyn_pop(); \
    double r_ = FN(a_, b_, &f_); \
    ci_errno(r_, a_, b_, f_, 0); \
    dyn_push(fv_f64(r_)); FM_FL(f_); \
    c->ra = ra; esp += 4; }

CI1(acos, fnvl_m_acos, 0)
CI1(asin, fnvl_m_asin, 1)
CI1(atan, fnvl_m_atan, 1)
CI2(atan2, fnvl_m_atan2)
CI1(cos, fnvl_m_cos, 0)
CI1(cosh, fnvl_m_cosh, 0)
CI1(exp, fnvl_m_exp, 1)
CI2(fmod, fnvl_m_fmod)
CI1(log, fnvl_m_log, 0)
CI2(pow, fnvl_m_pow)
CI1(sin, fnvl_m_sin, 0)
CI1(sinh, fnvl_m_sinh, 0)
CI1(sqrt, fnvl_m_sqrt, 1)
CI1(tan, fnvl_m_tan, 0)
CI1(tanh, fnvl_m_tanh, 1)
