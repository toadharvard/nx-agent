struct S { int pad[40000]; int f[8]; };
int big(S* s) { return s->pad[39000]; }
int fld(S* s, unsigned i) { return s->f[i]; }
int small(int* p) { return p[100]; }
