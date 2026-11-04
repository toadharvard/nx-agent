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
// the writer of the K9 poll's flag
// keys-expect: stlr\tw
// nokeys-reject: stlr
void st_brk() { g_brk = 1; }
// a store through a local pointer to the polled flag (V1's K8)
// keys-expect: stlr\tw
// nokeys-reject: stlr
void st_via_ptr(Worker *w) { int *q = &w->m_bDone; *q = 1; }
// the writers / readers of the shapes above: ordered only with unit A's keys
// keys-expect: dmb\tishld
// nokeys-reject: dmb
int test_bit(BFlag *f) { if (f->done) return 3; return 4; }
// keys-expect: dmb\tishld
// nokeys-reject: dmb
int test_pair(Pair2 *c) { return c->x == 0 && c->y == 0; }
// keys-expect: stlr\tw
// nokeys-reject: stlr
void st_vcast(VPoll *v) { v->m_bDone = 1; }
// keys-expect: stlr\tw
// nokeys-reject: stlr
void st_lflag(LPoll *p) { p->flag = 1; }
// keys-expect: stlr\twzr, \[x0\]
// nokeys-reject: stlr
void st_arith(Arr4 *o) { o->cnt[1] = 0; g_cnt4[2] = 0; }
// the lock is an inline member of the shared header: unit B discovers it itself, with or without A's keys
// keys-expect: stlr\twzr
// nokeys-expect: stlr\twzr
void st_spin(SpinL *l) { l->m_state = 0; }
// keys-expect: stlr\twzr
// nokeys-reject: stlr
void st_cond(CondA *a) { a->m_c = 0; }
// keys-expect: stlrh
// nokeys-reject: stlr
void st_uni(Uni *u) { u->lo = 0; }
// keys-expect: stlr\tw
// nokeys-reject: stlr
void st_pref(PRef *r) { *r->m_pRef = 1; }
