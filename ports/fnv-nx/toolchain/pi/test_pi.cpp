// test_pi.cpp - one function per verdict of toolchain/pi/test_pi.py
struct R { int a; int b; };
static inline int leaf(R* r) { return r->a + 1; }
static inline int mid(R* r) { return leaf(r) * 2; }
int top(R* r) { return mid(r); }
int dv(int a, int b) { return a / b; }
int dc(int a) { return a / 7; }
unsigned sh(unsigned a, unsigned n) { return a << n; }
int va(int n, ...) { return n; }
int vol(volatile int* p) { return *p; }
int un(int c) { int x; if (c) x = 1; return x; }
int as(int a) { __asm__("" : "+r"(a)); return a; }
__attribute__((noinline)) static int cp(int a, int b) { return a * b + a; }
int usecp(int a) { return cp(a, 3) + cp(a + 1, 3); }
struct K { int k; K(); ~K(); };
K::K() : k(1) {}
K::~K() {}
struct Q { int a; int b; };
void fill(Q*);
int m1() { Q q; q.a = 1; return q.a; }            // the bytes read are stored first
int m2() { Q q; q.a = 1; return q.b; }            // other bytes
int m3() { Q q; fill(&q); return q.a; }           // a call proves nothing (conservative)
int m4(int c) { Q q; if (c) q.a = 1; return q.a; }   // not on every path
int m5() { Q q = Q(); return q.b; }               // the whole variable
int ga(); int gb(); int two(int, int); struct W { int w(int, int); W& self(); };
int o1() { return two(ga(), gb()); }              // readable: two calls of different functions
int o2() { return two(ga(), ga()); }              // the same function twice: not readable from an object
int o3(int i) { return two(i++, ga()); }          // an effect that is not a call
int o4() { return two(ga(), 1); }                 // one argument with an effect: no question of order
int o5(W& x) { return x.self().w(ga(), 2); }      // the object counts
