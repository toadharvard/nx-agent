// wholeprog.h - the records two "units" share (wholeprog_a.cpp, wholeprog_b.cpp; tests/s12/order/run.py).
#include "fnvnx_win32.h"
struct Obj { LONG m_count; LONG m_lock; int m_state; int m_plain; LONG m_alias; LONG m_xcount; };
struct P { int a; int flag; };
void AtomicIncX(LONG &x);   // a forwarder defined in unit A, called only in unit C
struct Worker { int m_bDone; int m_bReady; int m_result; };
extern int g_go;
extern int g_other;
