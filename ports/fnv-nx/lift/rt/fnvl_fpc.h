/* The pure floating-point helpers of the lifted runtime (SSE scalar arithmetic and conversions, the x86 NaN / denormal / tininess rules): everything a DSL Form's emission names except state access.
   They touch the machine only through the hooks that the includer defines first: struct cpu *c (mxcsr), FOWN_SSE, sse_live, sse_fpcr, fnvl_fp_x87_clear, fnvl_fp_x87_flags and the register-barrier constraint FNVL_FPREG.
   rt/fnvl.h includes it with the AArch64 hooks; the host check of these helpers (proof/fpchost) includes it with the host hooks (fenv.h). */
template<class T> struct FX;
template<> struct FX<float>{ typedef u32 U; static const U S=0x80000000u, E=0x7f800000u, M=0x7fffffu, Q=0x400000u; };
template<> struct FX<double>{ typedef u64 U; static const U S=1ull<<63, E=0x7ffull<<52, M=(1ull<<52)-1, Q=1ull<<51; };
template<class T> static inline typename FX<T>::U fxbits(T v){ union{T t; typename FX<T>::U u;} x; x.t=v; return x.u; }
template<class T> static inline T fxval(typename FX<T>::U u){ union{T t; typename FX<T>::U u;} x; x.u=u; return x.t; }
template<class T> static inline T sse_daz(T v,u32 mx){ typename FX<T>::U b=fxbits(v); return ((mx&0x40)&&!(b&FX<T>::E)&&(b&FX<T>::M))?fxval<T>(b&FX<T>::S):v; }
template<class T> static inline T sse_ftz(T v,u32 mx){ typename FX<T>::U b=fxbits(v); return ((mx&0x8000)&&!(b&FX<T>::E)&&(b&FX<T>::M))?fxval<T>(b&FX<T>::S):v; }
template<class T> static inline T sse_qnan(T v){ return fxval<T>(fxbits(v)|FX<T>::Q); }
/* x86 NaN rule: the first operand's NaN wins (quieted), else the second's; invalid operation on numbers gives the negative default NaN */
template<class T> static inline T sse_nan2(T a,T b){ return a!=a?sse_qnan(a):sse_qnan(b); }
template<class T> static inline T sse_dnan(){ return fxval<T>(FX<T>::S|FX<T>::E|FX<T>::Q); }
FNVL_F2I static inline u32 sse_f2i(double v,u32 rc){ double r=rc==0?__builtin_roundeven(v):rc==1?__builtin_floor(v):rc==2?__builtin_ceil(v):__builtin_trunc(v);
  if(!(r>=-2147483648.0&&r<=2147483647.0)) return 0x80000000u; return (u32)(s32)r; }
static inline double sse_f2d(float v,u32 mx){ v=sse_daz(v,mx); if(v!=v){ u32 b=fxbits(v); return fxval<double>((u64)(b&0x80000000u)<<32|0x7ffull<<52|(u64)(b&0x7fffffu)<<29|1ull<<51); } return (double)v; }
static inline float sse_d2f(double v,u32 mx){ v=sse_daz(v,mx); if(v!=v){ u64 b=fxbits(v); return fxval<float>((u32)(b>>32)&0x80000000u|0x7f800000u|(u32)((b&FX<double>::M)>>29)|0x400000u); } return sse_ftz((float)v,mx); }
/* dsl.fp (fnvlift/dsl.py `fp`, the rules are in its docstring and in _fp_smt): scalar binary32 / binary64 add sub mul div sqrt (op 0..4) of the bit patterns a, b under the MXCSR value mx -> the result's
   bit pattern in the low w bits and THIS operation's flags IE DE ZE OE UE PE (MXCSR bits 0-5) in bits w..w+5. The host does the IEEE operation in the guest's rounding mode (FPCR RC from mx, sse_fpcr);
   its sticky IOC / DZC / OFC / IXC flags are IE / ZE / OE / PE; NaN operands, DAZ, DE, tininess AFTER rounding (UE: the result rounded to the destination precision with unbounded exponent range is nonzero and below the smallest normal, SoftFloat 8086-SSE specialize.h;
   the host's UFC is before rounding and is not used) and FTZ are computed here on the bit patterns. The live host flags of the SSE family are folded into c->mxcsr first (the family's sticky set is c->mxcsr | live), so none is lost. */
/* tininess AFTER rounding of a mul / div (SDM / SoftFloat 8086-SSE): the exact result V * 2^E (V an integer, a sticky bit for a division's remainder) rounded to P significant bits in the guest's rounding mode rc
   with unbounded exponent range is below the smallest normal 2^(1-BIAS). Integers only: the host's own rounding is at the subnormal grid and cannot answer this. a, b: finite operands after DAZ. */
template<class UT, int FB> static inline bool fx_tiny_after(bool div, UT a, UT b, u32 rc){
  const int W=sizeof(UT)*8, P=FB+1, BIAS=(1<<(W-FB-2))-1; const UT FM=((UT)1<<FB)-1; const bool neg=(((a^b)>>(W-1))&1)!=0;
  auto dec=[&](UT x, u128& m, int& e){ UT ef=(x>>FB)&((((UT)1)<<(W-FB-1))-1), f=x&FM; if(ef==0){ m=f; e=1-BIAS-FB; } else { m=f|((UT)1<<FB); e=(int)ef-BIAS-FB; } };
  const UT EXM=(UT)((((UT)1<<(W-1))-1)&~FM); if((a&EXM)==EXM||(b&EXM)==EXM) return false;      /* an infinite operand: the result is an exact zero or an infinity */
  u128 ma, mb; int ea, eb; dec(a,ma,ea); dec(b,mb,eb);
  if(ma==0||mb==0) return false;                                    /* an exact zero is not tiny */
  while(!(ma>>FB)){ ma<<=1; ea--; } while(!(mb>>FB)){ mb<<=1; eb--; }
  u128 V; int E; bool st=false;
  if(!div){ V=ma*mb; E=ea+eb; }
  else { const int S=P+3; const u128 num=ma<<S; u128 q=0, r=0; for(int i=P+S-1;i>=0;i--){ r=(r<<1)|((num>>i)&1); q<<=1; if(r>=mb){ r-=mb; q|=1; } } st=r!=0; V=q; E=ea-eb-S; }
  int L=0; for(u128 t=V;t;t>>=1) L++;
  int top;
  if(L>P){ const int d=L-P; const u128 low=V&(((u128)1<<d)-1), half=(u128)1<<(d-1), R=V>>d; const bool inex=low!=0||st, g=(low&half)!=0, rest=(low&(half-1))!=0||st;
    bool up=false; if(rc==0) up=g&&(rest||(R&1)); else if(rc==1) up=neg&&inex; else if(rc==2) up=!neg&&inex;
    top=E+d+P-1; if(up && R+1==((u128)1<<P)) top++; }
  else top=E+L-1;
  return top < 1-BIAS; }
/* fx_pre: the pre-computation exception flags of every SSE floating-point operation, the C twin of dsl._pre, in the priority order of SDM Vol 1 11.5.2.1 (Table 11-3, "SIMD Floating-Point Exception Priority"):
   row 1 #I of a signalling NaN operand; row 2 a QNaN operand (no exception, nothing below is checked); row 3 other #I (an invalid operation, or a QNaN operand of MINPS MAXPS COMISS and the CMPPS predicates
   lt le nlt nle: sig_qnan); row 4 #Z; row 5 #D (a denormal operand, DAZ clear).  A lower row's flag is not set when a higher row applies.  Returns IE 1, DE 2, ZE 4 (MXCSR bit values). */
static inline u32 fx_pre(bool snan, bool qnan_op, bool sig_qnan, bool other_inv, bool zdiv, bool denorm){
  if(snan || (qnan_op && sig_qnan) || other_inv) return 1u;
  if(qnan_op) return 0u;
  if(zdiv) return 4u;
  return denorm ? 2u : 0u; }
/* dsl.fp ops 5..16: min max, the eight CMPPS predicates, comi ucomi (fnvlift/dsl.py CMPPRED, _fp_smt): no rounding, integer compares of the bit patterns (a, b after DAZ; ao, bo the operands as read, for DE); the result's flags are IE and DE only.
   The value is the min / max result, the all-ones / zero predicate mask, or ZF<<2 | PF<<1 | CF of COMISS / UCOMISS. */
template<class UT, int FB> static inline u128 fx_dsl_fcmp(u32 op, UT ao, UT bo, UT a, UT b, u32 mx){
  const int W=sizeof(UT)*8; const UT SGN=(UT)1<<(W-1), FM=((UT)1<<FB)-1, EX=(UT)((((UT)1<<(W-1))-1)&~FM), QB=(UT)1<<(FB-1);
  auto isnan=[&](UT x){ return (x&EX)==EX && (x&FM)!=0; };
  auto issnan=[&](UT x){ return isnan(x) && !(x&QB); };
  auto issub=[&](UT x){ return (x&EX)==0 && (x&FM)!=0; };
  auto key=[&](UT x)->long long { long long m=(long long)(x&~SGN); return (x&SGN) ? -m : m; };
  const bool un=isnan(a)||isnan(b), sn=issnan(a)||issnan(b), daz=(mx>>6)&1;
  const bool lt=!un && key(a)<key(b), eq=!un && key(a)==key(b), gt=!un && key(a)>key(b);
  bool sig_qnan; UT val;
  if(op==5||op==6){ sig_qnan=true; const bool bz=(a&~SGN)==0 && (b&~SGN)==0; val=un||bz ? b : (op==5 ? (lt?a:b) : (gt?a:b)); }
  else if(op>=7&&op<=14){ const u32 k=op-7; sig_qnan=k==1||k==2||k==5||k==6;
    const bool t=k==0?eq : k==1?lt : k==2?(lt||eq) : k==3?un : k==4?(un||!eq) : k==5?(un||!lt) : k==6?(un||!(lt||eq)) : !un;
    val=t?(UT)~(UT)0:(UT)0; }
  else { sig_qnan=(op==15); val=(UT)(((un||eq)?4u:0u)|(un?2u:0u)|((un||lt)?1u:0u)); }
  const u32 pre=fx_pre(sn, un, sig_qnan, false, false, !daz && (issub(ao)||issub(bo)));
  return ((u128)pre<<W)|val; }
template<class FT, class UT, int FB> static inline u128 fx_dsl_fp_t(u32 op, UT a, UT b, u32 mx){
  const int W=sizeof(UT)*8; const UT SGN=(UT)1<<(W-1), FM=((UT)1<<FB)-1, EX=(UT)((((UT)1<<(W-1))-1)&~FM), QB=(UT)1<<(FB-1), MINN=(UT)1<<FB;
  const bool daz=(mx>>6)&1, um=(mx>>11)&1, ftz=(mx>>15)&1;
  auto isnan=[&](UT x){ return (x&EX)==EX && (x&FM)!=0; };
  auto issnan=[&](UT x){ return isnan(x) && !(x&QB); };
  auto issub=[&](UT x){ return (x&EX)==0 && (x&FM)!=0; };
  const bool unary=op==4;
  const UT ao=a, bo=unary?a:b;
  if(daz){ if(issub(a)) a&=SGN; if(issub(b)) b&=SGN; }
  if(op>=5) return fx_dsl_fcmp<UT,FB>(op,ao,bo,a,b,mx);
  const UT indef=(UT)(SGN|EX|QB);
  UT val; u32 fl=0;
  if(isnan(a)||(!unary&&isnan(b))){
    val=isnan(a)?(UT)(a|QB):(UT)(b|QB); fl=fx_pre(issnan(a)||(!unary&&issnan(b)), true, false, false, false, false);
    return ((u128)fl<<W)|val; }
  FOWN_SSE(); c->mxcsr|=sse_live(); sse_fpcr(mx); fnvl_fp_x87_clear();
  FT x, y, r; __builtin_memcpy(&x,&a,sizeof x); __builtin_memcpy(&y,&b,sizeof y);
  __asm__ volatile("" : FNVL_FPREG(x), FNVL_FPREG(y));
  switch(op){ case 0: r=x+y; break; case 1: r=x-y; break; case 2: r=x*y; break; case 3: r=x/y; break; default: r=__builtin_sqrt(x); }
  __asm__ volatile("" : FNVL_FPREG(r));
  const u32 hf=fnvl_fp_x87_flags(); fnvl_fp_x87_clear();
  const bool ie=hf&1u, ze=(hf>>2)&1u, oe=(hf>>3)&1u, pe=((hf>>5)&1u)!=0;
  __builtin_memcpy(&val,&r,sizeof val);
  const u32 pre=fx_pre(false, false, false, ie, ze, !daz && (issub(ao)||issub(bo)));
  if(pre&1u){ sse_fpcr(mx); return ((u128)1u<<W)|indef; }
  bool tiny=false;
  if((UT)(val&~SGN)<=MINN){                                         /* a tiny-after-rounding result is at most the smallest normal: add / sub there are exact (a multiple of the subnormal step), mul / div decide exactly in integers, sqrt is never tiny */
    if(op<=1) tiny=(UT)(val&~SGN)!=0 && (UT)(val&~SGN)<MINN;
    else if(op<=3) tiny=fx_tiny_after<UT,FB>(op==3,a,b,(mx>>13)&3u); }
  bool ue=tiny&&(pe||!um), pe2=pe||oe;
  if(ftz&&um&&tiny){ val&=SGN; ue=true; pe2=true; }
  fl=pre|(oe?8u:0u)|(ue?16u:0u)|(pe2?32u:0u);
  return ((u128)fl<<W)|val; }
static inline u128 fx_dsl_fp(u32 op, u32 w, u64 a, u64 b, u32 mx){
  return w==32 ? fx_dsl_fp_t<float,u32,23>(op,(u32)a,(u32)b,mx) : fx_dsl_fp_t<double,u64,52>(op,a,b,mx); }
/* dsl.cvt (fnvlift/dsl.py cvt, _cvt_smt): kind 0 = signed int32 -> binary rw (CVTSI2SS CVTSI2SD CVTDQ2PS CVTDQ2PD CVTPI2PS), kind 1 = binary sw -> int32 (CVTSS2SI ... CVTTPS2DQ; trunc: toward zero, else mx.RC).
   Software rounding by mx.RC (no FPCR): the value in the low rw bits (kind 0) or 32 bits (kind 1), the flags IE..PE (MXCSR bits 0-5) above it.  i2f binary32: the magnitude is cut to 24 significant bits by RC (tie: even), exactly representable, PE iff bits were dropped.
   f2i: DAZ reads a denormal operand as a zero of its sign; NaN, infinity or a rounded value outside int32 -> 0x80000000 and IE (dsl._pre row 3: fx_pre(false,false,false,true,false,false)); else PE iff the rounded value is not the operand. */
static inline u128 fx_dsl_cvt(u32 kind, u32 sw, u32 rw, u32 trunc, u64 a, u32 mx){
  const u32 rc=(mx>>13)&3u;
  if(kind==0){
    const int i=(int)(u32)a; const bool neg=i<0; const u32 m=neg ? 0u-(u32)i : (u32)i;
    if(rw==64){ const double r=(double)i; u64 v; __builtin_memcpy(&v,&r,8); return (u128)v; }
    u32 mag=m; bool inex=false; int L=0; for(u32 t=m;t;t>>=1) L++;
    if(L>24){ const int sh=L-24; const u32 low=m&((1u<<sh)-1u), half=1u<<(sh-1), tr=m-low, up=tr+(1u<<sh); inex=low!=0; bool u=false;
      if(rc==0) u=low>half||(low==half&&((tr>>sh)&1u)); else if(rc==1) u=neg&&inex; else if(rc==2) u=!neg&&inex;
      mag=u?up:tr; }
    float r=(float)mag; if(neg) r=-r; u32 v; __builtin_memcpy(&v,&r,4);
    return ((u128)(inex?32u:0u)<<32)|v; }
  double x; const bool d32=sw==32; const u32 daz=(mx>>6)&1u; bool nan, inf;
  if(d32){ u32 b=(u32)a; const u32 e=(b>>23)&0xffu, f=b&0x7fffffu; nan=e==0xffu&&f; inf=e==0xffu&&!f; if(daz&&e==0&&f) b&=0x80000000u; float t; __builtin_memcpy(&t,&b,4); x=(double)t; }
  else { u64 b=a; const u32 e=(u32)(b>>52)&0x7ffu; const u64 f=b&0xfffffffffffffull; nan=e==0x7ffu&&f; inf=e==0x7ffu&&!f; if(daz&&e==0&&f) b&=0x8000000000000000ull; __builtin_memcpy(&x,&b,8); }
  const double r=trunc ? __builtin_trunc(x) : rc==0 ? __builtin_roundeven(x) : rc==1 ? __builtin_floor(x) : rc==2 ? __builtin_ceil(x) : __builtin_trunc(x);
  const bool inr=!nan&&!inf&&r>=-2147483648.0&&r<2147483648.0;
  const u32 val=inr?(u32)(int)r:0x80000000u; const u32 fl=fx_pre(false,false,false,!inr,false,false)|((inr&&r!=x)?32u:0u);
  return ((u128)fl<<32)|val; }
