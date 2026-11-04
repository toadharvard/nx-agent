// wholeprog_c.cpp - unit C of the whole-program key test: it calls unit A's forwarder AtomicIncX on a field that no
// other unit hands to Interlocked. Only the merge over all units (keys_merge.py) makes Obj::m_xcount a key.
#include "wholeprog.h"
void use_x(Obj *o) { AtomicIncX(o->m_xcount); }
