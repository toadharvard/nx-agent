// wholeprog.h - the records two "units" share (wholeprog_a.cpp, wholeprog_b.cpp; tests/s12/order/run.py).
#include "fnvnx_win32.h"
struct Obj { LONG m_count; LONG m_lock; int m_state; int m_plain; LONG m_alias; LONG m_xcount; };
struct P { int a; int flag; };
void AtomicIncX(LONG &x);   // a forwarder defined in unit A, called only in unit C
struct Worker { int m_bDone; int m_bReady; int m_result; };
extern int g_go;
extern int g_other;
extern int g_brk;
extern int g_brk2;
// shapes from the adversarial review of the key list (tests/s12/order/run.py wholeprog)
struct BFlag { unsigned a : 1; unsigned done : 1; };
struct Pair2 { char x, y; short pad; };
struct VPoll { int a; BOOL m_bDone; };
struct LPoll { int a; int flag; };
struct Arr4 { int a; LONG cnt[4]; };
struct SpinL { LONG m_state; void Lock() { while (InterlockedCompareExchange((LONG *)this, 1, 0)) Sleep(0); } };
struct CondA { int a; LONG m_c; };
struct Uni { union { LONG whole; struct { short lo, hi; }; }; };
struct PRef { LONG *m_pRef; };
extern LONG g_cnt4[8];
