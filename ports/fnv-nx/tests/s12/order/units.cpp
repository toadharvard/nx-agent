// units.cpp - code generation of the fnvnx_order plugin and the barrier header, AArch64 ILP32 with the build's flags
// (tests/s12/order/run.py). Each function's body must match every `// expect:` regex and none of its
// `// reject:` regexes (searched in the function's assembly). Compiled with -include fnvnx_compat.h and no plugin
// option: the plugin has one memory rule (R9 TSO-MAP).
#include "fnvnx_win32.h"   // the inline Interlocked* as decomp units see them
struct Q {
  int data[4];
  volatile unsigned head;
  volatile unsigned char done;
  volatile unsigned short tag;
  volatile float vf;
  volatile double vd;
  volatile long long v64;
  void *volatile vp;
  int plain;
};
struct __attribute__((packed)) Packed { char c; volatile int x; };
struct Bits { volatile unsigned a : 3, b : 5; };
struct Pair { int a, b; };
volatile Pair g_pair;
volatile int g_flag;
int g_data;
extern int make_value();

// expect: ldar\tw
// reject: dmb
unsigned ld32(Q *q) { return q->head; }
// expect: stlrb\tw
void st8(Q *q) { q->done = 1; }
// expect: ldarh\tw
unsigned ld16(Q *q) { return q->tag; }
// expect: ldar\tx
long long ld64(Q *q) { return q->v64; }
// expect: stlr\tx
void st64(Q *q, long long v) { q->v64 = v; }
// expect: ldar\tw
// expect: fmov\ts0, w
float ldf(Q *q) { return q->vf; }
// expect: fmov\tx
// expect: stlr\tx
void std_(Q *q, double v) { q->vd = v; }
// expect: ldar\tw
void *ldp(Q *q) { return q->vp; }
// a misaligned volatile (packed): ldar would fault, so a plain load and an acquire fence
// expect: ldr\tw
// expect: dmb\tishld
// reject: ldar
int ldpacked(Packed *p) { return p->x; }
// a volatile bit-field: release fence, then the read-modify-write
// expect: dmb\tish\n
void stbits(Bits *b) { b->b = 7; }
// a copy of an aggregate: the plain copy, an acquire fence after the load and a release fence before the store
// expect: dmb\tishld
// expect: dmb\tish\n
void copypair(Pair *out) { *out = const_cast<Pair &>(g_pair); }
// a local volatile whose address is never taken: invisible to other threads, left alone
// reject: ldar
// reject: stlr
// reject: dmb
int localv() { volatile int i = 0; for (; i < 10; i = i + 1) {} return i; }
// an access through a cast to a pointer to volatile (the decomp's `*(volatile long*)&m_uiRefCount`)
// expect: ldar\tw
long castv(long *p) { return *(volatile long *)p; }
// v++ on a volatile is a load-acquire and a store-release (not atomic, as on x86)
// expect: ldar\tw
// expect: stlr\tw
unsigned inc(Q *q) { return q->head++; }
// the result of a call stored into a volatile
// expect: stlr\tw
void stcall() { g_flag = make_value(); }
// message passing: the store of 1 must stay (GCC deletes it without the plugin: dead before `g_data = 2`)
// expect: (\tstlr\t.*\n(.*\n)*?){2}\tstlr\t
void dse() { g_data = 1; g_flag = 1; g_data = 2; }
// R9 TSO-MAP: volatile is no mark, every access to memory that is not a proven local is ordered. A plain (non-
// volatile) field through a pointer, a plain global and a volatile global alike:
// expect: ldar\tw
// expect: stlr\tw
int plain(Q *q) { q->plain = q->data[1]; return q->data[0]; }
// expect: ldar\tw
// reject: dmb
int ld_global() { return g_data; }
// expect: stlr\tw
// reject: dmb
void st_global() { g_data = 3; }
// expect: ldar\tw
int ld_vglobal() { return g_flag; }
// expect: stlr\tw
void st_vglobal() { g_flag = 3; }
// an address-not-taken local, volatile or not, is no shared memory: plain code, no ordering
// reject: ldar
// reject: stlr
// reject: dmb
int local_plain(int n) { int s = 0; for (int i = 0; i < n; i++) s += i; return s; }
// a local whose address is taken is shared memory as far as the rule can prove: ordered
// expect: stlr\tw
extern void sink(int *);
int local_taken() { int x = 1; sink(&x); return x; }
// a plain field next to an Interlocked operand (NiRefObject::m_uiRefCount style)
struct RC { LONG m_uiRefCount; long other; };
// Interlocked* sites keep the full x86 lock semantics, a refcount increment or decrement included
// expect: ldaxr\tw
// expect: dmb\tish\n
// reject: ldxr\tw
void rc_inc(RC *r) { InterlockedIncrement((volatile LONG *)&r->m_uiRefCount); }
// expect: ldaxr\tw
// expect: dmb\tish\n
// reject: ldxr\tw
void rc_dec(RC *r, void (*f)(RC *)) { if (InterlockedDecrement((volatile LONG *)&r->m_uiRefCount) == 0) f(r); }
// expect: stlr\tw
// expect: ldar\tw
void rc_zero(RC *r) { r->m_uiRefCount = 0; r->other = r->other + 1; }
// expect: ldar\tw
long rc_get(RC *r) { return r->m_uiRefCount; }
// the barrier intrinsics
// expect: dmb[\t ]ish\n
void rwb() { _ReadWriteBarrier(); }
// expect: dmb[\t ]ishld
void rb() { _ReadBarrier(); }
// expect: dmb[\t ]ishst
void wb() { _WriteBarrier(); }
// expect: dmb[\t ]ish\n
void mb() { MemoryBarrier(); }
// expect: yield
void yp() { YieldProcessor(); }
// R9 totality: every memory operand of every statement is classified. An aggregate passed by value reads shared
// memory in the caller (acquire fence after the copy); a block builtin touches memory through its pointer
// arguments (release fence before a write, acquire fence after a read); a return slot is a store.
struct Big { int a[8]; };
Big g_big;
extern void take(Big);
extern Big make_big();
// expect: dmb\tishld
void pass_global() { take(g_big); }
// expect: dmb\tishld
void pass_deref(Big *p) { take(*p); }
// block builtin on constants the compiler keeps read-only: no shared memory, no fence (aggregate locals are
// addressable for the C++ front end, so they count as shared by the existing local rule)
// reject: dmb
// reject: ldar
static const char g_ro[16] = "abcdefghijklmno";
int blk_ro(unsigned n) { return __builtin_memcmp(g_ro, "abcdefgh", n); }
// expect: dmb\tish\n
// expect: dmb\tishld
void blk_copy(void *p, const void *q, unsigned n) { __builtin_memcpy(p, q, n); }
// expect: dmb\tish\n
// expect: dmb\tishld
void blk_move(void *p, const void *q, unsigned n) { __builtin_memmove(p, q, n); }
// expect: dmb\tishld
int blk_global() { Big l; __builtin_memcpy(&l, &g_big, sizeof l); return l.a[3]; }
// expect: dmb\tish\n
void blk_set(void *p) { __builtin_memset(p, 0, 64); }
// a pure read through pointer arguments: only the acquire fence (fnspec says read-only)
// expect: dmb\tishld
// reject: dmb\tish\n
int blk_cmp(const void *a, const void *b) { return __builtin_memcmp(a, b, 16); }
// expect: dmb\tish\n
void ret_slot(Big *p) { *p = make_big(); }
// the atomic builtins carry their own ordering: no extra fence from the block rule
// reject: dmb\tishld
unsigned atomic_add(unsigned *p) { return __atomic_fetch_add(p, 1, __ATOMIC_RELAXED); }
