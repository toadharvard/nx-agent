// Host build of the pure floating-point helpers of the lifted runtime (rt/fnvl_fpc.h, the one text the guest compiles).  The hooks below are the host side of the AArch64 FPCR / FPSR
// operations of rt/fnvl.h: the same operations through ISO C <fenv.h> (rounding direction, the sticky IEEE flags).  The helper text is not copied: it is the generated output GEN of lift/rt.
#include <fenv.h>
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64; typedef unsigned __int128 u128; typedef int s32;
struct cpu_ { u32 mxcsr; };
static cpu_ cpu_state; static cpu_ *const c = &cpu_state;
#define FOWN_SSE() do{}while(0)
#define FNVL_F2I
#define FNVL_FPREG "+x"
static inline u32 sse_live(void){ return 0; }
static inline void fnvl_fp_x87_clear(void){ feclearexcept(FE_ALL_EXCEPT); }
static inline u32 fnvl_fp_x87_flags(void){ int e=fetestexcept(FE_ALL_EXCEPT); return (e&FE_INVALID?1u:0u)|(e&FE_DIVBYZERO?4u:0u)|(e&FE_OVERFLOW?8u:0u)|(e&FE_INEXACT?32u:0u); }
static inline void sse_fpcr(u32 mx){ static const int m[4]={FE_TONEAREST,FE_DOWNWARD,FE_UPWARD,FE_TOWARDZERO}; fesetround(m[(mx>>13)&3]); }
#include "fnvl_fpc.h"
// The exported entry points: one per helper that the Forms' emission names (fx_dsl_cvt, fx_dsl_fp) and per other helper of fnvl_fpc.h.
extern "C" void fpc_cvt(u32 k, u32 sw, u32 rw, u32 t, u64 a, u32 mx, u64 *val, u32 *fl){ u128 r=fx_dsl_cvt(k,sw,rw,t,a,mx); *val=(u64)r & (k==0&&rw==64?~0ull:0xffffffffull); *fl=(u32)(r>>(k==0?rw:32))&0x3f; }
extern "C" void fpc_fp(u32 op, u32 w, u64 a, u64 b, u32 mx, u64 *val, u32 *fl){ u128 r=fx_dsl_fp(op,w,a,b,mx); *val=(u64)r & (w==32?0xffffffffull:~0ull); *fl=(u32)(r>>w)&0x3f; }
extern "C" u32 fpc_f2i(double v, u32 rc){ return sse_f2i(v,rc); }
