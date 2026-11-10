
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64;
typedef signed char s8; typedef short s16; typedef int s32; typedef long long s64;
typedef u8 __attribute__((may_alias)) u8a; typedef u16 __attribute__((aligned(1),may_alias)) u16a;
typedef u32 __attribute__((aligned(1),may_alias)) u32a; typedef u64 __attribute__((aligned(1),may_alias)) u64a;
typedef float __attribute__((aligned(1),may_alias)) f32a; typedef double __attribute__((aligned(1),may_alias)) f64a;
typedef union { u32 u[4]; float f[4]; double d[2]; u64 q[2]; } __attribute__((may_alias)) xmm_t;
struct cpu { u32 r[8]; u32 fs; u32 ftop, ftag, fcw, fc; double st[8]; xmm_t x[8]; u32 fl; };
/* FNVL_FLAGS_IO (test/instest.py): the arithmetic flags, DF and the x87 condition bits also travel in c->fl / c->fc.
   fl: the EFLAGS bit positions (CF 0, PF 2, AF 4, ZF 6, SF 7, DF 10, OF 11); fc: C0 bit 8, C2 bit 10, C3 bit 14. */
#ifdef FNVL_FLAGS_IO
#define FL_IN do{ u32 f_=c->fl; cf=f_&1; pf=(f_>>2)&1; af=(f_>>4)&1; zf=(f_>>6)&1; sf=(f_>>7)&1; df=(f_>>10)&1; of=(f_>>11)&1; c0=(c->fc>>8)&1; c2=(c->fc>>10)&1; c3=(c->fc>>14)&1; }while(0)
#define FL_OUT do{ c->fl=(cf&1)|((pf&1)<<2)|((af&1)<<4)|((zf&1)<<6)|((sf&1)<<7)|((df&1)<<10)|((of&1)<<11); c->fc=((c0&1)<<8)|((c2&1)<<10)|((c3&1)<<14); }while(0)
#else
#define FL_IN do{}while(0)
#define FL_OUT do{}while(0)
#endif
extern "C" { void fnvl_call(struct cpu*, u32 target); void fnvl_tail(struct cpu*, u32 target); void fnvl_trap(struct cpu*, u32 at, u32 why); }
#define P(a) ((unsigned long)(u32)(a))
#define M8(a) (*(u8a*)P(a))
#define M16(a) (*(u16a*)P(a))
#define M32(a) (*(u32a*)P(a))
#define M64(a) (*(u64a*)P(a))
#define MF32(a) (*(f32a*)P(a))
#define MF64(a) (*(f64a*)P(a))
#define PAR(x) (!(__builtin_popcount((x)&0xffu)&1))
#define DECL u32 eax,ecx,edx,ebx,esp,ebp,esi,edi; u32 cf=0,zf=0,sf=0,of=0,pf=0,af=0,df=0; u32 ftop=0,ftag=0,fcw=0x27f,c0=0,c2=0,c3=0; double st[8]; xmm_t x0,x1,x2,x3,x4,x5,x6,x7; (void)af;(void)pf;(void)df;(void)c2
#define SYNC_IN do{ eax=c->r[0];ecx=c->r[1];edx=c->r[2];ebx=c->r[3];esp=c->r[4];ebp=c->r[5];esi=c->r[6];edi=c->r[7]; FL_IN; }while(0)
#define SYNC_OUT do{ c->r[0]=eax;c->r[1]=ecx;c->r[2]=edx;c->r[3]=ebx;c->r[4]=esp;c->r[5]=ebp;c->r[6]=esi;c->r[7]=edi; FL_OUT; }while(0)
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
