
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64;
typedef signed char s8; typedef short s16; typedef int s32; typedef long long s64;
typedef u8 __attribute__((may_alias)) u8a; typedef u16 __attribute__((aligned(1),may_alias)) u16a;
typedef u32 __attribute__((aligned(1),may_alias)) u32a; typedef u64 __attribute__((aligned(1),may_alias)) u64a;
typedef float __attribute__((aligned(1),may_alias)) f32a; typedef double __attribute__((aligned(1),may_alias)) f64a;
typedef union { u32 u[4]; float f[4]; double d[2]; u64 q[2]; u8 b[16]; s8 sb[16]; u16 w[8]; s16 sw[8]; s32 sd[4]; s64 sq[2]; } __attribute__((may_alias)) xmm_t;
struct cpu { u32 r[8]; u32 fs; u32 ftop, ftag, fcw, fc; double st[8]; xmm_t x[8]; u32 fl; u32 mxcsr; u64 mm[8]; u32 ra; };   /* mxcsr (reset 0x1f80) and mm0-7 are appended: SSE family (W6). r[] is only the save area of the
   entry/exit bridges (fnvl_enter): while lifted code runs the eight guest registers live in w19..w26. ra: the address the last `ret` popped (control-transfer rule, README) */
/* FNVL_FLAGS_IO (test/instest.py): the arithmetic flags, DF and the x87 condition bits also travel in c->fl / c->fc.
   fl: the EFLAGS bit positions (CF 0, PF 2, AF 4, ZF 6, SF 7, DF 10, OF 11); fc: C0 bit 8, C2 bit 10, C3 bit 14. */
#ifdef FNVL_FLAGS_IO
#define FL_IN do{ u32 f_=c->fl; cf=f_&1; pf=(f_>>2)&1; af=(f_>>4)&1; zf=(f_>>6)&1; sf=(f_>>7)&1; df=(f_>>10)&1; of=(f_>>11)&1; c0=(c->fc>>8)&1; c2=(c->fc>>10)&1; c3=(c->fc>>14)&1; }while(0)
#define FL_OUT do{ c->fl=(cf&1)|((pf&1)<<2)|((af&1)<<4)|((zf&1)<<6)|((sf&1)<<7)|((df&1)<<10)|((of&1)<<11); c->fc=((c0&1)<<8)|((c2&1)<<10)|((c3&1)<<14); }while(0)
#else
#define FL_IN do{}while(0)
#define FL_OUT do{}while(0)
#endif
/* Guest registers: GCC global register variables in callee-saved host registers (ILP32: the 32-bit view w19..w26), the per-thread
   `struct cpu *` in x27. Lifted files are compiled with -ffixed-x19..-ffixed-x27; code compiled without it (the runtime) preserves them by the
   AAPCS, so a call into it keeps the guest state. REGS() is an empty asm that tells GCC every guest register is read and written at that point:
   it stands before and after each call so no guest register value is cached or dropped across it. Native code that calls a lifted function
   saves x19..x27 itself (fnvl_enter). */
register u32 eax asm("x19"); register u32 ecx asm("x20"); register u32 edx asm("x21"); register u32 ebx asm("x22");
register u32 esp asm("x23"); register u32 ebp asm("x24"); register u32 esi asm("x25"); register u32 edi asm("x26");
register struct cpu *c asm("x27");
#define REGS() __asm__ volatile("" : "+r"(eax),"+r"(ecx),"+r"(edx),"+r"(ebx),"+r"(esp),"+r"(ebp),"+r"(esi),"+r"(edi))
/* Control transfer (README): a lifted function is `void L_<addr>(void)`. X_ is the x86-state entry of an address: L_ while everything is lifted
   (the bench); FNVL_STUBX (test/instest.py) makes a call/tail to another entry a stub event instead. */
extern "C" { void fnvl_call(struct cpu*, u32 target); void fnvl_tail(struct cpu*, u32 target); void (*fnvl_dispatch(u32 image_addr))(void); void fnvl_raise(u32 code, u32 at);
             void fnvl_trace(u32 entry, u32 esp); }
#ifdef FNVL_STUBX
#define CALLX(h) fnvl_call(c,0x##h)
#else
#define CALLX(h) L_##h()
#endif
#ifdef FNVL_TRACE
#define TRACE(a) fnvl_trace(a,esp)
#else
#define TRACE(a) do{}while(0)
#endif
/* Guest address -> host pointer. x86 addresses wrap at 2^32. GCC for AArch64 ILP32 folds a constant of a 32-bit
   sum into the 64-bit addressing mode (uxtw x0,w0; ldr [x0,#64]), which does not wrap; the empty asm makes the 32-bit
   sum a value of its own, so every access uses the wrapped address (test/addrwrap.py). */
static inline __attribute__((always_inline)) unsigned long P(u32 a){ __asm__("" : "+r"(a)); return (unsigned long)a; }
#define M8(a) (*(u8a*)P(a))
#define M16(a) (*(u16a*)P(a))
#define M32(a) (*(u32a*)P(a))
#define M64(a) (*(u64a*)P(a))
#define MF32(a) (*(f32a*)P(a))
#define MF64(a) (*(f64a*)P(a))
#define PAR(x) (!(__builtin_popcount((x)&0xffu)&1))
#define DECL u32 cf=0,zf=0,sf=0,of=0,pf=0,af=0,df=0; u32 ftop=0,ftag=0,fcw=0x27f,c0=0,c2=0,c3=0; double st[8]; xmm_t x0,x1,x2,x3,x4,x5,x6,x7; (void)af;(void)pf;(void)df;(void)c2
#define SYNC_IN do{ FL_IN; }while(0)
#define SYNC_OUT do{ FL_OUT; }while(0)   /* the general registers need no sync; flags (instest), x87 and xmm state are appended by the lifter */
#define SYNC_F_IN do{ ftop=c->ftop;ftag=c->ftag;fcw=c->fcw; for(int i_=0;i_<8;i_++) st[i_]=c->st[i_]; }while(0)
#define SYNC_F_OUT do{ c->ftop=ftop;c->ftag=ftag;c->fcw=fcw; for(int i_=0;i_<8;i_++) c->st[i_]=st[i_]; }while(0)
#define SYNC_X_IN do{ x0=c->x[0];x1=c->x[1];x2=c->x[2];x3=c->x[3];x4=c->x[4];x5=c->x[5];x6=c->x[6];x7=c->x[7]; }while(0)
#define SYNC_X_OUT do{ c->x[0]=x0;c->x[1]=x1;c->x[2]=x2;c->x[3]=x3;c->x[4]=x4;c->x[5]=x5;c->x[6]=x6;c->x[7]=x7; }while(0)
#define ST(i) st[(ftop+(i))&7]
#define FPUSH(v) do{ double v_=(v); ftop=(ftop-1)&7; st[ftop]=v_; ftag|=1u<<ftop; }while(0)
#define FPOP() do{ ftag&=~(1u<<ftop); ftop=(ftop+1)&7; }while(0)
#define FCOM(a,b) do{ double a_=(a), b_=(b); if(a_!=a_||b_!=b_){c3=c2=c0=1;} else {c3=a_==b_; c2=0; c0=a_<b_;} }while(0)
/* x86 default NaN (invalid operation) is the negative quiet NaN; AArch64 generates the positive one */
static inline double fxn(double r,double x,double y){ if(__builtin_expect(r!=r,0)&&x==x&&y==y) return -__builtin_nan(""); return r; }
#define FXN(r,x,y) fxn(r,x,y)
static inline float fxnf(float r,float x,float y){ if(__builtin_expect(r!=r,0)&&x==x&&y==y) return -__builtin_nanf(""); return r; }
#define FSW ((ftop<<11)|(c3<<14)|(c2<<10)|(c0<<8))
static inline double fx_round(double v,u32 rc){ return rc==0?__builtin_rint(v):rc==1?__builtin_floor(v):rc==2?__builtin_ceil(v):__builtin_trunc(v); }
static inline u32 fx_i32(double v,u32 rc){ double r=fx_round(v,rc); if(!(r>=-2147483648.0&&r<=2147483647.0)) return 0x80000000u; return (u32)(s32)r; }
static inline u32 fx_i16(double v,u32 rc){ double r=fx_round(v,rc); if(!(r>=-32768.0&&r<=32767.0)) return 0x8000u; return (u16)(s16)r; }
static inline u64 fx_i64(double v,u32 rc){ double r=fx_round(v,rc); if(!(r>=-9223372036854775808.0&&r<9223372036854775808.0)) return 0x8000000000000000ull; return (u64)(s64)r; }
/* ---- SSE / SSE2 / MMX runtime (W6). MXCSR: RC, FTZ, DAZ are honoured; the exception status bits and the exception masks are not
   modelled (no SIMD exception is ever raised). ldmxcsr mirrors RC into FPCR so that host arithmetic rounds like the guest. mm0-7 live in
   c->mm and are NOT aliased to the x87 stack: MMX_TOUCH() is the hook the x87 owner fills in (tag word / top-of-stack on MMX use, emms). ---- */
#define MMX_TOUCH() do{}while(0)
extern "C" { u32 fnvl_rcp32(u32 bits); u32 fnvl_rsqrt32(u32 bits); }   /* reference-CPU tables, supplied by the runtime owner */
typedef xmm_t __attribute__((aligned(1))) xmm_ua;
#define MX(a) (*(xmm_ua*)P(a))
static inline xmm_t mmx_x(u64 v){ xmm_t r; r.q[0]=v; r.q[1]=0; return r; }
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
template<int OP,class T> static inline T sse_arith(T a,T b,u32 mx){
  a=sse_daz(a,mx); b=sse_daz(b,mx); T r=OP==0?a+b:OP==1?a-b:OP==2?a*b:a/b;
  if(__builtin_expect(r!=r,0)) return (a!=a||b!=b)?sse_nan2(a,b):sse_dnan<T>();
  return sse_ftz(r,mx); }
static inline float sse_sqrtv(float a){ return __builtin_sqrtf(a); }
static inline double sse_sqrtv(double a){ return __builtin_sqrt(a); }
template<class T> static inline T sse_sqrt(T a,u32 mx){ a=sse_daz(a,mx); T r=sse_sqrtv(a); if(__builtin_expect(r!=r,0)) return a!=a?sse_qnan(a):sse_dnan<T>(); return sse_ftz(r,mx); }
/* min/max: the second operand is returned when either is NaN or both are zero; the returned operand is not quieted or flushed */
template<class T> static inline T sse_min(T a,T b,u32 mx){ return sse_daz(a,mx)<sse_daz(b,mx)?a:b; }
template<class T> static inline T sse_max(T a,T b,u32 mx){ return sse_daz(a,mx)>sse_daz(b,mx)?a:b; }
template<int P,class T> static inline bool sse_cmpb(T a,T b,u32 mx){ a=sse_daz(a,mx); b=sse_daz(b,mx);
  return P==0?a==b:P==1?a<b:P==2?a<=b:P==3?(a!=a||b!=b):P==4?!(a==b):P==5?!(a<b):P==6?!(a<=b):(a==a&&b==b); }
/* comis/ucomis -> EFLAGS bits (ZF 0x40, PF 4, CF 1) */
template<class T> static inline u32 sse_comi(T a,T b,u32 mx){ a=sse_daz(a,mx); b=sse_daz(b,mx); return (a!=a||b!=b)?0x45u:a==b?0x40u:a<b?1u:0u; }
static inline u32 sse_f2i(double v,u32 rc){ double r=rc==0?__builtin_roundeven(v):rc==1?__builtin_floor(v):rc==2?__builtin_ceil(v):__builtin_trunc(v);
  if(!(r>=-2147483648.0&&r<=2147483647.0)) return 0x80000000u; return (u32)(s32)r; }
static inline double sse_f2d(float v,u32 mx){ v=sse_daz(v,mx); if(v!=v){ u32 b=fxbits(v); return fxval<double>((u64)(b&0x80000000u)<<32|0x7ffull<<52|(u64)(b&0x7fffffu)<<29|1ull<<51); } return (double)v; }
static inline float sse_d2f(double v,u32 mx){ v=sse_daz(v,mx); if(v!=v){ u64 b=fxbits(v); return fxval<float>((u32)(b>>32)&0x80000000u|0x7f800000u|(u32)((b&FX<double>::M)>>29)|0x400000u); } return sse_ftz((float)v,mx); }
static inline void sse_fpcr(u32 mx){ static const unsigned char m[4]={0,2,1,3}; u64 f; __asm__ volatile("mrs %0, fpcr":"=r"(f)); f=(f&~(3ull<<22))|((u64)m[(mx>>13)&3]<<22); __asm__ volatile("msr fpcr, %0"::"r"(f)); }
static inline int sse_sat(int v,int lo,int hi){ return v<lo?lo:v>hi?hi:v; }
static inline u64 sse_sad(const u8*a,const u8*b){ u32 s=0; for(int i=0;i<8;i++) s+=a[i]>b[i]?a[i]-b[i]:b[i]-a[i]; return s; }
static inline u32 sse_pmovmskb(xmm_t s,int n){ u32 r=0; for(int i=0;i<n;i++) r|=(u32)(s.b[i]>>7)<<i; return r; }
