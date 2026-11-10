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
