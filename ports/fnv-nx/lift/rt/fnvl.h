
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64;
typedef unsigned __int128 u128; typedef signed char s8; typedef short s16; typedef int s32; typedef long long s64;
typedef u8 __attribute__((may_alias)) u8a; typedef u16 __attribute__((aligned(1),may_alias)) u16a;
typedef u32 __attribute__((aligned(1),may_alias)) u32a; typedef u64 __attribute__((aligned(1),may_alias)) u64a;
typedef float __attribute__((aligned(1),may_alias)) f32a; typedef double __attribute__((aligned(1),may_alias)) f64a;
typedef union { u32 u[4]; float f[4]; double d[2]; u64 q[2]; u8 b[16]; s8 sb[16]; u16 w[8]; s16 sw[8]; s32 sd[4]; s64 sq[2]; } __attribute__((may_alias)) xmm_t;
/* x87 stack slot (R8): a value with its source format until the first arithmetic use. k: 0 empty, 1 F64 (v = double bits), 2 F32 (v = raw pattern),
   3/4/5 I16/I32/I64 (v = the integer sign-extended to 64 bits), 6 M80 (v = significand, e = sign<<15|exp). rt/fnvl_x87.h */
struct fv { u64 v; u32 e; u32 k; };
struct fnvl_nframe;   /* rt/imp.h: one per live import entry of the thread (native frames between lifted frames) */
#include "fnvl_abi.h"
struct cpu { u32 r[8]; u32 fs; u32 ftop, fcw, fc; xmm_t x[8]; u32 fl; u32 mxcsr; u64 mm_unused[8]; u32 ra; u32 fex; fv fx[8]; u32 eflx; struct fnvl_nframe *nf; u32 hfpcr, hfpsr; u32 sel[6]; u32 teb; u32 fip, fdp; u32 msvcrt[6]; u32 eflags; u32 dr[6]; u32 fsw, fop, fcs, fds; };   /* mxcsr (reset 0x1f80) is appended: SSE family (W6); mm_unused is the former separate MM register file, kept only so the offsets hold: mm0-7 ARE the significands of fx[0..7] (rt/fnvl_x87.h, MMX aliasing). r[] is only the save area of the
   entry/exit bridges (fnvl_enter): while lifted code runs the eight guest registers live in w19..w26. ra: the address the last `ret` popped (control-transfer rule, README).
   x87 (W13): fx[8] are the PHYSICAL registers (carriers; k==0 is the empty tag), ftop the TOP, fcw the control word, fex the exception status bits that the host FPSR cannot
   hold (invalid + stack fault of an overflow/underflow). eflx (W14): the EFLAGS bits AC (18) and ID (21), the only non-arithmetic bits pushfd/popfd keep (reset 0; see EFL_PUSH). The pre-R8 double stack `st` and tag word `ftag` are deleted (nothing read them; the x87 state is fx/ftop/fcw/fex). eflags, fc (and dr, the debug registers Dr0 Dr1 Dr2 Dr3 Dr6 Dr7 of the CONTEXT):
   the state that a hardware-exception CONTEXT names and that is not a register of the lifted code: at every raise site the lifter flushes its flag and condition-bit locals into c->eflags / c->fc (RAISE_OUT)
   and reloads them after a continue (RAISE_IN); rt/seh.cc writes the CONTEXT from them and reads them back. */
static_assert(__builtin_offsetof(struct cpu, ra) == FNVL_CPU_RA_OFF, "rt/fnvl_abi.h: FNVL_CPU_RA_OFF is the offset of struct cpu::ra");
/* The layout of struct cpu is append-only: every field that existed before the x87 status words (fsw, fop, fcs, fds) keeps its offset (ILP32). */
static_assert(__builtin_offsetof(struct cpu, fex) == 252 && __builtin_offsetof(struct cpu, fx) == 256 && __builtin_offsetof(struct cpu, eflx) == 384 && __builtin_offsetof(struct cpu, nf) == 388 &&
              __builtin_offsetof(struct cpu, hfpcr) == 392 && __builtin_offsetof(struct cpu, sel) == 400 && __builtin_offsetof(struct cpu, teb) == 424 && __builtin_offsetof(struct cpu, fip) == 428 &&
              __builtin_offsetof(struct cpu, fdp) == 432 && __builtin_offsetof(struct cpu, msvcrt) == 436 && __builtin_offsetof(struct cpu, eflags) == 460 && __builtin_offsetof(struct cpu, dr) == 464 &&
              __builtin_offsetof(struct cpu, fsw) == 488 && __builtin_offsetof(struct cpu, fop) == 492 && __builtin_offsetof(struct cpu, fcs) == 496 && __builtin_offsetof(struct cpu, fds) == 500 && sizeof(struct cpu) == 504,
              "struct cpu: earlier offsets hold; fsw fop fcs fds are appended");
/* New x87 state (appended): fsw = the ES (bit 7) and B (bit 15) bits of the FSW, loaded verbatim by FRSTOR / FLDENV / a CONTEXT restore and cleared by FNINIT / FNCLEX; the FSW image the program or a CONTEXT sees is
   TOP | C0..C3 | the sticky bits | (fsw | ES/B derived from the flags and FCW), where ES = B = ((flags & ~FCW & 0x3f) != 0) (SDM Vol 1 8.5: ES is set when an unmasked exception flag is set). fop (FOP, 11 bits), fcs (FCS),
   fds (FDS) are the last-instruction opcode and the selectors, written by the same x87 entry as fip / fdp (fnvlift/f_x87.py ptrs) and loaded by FLDENV / FRSTOR / a CONTEXT restore. */
/* FNVL_FLAGS_IO (the retired one-instruction harness): the arithmetic flags, DF and the x87 condition bits also travel in c->fl / c->fc.
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
   L_ function (CALLX); the one-instruction tests (the retired one-instruction harness) redefine CALLX after the prelude in their own sources. */
#include "fnvl_faults.h"      /* the FNVL_* fault codes: generated from fnvlift/faults.json, the one CPU fault table (tools/gen_faults.py) */
/* hardware exception codes passed to fnvl_raise(code, at) (rt/seh.cc maps them to NTSTATUS): divide by zero / quotient overflow, #BP, #UD */
#include "fnvl_host.h"      /* fnvnx_entry_fn (ENTRY1): what the dispatcher returns */
extern "C" { fnvnx_entry_fn fnvl_dispatch(u32 image_addr);
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
/* Rule LK-ONLY by construction: every plain accessor macro (M8..M64, MF32, MF64, MX) passes the name `lkwin_` to its accessor. Outside a locked instruction `lkwin_` is the global NoLkWin and the call
   compiles; LK_BEGIN declares a block-local `lkwin_` of another type, so a plain guest access spelled inside the window of a locked instruction (by any emitter path) does not compile.
   The accessors themselves (GM, GMF, GMX) are named only here. */
struct NoLkWin {}; struct LkWin {};
static constexpr NoLkWin lkwin_ = {};
#define FNVL_A_ "mov %w[a], %w[a]\n"
#define FNVL_K_ "movz %w[a], %[lo]\nmovk %w[a], %[hi], lsl 16\n"
#define FNVL_GM_(T, R, SUF, MASK) \
template<> struct GM<T> { u32 a; \
  __attribute__((always_inline)) operator T() const { u32 a_=a; T v; \
    if(sizeof(T)==1) __asm__ volatile(FNVL_A_ "ldar" SUF " %" R "[v], [%x[a]]" : [v]"=&r"(v), [a]"+r"(a_) : : "memory"); \
    else __asm__ volatile(FNVL_A_ "tst %w[a], " #MASK "\nb.ne 1f\nldar" SUF " %" R "[v], [%x[a]]\nb 2f\n1: ldr" SUF " %" R "[v], [%x[a]]\ndmb ishld\n2:" \
                          : [v]"=&r"(v), [a]"+r"(a_) : : "memory", "cc"); \
    return v; } \
  __attribute__((always_inline)) T operator=(T v) const { u32 a_=a; \
    if(sizeof(T)==1) __asm__ volatile(FNVL_A_ "stlr" SUF " %" R "[v], [%x[a]]" : [a]"+r"(a_) : [v]"r"(v) : "memory"); \
    else __asm__ volatile(FNVL_A_ "tst %w[a], " #MASK "\nb.ne 1f\nstlr" SUF " %" R "[v], [%x[a]]\nb 2f\n1: dmb ishst\nstr" SUF " %" R "[v], [%x[a]]\n2:" \
                          : [a]"+r"(a_) : [v]"r"(v) : "memory", "cc"); \
    return v; } \
  __attribute__((always_inline)) T operator=(const GM &o) const { return *this=(T)o; } \
  __attribute__((always_inline)) const GM &g(NoLkWin) const { return *this; } };
FNVL_GM_(u8, "w", "b", 0)
FNVL_GM_(u16, "w", "h", 1)
FNVL_GM_(u32, "w", "", 3)
FNVL_GM_(u64, "x", "", 7)
template<class F,class T> struct GMF { u32 a;      /* a float / double cell: the same access on its bit pattern */
  __attribute__((always_inline)) operator F() const { union{ T u; F f; } x; x.u=(T)GM<T>{a}; return x.f; }
  __attribute__((always_inline)) F operator=(F v) const { union{ T u; F f; } x; x.f=v; GM<T>{a}=x.u; return v; }
  __attribute__((always_inline)) F operator=(const GMF &o) const { return *this=(F)o; }
  __attribute__((always_inline)) const GMF &g(NoLkWin) const { return *this; } };
/* A static address (an instruction whose memory operand is a displacement alone) takes the K form: the address is the template parameter A, so the two halves of the movz / movk operands are constants by
   construction, never by what the optimizer folds; every other address takes the register form above. */
template<class T, u32 A> struct GMK;
#define FNVL_GMK_(T, R, SUF, MASK) \
template<u32 A> struct GMK<T, A> { \
  __attribute__((always_inline)) operator T() const { u32 a_; T v; \
    if(A&MASK) __asm__ volatile(FNVL_K_ "ldr" SUF " %" R "[v], [%x[a]]\ndmb ishld" : [v]"=&r"(v), [a]"=&r"(a_) : [lo]"i"(A&0xffffu), [hi]"i"(A>>16) : "memory"); \
    else __asm__ volatile(FNVL_K_ "ldar" SUF " %" R "[v], [%x[a]]" : [v]"=&r"(v), [a]"=&r"(a_) : [lo]"i"(A&0xffffu), [hi]"i"(A>>16) : "memory"); \
    return v; } \
  __attribute__((always_inline)) T operator=(T v) const { u32 a_; \
    if(A&MASK) __asm__ volatile(FNVL_K_ "dmb ishst\nstr" SUF " %" R "[v], [%x[a]]" : [a]"=&r"(a_) : [v]"r"(v), [lo]"i"(A&0xffffu), [hi]"i"(A>>16) : "memory"); \
    else __asm__ volatile(FNVL_K_ "stlr" SUF " %" R "[v], [%x[a]]" : [a]"=&r"(a_) : [v]"r"(v), [lo]"i"(A&0xffffu), [hi]"i"(A>>16) : "memory"); \
    return v; } \
  __attribute__((always_inline)) T operator=(const GMK &o) const { return *this=(T)o; } \
  __attribute__((always_inline)) const GMK &g(NoLkWin) const { return *this; } };
FNVL_GMK_(u8, "w", "b", 0)
FNVL_GMK_(u16, "w", "h", 1)
FNVL_GMK_(u32, "w", "", 3)
FNVL_GMK_(u64, "x", "", 7)
template<class F,class T,u32 A> struct GMKF {
  __attribute__((always_inline)) operator F() const { union{ T u; F f; } x; x.u=(T)GMK<T,A>{}; return x.f; }
  __attribute__((always_inline)) F operator=(F v) const { union{ T u; F f; } x; x.f=v; GMK<T,A>{}=x.u; return v; }
  __attribute__((always_inline)) F operator=(const GMKF &o) const { return *this=(F)o; }
  __attribute__((always_inline)) const GMKF &g(NoLkWin) const { return *this; } };
#define MK8(A) (GMK<u8,(A)>{}.g(lkwin_))
#define MK16(A) (GMK<u16,(A)>{}.g(lkwin_))
#define MK32(A) (GMK<u32,(A)>{}.g(lkwin_))
#define MK64(A) (GMK<u64,(A)>{}.g(lkwin_))
#define MKF32(A) (GMKF<float,u32,(A)>{}.g(lkwin_))
#define MKF64(A) (GMKF<double,u64,(A)>{}.g(lkwin_))
#define M8(a) (GM<u8>{(u32)(a)}.g(lkwin_))
#define M16(a) (GM<u16>{(u32)(a)}.g(lkwin_))
#define M32(a) (GM<u32>{(u32)(a)}.g(lkwin_))
#define M64(a) (GM<u64>{(u32)(a)}.g(lkwin_))
#define MF32(a) (GMF<float,u32>{(u32)(a)}.g(lkwin_))
#define MF64(a) (GMF<double,u64>{(u32)(a)}.g(lkwin_))
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
/* `.fnvl.lk` (12-byte records of three words: x86 pc, host start, host end; pc plain, start and end R_AARCH64_P32_ABS32 in the same executable section; docs/ORDERING.md). A locked
   instruction: the range is the exclusive sequence of the aligned commit below, `ldaxr ... cbnz`, emitted by that ONE asm statement. The labels and the record are text of the same asm, so the compiler
   cannot place anything but the sequence between them: it treats the statement as one opaque, indivisible block (ASM-001). The `ldar` load, the body on the private copy and the world-stop path lie
   outside it; the pc is the template parameter of lk_commit<pc>(lk_) that fnvlift/core.py passes. */
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
  template<u32 PC> static __attribute__((always_inline)) bool commit(u32 a, T o, T n){ u32 ok, s; T v; \
    if(sizeof(T)==1 || __builtin_expect(!(a & MASK), 1)) { \
      __asm__ volatile(FNVL_A_ "3: ldaxr" SUF " %" R "[v], [%x[a]]\ncmp %" R "[v], %" R "[o]\nb.ne 5f\nstlxr" SUF " %w[s], %" R "[n], [%x[a]]\ncbnz %w[s], 3b\n" \
        "9:\n.pushsection .fnvl.lk,\"a\",%%progbits\n.balign 4\n.word %c[pc]\n.word 3b\n.word 9b\n.popsection\n" \
        "dmb ish\nmov %w[ok], 1\nb 2f\n5: mov %w[ok], 0\n2:" \
        : [ok]"=&r"(ok), [a]"+r"(a), [s]"=&r"(s), [v]"=&r"(v) : [o]"r"((W)o), [n]"r"((W)n), [pc]"i"(PC) : "memory", "cc"); \
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
template<u32 PC, class T> static inline __attribute__((always_inline)) bool lk_commit(LKS<T> &s){
  if(!s.st) return true;
  bool ok=LKA<T>::template commit<PC>(s.a,s.o,s.n);
  if(!ok) s.st=0;
  return ok; }
#define LK_BEGIN(n) const LkWin lkwin_ = {}; LKS<u##n> lk_={0,0,0,0}; const u32 lkr_[8]={eax,ecx,edx,ebx,esp,ebp,esi,edi}, lkf_[6]={cf,zf,sf,of,pf,af}
#define LK_RESTORE do{ eax=lkr_[0];ecx=lkr_[1];edx=lkr_[2];ebx=lkr_[3];esp=lkr_[4];ebp=lkr_[5];esi=lkr_[6];edi=lkr_[7]; cf=lkf_[0];zf=lkf_[1];sf=lkf_[2];of=lkf_[3];pf=lkf_[4];af=lkf_[5]; }while(0)
#define LK8(a) (LKP<u8>{&lk_,(u32)(a)})
#define LK16(a) (LKP<u16>{&lk_,(u32)(a)})
#define LK32(a) (LKP<u32>{&lk_,(u32)(a)})
#define LK64(a) (LKP<u64>{&lk_,(u32)(a)})
#define PAR(x) (!(__builtin_popcount((x)&0xffu)&1))
#define DECL u32 cf=0,zf=0,sf=0,of=0,pf=0,af=0,df=0; u32 c0=0,c2=0,c3=0; xmm_t x0,x1,x2,x3,x4,x5,x6,x7; (void)af;(void)pf;(void)df;(void)c2
#define SYNC_IN do{ FL_IN; }while(0)
#define SYNC_OUT do{ FL_OUT; }while(0)   /* the general registers need no sync; flags (the retired one-instruction harness), x87 (per site, f_x87_ctl.py) and xmm state are appended by the lifter */
#define SYNC_X_IN do{ x0=c->x[0];x1=c->x[1];x2=c->x[2];x3=c->x[3];x4=c->x[4];x5=c->x[5];x6=c->x[6];x7=c->x[7]; }while(0)
#define SYNC_X_OUT do{ c->x[0]=x0;c->x[1]=x1;c->x[2]=x2;c->x[3]=x3;c->x[4]=x4;c->x[5]=x5;c->x[6]=x6;c->x[7]=x7; }while(0)
/* `.fnvl.fsw` (the same 12-byte layout), an x87 status instruction (FSTSW, FNSTENV, FNSAVE): the range is the store of the status word, one instruction, and nothing else. FSW_ST16 / FSW_ST32 store it as `dmb ishst; str`
   (a status-word store needs no more than TSO: every earlier guest load is an acquire, so it is ordered before the store, and `dmb ishst` orders the earlier stores; a plain str also takes an unaligned
   address): one asm statement holds the dmb, the labels and the record, so they bracket exactly that str (ASM-001), and one instruction yields one record. FSW_AX is the write of AX, the one
   instruction `bfxil` on the register of EAX (no guest access). */
#define FNVL_FSWREC_ "9:\n.pushsection .fnvl.fsw,\"a\",%%progbits\n.balign 4\n.word %c[pc]\n.word 8b\n.word 9b\n.popsection\n"
#define FNVL_FSW_(T, R, SUF, MASK) \
template<u32 PC> static inline __attribute__((always_inline)) void fsw_st(u32 a, T v, T*){ \
  __asm__ volatile(FNVL_A_ "dmb ishst\n8: str" SUF " %" R "[v], [%x[a]]\n" FNVL_FSWREC_ : [a]"+r"(a) : [v]"r"(v), [pc]"i"(PC) : "memory"); } \
template<u32 A, u32 PC> static inline __attribute__((always_inline)) void fsw_stk(T v, T*){ u32 a_; \
  __asm__ volatile(FNVL_K_ "dmb ishst\n8: str" SUF " %" R "[v], [%x[a]]\n" FNVL_FSWREC_ : [a]"=&r"(a_) : [v]"r"(v), [lo]"i"(A&0xffffu), [hi]"i"(A>>16), [pc]"i"(PC) : "memory"); }
FNVL_FSW_(u16, "w", "h", 1)
FNVL_FSW_(u32, "w", "", 3)
#define FSW_ST16(A, V, PC) fsw_st<(PC)>((u32)(A), (u16)(V), (u16*)0)
#define FSW_STK16(A, V, PC) fsw_stk<(A),(PC)>((u16)(V), (u16*)0)
#define FSW_ST32(A, V, PC) fsw_st<(PC)>((u32)(A), (u32)(V), (u32*)0)
#define FSW_STK32(A, V, PC) fsw_stk<(A),(PC)>((u32)(V), (u32*)0)
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
  __attribute__((always_inline)) xmm_t operator=(const GMX &o) const { return *this=(xmm_t)o; }
  __attribute__((always_inline)) const GMX &g(NoLkWin) const { return *this; } };
#define MX(a) (GMX{(u32)(a)}.g(lkwin_))
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
/* DSL SSE state (fnvlift/dsl.py F.xmm / F.mxcsr and the xmm<i> / mxcsr pre-state): the whole value of the register as a u128 (little endian: q[0] is the low half), the architectural
   MXCSR image = c->mxcsr (flags of the SSE family's host FPSR are added while it is the owner); a write replaces the whole register / the whole MXCSR: the flags the host FPSR holds for the
   SSE family are cleared (the new value carries the sticky flags), the host rounding mode follows RC (bits 13-14). */
static inline u128 fx_dsl_xmm(u32 i){ return ((u128)c->x[i].q[1]<<64)|c->x[i].q[0]; }
static inline void fx_dsl_xmm_set(u32 i, u128 v){ c->x[i].q[0]=(u64)v; c->x[i].q[1]=(u64)(v>>64); }
static inline u32 fx_dsl_mxcsr(void){ return c->mxcsr|sse_live(); }
static inline void fx_dsl_mxcsr_set(u32 v){ FOWN_SSE(); fnvl_fp_x87_clear(); c->mxcsr=v; sse_fpcr(v); }
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
  bool invalid; UT val;
  if(op==5||op==6){ invalid=un; const bool bz=(a&~SGN)==0 && (b&~SGN)==0; val=un||bz ? b : (op==5 ? (lt?a:b) : (gt?a:b)); }
  else if(op>=7&&op<=14){ const u32 k=op-7; const bool sig=k==1||k==2||k==5||k==6; invalid=sig?un:sn;
    const bool t=k==0?eq : k==1?lt : k==2?(lt||eq) : k==3?un : k==4?(un||!eq) : k==5?(un||!lt) : k==6?(un||!(lt||eq)) : !un;
    val=t?(UT)~(UT)0:(UT)0; }
  else { invalid=(op==15)?un:sn; val=(UT)(((un||eq)?4u:0u)|(un?2u:0u)|((un||lt)?1u:0u)); }
  const bool de=!daz && !invalid && (issub(ao)||issub(bo));
  return ((u128)((invalid?1u:0u)|(de?2u:0u))<<W)|val; }
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
    val=isnan(a)?(UT)(a|QB):(UT)(b|QB); fl=(issnan(a)||(!unary&&issnan(b)))?1u:0u;
    return ((u128)fl<<W)|val; }
  FOWN_SSE(); c->mxcsr|=sse_live(); sse_fpcr(mx); fnvl_fp_x87_clear();
  FT x, y, r; __builtin_memcpy(&x,&a,sizeof x); __builtin_memcpy(&y,&b,sizeof y);
  __asm__ volatile("" : "+w"(x), "+w"(y));
  switch(op){ case 0: r=x+y; break; case 1: r=x-y; break; case 2: r=x*y; break; case 3: r=x/y; break; default: r=__builtin_sqrt(x); }
  __asm__ volatile("" : "+w"(r));
  const u32 hf=fnvl_fp_x87_flags(); fnvl_fp_x87_clear();
  const bool ie=hf&1u, ze=(hf>>2)&1u, oe=(hf>>3)&1u, pe=((hf>>5)&1u)!=0;
  __builtin_memcpy(&val,&r,sizeof val);
  if(ie){ sse_fpcr(mx); return ((u128)1u<<W)|indef; }
  const bool de=!daz && (issub(ao)||issub(bo));
  bool tiny=false;
  if((UT)(val&~SGN)<=MINN){                                         /* a tiny-after-rounding result is at most the smallest normal: add / sub there are exact (a multiple of the subnormal step), mul / div decide exactly in integers, sqrt is never tiny */
    if(op<=1) tiny=(UT)(val&~SGN)!=0 && (UT)(val&~SGN)<MINN;
    else if(op<=3) tiny=fx_tiny_after<UT,FB>(op==3,a,b,(mx>>13)&3u); }
  bool ue=tiny&&(pe||!um), pe2=pe||oe;
  if(ftz&&um&&tiny){ val&=SGN; ue=true; pe2=true; }
  fl=(de?2u:0u)|(ze?4u:0u)|(oe?8u:0u)|(ue?16u:0u)|(pe2?32u:0u);
  return ((u128)fl<<W)|val; }
static inline u128 fx_dsl_fp(u32 op, u32 w, u64 a, u64 b, u32 mx){
  return w==32 ? fx_dsl_fp_t<float,u32,23>(op,(u32)a,(u32)b,mx) : fx_dsl_fp_t<double,u64,52>(op,a,b,mx); }
static inline int sse_sat(int v,int lo,int hi){ return v<lo?lo:v>hi?hi:v; }
static inline u64 sse_sad(const u8*a,const u8*b){ u32 s=0; for(int i=0;i<8;i++) s+=a[i]>b[i]?a[i]-b[i]:b[i]-a[i]; return s; }
static inline u32 sse_pmovmskb(xmm_t s,int n){ u32 r=0; for(int i=0;i<n;i++) r|=(u32)(s.b[i]>>7)<<i; return r; }
/* ---- Integer / system helpers (W14). ---- */
/* The hardware exception codes (FNVL_*) are one table, fnvlift/faults.json (status, trap/fault, address rule, source per row): fnvl_faults.h and the cases of rt/seh.cc are generated from it.
   `int 3` / `int 0x2d` use FNVL_BP with at = the address of the byte after the opcode byte (see f_sys.py). A #GP from a software interrupt whose gate has DPL 0 is fnvl_raise_av(0, 0xFFFFFFFF, at). */
extern "C" { void fnvl_cpuid(u32 leaf, u32 sub, u32 out[4]); u64 fnvl_rdtsc(void); }
/* The hardware-raise entries of the runtime (rt/seh.cc) are declared ONLY here, inside namespace fnvl_rt, and only FNVL_RAISE / FNVL_RAISE_AV below name them: a lifted unit that calls the raw
   fnvl_raise or fnvl_raise_av (no EFLAGS / C0 C2 C3 flush) does not find the name and is a compile error. They are the same C-linkage functions as the definitions in seh.cc. */
namespace fnvl_rt { extern "C" { void fnvl_raise(u32 code, u32 at); void fnvl_raise_av(u32 access, u32 addr, u32 at); } }
#define FNVL_EFL_KEEP 0x244000u      /* the non-arithmetic EFLAGS bits a user-mode popfd can change and pushfd shows: NT (14), AC (18), ID (21) */
/* EFLAGS image of pushfd: the arithmetic flags and DF of the lifted state, bit 1, IF (9) set, AC and ID from c->eflx, everything else 0.
   popfd loads CF PF AF ZF SF DF OF; AC and ID are kept in c->eflx; TF/IF/IOPL/NT/RF/VM/VIF/VIP changes are ignored (user mode cannot change them). */
#define EFL_PUSH() ((u32)((cf&1)|2u|((pf&1)<<2)|((af&1)<<4)|((zf&1)<<6)|((sf&1)<<7)|0x200u|((df&1)<<10)|((of&1)<<11)|(c->eflx&FNVL_EFL_KEEP)))
#define EFL_POP(v_) do{ u32 f_=(v_); cf=f_&1; pf=(f_>>2)&1; af=(f_>>4)&1; zf=(f_>>6)&1; sf=(f_>>7)&1; df=(f_>>10)&1; of=(f_>>11)&1; c->eflx=f_&FNVL_EFL_KEEP; }while(0)
/* RAISE_OUT / RAISE_IN: the flush rule of every explicit raise site (the lifter emits only FNVL_RAISE / FNVL_RAISE_AV; the raw entries are not visible to a lifted unit).
   Everything the CONTEXT names that the lifted function keeps in locals goes to struct cpu before the call: EFLAGS (the pushfd image, EFL_PUSH) into c->eflags and the x87 condition bits
   C0 C2 C3 (FSW bits 8 10 14) into c->fc; the x87 stack and xmm state are flushed by the same sites (fnvlift f_x87_ctl.store, SYNC_X_OUT). After a continue the handler's EFLAGS and condition
   bits are loaded back (EFL_POP: the arithmetic flags and DF; AC and ID into c->eflx; the other bits cannot be changed by user mode). */
#define RAISE_OUT do{ c->eflags=EFL_PUSH(); c->fc=((c0&1)<<8)|((c2&1)<<10)|((c3&1)<<14); }while(0)
#define RAISE_IN do{ u32 ef_=c->eflags; EFL_POP(ef_); c0=(c->fc>>8)&1; c2=(c->fc>>10)&1; c3=(c->fc>>14)&1; }while(0)
#define FNVL_RAISE(code, at) do{ RAISE_OUT; fnvl_rt::fnvl_raise(code, at); RAISE_IN; }while(0)
#define FNVL_RAISE_AV(acc, addr, at) do{ RAISE_OUT; fnvl_rt::fnvl_raise_av(acc, addr, at); RAISE_IN; }while(0)
/* aam/aad/aaa/aas/daa/das are generated inline; the selectors of the process (SEL_CS SEL_DS SEL_ES SEL_SS SEL_FS SEL_TR) and fnvl_seg_ok are generated from platform/declared/x86sys.json (tools/gen_sys.py, included below at SEG_*). */
/* ---- W15: class rules of the system / rare-instruction family (fnvlift/f_sys.py, f_int.py). The fault codes they raise (STEP, LOCK, DBGSRV, FLT_*) are rows of fnvlift/faults.json. ---- */
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
/* SELK strips the RPL. fnvl_seg_ok(idx, sel) (generated: the loadable selectors per register are mov_to_sreg of platform/declared/x86sys.json; a selector the record leaves undecided calls
   fnvl_undecided_selector, which has no definition: the lifted code that can load one does not link). */
#include "fnvl_sys.h"
/* Segment register STATE (lead): fnvl_seg_set(c, idx, sel): c->sel[idx] = sel and, for FS, c->fs = (SELK(sel)==SELK(SEL_FS)) ? c->teb : 0 (a flat selector has base 0; the null
   selector leaves no usable base); fnvl_seg_get(c, idx) reads c->sel[idx] (CS: SEL_CS). The caller has already checked fnvl_seg_ok. The lifter emits fnvl_seg_set only for a load
   instruction and, while f_int.SEG_STATE is False, still reads the register as the process constant of SEGSEL. */
static inline void fnvl_seg_set(struct cpu *p, u32 idx, u32 sel){ p->sel[idx]=sel; if(idx==SEG_FS) p->fs=(SELK(sel)==SELK(SEL_FS))?p->teb:0u; }
static inline u32 fnvl_seg_get(struct cpu *p, u32 idx){ return p->sel[idx]; }
/* Segment state of a fresh thread (rt/enter.cc cpu_reset): the process constants of a native 32-bit Windows 7 user process (rt/seh.cc make_context): CS 0x1b, DS=ES=SS 0x23, FS 0x3b with
   base = TEB, GS null. struct cpu.sel is indexed ES, CS, SS, DS, FS, GS (SEG_*); sel and teb are the last fields of struct cpu. */
static inline void fnvl_seg_init(struct cpu *p, u32 teb){ p->teb=teb; p->sel[SEG_ES]=SEL_ES; p->sel[SEG_CS]=SEL_CS; p->sel[SEG_SS]=SEL_SS; p->sel[SEG_DS]=SEL_DS; p->sel[SEG_FS]=SEL_FS; p->fs=teb; }
/* Base of a `gs:` access, by the CURRENT GS selector (the null selector never gets here: the lifter emits a #GP(0) guard before the instruction): the FS descriptor -> TEB, a flat one -> 0. */
#define FNVL_GS_BASE() (SELK(c->sel[SEG_GS])==SELK(SEL_FS)?c->teb:0u)
/* The descriptor-table registers, LDTR and CR0 have no source in platform/declared/x86sys.json (sgdt, sidt, sldt, smsw: undecided): no constant here and no Form for them. */
