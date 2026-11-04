// units.cpp - code generation of the fnvnx_order plugin and the barrier header, AArch64 ILP32 with the build's flags
// (tests/s12/order/run.py). Each function's body must match every `// expect:` regex and none of its
// `// reject:` regexes (searched in the function's assembly). `// mode: <m>` lines select a compile.
// mode: volatile (default) - the build's configuration; compiled with -include fnvnx_compat.h
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
// a load of a volatile aggregate: the plain copy, then an acquire fence (the store to *out is not volatile)
// expect: dmb\tishld
// reject: dmb\tish\n
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
// expect: mov\tw\d+, 1\n
// expect: str\tw\d+, \[x\d+\]\n(.*\n)*\tstlr
void dse() { g_data = 1; g_flag = 1; g_data = 2; }
// a plain field stays plain
// reject: ldar
// reject: stlr
int plain(Q *q) { q->plain = q->data[1]; return q->data[0]; }
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

// mode: keys - the ATOMIC-USE key list: a plain Q::plain becomes release/acquire
// keys-expect: stlr\tw
void keyed_store(Q *q) { q->plain = 0; }
// keys-expect: ldar\tw
int keyed_load(Q *q) { return q->plain; }

// mode: tso - every shared access ordered
// tso-expect: ldar\tw
// tso-expect: stlr\tw
void tso(Q *q) { q->plain = q->data[0]; }

// mode: off - no ordering at all
// off-reject: ldar
// off-reject: stlr
int off(Q *q) { q->done = 2; return q->head; }
