/* math.c - MSVC float classification helpers and the x87/SSE control word.
 *
 * _controlfp/_control87 keep MSVC's abstract control word. Only the rounding-control field (_MCW_RC) has an
 * effect: it is mapped onto AArch64 FPCR.RMode (bits 23:22), so S8's replacement of x87 fistp (which rounds
 * in the current mode) and every other conversion follow the game's requests:
 *     _RC_NEAR -> RN (00)   _RC_UP -> RP (01)   _RC_DOWN -> RM (10)   _RC_CHOP -> RZ (11)
 * Precision control (_MCW_PC: x87 24/53/64-bit mantissa) is accepted, remembered and ignored: AArch64 has
 * no such mode and the game only ever asks for 24 or 53 bits for speed. Exception masks (_MCW_EM) are
 * remembered; traps are never enabled. Denormal control (_MCW_DN, flush-to-zero) maps onto FPCR.FZ. */
#include "fnvnx_msvcrt.h"

#include <fenv.h>
#include <math.h>
#include <stdint.h>

int _finite(double x) { return isfinite(x) ? 1 : 0; }
int _isnan(double x) { return isnan(x) ? 1 : 0; }
double _chgsign(double x) { return -x; }
double _copysign(double x, double y) { return copysign(x, y); }
double _hypot(double x, double y) { return hypot(x, y); }
float _hypotf(float x, float y) { return hypotf(x, y); }
double _logb(double x) { return logb(x); }
double _nextafter(double x, double y) { return nextafter(x, y); }
double _scalb(double x, long e) { return scalbn(x, (int)e); }

/* _ftol2 / _ftol2_sse: truncation toward zero into 64 bits; out of range and NaN give the x87 "integer
 * indefinite" 0x8000000000000000 like cvttsd2si/fistp do. */
long long fnvnx_ftol2(double x)
{
    if (!(x > -9223372036854775808.0 && x < 9223372036854775808.0))
        return (long long)0x8000000000000000ull;
    return (long long)x;
}

#define RMODE_SHIFT 22
#define FPCR_FZ (1u << 24)

#if defined(__aarch64__)
static inline uint64_t get_fpcr(void) { uint64_t v; __asm__ __volatile__("mrs %0, fpcr" : "=r"(v)); return v; }
static inline void set_fpcr(uint64_t v) { __asm__ __volatile__("msr fpcr, %0" : : "r"(v)); }
#endif

static unsigned int g_cw = _CW_DEFAULT; /* PC, EM and DN fields as last set (RC is read from hardware) */

static unsigned int hw_rc(void)
{
#if defined(__aarch64__)
    switch ((get_fpcr() >> RMODE_SHIFT) & 3) {
    case 0: return _RC_NEAR;
    case 1: return _RC_UP;
    case 2: return _RC_DOWN;
    default: return _RC_CHOP;
    }
#else
    switch (fegetround()) {
    case FE_UPWARD: return _RC_UP;
    case FE_DOWNWARD: return _RC_DOWN;
    case FE_TOWARDZERO: return _RC_CHOP;
    default: return _RC_NEAR;
    }
#endif
}

static void set_hw(unsigned int cw)
{
#if defined(__aarch64__)
    uint64_t r;
    switch (cw & _MCW_RC) {
    case _RC_UP: r = 1; break;
    case _RC_DOWN: r = 2; break;
    case _RC_CHOP: r = 3; break;
    default: r = 0; break;
    }
    uint64_t v = get_fpcr() & ~((uint64_t)3 << RMODE_SHIFT) & ~(uint64_t)FPCR_FZ;
    v |= r << RMODE_SHIFT;
    if ((cw & _MCW_DN) == _DN_FLUSH)
        v |= FPCR_FZ;
    set_fpcr(v);
#else
    switch (cw & _MCW_RC) {
    case _RC_UP: fesetround(FE_UPWARD); break;
    case _RC_DOWN: fesetround(FE_DOWNWARD); break;
    case _RC_CHOP: fesetround(FE_TOWARDZERO); break;
    default: fesetround(FE_TONEAREST); break;
    }
#endif
}

unsigned int _control87(unsigned int newval, unsigned int mask)
{
    unsigned int cur = (g_cw & ~_MCW_RC) | hw_rc();
    if (mask) {
        cur = (cur & ~mask) | (newval & mask);
        g_cw = cur;
        set_hw(cur);
    }
    return cur;
}

unsigned int _controlfp(unsigned int newval, unsigned int mask)
{
    /* _controlfp ignores _EM_DENORMAL in the mask, as MSVC's does */
    return _control87(newval, mask & ~(unsigned int)_EM_DENORMAL);
}

errno_t _controlfp_s(unsigned int *cur, unsigned int newval, unsigned int mask)
{
    unsigned int valid = _MCW_EM | _MCW_RC | _MCW_PC | _MCW_DN;
    if (mask & ~valid) {
        if (cur)
            *cur = _control87(0, 0);
        return FNVNX_EINVAL;
    }
    unsigned int r = _controlfp(newval, mask);
    if (cur)
        *cur = r;
    return 0;
}

/* status word in MSVC's abstract _SW_* encoding */
#define SW_INEXACT 0x01
#define SW_UNDERFLOW 0x02
#define SW_OVERFLOW 0x04
#define SW_ZERODIVIDE 0x08
#define SW_INVALID 0x10

unsigned int _statusfp(void)
{
    int e = fetestexcept(FE_ALL_EXCEPT);
    unsigned int s = 0;
    if (e & FE_INEXACT) s |= SW_INEXACT;
    if (e & FE_UNDERFLOW) s |= SW_UNDERFLOW;
    if (e & FE_OVERFLOW) s |= SW_OVERFLOW;
    if (e & FE_DIVBYZERO) s |= SW_ZERODIVIDE;
    if (e & FE_INVALID) s |= SW_INVALID;
    return s;
}

unsigned int _clearfp(void)
{
    unsigned int s = _statusfp();
    feclearexcept(FE_ALL_EXCEPT);
    return s;
}

void _fpreset(void)
{
    feclearexcept(FE_ALL_EXCEPT);
    _control87(_CW_DEFAULT, 0xFFFFFFFFu);
}
