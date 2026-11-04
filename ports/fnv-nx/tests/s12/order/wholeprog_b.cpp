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
