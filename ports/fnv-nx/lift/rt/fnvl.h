
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64;
typedef signed char s8; typedef short s16; typedef int s32; typedef long long s64;
typedef u8 __attribute__((may_alias)) u8a; typedef u16 __attribute__((aligned(1),may_alias)) u16a;
typedef u32 __attribute__((aligned(1),may_alias)) u32a; typedef u64 __attribute__((aligned(1),may_alias)) u64a;
typedef float __attribute__((aligned(1),may_alias)) f32a; typedef double __attribute__((aligned(1),may_alias)) f64a;
typedef union { u32 u[4]; float f[4]; double d[2]; u64 q[2]; u8 b[16]; s8 sb[16]; u16 w[8]; s16 sw[8]; s32 sd[4]; s64 sq[2]; } __attribute__((may_alias)) xmm_t;
/* x87 stack slot (R8): a value with its source format until the first arithmetic use. k: 0 empty, 1 F64 (v = double bits), 2 F32 (v = raw pattern),
   3/4/5 I16/I32/I64 (v = the integer sign-extended to 64 bits), 6 M80 (v = significand, e = sign<<15|exp). rt/fnvl_x87.h */
struct fv { u64 v; u32 e; u32 k; };
struct fnvl_nframe;   /* rt/imp.h: one per live import entry of the thread (native frames between lifted frames) */
#include "fnvl_abi.h"
struct cpu { u32 r[8]; u32 fs; u32 ftop, fcw, fc; xmm_t x[8]; u32 fl; u32 mxcsr; u64 mm_unused[8]; u32 ra; u32 fex; fv fx[8]; u32 eflx; struct fnvl_nframe *nf; u32 hfpcr, hfpsr; u32 sel[6]; u32 teb; u32 fip, fdp; u32 msvcrt[6]; u32 eflags; u32 dr[6]; };   /* mxcsr (reset 0x1f80) is appended: SSE family (W6); mm_unused is the former separate MM register file, kept only so the offsets hold: mm0-7 ARE the significands of fx[0..7] (rt/fnvl_x87.h, MMX aliasing). r[] is only the save area of the
   entry/exit bridges (fnvl_enter): while lifted code runs the eight guest registers live in w19..w26. ra: the address the last `ret` popped (control-transfer rule, README).
   x87 (W13): fx[8] are the PHYSICAL registers (carriers; k==0 is the empty tag), ftop the TOP, fcw the control word, fex the exception status bits that the host FPSR cannot
   hold (invalid + stack fault of an overflow/underflow). eflx (W14): the EFLAGS bits AC (18) and ID (21), the only non-arithmetic bits pushfd/popfd keep (reset 0; see EFL_PUSH). The pre-R8 double stack `st` and tag word `ftag` are deleted (nothing read them; the x87 state is fx/ftop/fcw/fex). eflags, fc (and dr, the debug registers Dr0 Dr1 Dr2 Dr3 Dr6 Dr7 of the CONTEXT):
   the state that a hardware-exception CONTEXT names and that is not a register of the lifted code: at every raise site the lifter flushes its flag and condition-bit locals into c->eflags / c->fc (RAISE_OUT)
   and reloads them after a continue (RAISE_IN); rt/seh.cc writes the CONTEXT from them and reads them back. */
static_assert(__builtin_offsetof(struct cpu, ra) == FNVL_CPU_RA_OFF, "rt/fnvl_abi.h: FNVL_CPU_RA_OFF is the offset of struct cpu::ra");
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
/* Control transfer (README): a lifted function is `void L_<addr>(void)`. A direct call or tail to another entry is a C call of its
   L_ function (CALLX); the one-instruction tests (test/instest.py) redefine CALLX after the prelude in their own sources. */
/* hardware exception codes passed to fnvl_raise(code, at) (rt/seh.cc maps them to NTSTATUS): divide by zero / quotient overflow, #BP, #UD */
#define FNVL_DE0 1
#define FNVL_DEOVF 5
#define FNVL_BP 3
#define FNVL_UD 4
extern "C" { void (*fnvl_dispatch(u32 image_addr))(void);
             void fnvl_trace(u32 entry, u32 esp); }
#define CALLX(h) L_##h()
#ifdef FNVL_TRACE
#define TRACE(a) fnvl_trace(a,esp)
#else
#define TRACE(a) do{}while(0)
#endif
/* Guest memory access (README R9, docs/ORDERING.md "Lifted code"). x86 is TSO and its addresses wrap at 2^32; AArch64 is weakly ordered, guest
   threads run on separate cores, and ldar / stlr / the exclusives take a base register only and fault on a misaligned address. Rule: every guest
   access is one of the assembler templates below, so the compiler can not reorder, split, re-address or delete any part of it:
     the address is the 32-bit sum, zero-extended inside the template (mov wA, wA) or built there from its two halves when it is a constant;
     aligned    load = ldar, store = stlr (sequentially consistent, stronger than TSO);
     unaligned  load = ldr + dmb ishld, store = dmb ishst + str: the load is ordered before every later access, every earlier store before the
                store; earlier loads are already ordered by their own acquire / dmb ishld.
   The alignment test is part of the template (a constant address is decided when the unit is compiled). The templates are volatile: a load whose
   value is dead still faults like the x86 instruction. tools/gate_order.py proves on the objects that no other instruction touches guest memory.
   Host accesses (struct cpu through x27, C locals through sp) are private to the thread and stay plain. M8..MX yield an accessor: conversion to the
   value type loads, assignment stores. */
template<class T> struct GM;
template<class T> struct LKA;
#define FNVL_A_ "mov %w[a], %w[a]\n"
#define FNVL_K_ "movz %w[a], %[lo]\nmovk %w[a], %[hi], lsl 16\n"
#define FNVL_GM_(T, R, SUF, MASK) \
template<> struct GM<T> { u32 a; \
  __attribute__((always_inline)) operator T() const { u32 a_=a; T v; \
    if(sizeof(T)==1) __asm__ volatile(FNVL_A_ "ldar" SUF " %" R "[v], [%x[a]]" : [v]"=&r"(v), [a]"+r"(a_) : : "memory"); \
    else if(__builtin_constant_p(a)){ \
      if(a&MASK) __asm__ volatile(FNVL_K_ "ldr" SUF " %" R "[v], [%x[a]]\ndmb ishld" : [v]"=&r"(v), [a]"=&r"(a_) : [lo]"i"(a&0xffffu), [hi]"i"(a>>16) : "memory"); \
      else __asm__ volatile(FNVL_K_ "ldar" SUF " %" R "[v], [%x[a]]" : [v]"=&r"(v), [a]"=&r"(a_) : [lo]"i"(a&0xffffu), [hi]"i"(a>>16) : "memory"); } \
    else __asm__ volatile(FNVL_A_ "tst %w[a], " #MASK "\nb.ne 1f\nldar" SUF " %" R "[v], [%x[a]]\nb 2f\n1: ldr" SUF " %" R "[v], [%x[a]]\ndmb ishld\n2:" \
                          : [v]"=&r"(v), [a]"+r"(a_) : : "memory", "cc"); \
    return v; } \
  __attribute__((always_inline)) T operator=(T v) const { u32 a_=a; \
    if(sizeof(T)==1) __asm__ volatile(FNVL_A_ "stlr" SUF " %" R "[v], [%x[a]]" : [a]"+r"(a_) : [v]"r"(v) : "memory"); \
    else if(__builtin_constant_p(a)){ \
      if(a&MASK) __asm__ volatile(FNVL_K_ "dmb ishst\nstr" SUF " %" R "[v], [%x[a]]" : [a]"=&r"(a_) : [v]"r"(v), [lo]"i"(a&0xffffu), [hi]"i"(a>>16) : "memory"); \
      else __asm__ volatile(FNVL_K_ "stlr" SUF " %" R "[v], [%x[a]]" : [a]"=&r"(a_) : [v]"r"(v), [lo]"i"(a&0xffffu), [hi]"i"(a>>16) : "memory"); } \
    else __asm__ volatile(FNVL_A_ "tst %w[a], " #MASK "\nb.ne 1f\nstlr" SUF " %" R "[v], [%x[a]]\nb 2f\n1: dmb ishst\nstr" SUF " %" R "[v], [%x[a]]\n2:" \
                          : [a]"+r"(a_) : [v]"r"(v) : "memory", "cc"); \
    return v; } \
  __attribute__((always_inline)) T operator=(const GM &o) const { return *this=(T)o; } };
FNVL_GM_(u8, "w", "b", 0)
FNVL_GM_(u16, "w", "h", 1)
FNVL_GM_(u32, "w", "", 3)
FNVL_GM_(u64, "x", "", 7)
template<class F,class T> struct GMF { u32 a;      /* a float / double cell: the same access on its bit pattern */
  __attribute__((always_inline)) operator F() const { union{ T u; F f; } x; x.u=(T)GM<T>{a}; return x.f; }
  __attribute__((always_inline)) F operator=(F v) const { union{ T u; F f; } x; x.f=v; GM<T>{a}=x.u; return v; }
  __attribute__((always_inline)) F operator=(const GMF &o) const { return *this=(F)o; } };
#define M8(a) (GM<u8>{(u32)(a)})
#define M16(a) (GM<u16>{(u32)(a)})
#define M32(a) (GM<u32>{(u32)(a)})
#define M64(a) (GM<u64>{(u32)(a)})
#define MF32(a) (GMF<float,u32>{(u32)(a)})
#define MF64(a) (GMF<double,u64>{(u32)(a)})
/* LOCK-prefixed instructions and XCHG with memory (SDM Vol 2 "LOCK", "XCHG"): the instruction is one atomic read-modify-write of its memory operand.
   Rule (fnvlift/core.py lock mode): the instruction body runs on a private copy of the operand (LK8..LK64: the first use loads the cell, a store
   changes the copy), then lk_commit replaces the cell by the copy iff it still holds the loaded value (ldaxr / stlxr compare-and-swap); if not, the
   registers and flags are put back and the body runs again on the new value. The commit point is the instruction's place in the total order of
   locked operations; `dmb ish` after it makes it a full barrier like a locked x86 instruction.
   A misaligned operand (x86 locks a split operand, ldaxr faults) takes the WORLD-STOP PATH: the load runs
   fnvl_host_world_stop(), then a plain unaligned load; the commit is a plain unaligned store, `dmb ish`, fnvl_host_world_resume(). While every other guest thread is
   suspended no other guest access interleaves, so that commit never fails. The window holds the body of one lockable instruction (LOCKABLE of fnvlift/f_sys.py: add adc and
   btc btr bts cmpxchg cmpxchg8b dec inc neg not or sbb sub xor xadd xchg): straight-line register arithmetic with no call, no lock and no raise. Before the stop the load writes the
   first and the last byte of the cell back to themselves with a byte compare-and-swap: a cell that is not readable and writable faults there, with the world running, and no
   access can fault while it is stopped. fnvl_host_world_stop / fnvl_host_world_resume are the L5 platform's (platform/os/os.h and os_world.c: stop / resume every other thread of the process, synchronously, under one global lock; resume is called on the thread that stopped). */
extern "C" { void fnvl_host_world_stop(void); void fnvl_host_world_resume(void); }
/* `.fnvl.lk` (one 12-byte record per locked instruction: x86 pc as a plain word, host start, host end as R_AARCH64_P32_ABS32 in the same section): the range is the
   exclusive sequence of the aligned commit below, `ldaxr ... cbnz`, and is emitted by that ONE asm statement. The two labels and the record are text of the same asm, so the compiler
   cannot place anything but the sequence between them: it treats the statement as one opaque, indivisible block (ASM-001). The `ldar` load, the body on the private copy and the
   world-stop path lie outside it; the pc is the constant operand lk_commit(lk_, pc) that fnvlift/core.py passes. */
#define FNVL_LK_(T, R, SUF, MASK, LAST, W) \
template<> struct LKA<T> { \
  static __attribute__((always_inline)) T load(u32 a){ T o; \
    if(sizeof(T)==1 || __builtin_expect(!(a & MASK), 1)) __asm__ volatile(FNVL_A_ "ldar" SUF " %" R "[o], [%x[a]]" : [o]"=&r"(o), [a]"+r"(a) : : "memory"); \
    else { u32 s, t_; unsigned long t; \
      __asm__ volatile(FNVL_A_ "1: ldaxrb %w[s], [%x[a]]\nstlxrb %w[u], %w[s], [%x[a]]\ncbnz %w[u], 1b\n" \
        "add %w[t], %w[a], " #LAST "\n" \
        "3: ldaxrb %w[s], [%x[t]]\nstlxrb %w[u], %w[s], [%x[t]]\ncbnz %w[u], 3b" \
        : [a]"+r"(a), [s]"=&r"(s), [u]"=&r"(t_), [t]"=&r"(t) : : "memory", "cc"); \
      fnvl_host_world_stop(); \
      __asm__ volatile(FNVL_A_ "ldr" SUF " %" R "[o], [%x[a]]\ndmb ishld" : [o]"=&r"(o), [a]"+r"(a) : : "memory"); } \
    return o; } \
  static __attribute__((always_inline)) bool commit(u32 a, T o, T n, u32 pc){ u32 ok, s; T v; \
    if(sizeof(T)==1 || __builtin_expect(!(a & MASK), 1)) { \
      __asm__ volatile(FNVL_A_ "3: ldaxr" SUF " %" R "[v], [%x[a]]\ncmp %" R "[v], %" R "[o]\nb.ne 5f\nstlxr" SUF " %w[s], %" R "[n], [%x[a]]\ncbnz %w[s], 3b\n" \
        "9:\n.pushsection .fnvl.lk,\"a\",%%progbits\n.balign 4\n.word %c[pc]\n.word 3b\n.word 9b\n.popsection\n" \
        "dmb ish\nmov %w[ok], 1\nb 2f\n5: mov %w[ok], 0\n2:" \
        : [ok]"=&r"(ok), [a]"+r"(a), [s]"=&r"(s), [v]"=&r"(v) : [o]"r"((W)o), [n]"r"((W)n), [pc]"i"(pc) : "memory", "cc"); \
      return ok; } \
    __asm__ volatile(FNVL_A_ "dmb ishst\nstr" SUF " %" R "[n], [%x[a]]\ndmb ish" : [a]"+r"(a) : [n]"r"((W)n) : "memory"); \
    fnvl_host_world_resume(); \
    return true; } };
FNVL_LK_(u8, "w", "b", 0, 0, u32)
FNVL_LK_(u16, "w", "h", 1, 1, u32)
FNVL_LK_(u32, "w", "", 3, 3, u32)
FNVL_LK_(u64, "x", "", 7, 7, u64)
template<class T> struct LKS { u32 a; T o, n; u32 st; };
template<class T> struct LKP { LKS<T> *s; u32 a;
  __attribute__((always_inline)) operator T() const { if(!s->st){ s->a=a; s->st=1; s->o=LKA<T>::load(a); s->n=s->o; } return s->n; }
  __attribute__((always_inline)) T operator=(T v) const { (void)(T)*this; s->n=v; return v; } };
template<class T> static inline __attribute__((always_inline)) bool lk_commit(LKS<T> &s, u32 pc){
  if(!s.st) return true;
  bool ok=LKA<T>::commit(s.a,s.o,s.n,pc);
  if(!ok) s.st=0;
  return ok; }
#define LK_BEGIN(n) LKS<u##n> lk_={0,0,0,0}; const u32 lkr_[8]={eax,ecx,edx,ebx,esp,ebp,esi,edi}, lkf_[6]={cf,zf,sf,of,pf,af}
#define LK_RESTORE do{ eax=lkr_[0];ecx=lkr_[1];edx=lkr_[2];ebx=lkr_[3];esp=lkr_[4];ebp=lkr_[5];esi=lkr_[6];edi=lkr_[7]; cf=lkf_[0];zf=lkf_[1];sf=lkf_[2];of=lkf_[3];pf=lkf_[4];af=lkf_[5]; }while(0)
#define LK8(a) (LKP<u8>{&lk_,(u32)(a)})
#define LK16(a) (LKP<u16>{&lk_,(u32)(a)})
#define LK32(a) (LKP<u32>{&lk_,(u32)(a)})
#define LK64(a) (LKP<u64>{&lk_,(u32)(a)})
#define PAR(x) (!(__builtin_popcount((x)&0xffu)&1))
#define DECL u32 cf=0,zf=0,sf=0,of=0,pf=0,af=0,df=0; u32 c0=0,c2=0,c3=0; xmm_t x0,x1,x2,x3,x4,x5,x6,x7; (void)af;(void)pf;(void)df;(void)c2
#define SYNC_IN do{ FL_IN; }while(0)
#define SYNC_OUT do{ FL_OUT; }while(0)   /* the general registers need no sync; flags (instest), x87 (per site, f_x87_ctl.py) and xmm state are appended by the lifter */
#define SYNC_X_IN do{ x0=c->x[0];x1=c->x[1];x2=c->x[2];x3=c->x[3];x4=c->x[4];x5=c->x[5];x6=c->x[6];x7=c->x[7]; }while(0)
#define SYNC_X_OUT do{ c->x[0]=x0;c->x[1]=x1;c->x[2]=x2;c->x[3]=x3;c->x[4]=x4;c->x[5]=x5;c->x[6]=x6;c->x[7]=x7; }while(0)
/* `.fnvl.fsw` (same 12-byte record as `.fnvl.lk`: x86 pc plain, host start, host end as R_AARCH64_P32_ABS32): the store of the x87 status word, and nothing else. FSW_ST16 / FSW_ST32 are
   the store of GM<u16> / GM<u32> with the range around the one store instruction (the aligned stlr, the unaligned str behind its dmb ishst, one asm statement each, so the labels bracket exactly
   that instruction, ASM-001); FSW_AX is the write of AX, the one instruction `bfxil` on the register of EAX (no guest access). Both paths of an address that is not constant record: one pc
   may have several records. */
#define FNVL_FSWREC_ "9:\n.pushsection .fnvl.fsw,\"a\",%%progbits\n.balign 4\n.word %c[pc]\n.word 8b\n.word 9b\n.popsection\n"
#define FNVL_FSW_(T, R, SUF, MASK) \
static inline __attribute__((always_inline)) void fsw_st(u32 a, T v, u32 pc, T*){ \
  if(__builtin_constant_p(a)){ \
    if(a&MASK) __asm__ volatile(FNVL_K_ "dmb ishst\n8: str" SUF " %" R "[v], [%x[a]]\n" FNVL_FSWREC_ : [a]"=&r"(a) : [v]"r"(v), [lo]"i"(a&0xffffu), [hi]"i"(a>>16), [pc]"i"(pc) : "memory"); \
    else __asm__ volatile(FNVL_K_ "8: stlr" SUF " %" R "[v], [%x[a]]\n" FNVL_FSWREC_ : [a]"=&r"(a) : [v]"r"(v), [lo]"i"(a&0xffffu), [hi]"i"(a>>16), [pc]"i"(pc) : "memory"); } \
  else if(__builtin_expect(!(a&MASK), 1)) __asm__ volatile(FNVL_A_ "8: stlr" SUF " %" R "[v], [%x[a]]\n" FNVL_FSWREC_ : [a]"+r"(a) : [v]"r"(v), [pc]"i"(pc) : "memory"); \
  else __asm__ volatile(FNVL_A_ "dmb ishst\n8: str" SUF " %" R "[v], [%x[a]]\n" FNVL_FSWREC_ : [a]"+r"(a) : [v]"r"(v), [pc]"i"(pc) : "memory"); }
FNVL_FSW_(u16, "w", "h", 1)
FNVL_FSW_(u32, "w", "", 3)
#define FSW_ST16(A, V, PC) fsw_st((u32)(A), (u16)(V), (PC), (u16*)0)
#define FSW_ST32(A, V, PC) fsw_st((u32)(A), (u32)(V), (PC), (u32*)0)
#define FSW_AX(V, PC) do{ u32 v_=(V); __asm__ volatile("8: bfxil %w[r], %w[v], 0, 16\n" FNVL_FSWREC_ : [r]"+r"(eax) : [v]"r"(v_), [pc]"i"(PC)); }while(0)
#include "fnvl_x87.h"
/* ---- SSE / SSE2 / MMX runtime (W6). MXCSR: RC, FTZ, DAZ are honoured; the exception status bits and the exception masks are not
   modelled (no SIMD exception is ever raised). ldmxcsr mirrors RC into FPCR so that host arithmetic rounds like the guest. mm0-7 are the significands of the physical
   x87 registers (MMR / MMW / MMX_ENTER / MMX_EMMS in rt/fnvl_x87.h). ---- */
extern "C" { u32 fnvl_rcp32(u32 bits); u32 fnvl_rsqrt32(u32 bits); }   /* reference-CPU tables, supplied by the runtime owner */
typedef xmm_t __attribute__((aligned(1))) xmm_ua;
struct GMX { u32 a;        /* a 16-byte cell: two 8-byte accesses under the rule above; the second address wraps at 2^32 like the first */
  __attribute__((always_inline)) operator xmm_t() const { xmm_t r; r.q[0]=(u64)GM<u64>{a}; r.q[1]=(u64)GM<u64>{a+8u}; return r; }
  __attribute__((always_inline)) xmm_t operator=(const xmm_t &v) const { GM<u64>{a}=v.q[0]; GM<u64>{a+8u}=v.q[1]; return v; }
  __attribute__((always_inline)) xmm_t operator=(const GMX &o) const { return *this=(xmm_t)o; } };
#define MX(a) (GMX{(u32)(a)})
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
FNVL_F2I static inline u32 sse_f2i(double v,u32 rc){ double r=rc==0?__builtin_roundeven(v):rc==1?__builtin_floor(v):rc==2?__builtin_ceil(v):__builtin_trunc(v);
  if(!(r>=-2147483648.0&&r<=2147483647.0)) return 0x80000000u; return (u32)(s32)r; }
static inline double sse_f2d(float v,u32 mx){ v=sse_daz(v,mx); if(v!=v){ u32 b=fxbits(v); return fxval<double>((u64)(b&0x80000000u)<<32|0x7ffull<<52|(u64)(b&0x7fffffu)<<29|1ull<<51); } return (double)v; }
static inline float sse_d2f(double v,u32 mx){ v=sse_daz(v,mx); if(v!=v){ u64 b=fxbits(v); return fxval<float>((u32)(b>>32)&0x80000000u|0x7f800000u|(u32)((b&FX<double>::M)>>29)|0x400000u); } return sse_ftz((float)v,mx); }
static inline void sse_fpcr(u32 mx){ static const unsigned char m[4]={0,2,1,3}; u64 f; __asm__ volatile("mrs %0, fpcr":"=r"(f)); f=(f&~(3ull<<22))|((u64)m[(mx>>13)&3]<<22); __asm__ volatile("msr fpcr, %0"::"r"(f)); }
static inline int sse_sat(int v,int lo,int hi){ return v<lo?lo:v>hi?hi:v; }
static inline u64 sse_sad(const u8*a,const u8*b){ u32 s=0; for(int i=0;i<8;i++) s+=a[i]>b[i]?a[i]-b[i]:b[i]-a[i]; return s; }
static inline u32 sse_pmovmskb(xmm_t s,int n){ u32 r=0; for(int i=0;i<n;i++) r|=(u32)(s.b[i]>>7)<<i; return r; }
/* ---- Integer / system helpers (W14). ---- */
/* more hardware exception codes for fnvl_raise(code, at); rt/seh.cc maps them (the case in its status mapping is the runtime owner's):
   FNVL_PRIV 6  STATUS_PRIVILEGED_INSTRUCTION 0xC0000096, a fault: ExceptionAddress = Context.Eip = at
   FNVL_BOUNDS 7  STATUS_ARRAY_BOUNDS_EXCEEDED 0xC000008C (bound, `int 5`), a fault: Eip = at
   FNVL_INTO 8  STATUS_INTEGER_OVERFLOW 0xC0000095 (into, `int 4`), a trap: ExceptionAddress = at, Context.Eip = at+1
   FNVL_ASSERT 9  STATUS_ASSERTION_FAILURE 0xC0000420 (`int 0x2c`): ExceptionAddress = Context.Eip = at
   `int 3` / `int 0x2d` use FNVL_BP with at = the address of the byte after the opcode byte (see f_sys.py). A #GP from a software interrupt whose gate has DPL 0
   is fnvl_raise_av(0, 0xFFFFFFFF, at). */
#define FNVL_PRIV 6
#define FNVL_BOUNDS 7
#define FNVL_INTO 8
#define FNVL_ASSERT 9
extern "C" { void fnvl_cpuid(u32 leaf, u32 sub, u32 out[4]); u64 fnvl_rdtsc(void); }
/* The hardware-raise entries of the runtime (rt/seh.cc) are declared ONLY here, inside namespace fnvl_rt, and only FNVL_RAISE / FNVL_RAISE_AV below name them: a lifted unit that calls the raw
   fnvl_raise or fnvl_raise_av (no EFLAGS / C0 C2 C3 flush) does not find the name and is a compile error. They are the same C-linkage functions as the definitions in seh.cc. */
namespace fnvl_rt { extern "C" { void fnvl_raise(u32 code, u32 at); void fnvl_raise_av(u32 access, u32 addr, u32 at); } }
/* EFLAGS image of pushfd: the arithmetic flags and DF of the lifted state, bit 1, IF (9) set, AC and ID from c->eflx, everything else 0.
   popfd loads CF PF AF ZF SF DF OF; AC and ID are kept in c->eflx; TF/IF/IOPL/NT/RF/VM/VIF/VIP changes are ignored (user mode cannot change them). */
#define EFL_PUSH() ((u32)((cf&1)|2u|((pf&1)<<2)|((af&1)<<4)|((zf&1)<<6)|((sf&1)<<7)|0x200u|((df&1)<<10)|((of&1)<<11)|(c->eflx&0x240000u)))
#define EFL_POP(v_) do{ u32 f_=(v_); cf=f_&1; pf=(f_>>2)&1; af=(f_>>4)&1; zf=(f_>>6)&1; sf=(f_>>7)&1; df=(f_>>10)&1; of=(f_>>11)&1; c->eflx=f_&0x240000u; }while(0)
/* RAISE_OUT / RAISE_IN: the flush rule of every explicit raise site (the lifter emits only FNVL_RAISE / FNVL_RAISE_AV; the raw entries are not visible to a lifted unit).
   Everything the CONTEXT names that the lifted function keeps in locals goes to struct cpu before the call: EFLAGS (the pushfd image, EFL_PUSH) into c->eflags and the x87 condition bits
   C0 C2 C3 (FSW bits 8 10 14) into c->fc; the x87 stack and xmm state are flushed by the same sites (fnvlift f_x87_ctl.store, SYNC_X_OUT). After a continue the handler's EFLAGS and condition
   bits are loaded back (EFL_POP: the arithmetic flags and DF; AC and ID into c->eflx; the other bits cannot be changed by user mode). */
#define RAISE_OUT do{ c->eflags=EFL_PUSH(); c->fc=((c0&1)<<8)|((c2&1)<<10)|((c3&1)<<14); }while(0)
#define RAISE_IN do{ u32 ef_=c->eflags; EFL_POP(ef_); c0=(c->fc>>8)&1; c2=(c->fc>>10)&1; c3=(c->fc>>14)&1; }while(0)
#define FNVL_RAISE(code, at) do{ RAISE_OUT; fnvl_rt::fnvl_raise(code, at); RAISE_IN; }while(0)
#define FNVL_RAISE_AV(acc, addr, at) do{ RAISE_OUT; fnvl_rt::fnvl_raise_av(acc, addr, at); RAISE_IN; }while(0)
/* aam/aad/aaa/aas/daa/das are generated inline; segment selector values of a Win32 x86 user process (as in rt/seh.cc's CONTEXT) */
#define SEL_CS 0x1bu
#define SEL_DS 0x23u
#define SEL_FS 0x3bu
/* ---- W15: class rules of the system / rare-instruction family (fnvlift/f_sys.py, f_int.py). Appended; nothing above changed. ----
   More hardware exception codes for fnvl_raise(code, at) (the mapping lines for rt/seh.cc are in the W15 report):
   FNVL_STEP 10  STATUS_SINGLE_STEP 0x80000004 (`int1` / icebp, F1: a debug TRAP): ExceptionAddress = Context.Eip = at, which is the address of the NEXT instruction
   (KiTrap01: KiDispatchException0Args(STATUS_SINGLE_STEP, TrapFrame->Eip), no parameters). */
#define FNVL_STEP 10
/* FNVL_LOCK 11  STATUS_INVALID_LOCK_SEQUENCE 0xC000001E: #UD of an instruction that has an F0 prefix among its bytes (KiTrap06Handler); at = the instruction, a fault. */
#define FNVL_LOCK 11
/* FNVL_DBGSRV 12  `int 0x2d` (the debug service), at = the instruction; the rule of Wine 9.0 handle_interrupt, rt/seh.cc fnvl_raise. */
#define FNVL_DBGSRV 12
/* Segment registers of the process model (rt/seh.cc CONTEXT values above): index into the register file. Every segment has base 0 and limit 4 GiB except FS
   (base = TEB, limit 0xFFF); the only descriptors a ring-3 load can reach are the three of the model GDT below, plus the null selector for ES/DS/FS/GS.
   SELK strips the RPL: it keeps the descriptor index and the TI bit (a TI=1 selector names the LDT, which a Win32 process has none of: always invalid). */
#define SEG_ES 0
#define SEG_CS 1
#define SEG_SS 2
#define SEG_DS 3
#define SEG_FS 4
#define SEG_GS 5
#define SELK(s_) ((s_)&0xfffcu)
/* Model GDT (the NT x86 user descriptors; SDM Vol 3 3.4.5 for the layout): high dword of the descriptor of the selector, 0 = no descriptor a ring-3 process can name
   (null, kernel segments and TSS: DPL 0, not accessible at CPL 3; every index outside the three: not present).
   CS 0x00cffb00: G=1 D=1 limit 0xfffff, P DPL=3 S=1 type 0xb (execute/read, accessed).  DS 0x00cff300: the same as data, type 0x3 (read/write, accessed).
   FS 0x0040f300: G=0 D=1 limit 0xfff (the TEB page), DPL=3 data read/write; its base is the TEB (the cpu's fs base), which no instruction below reads. */
static inline u32 fnvl_gdt_hi(u32 sel){ u32 k=SELK(sel); return k==SELK(SEL_CS)?0x00cffb00u:k==SELK(SEL_DS)?0x00cff300u:k==SELK(SEL_FS)?0x0040f300u:0u; }
static inline u32 fnvl_gdt_limit(u32 hi){ u32 l=hi==0x0040f300u?0xfffu:0xfffffu; return (hi&0x00800000u)?(l<<12)|0xfffu:l; }
/* May a MOV/POP/LDS... load selector `sel` into segment register idx at CPL 3 (SDM Vol 3 5.2 / MOV-to-Sreg pseudo-code)? ES/DS/FS/GS: the null selector (index 0, TI 0, any RPL),
   or a data / readable-code segment with DPL >= max(CPL, RPL) = 3: all three model descriptors. SS: not null, RPL == CPL == 3, a writable data segment (DS or FS). CS is never
   loaded by these instructions (a far transfer checks it: RPL <= 3 and the CS index). */
static inline u32 fnvl_seg_ok(u32 idx, u32 sel){ u32 k=SELK(sel);
  if(idx==SEG_SS) return (sel&3)==3&&(k==SELK(SEL_DS)||k==SELK(SEL_FS));
  return k==0||k==SELK(SEL_CS)||k==SELK(SEL_DS)||k==SELK(SEL_FS); }
/* Segment register STATE (lead): fnvl_seg_set(c, idx, sel): c->sel[idx] = sel and, for FS, c->fs = (SELK(sel)==SELK(SEL_FS)) ? c->teb : 0 (a flat selector has base 0; the null
   selector leaves no usable base); fnvl_seg_get(c, idx) reads c->sel[idx] (CS: SEL_CS). The caller has already checked fnvl_seg_ok. The lifter emits fnvl_seg_set only for a load
   instruction and, while f_int.SEG_STATE is False, still reads the register as the process constant of SEGSEL. */
static inline void fnvl_seg_set(struct cpu *p, u32 idx, u32 sel){ p->sel[idx]=sel; if(idx==SEG_FS) p->fs=(SELK(sel)==SELK(SEL_FS))?p->teb:0u; }
static inline u32 fnvl_seg_get(struct cpu *p, u32 idx){ return p->sel[idx]; }
/* Segment state of a fresh thread (rt/enter.cc cpu_reset): the process constants of a native 32-bit Windows 7 user process (rt/seh.cc make_context): CS 0x1b, DS=ES=SS 0x23, FS 0x3b with
   base = TEB, GS null. struct cpu.sel is indexed ES, CS, SS, DS, FS, GS (SEG_*); sel and teb are the last fields of struct cpu. */
static inline void fnvl_seg_init(struct cpu *p, u32 teb){ p->teb=teb; p->sel[SEG_ES]=SEL_DS; p->sel[SEG_CS]=SEL_CS; p->sel[SEG_SS]=SEL_DS; p->sel[SEG_DS]=SEL_DS; p->sel[SEG_FS]=SEL_FS; p->sel[SEG_GS]=0u; p->fs=teb; }
/* Base of a `gs:` access, by the CURRENT GS selector (the null selector never gets here: the lifter emits a #GP(0) guard before the instruction): the FS descriptor -> TEB, a flat one -> 0. */
#define FNVL_GS_BASE() (SELK(c->sel[SEG_GS])==SELK(SEL_FS)?c->teb:0u)
/* Descriptor-table registers as sgdt/sidt/sldt/str/smsw read them (readable at CPL 3, CR4.UMIP = 0). The reference OS state is not derivable from the instruction set, so the
   model fixes it: GDT/IDT in kernel space with the standard NT x86 sizes (GDT 128 descriptors, IDT 256 gates), no LDT (selector 0), TR = KGDT_TSS 0x28 (RPL 0),
   MSW/CR0 = PG|WP|NE|ET|MP|PE with TS clear: 0x80010033. */
#define FNVL_GDTR_BASE 0x80b95000u
#define FNVL_GDTR_LIMIT 0x03ffu
#define FNVL_IDTR_BASE 0x80b95400u
#define FNVL_IDTR_LIMIT 0x07ffu
#define FNVL_LDTR_SEL 0u
#define FNVL_TR_SEL 0x28u
#define FNVL_CR0 0x80010033u
