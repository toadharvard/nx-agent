// wholeprog_a.cpp - unit A of the whole-program key test: it hands fields to Interlocked* (directly and through
// forwarders) and polls plain memory. Its discover= output is the key list unit B is compiled with.
// discover: field Obj::m_count
// discover: field Obj::m_lock
// discover: field Worker::m_bDone
// discover: var g_go
// discover: field Obj::m_alias
// discover: field P::flag
// discover: field Obj::m_xcount
// discover: var g_brk
// discover: field BFlag::done
// discover: field Pair2::x
// discover: field Pair2::y
// discover: field VPoll::m_bDone
// discover: field LPoll::flag
// discover: field Arr4::cnt
// discover: var g_cnt4
// discover: field SpinL::m_state
// discover: field CondA::m_c
// discover: field Uni::whole
// discover: pfield PRef::m_pRef
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
// POLL-PLAIN with the test inside an endless loop (V1's K9)
// expect: ldar\tw
void brk_wait() { for (;;) { if (g_brk) break; Sleep(0); } }
// not a poll: the endless loop does work besides the test
// reject: ldar
int brk_work() { int n = 0; for (;;) { if (g_brk2) break; n++; } return n; }
// a bit-field poll: the front end folds `!f->done` into a BIT_FIELD_REF mask test
// expect: dmb\tishld
void wait_bit(BFlag *f) { while (!f->done) Sleep(0); }
// two adjacent char flags compared together: merged into one halfword load
// expect: dmb\tishld
void wait_pair(Pair2 *c) { while (c->x == 0 && c->y == 0) Sleep(0); }
// a poll through a volatile cast: ordered here already; its writer elsewhere needs the key
void wait_vcast(VPoll *v) { while (!*(volatile BOOL *)&v->m_bDone) Sleep(0); }
// a poll through a local pointer
// expect: ldar\tw
void wait_lptr(LPoll *p) { int *f = &p->flag; while (*f == 0) Sleep(0); }
// Interlocked on pointer arithmetic
void USED arith(Arr4 *o, int i) { InterlockedIncrement(o->cnt + i); InterlockedIncrement(g_cnt4 + i); }
// a lock on (LONG *)this: the object's first member
void USED spin(SpinL *l) { l->Lock(); }
// a conditional operand
void USED cond_op(CondA *a, CondA *b, int c) { InterlockedIncrement(c ? &a->m_c : &b->m_c); }
// a union member (its siblings are the same memory)
void USED uni(Uni *u) { InterlockedIncrement(&u->whole); }
// the pointee of a pointer field
void USED pref(PRef *r) { InterlockedIncrement(r->m_pRef); }
