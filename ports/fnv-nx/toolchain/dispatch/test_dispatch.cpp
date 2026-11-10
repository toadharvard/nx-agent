// test_dispatch.cpp - call shapes for toolchain/dispatch/test_dispatch.py
struct A { virtual int f(int); virtual ~A(); };
typedef int (*FP)(int);
int c1(FP p, int x) { return p(x); }
int c2(A* a, int x) { return a->f(x) + 1; }
void c3(A* a) { delete a; }
int c4(FP p, int x) { return p(x); }          // a tail call
int c5(FP p, FP q, int x) { int s = 0; for (int i = 0; i < x; i++) s += p(i) + q(i); return s; }
int direct(int); int c6(int x) { return direct(x); }
