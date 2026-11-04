// wholeprog_a.cpp - unit A of the whole-program key test: it hands fields to Interlocked* (directly and through
// forwarders) and polls plain memory. Its discover= output is the key list unit B is compiled with.
// discover: field Obj::m_count
// discover: field Obj::m_lock
// discover: field Worker::m_bDone
// discover: var g_go
// discover: field Obj::m_alias
// discover: field P::flag
// discover: field Obj::m_xcount
#include "wholeprog.h"
#define USED __attribute__((used))
// unit A sees only its own facts about m_xcount: AtomicIncX is a forwarder; unit C calls it (keys_merge.py)
// forwarders: by reference, by pointer, and a forwarder of a forwarder
inline USED void AtomicInc(LONG &x) { InterlockedIncrement(&x); }
inline USED void AtomicIncP(volatile LONG *x) { InterlockedIncrement(x); }
inline USED void AtomicInc2(LONG &x) { AtomicInc(x); }
void USED use(Obj *o) { AtomicInc2(o->m_count); }
void USED lock(Obj *o) { while (InterlockedCompareExchange(&o->m_lock, 1, 0)) {} }
// a field reached only through a forwarder is ordered in this unit too
// expect: stlr\tw
void reset_count(Obj *o) { o->m_count = 0; }
// expect: ldar\tw
int read_count(Obj *o) { return o->m_count; }
// reject: ldar
// reject: stlr
int touch_state(Obj *o) { o->m_state = 1; return o->m_plain; }
// POLL-PLAIN, wait: the flag is reread with ldar on every round
// expect: ldar\tw
void wait_done(Worker *w) { while (!w->m_bDone) Sleep(0); }
// POLL-PLAIN, spin (GCC would hoist the load and spin forever)
// expect: ldar\tw
void spin_go() { while (!g_go) {} }
// not a poll: the body does work
// reject: ldar
int count_until_ready(Worker *w) { int n = 0; while (!w->m_bReady) n++; return n; }
// not a poll: the condition calls a function
// reject: ldar
int not_poll_call(Worker *w) { while (w->m_result != GetTickCount()) Sleep(0); return 0; }
// a forwarder called in another unit (wholeprog_c.cpp): unit A writes `fwd`, unit C `call`, the merge resolves it
void AtomicIncX(LONG &x) { InterlockedIncrement(&x); }
// an Interlocked operand through a local pointer (the decompiler's own shape)
void USED alias(Obj *o) { LONG *p = &o->m_alias; InterlockedCompareExchange(p, 1, 0); }
// POLL-PLAIN on P::flag; unit B copies the whole record over it
// expect: ldar\tw
void wait_p(P *p) { while (!p->flag) Sleep(0); }
