/* x87 runtime of lifted code (R8, W13). Included from fnvl.h; the lifter's PRELUDE inlines it (and x87.h) textually.
   Arithmetic is binary64, native, rounding mode from the host FPCR; the precision control is ignored. A stack slot is a CARRIER (struct fv): the value
   in its source format until the first arithmetic use, so copies are exact and a conversion rounds once. Exact classes (bit for bit like the x87):
   loads, stores, copies, fist, compares, fxam, control/status words, fchs/fabs. Everything that needs 80 bits (m80 carriers, fild m64 beyond 2^53
   compared or stored as m80) goes through rt/x87.c (fnvl_x_*), which is hardware-equal. */
#include "x87.h"
#include "fmath.h"
enum { FK_E, FK_F64, FK_F32, FK_I16, FK_I32, FK_I64, FK_M80 };
#define FQ64 (1ull<<51)
static inline fv fvmk(u32 k,u64 v,u32 e){ fv r; r.v=v; r.e=e; r.k=k; return r; }
static inline u64 d2u(double d){ u64 u; __builtin_memcpy(&u,&d,8); return u; }
static inline double u2d(u64 u){ double d; __builtin_memcpy(&d,&u,8); return d; }
static inline u32 f2u(float f){ u32 u; __builtin_memcpy(&u,&f,4); return u; }
static inline float u2f(u32 u){ float f; __builtin_memcpy(&f,&u,4); return f; }
static inline fv fv_f64(double d){ return fvmk(FK_F64,d2u(d),0); }
/* fld m32 / m64 are format conversions on the x87: a signalling NaN becomes the quiet one (payload kept). fld m80 is not. */
static inline fv fv_ld32(u32 r){ if(__builtin_expect((r&0x7f800000u)==0x7f800000u&&(r&0x7fffffu),0)) r|=0x400000u; return fvmk(FK_F32,r,0); }
static inline fv fv_ld64(u64 r){ if(__builtin_expect(((r>>52)&0x7ff)==0x7ff&&(r&(FQ64*2-1)),0)) r|=FQ64; return fvmk(FK_F64,r,0); }
static inline fv fv_ldi(u32 k,s64 x){ return fvmk(k,(u64)x,0); }
static inline fv fv_ld80(u64 m,u32 se){ return fvmk(FK_M80,m,se&0xffffu); }
#define FV_INDEF fvmk(FK_M80,0xC000000000000000ull,0xFFFFu)
/* hooks of R8 control state: ONE place each (README "control state"). The shadow-set rule that separates SSE flags is not implemented yet. */
static inline void fnvl_fp_set_rc_x87(u32 rc){ static const unsigned char m[4]={0,2,1,3}; u64 f; __asm__ volatile("mrs %0, fpcr":"=r"(f)); f=(f&~(3ull<<22))|((u64)m[rc&3]<<22); __asm__ volatile("msr fpcr, %0"::"r"(f)); }
static inline u32 fnvl_fp_x87_flags(void){ u64 f; __asm__ volatile("mrs %0, fpsr":"=r"(f)); return (u32)((f&1)|((f>>1&1)<<2)|((f>>2&1)<<3)|((f>>3&1)<<4)|((f>>4&1)<<5)|((f>>7&1)<<1)); }
static inline void fnvl_fp_x87_clear(void){ u64 f; __asm__ volatile("mrs %0, fpsr":"=r"(f)); f&=~0x9full; __asm__ volatile("msr fpsr, %0"::"r"(f)); }
/* exact 80-bit image of a carrier */
static inline fnvl_f80 fv_x(fv a){ u32 f=0; fnvl_f80 r;
  switch(a.k){ case FK_F64: return fnvl_x_from_f64(a.v,&f); case FK_F32: return fnvl_x_from_f32((u32)a.v,&f);
    case FK_I16: case FK_I32: case FK_I64: return fnvl_x_from_i64((s64)a.v); default: r.m=a.v; r.se=(u16)a.e; return r; } }
/* first arithmetic use: F32 widens exactly, I64 rounds by the rounding mode, M80 converts (rt/x87.c) */
static inline double fv_d(fv a){ u32 f=0;
  switch(a.k){ case FK_F64: return u2d(a.v); case FK_F32: return (double)u2f((u32)a.v);
    case FK_I16: case FK_I32: case FK_I64: return (double)(s64)a.v; default: return u2d(fnvl_x_to_f64(fv_x(a),c->fcw,&f)); } }
/* x87 NaN rules for a NaN result: operands numbers -> the negative default NaN (AArch64 gives the positive one); one NaN operand -> it, quieted (the host does
   the same); two NaNs -> the larger significand (a QNaN beats an SNaN), a tie goes to the positive one */
static __attribute__((noinline)) double fxn_slow(double r,double x,double y){
  if(x==x&&y==y) return -__builtin_nan("");
  if(x!=x&&y!=y){ u64 a=d2u(x),b=d2u(y),ma=a&(FQ64*2-1),mb=b&(FQ64*2-1); return u2d((ma>mb?a:mb>ma?b:(a>>63?b:a))|FQ64); }
  return r; }
static inline double fxn(double r,double x,double y){ return __builtin_expect(r!=r,0)?fxn_slow(r,x,y):r; }
#define FXN(r,x,y) fxn(r,x,y)
static inline double fx_round(double v,u32 rc){ return rc==0?__builtin_roundeven(v):rc==1?__builtin_floor(v):rc==2?__builtin_ceil(v):__builtin_trunc(v); }
/* stores */
static inline u32 fv_s32(fv a){ u32 f=0;
  switch(a.k){ case FK_F32: return (u32)a.v; case FK_F64: return f2u((float)u2d(a.v)); case FK_I16: case FK_I32: case FK_I64: return f2u((float)(s64)a.v);
    default: return fnvl_x_to_f32(fv_x(a),c->fcw,&f); } }
static inline u64 fv_s64(fv a){ u32 f=0;
  switch(a.k){ case FK_F64: return a.v; case FK_F32: return d2u((double)u2f((u32)a.v)); case FK_I16: case FK_I32: case FK_I64: return d2u((double)(s64)a.v);
    default: return fnvl_x_to_f64(fv_x(a),c->fcw,&f); } }
/* fist/fistp/fisttp: round by rc (fist: the control word's RC), out of range and NaN give the integer indefinite. Returns the pattern, the caller truncates it. */
static inline u64 fv_i(fv a,u32 bits,u32 rc){ u64 ind=1ull<<(bits-1); u32 f=0; double d,r;
  switch(a.k){
  case FK_I16: case FK_I32: case FK_I64: { s64 x=(s64)a.v; return (bits==64||(x>=-(s64)ind&&x<(s64)ind))?(u64)x:ind; }
  case FK_F64: case FK_F32: d=fv_d(a); if(d!=d) return ind; r=fx_round(d,rc); break;
  default: return (u64)fnvl_x_to_i64(fv_x(a),rc,bits,&f); }
  if(bits==64) return (r>=-9223372036854775808.0&&r<9223372036854775808.0)?(u64)(s64)r:ind;
  return (r>=-(double)ind&&r<(double)ind)?(u64)(s64)r:ind; }
#define FIST(a,bits) fv_i((a),(bits),(c->fcw>>10)&3)
#define FISTT(a,bits) fv_i((a),(bits),3)
/* fchs/fabs: the sign of any carrier, nothing else. An integer carrier cannot hold -0 (or 2^63): it becomes F64 (exact) or M80. */
static inline fv fv_sg(fv a,u32 chs){ s64 x;
  switch(a.k){ case FK_F64: return fvmk(FK_F64,chs?a.v^(1ull<<63):a.v&~(1ull<<63),0); case FK_F32: return fvmk(FK_F32,chs?a.v^0x80000000u:a.v&0x7fffffffu,0);
    case FK_I16: case FK_I32: case FK_I64: x=(s64)a.v; if(chs?x==0:0) return fv_f64(-0.0); if(!chs&&x>=0) return a;
      if(x==(s64)(1ull<<63)) return fv_f64(9223372036854775808.0); return fvmk(a.k,(u64)-x,0);
    default: return fvmk(FK_M80,a.v,chs?a.e^0x8000u:a.e&0x7fffu); } }
/* frndint */
static inline fv fv_rnd(fv a){ u32 f=0, rc=(c->fcw>>10)&3; fnvl_f80 r;
  switch(a.k){ case FK_F64: case FK_F32: return fv_f64(fx_round(fv_d(a),rc)); case FK_I16: case FK_I32: case FK_I64: return a;
    default: r=fnvl_x_rndint(fv_x(a),c->fcw,&f); return fvmk(FK_M80,r.m,r.se); } }
/* compares: C3<<14|C2<<10|C0<<8, exact. Pairs of F64/F32/I16/I32 are exact in double; I64 and M80 go through the 80-bit core. */
static inline u32 fv_cmp(fv a,fv b){ u32 f=0;
  if(__builtin_expect((0x1eu>>a.k)&(0x1eu>>b.k)&1,1)){ double x=fv_d(a),y=fv_d(b); return (x!=x||y!=y)?0x4500u:x==y?0x4000u:x<y?0x100u:0u; }
  return fnvl_x_cmp(fv_x(a),fv_x(b),1,&f); }
#define FCMP(A,B) do{ u32 r_=fv_cmp((A),(B)); c0=(r_>>8)&1; c2=(r_>>10)&1; c3=(r_>>14)&1; c->fex&=~0x200u; }while(0)
#define FCMPI(A,B) do{ u32 r_=fv_cmp((A),(B)); zf=(r_>>14)&1; pf=(r_>>10)&1; cf=(r_>>8)&1; of=sf=af=0; }while(0)
#define FXAM(F) do{ fv f_=(F); u32 r_=fnvl_x_xam(fv_x(f_),f_.k==0); c0=(r_>>8)&1; c2=(r_>>10)&1; c3=(r_>>14)&1; c->fex=(c->fex&~0x200u)|(r_&0x200u); }while(0)
#define FSWV_(TOP) ((u32)(TOP)<<11|(c3<<14)|(c2<<10)|(c0<<8)|fnvl_fp_x87_flags()|c->fex)
#ifdef FNVL_TRACE
extern "C" u32 fnvl_trace_sw(u32 fsw);   /* test stand: reports the FSW delivered to the guest (sticky exception bits are not compared with the x86 oracle) */
#define FSWV(TOP) fnvl_trace_sw(FSWV_(TOP))
#else
#define FSWV(TOP) FSWV_(TOP)
#endif
/* fsin fcos fsincos fptan f2xm1 fyl2x fyl2xp1 fpatan fscale fxtract fprem fprem1 (rt/fmath.c, README "One reference CPU model"): correctly rounded / exact
   binary64 results on the carriers' doubles. The exception bits it reports (IE ZE OE UE PE) are ORed into c->fex; C1 (bit 9 of c->fex: the instruction
   rounded up, or fprem's quotient bit Q0) is ASSIGNED (FM_FL) and cleared by FCMP, set by FXAM. C0/C2/C3 are the lifter's locals. fsin/fcos/fsincos/fptan:
   for finite |x| >= 2^63 C2 = 1 and the stack is left unchanged (SDM), otherwise C2 = 0. Data dependent stack depth (fptan, fsincos push only when C2 = 0)
   is why those two always use the dynamic stack. */
#define FM_FL(F) (c->fex=(c->fex&~0x200u)|(F))
#define FM_UN(FN,D,S) do{ u32 f_=0; double x_=fv_d(S); D=fv_f64(FN(x_,&f_)); FM_FL(f_); }while(0)                                   /* f2xm1 */
#define FM_BIN(FN,D,A,B) do{ u32 f_=0; double a_=fv_d(A), b_=fv_d(B); D=fv_f64(FN(a_,b_,&f_)); FM_FL(f_); }while(0)               /* fpatan fyl2x fyl2xp1 fscale: FN(first, second) */
#define FM_TRIG(FN,D,S) do{ u32 f_=0; double x_=fv_d(S); if(fnvl_m_trig_big(x_)) c2=1; else{ c2=0; D=fv_f64(FN(x_,&f_)); FM_FL(f_); } }while(0)      /* fsin fcos */
#define FM_TAN(D,S) do{ u32 f_=0; double x_=fv_d(S); if(fnvl_m_trig_big(x_)) c2=1; else{ c2=0; D=fv_f64(fnvl_m_tan(x_,&f_)); dyn_push(fvmk(FK_F64,0x3ff0000000000000ull,0)); FM_FL(f_); } }while(0)
#define FM_SINCOS(D,S) do{ u32 f_=0; double x_=fv_d(S), s_, k_; if(fnvl_m_trig_big(x_)) c2=1; else{ c2=0; fnvl_m_sincos(x_,&s_,&k_,&f_); D=fv_f64(s_); dyn_push(fv_f64(k_)); FM_FL(f_); } }while(0)
#define FM_PREM(D,A,B,NEAR) do{ double a_=fv_d(A), b_=fv_d(B); u32 f_=0, k_=0; D=fv_f64(fnvl_m_fprem(a_,b_,(NEAR),&k_,&f_)); c0=(k_>>8)&1; c2=(k_>>10)&1; c3=(k_>>14)&1; FM_FL(f_|(k_&0x200u)); }while(0)
/* control: fldcw keeps the word and synchronises the host rounding mode; the MSVC bracket fnstcw/fldcw/fistp/fldcw needs no special case.
   TODO(R8 fold): fold the bracket (`fnstcw [a]; or ah,0xc; fldcw [b]; fistp; fldcw [a]`) into one truncating fistp (host rounding mode left alone). */
#define FLDCW(W) do{ c->fcw=(W)&0xffffu; fnvl_fp_set_rc_x87((c->fcw>>10)&3); }while(0)
#define FNCLEX() do{ c->fex=0; fnvl_fp_x87_clear(); }while(0)
/* dynamic stack: every x87 instruction of a function whose depth is not static goes through c->fx / c->ftop. An empty operand or a push on a full stack
   sets invalid + stack fault and gives the real indefinite. */
static inline fv dyn_rd(u32 i){ fv *p=&c->fx[(c->ftop+i)&7]; if(__builtin_expect(!p->k,0)){ c->fex|=0x41; return FV_INDEF; } return *p; }
static inline void dyn_push(fv v){ u32 t=(c->ftop-1)&7; if(__builtin_expect(c->fx[t].k!=0,0)){ c->fex|=0x41; v=FV_INDEF; } c->ftop=t; c->fx[t]=v; }
static inline void dyn_pop(void){ fv *p=&c->fx[c->ftop&7]; if(__builtin_expect(!p->k,0)) c->fex|=0x41; p->k=0; c->ftop=(c->ftop+1)&7; }
static inline void fnvl_x87_init(void){ for(int i=0;i<8;i++) c->fx[i].k=0; c->ftop=0; c->fcw=0x37f; c->fex=0; fnvl_fp_set_rc_x87(0); fnvl_fp_x87_clear(); }
/* fbld / fbstp: packed BCD (rt/x87.c; A-BCD: digits above 9 follow the arithmetic formula d*10^k). The value of an 18-digit number is an integer below 10^18: exact in the M80 carrier. */
static inline fv fv_ldbcd(u64 lo,u32 hi){ u8 b[10]; u16 h=(u16)hi; __builtin_memcpy(b,&lo,8); __builtin_memcpy(b+8,&h,2); fnvl_f80 r=fnvl_x_from_bcd(b); return fvmk(FK_M80,r.m,r.se); }
static inline void fv_stbcd(fv a,u64 *lo,u64 *hi){ u8 b[10]; u32 f=0; u16 h; fnvl_x_to_bcd(fv_x(a),c->fcw,b,&f); __builtin_memcpy(lo,b,8); __builtin_memcpy(&h,b+8,2); *hi=h; FM_FL(f); }
/* fldenv / frstor (SDM Vol 1 8.1.10, 32-bit layout fig 8-9, 16-bit layout fig 8-11; assumptions A-EMPTY and A-ES: see f_x87_ctl.py env()). Dynamic stack only. The image words: FCW at 0,
   FSW at w, FTW at 2w with w = 4 (32-bit operand size) or 2; frstor's ST(i) images follow the 7 dwords (28 bytes) / 7 words (14 bytes) of the environment, 10 bytes each in ST order. */
static inline u32 fx_env_ld(u32 a,u32 w,u32 *ftw){ u32 fsw=M16(a+w); *ftw=M16(a+2*w); FLDCW(M16(a)); c->ftop=(fsw>>11)&7; c->fex=fsw&0x27fu; fnvl_fp_x87_clear(); return fsw; }
#define FLDENV(A,D32) do{ u32 ftw_, a_=(A), fsw_=fx_env_ld(a_,(D32)?4u:2u,&ftw_); c0=(fsw_>>8)&1; c2=(fsw_>>10)&1; c3=(fsw_>>14)&1; \
  for(u32 p_=0;p_<8;p_++){ if(((ftw_>>(2*p_))&3)==3) c->fx[p_].k=0; else if(!c->fx[p_].k) c->fx[p_]=FV_INDEF; } }while(0)
#define FRSTOR(A,D32) do{ u32 ftw_, a_=(A), r_=a_+((D32)?28u:14u), fsw_=fx_env_ld(a_,(D32)?4u:2u,&ftw_); c0=(fsw_>>8)&1; c2=(fsw_>>10)&1; c3=(fsw_>>14)&1; \
  for(u32 i_=0;i_<8;i_++){ u32 p_=(c->ftop+i_)&7; if(((ftw_>>(2*p_))&3)==3) c->fx[p_].k=0; else c->fx[p_]=fv_ld80(M64(r_+10*i_),M16(r_+10*i_+8u)); } }while(0)
