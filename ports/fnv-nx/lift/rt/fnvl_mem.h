/* R9 memory-ordering class of a guest-memory access, bound by C++ scope. Included by fnvl.h after the plain M8..MF64 / MX macros, which it redefines:
   M8/M16/M32/M64/MF32/MF64/MX(addr) keep their lvalue syntax and mean `fnvl_m<TAG, FNVL_R9>(P(addr))`, where FNVL_R9 is an enumerator found by
   ordinary scoping:
     namespace scope (here)            FNVL_R9 = 1  ORDERED, the default: every access for which no class is known
     `{ enum { FNVL_R9 = 0 }; ... }`   PLAIN: the lifter wraps the text of an instruction that tools/memclass.py proved all-plain (thread-local or read-only)
   Class 0: the plain lvalue of today (`*(T*)p`, a real reference: object code unchanged).
   Class 1 (rule R9 (b)), a proxy object (read = conversion, write = operator=):
     width 1/2/4/8, aligned address : one ldar / stlr (__atomic_*, __ATOMIC_SEQ_CST; build with -mno-outline-atomics)
     address not aligned (run-time test `tst; b.ne`, a full second path, not a trap): plain load then `dmb ishld`; `dmb ishst` then plain store
     width 16 (xmm): plain load then `dmb ishld`; `dmb ishst` then plain store (no single-copy-atomic 16-byte access without LSE2)
   Compound assignment, address-of and reference binding do not compile in class 1 (x86 non-locked RMW is read, modify, write as separate accesses,
   which the lifter already emits as separate statements).
   Class 2 (a LOCK-prefixed RMW or xchg on a non-plain location, fnvlift/memclass.py): the lifter emits ONE __atomic_* operation (seq_cst, `dmb ish`) whose old
   value is FNVL_LK, then the plain handler text with `enum { FNVL_R9 = 2 }`: its read of the operand is FNVL_LK, its store was done by the atomic operation.
   LOCK RMW / xchg-mem on ANY address (x86: atomic with respect to every other memory access of every thread, aligned or split), rule decided with L1:
     (i)  address = 0 mod width (width 1: always; statically PROVED by const_address in fnvlift/memclass.py: no run-time test, else one `tst; b.ne`):
          one __atomic_* operation (seq_cst: ldaxr/stlxr loop, build with -mno-outline-atomics) followed by `dmb ish`;
     (ii) otherwise the SPLIT-LOCK PATH, a full second path selected by machine state (not a fallback after a failure):
          fnvl_world_stop(); plain unaligned load, modify, plain unaligned store; `dmb ish`; fnvl_world_resume().
     (i) and (ii) compose: while every other guest thread is suspended no other guest access interleaves with (ii); a suspended thread that was inside its own
     ldaxr/stlxr loop loses its exclusive reservation through the store of (ii) and retries.
   CONTRACT of fnvl_world_stop / fnvl_world_resume (implemented by the guest-thread runtime: rt + platform layer, e.g. Horizon svcSetThreadActivity, emulator service):
     - stop: on return, every OTHER guest thread is either outside any guest memory access or suspended at an instruction boundary of host code, and the stores
       it performed before are visible to the caller (the callee ends with `dmb ish`). Host threads that never touch guest memory need not stop.
       Reentrancy: ONE global lock is taken in stop and released in resume; a second thread that wants to stop blocks inside stop (and may itself be suspended there
       by the first); the lock is not recursive and the caller never nests stop calls.
     - resume: makes the caller's stores visible (`dmb ish` first), releases the global lock, resumes the suspended threads.
     - the caller makes no call and takes no lock between stop and resume (the window is the plain RMW and one `dmb ish`).
     - a single-thread world (the test harnesses) implements both as an empty body. */
#ifndef FNVL_MEM_H
#define FNVL_MEM_H
#define FNVL_AI __attribute__((always_inline)) inline
#define FNVL_DMB(k) __asm__ volatile("dmb " #k ::: "memory")
enum { FNVL_R9 = 1 };
template<int N> struct FNVL_UI;
template<> struct FNVL_UI<1> { typedef u8 type; };
template<> struct FNVL_UI<2> { typedef u16 type; };
template<> struct FNVL_UI<4> { typedef u32 type; };
template<> struct FNVL_UI<8> { typedef u64 type; };
template<class U> struct FNVL_UA;
template<> struct FNVL_UA<u8> { typedef u8a A; };
template<> struct FNVL_UA<u16> { typedef u16a A; };
template<> struct FNVL_UA<u32> { typedef u32a A; };
template<> struct FNVL_UA<u64> { typedef u64a A; };
/* tags: V value type, A its unaligned may_alias access type (the attributes live in the typedef, so the type is not a template argument) */
struct FNVL_T8 { typedef u8 V; typedef u8a A; };
struct FNVL_T16 { typedef u16 V; typedef u16a A; };
struct FNVL_T32 { typedef u32 V; typedef u32a A; };
struct FNVL_T64 { typedef u64 V; typedef u64a A; };
struct FNVL_TF32 { typedef float V; typedef f32a A; };
struct FNVL_TF64 { typedef double V; typedef f64a A; };
struct FNVL_TX { typedef xmm_t V; typedef xmm_ua A; };
template<class G, bool W = (sizeof(typename G::V) <= 8)> struct fnvl_mem;
template<class G> struct fnvl_mem<G, true> {          /* width 1/2/4/8 */
  typedef typename G::V V; typedef typename FNVL_UI<sizeof(V)>::type U; typedef U __attribute__((may_alias)) N;
  unsigned long p;
  static FNVL_AI bool al(unsigned long p) { return sizeof(U) == 1 || (p & (sizeof(U) - 1)) == 0; }
  FNVL_AI operator V() const {
    U u;
    if (__builtin_expect(al(p), 1)) u = __atomic_load_n((N*)p, __ATOMIC_SEQ_CST);
    else { u = *(typename FNVL_UA<U>::A*)p; FNVL_DMB(ishld); }
    V v; __builtin_memcpy(&v, &u, sizeof v); return v;
  }
  FNVL_AI V operator=(V v) const {
    U u; __builtin_memcpy(&u, &v, sizeof u);
    if (__builtin_expect(al(p), 1)) __atomic_store_n((N*)p, u, __ATOMIC_SEQ_CST);
    else { FNVL_DMB(ishst); *(typename FNVL_UA<U>::A*)p = u; }
    return v;
  }
  FNVL_AI V operator=(const fnvl_mem& o) const { return *this = (V)o; }     /* movs: ordered load, ordered store (never the proxy's own copy) */
};
template<class G> struct fnvl_mem<G, false> {         /* width 16 */
  typedef typename G::V V; typedef typename G::A A;
  unsigned long p;
  FNVL_AI operator V() const { V v = *(A*)p; FNVL_DMB(ishld); return v; }
  FNVL_AI V operator=(V v) const { FNVL_DMB(ishst); *(A*)p = v; return v; }
  FNVL_AI V operator=(const fnvl_mem& o) const { return *this = (V)o; }
};
template<class G> struct fnvl_lk {                    /* class 2: the old value an atomic RMW already returned; its store is done by that operation */
  typedef typename G::V V; u64 v;
  FNVL_AI operator V() const { return (V)v; }
  FNVL_AI V operator=(V x) const { return x; }
  FNVL_AI V operator=(const fnvl_lk& o) const { return (V)o.v; }
};
static constexpr u64 FNVL_LK = 0;                      /* namespace default; a locked instruction declares its own FNVL_LK next to `enum { FNVL_R9 = 2 }` */
template<class G, int C> FNVL_AI decltype(auto) fnvl_m(unsigned long p, u64 lk) {
  if constexpr (C == 0) return (*(typename G::A*)p);
  else if constexpr (C == 2) return fnvl_lk<G>{lk};
  else return fnvl_mem<G>{p};
}
#undef M8
#undef M16
#undef M32
#undef M64
#undef MF32
#undef MF64
#undef MX
#define M8(a) fnvl_m<FNVL_T8, FNVL_R9>(P(a), FNVL_LK)
#define M16(a) fnvl_m<FNVL_T16, FNVL_R9>(P(a), FNVL_LK)
#define M32(a) fnvl_m<FNVL_T32, FNVL_R9>(P(a), FNVL_LK)
#define M64(a) fnvl_m<FNVL_T64, FNVL_R9>(P(a), FNVL_LK)
#define MF32(a) fnvl_m<FNVL_TF32, FNVL_R9>(P(a), FNVL_LK)
#define MF64(a) fnvl_m<FNVL_TF64, FNVL_R9>(P(a), FNVL_LK)
#define MX(a) fnvl_m<FNVL_TX, FNVL_R9>(P(a), FNVL_LK)
extern "C" void fnvl_world_stop(void);
extern "C" void fnvl_world_resume(void);
enum { FNVL_ADD, FNVL_SUB, FNVL_AND, FNVL_OR, FNVL_XOR, FNVL_XCHG };
/* a: guest address; PROVEN: statically proved = 0 mod sizeof(U) (the run-time test and path (ii) are compiled out). -> old value */
template<class U> FNVL_AI bool fnvl_al(u32 a) { return sizeof(U) == 1 || (a & (sizeof(U) - 1)) == 0; }
template<class U, int OP, bool PROVEN> FNVL_AI U fnvl_rmw(u32 a, U v) {
  typedef U __attribute__((may_alias)) N; typedef typename FNVL_UA<U>::A A; U o;
  if (PROVEN || __builtin_expect(fnvl_al<U>(a), 1)) {
    N* p = (N*)P(a);
    if (OP == FNVL_ADD) o = __atomic_fetch_add(p, v, __ATOMIC_SEQ_CST);
    else if (OP == FNVL_SUB) o = __atomic_fetch_sub(p, v, __ATOMIC_SEQ_CST);
    else if (OP == FNVL_AND) o = __atomic_fetch_and(p, v, __ATOMIC_SEQ_CST);
    else if (OP == FNVL_OR) o = __atomic_fetch_or(p, v, __ATOMIC_SEQ_CST);
    else if (OP == FNVL_XOR) o = __atomic_fetch_xor(p, v, __ATOMIC_SEQ_CST);
    else o = __atomic_exchange_n(p, v, __ATOMIC_SEQ_CST);
    FNVL_DMB(ish);
  } else {
    fnvl_world_stop();
    A* p = (A*)P(a); o = *p;
    *p = (U)(OP == FNVL_ADD ? o + v : OP == FNVL_SUB ? o - v : OP == FNVL_AND ? o & v : OP == FNVL_OR ? o | v : OP == FNVL_XOR ? o ^ v : v);
    FNVL_DMB(ish);
    fnvl_world_resume();
  }
  return o;
}
/* compare-exchange: stores d when the old value equals e, else the old value (x86 always writes); -> old value */
template<class U, bool PROVEN> FNVL_AI U fnvl_cas(u32 a, U e, U d) {
  typedef U __attribute__((may_alias)) N; typedef typename FNVL_UA<U>::A A;
  if (PROVEN || __builtin_expect(fnvl_al<U>(a), 1)) {
    __atomic_compare_exchange_n((N*)P(a), &e, d, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST); FNVL_DMB(ish); return e;
  }
  fnvl_world_stop();
  A* p = (A*)P(a); U o = *p; *p = o == e ? d : o;
  FNVL_DMB(ish);
  fnvl_world_resume();
  return o;
}
/* any other RMW (adc sbb neg): f(old) -> new, a pure function of the old value */
template<class U, bool PROVEN, class F> FNVL_AI U fnvl_rmwf(u32 a, F f) {
  typedef U __attribute__((may_alias)) N; typedef typename FNVL_UA<U>::A A; U o;
  if (PROVEN || __builtin_expect(fnvl_al<U>(a), 1)) {
    N* p = (N*)P(a); o = __atomic_load_n(p, __ATOMIC_SEQ_CST);
    while (!__atomic_compare_exchange_n(p, &o, f(o), 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST));
    FNVL_DMB(ish);
  } else {
    fnvl_world_stop();
    A* p = (A*)P(a); o = *p; *p = f(o);
    FNVL_DMB(ish);
    fnvl_world_resume();
  }
  return o;
}
#endif
