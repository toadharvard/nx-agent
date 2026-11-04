// refcount.cpp - classes of Interlocked* sites and their code (fnvnx_order refcount=weak; tests/s12/order/run.py).
// Compiled like units.cpp (AArch64 ILP32, the build's flags, fnvnx_compat.h) with fnvnx_win32.h, as decomp units
// see it. `// expect:` / `// reject:` apply to the default mode, `// full-...` to refcount=full.
#include "fnvnx_win32.h"
#define USED __attribute__((used))   // in-class members are emitted although nothing calls them

struct NiRefObject {
  virtual ~NiRefObject();
  virtual void DeleteThis();
  unsigned int m_uiRefCount;
  long count;
  // refcount increment, result discarded: relaxed, inline, no barrier
  // expect: ldxr\tw
  // expect: stxr\tw
  // reject: dmb
  // reject: ldaxr
  // reject: stlxr
  // reject: \tbl\t
  // full-expect: ldaxr\tw
  // full-expect: dmb\tish\n
  USED void IncRefCount() { InterlockedIncrement((volatile LONG *)&m_uiRefCount); }
  // refcount decrement tested against 0: release, acquire fence on the path to DeleteThis only
  // expect: ldxr\tw
  // expect: stlxr\tw
  // expect: dmb\tishld
  // reject: dmb\tish\n
  // reject: ldaxr
  // full-expect: dmb\tish\n
  USED void DecRefCount() { if (InterlockedDecrement((volatile LONG *)&m_uiRefCount) == 0) DeleteThis(); }
  // a counter that is not a refcount by name: full x86 semantics, but inline (no call)
  // expect: ldaxr\tw
  // expect: stlxr\tw
  // expect: dmb\tish\n
  // reject: \tbl\t
  USED void Count() { InterlockedIncrement(&count); }
  // refcount increment whose value is used: not the pattern, full
  // expect: ldaxr\tw
  // expect: dmb\tish\n
  USED long IncAndGet() { return InterlockedIncrement((volatile LONG *)&m_uiRefCount); }
  // refcount decrement compared with 1: not the pattern, full
  // expect: ldaxr\tw
  // expect: dmb\tish\n
  USED bool DecIsOne() { return InterlockedDecrement((volatile LONG *)&m_uiRefCount) == 1; }
};

struct Shared { long refs; };
// `!InterlockedDecrement(&p->refs)` and a discarded decrement: release
// expect: stlxr\tw
// expect: dmb\tishld
// reject: dmb\tish\n
bool release_ref(Shared *p) { return !InterlockedDecrement(&p->refs); }
// expect: stlxr\tw
// reject: dmb\tish\n
void drop_ref(Shared *p) { InterlockedDecrement(&p->refs); }

// a global counter (DAT_ style): full
volatile LONG DAT_011f4400;
// expect: ldaxr\tw
// expect: dmb\tish\n
void global_dec() { InterlockedDecrement(&DAT_011f4400); }

// a refcount name in a packed record: not aligned by declaration, full (the inline misaligned check stays)
#pragma pack(push, 1)
struct Packed { char c; long refs; };
#pragma pack(pop)
// expect: ldaxr\tw
// expect: dmb\tish\n
void packed_inc(Packed *p) { InterlockedIncrement(&p->refs); }

// locks, queues, flags: full, inline
// expect: ldaxr\tw
// expect: dmb\tish\n
// reject: \tbl\t
long cas(volatile LONG *p, long v) { return InterlockedCompareExchange(p, v, 0); }
// expect: ldaxr\tw
// expect: dmb\tish\n
// reject: \tbl\t
long xchg(volatile LONG *p, long v) { return InterlockedExchange(p, v); }
