// wholeprog_b.cpp - unit B of the whole-program key test: it never calls Interlocked* on these fields and never
// polls them. Compiled with unit A's keys (keys-...) its plain stores and loads of A's operands are ordered;
// without them (nokeys-...) they are not: the test bites.
#include "wholeprog.h"
// keys-expect: stlr\tw
// nokeys-reject: stlr
void unlock(Obj *o) { o->m_lock = 0; }
// keys-expect: stlr\tw
// nokeys-reject: stlr
void init_count(Obj *o) { o->m_count = 0; }
// the writer of the polled flag: the result is published before it (release)
// keys-expect: stlr\tw
// nokeys-reject: stlr
void finish(Worker *w, int r) { w->m_result = r; w->m_bDone = 1; }
// keys-expect: stlr\tw
// nokeys-reject: stlr
void go() { g_go = 1; }
// keys-reject: ldar
// keys-reject: stlr
int read_state(Obj *o, Worker *w) { g_other = w->m_bReady; return o->m_state + o->m_plain; }
// the Interlocked operand A reached through a local pointer
// keys-expect: stlr\tw
// nokeys-reject: stlr
void st_alias(Obj *o) { o->m_alias = 0; }
// the operand of A's forwarder, called with it only in unit C: a key only after the cross-unit merge
// keys-expect: stlr\tw
// nokeys-reject: stlr
void st_xcount(Obj *o) { o->m_xcount = 0; }
// a record copy over the polled P::flag: fenced (dmb ish before the store)
// keys-expect: dmb\tish
// nokeys-reject: dmb
void st_copy(P *p, const P *n) { *p = *n; }
