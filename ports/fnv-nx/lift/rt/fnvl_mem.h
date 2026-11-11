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
   XCHG8A/16A/32A(addr, v): x86 xchg with a memory operand (implicitly locked, a full barrier) on an address PROVED naturally aligned (const_address in
   fnvlift/memclass.py): __atomic_exchange_n (ldaxr/stlxr loop) followed by `dmb ish` (lead decision: the loop alone does not order an earlier
   `dmb ishst; str` before a later plain `ldr; dmb ishld`). No run-time alignment test: an address not proved aligned is a lift error. */
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
static FNVL_AI u32 XCHG8A(u32 a, u32 v) { u8 r = __atomic_exchange_n((u8*)P(a), (u8)v, __ATOMIC_SEQ_CST); FNVL_DMB(ish); return r; }
static FNVL_AI u32 XCHG16A(u32 a, u32 v) { u16 r = __atomic_exchange_n((u16*)P(a), (u16)v, __ATOMIC_SEQ_CST); FNVL_DMB(ish); return r; }
static FNVL_AI u32 XCHG32A(u32 a, u32 v) { u32 r = __atomic_exchange_n((u32*)P(a), v, __ATOMIC_SEQ_CST); FNVL_DMB(ish); return r; }
#endif
