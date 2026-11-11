/* msvcrt.dll floating-point control and classification on the GUEST fp state: the guest entries of the bridge rows bound `l3` (_controlfp, _clearfp, _fpclass,
   _finite, _isnan). C++, lifted-code flags: aarch64-linux-gnu-g++ -x c++ <FLAGS> -I rt -c msvcrt_fp.cc
   Reference: Wine 9.0 dlls/msvcrt/math.c (i386, sse2_supported = the reference CPU has SSE2): _controlfp -> _control87 -> __control87_2 -> _setfp (x87) and
   _setfp_sse; _clearfp -> _setfp / _setfp_sse with sw_mask = _MCW_EM; _fpclass, _finite, _isnan. cdecl: arguments at esp+4.., result in eax.
   The state is the lifted code's own (rt/fnvl.h, rt/fnvl_x87.h): the x87 control word c->fcw and the SSE control/status word c->mxcsr, changed with the same
   helpers the lifted fldcw / ldmxcsr / fnclex forms use (FLDCW: control word + host rounding mode; sse_fpcr; FNCLEX: c->fex and the host FPSR), in the order
   Wine executes the instructions (x87 first, then SSE). The x87 sticky flags are read as `fnstsw` reads them (host FPSR bits | c->fex); the SSE sticky bits are
   the ones of c->mxcsr, which is all `stmxcsr` shows (the model sets no SIMD status bits itself: rt/fnvl.h). Only the two call patterns of the entries are
   transcribed: _setfp(cw, mask, NULL, 0) and _setfp(NULL, 0, &sw, _MCW_EM), the same for the SSE variant. */
#include "fnvl.h"
#include "imp.h"

extern "C" {
void fnvl_msvcrt_controlfp(void);
void fnvl_msvcrt_clearfp(void);
void fnvl_msvcrt_fpclass(void);
void fnvl_msvcrt_finite(void);
void fnvl_msvcrt_isnan(void);
}

#define MCW_EM 0x0008001fu
#define MCW_IC 0x00040000u
#define MCW_RC 0x00000300u
#define MCW_PC 0x00030000u
#define MCW_DN 0x03000000u
#define EM_INVALID 0x10u
#define EM_DENORMAL 0x80000u
#define EM_ZERODIVIDE 8u
#define EM_OVERFLOW 4u
#define EM_UNDERFLOW 2u
#define EM_INEXACT 1u
#define IC_AFFINE 0x40000u
#define RC_UP 0x200u
#define RC_DOWN 0x100u
#define PC_24 0x20000u
#define PC_53 0x10000u
#define PC_64 0u
#define DN_FLUSH 0x01000000u
#define DN_FLUSH_OPERANDS_SAVE_RESULTS 0x02000000u
#define DN_SAVE_OPERANDS_FLUSH_RESULTS 0x03000000u
#define EM_AMBIGUOUS 0x80000000u
#define SW_INEXACT 1u
#define SW_UNDERFLOW 2u
#define SW_OVERFLOW 4u
#define SW_ZERODIVIDE 8u
#define SW_INVALID 0x10u
#define SW_DENORMAL 0x80000u

/* the six exception bits of an x87 status word / MXCSR (IE DE ZE OE UE PE, bits 0..5) -> _SW_* */
static u32 sw_flags(u32 w)
{
    u32 f = 0;
    if (w & 0x1) f |= SW_INVALID;
    if (w & 0x2) f |= SW_DENORMAL;
    if (w & 0x4) f |= SW_ZERODIVIDE;
    if (w & 0x8) f |= SW_OVERFLOW;
    if (w & 0x10) f |= SW_UNDERFLOW;
    if (w & 0x20) f |= SW_INEXACT;
    return f;
}

/* Wine _setfp, x87 part with cw != NULL, sw == NULL: new = (current & ~mask) | (newval & mask) over the fields EM, IC, RC, PC; fldcw when the word changed.
   -> the control flags after the change (*cw) */
static u32 x87_control(u32 newval, u32 mask)
{
    u32 oldcw = c->fcw, newcw = oldcw, flags = 0;                  /* fstcw */
    mask &= MCW_EM | MCW_IC | MCW_RC | MCW_PC;
    if (newcw & 0x1) flags |= EM_INVALID;
    if (newcw & 0x2) flags |= EM_DENORMAL;
    if (newcw & 0x4) flags |= EM_ZERODIVIDE;
    if (newcw & 0x8) flags |= EM_OVERFLOW;
    if (newcw & 0x10) flags |= EM_UNDERFLOW;
    if (newcw & 0x20) flags |= EM_INEXACT;
    switch (newcw & 0xc00) { case 0xc00: flags |= RC_UP | RC_DOWN; break; case 0x800: flags |= RC_UP; break; case 0x400: flags |= RC_DOWN; break; }
    switch (newcw & 0x300) { case 0x0: flags |= PC_24; break; case 0x200: flags |= PC_53; break; case 0x300: flags |= PC_64; break; }
    if (newcw & 0x1000) flags |= IC_AFFINE;
    u32 cw = (flags & ~mask) | (newval & mask);
    newcw &= ~0x1f3fu;
    if (cw & EM_INVALID) newcw |= 0x1;
    if (cw & EM_DENORMAL) newcw |= 0x2;
    if (cw & EM_ZERODIVIDE) newcw |= 0x4;
    if (cw & EM_OVERFLOW) newcw |= 0x8;
    if (cw & EM_UNDERFLOW) newcw |= 0x10;
    if (cw & EM_INEXACT) newcw |= 0x20;
    switch (cw & MCW_RC) { case RC_UP | RC_DOWN: newcw |= 0xc00; break; case RC_UP: newcw |= 0x800; break; case RC_DOWN: newcw |= 0x400; break; }
    switch (cw & MCW_PC) { case PC_64: newcw |= 0x300; break; case PC_53: newcw |= 0x200; break; case PC_24: newcw |= 0x0; break; }
    if (cw & IC_AFFINE) newcw |= 0x1000;
    if (oldcw != newcw) FLDCW(newcw);
    return cw;
}

/* Wine _setfp_sse with cw != NULL, sw == NULL: fields EM, RC, DN of MXCSR; the status bits are cleared when anything changes; ldmxcsr when the word changed */
static u32 sse_control(u32 newval, u32 mask)
{
    u32 old_fpword = c->mxcsr, fpword = old_fpword, flags = 0;     /* stmxcsr */
    mask &= MCW_EM | MCW_RC | MCW_DN;
    if (fpword & 0x80) flags |= EM_INVALID;
    if (fpword & 0x100) flags |= EM_DENORMAL;
    if (fpword & 0x200) flags |= EM_ZERODIVIDE;
    if (fpword & 0x400) flags |= EM_OVERFLOW;
    if (fpword & 0x800) flags |= EM_UNDERFLOW;
    if (fpword & 0x1000) flags |= EM_INEXACT;
    switch (fpword & 0x6000) { case 0x6000: flags |= RC_UP | RC_DOWN; break; case 0x4000: flags |= RC_UP; break; case 0x2000: flags |= RC_DOWN; break; }
    switch (fpword & 0x8040) {
    case 0x0040: flags |= DN_FLUSH_OPERANDS_SAVE_RESULTS; break;
    case 0x8000: flags |= DN_SAVE_OPERANDS_FLUSH_RESULTS; break;
    case 0x8040: flags |= DN_FLUSH; break;
    }
    u32 cw = (flags & ~mask) | (newval & mask);
    fpword &= ~0xffc0u;
    if (cw & EM_INVALID) fpword |= 0x80;
    if (cw & EM_DENORMAL) fpword |= 0x100;
    if (cw & EM_ZERODIVIDE) fpword |= 0x200;
    if (cw & EM_OVERFLOW) fpword |= 0x400;
    if (cw & EM_UNDERFLOW) fpword |= 0x800;
    if (cw & EM_INEXACT) fpword |= 0x1000;
    switch (cw & MCW_RC) { case RC_UP | RC_DOWN: fpword |= 0x6000; break; case RC_UP: fpword |= 0x4000; break; case RC_DOWN: fpword |= 0x2000; break; }
    switch (cw & MCW_DN) {
    case DN_FLUSH_OPERANDS_SAVE_RESULTS: fpword |= 0x0040; break;
    case DN_SAVE_OPERANDS_FLUSH_RESULTS: fpword |= 0x8000; break;
    case DN_FLUSH: fpword |= 0x8040; break;
    }
    if (fpword != old_fpword) fpword &= ~0x3fu;                    /* clear the status word if anything changes */
    if (fpword != old_fpword) { c->mxcsr = fpword; sse_fpcr(fpword); }       /* ldmxcsr */
    return cw;
}

/* _controlfp(newval, mask), cdecl: Wine _controlfp -> _control87 (mask without _EM_DENORMAL) -> __control87_2 (x87, then SSE) */
extern "C" void fnvl_msvcrt_controlfp(void)
{
    u32 newval = M32(esp + 4), mask = M32(esp + 8) & ~EM_DENORMAL;
    u32 flags = x87_control(newval, mask), sse2_cw = sse_control(newval, mask);
    if ((flags ^ sse2_cw) & (MCW_EM | MCW_RC)) flags |= EM_AMBIGUOUS;
    flags |= sse2_cw;
    eax = flags; c->ra = M32(esp); esp += 4;
}

/* _clearfp(), cdecl: the status flags of the x87 unit and of MXCSR as _SW_* bits, both cleared. Wine _setfp / _setfp_sse with sw_mask = _MCW_EM (which covers
   every _SW_* bit, so the new status is empty): the x87 status is cleared with fnclex when any of its six bits was set, MXCSR is written when it changed. */
extern "C" void fnvl_msvcrt_clearfp(void)
{
    u32 sw = (fnvl_fp_x87_flags() | c->fex) & 0x3f;                 /* fnstsw: host FPSR flags | the bits the host FPSR cannot hold */
    u32 flags = sw_flags(sw);
    if (sw) FNCLEX();
    u32 old_fpword = c->mxcsr, fpword = old_fpword & ~0x3fu;
    flags |= sw_flags(old_fpword);
    if (fpword != old_fpword) { c->mxcsr = fpword; sse_fpcr(fpword); }
    eax = flags; c->ra = M32(esp); esp += 4;
}

/* _fpclass(double), cdecl: Wine _fpclass */
extern "C" void fnvl_msvcrt_fpclass(void)
{
    u64 u = M64(esp + 4); u32 e = (u32)(u >> 52) & 0x7ff, s = (u32)(u >> 63), r;
    if (e == 0) r = (u << 1) ? (s ? 0x10 : 0x80) : (s ? 0x20 : 0x40);               /* _FPCLASS_ND / PD, NZ / PZ */
    else if (e == 0x7ff) r = (u << 12) ? (((u >> 51) & 1) ? 0x2 : 0x1) : (s ? 0x4 : 0x200);     /* QNAN / SNAN, NINF / PINF */
    else r = s ? 0x8 : 0x100;                                                          /* NN / PN */
    eax = r; c->ra = M32(esp); esp += 4;
}

/* _finite(double), _isnan(double), cdecl: Wine _finite, _isnan */
extern "C" void fnvl_msvcrt_finite(void)
{
    u64 u = M64(esp + 4);
    eax = (u & (~0ull >> 1)) < (0x7ffull << 52); c->ra = M32(esp); esp += 4;
}
extern "C" void fnvl_msvcrt_isnan(void)
{
    u64 u = M64(esp + 4);
    eax = (u & (~0ull >> 1)) > (0x7ffull << 52); c->ra = M32(esp); esp += 4;
}
