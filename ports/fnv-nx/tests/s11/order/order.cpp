// order.cpp - evaluation order of the S12 classes ORDER-OBJECT and ORDER-OPERATOR (and the neighbours ORDER-ARGS,
// ORDER-ASSIGN, ORDER-BINOP), compiled by MSVC 2008 (run under Wine) and by the build's GCC (AArch64 ILP32 with the
// fnvnx_order plugin, under qemu): tools/equiv/order_check.py compares the two per case. Synthetic, ours.
//
// Every case prints "<name> <log>": the log has one letter per expression in the order the program evaluated them
// (o/p: the object or left operand, a/b/c: the arguments or right operand), then ':' and what the callee received.
// The S12 class of each case is in the comment after it ("class: ...").
#ifdef _MSC_VER
#include <stdio.h>
#define NOINL __declspec(noinline)
static void put(const char *s) { fputs(s, stdout); }
#else
#define NOINL __attribute__((noinline))
extern "C" void v1_puts(const char *);
static void put(const char *s) { v1_puts(s); }
#endif

static char g_log[64];
static int g_n;
static void mark(char c) { if (g_n < 62) { g_log[g_n++] = c; g_log[g_n] = 0; } }
static void num(int v) { mark((char)('0' + v)); }
static void out(const char *name) {
  put(name); put(" "); put(g_log); put("\n");
  g_n = 0; g_log[0] = 0;
}

NOINL int a() { mark('a'); return 1; }
NOINL int b() { mark('b'); return 2; }
NOINL int c() { mark('c'); return 3; }

struct X;
struct X {
  int v;
  X *nx;
  NOINL void m1(int p) { mark(':'); num(p); }
  NOINL void m2(int p, int q) { mark(':'); num(p); num(q); }
  virtual void vm(int p) { mark(':'); num(p); }
  NOINL static void sm(int p) { mark(':'); num(p); }
  NOINL X *next() { mark('n'); return nx; }
  NOINL X &operator<<(int p) { mark(':'); num(p); return *this; }
  NOINL X &operator>>(int p) { mark(':'); num(p); return *this; }
  NOINL X &operator=(int p) { mark(':'); num(p); return *this; }
  NOINL int operator[](int p) { mark(':'); num(p); return p; }
  NOINL int operator+(int p) { mark(':'); num(p); return p; }
  NOINL int operator==(int p) { mark(':'); num(p); return p; }
  NOINL X &operator+=(int p) { mark(':'); num(p); return *this; }
  NOINL int operator()(int p, int q) { mark(':'); num(p); num(q); return p; }
};
struct Y { int v; };
NOINL Y &operator<<(Y &y, int p) { mark(':'); num(p); return y; }
NOINL int operator+(Y &y, int p) { mark(':'); num(p); return p + y.v; }
NOINL int operator-(int p, Y &y) { mark(':'); num(p); return p - y.v; }
NOINL Y &operator+=(Y &y, int p) { mark(':'); num(p); return y; }

static X *g_x;
static Y g_y;
static int g_arr[8];
static int g_int;
NOINL X *obj() { mark('o'); return g_x; }
NOINL X &ref() { mark('o'); return *g_x; }
NOINL Y &yref() { mark('o'); return g_y; }
NOINL int *ptr() { mark('p'); return &g_int; }
NOINL int *arr() { mark('p'); return g_arr; }
NOINL int idx() { mark('a'); return 2; }
typedef void (X::*MemFn)(int);
NOINL MemFn pmf() { mark('m'); return &X::m1; }

extern "C" int main() {
  X x0, x1;
  x0.v = 0; x0.nx = &x1; x1.v = 1; x1.nx = &x0;
  g_x = &x0; g_y.v = 0;
  obj()->m1(a()); out("obj-arrow");                  // class: ORDER-OBJECT
  obj()->m2(a(), b()); out("obj-arrow2");            // class: ORDER-OBJECT
  (*obj()).m1(a()); out("obj-deref");                // class: ORDER-OBJECT
  ref().m1(a()); out("obj-ref");                     // class: ORDER-OBJECT
  obj()->vm(a()); out("obj-virtual");                // class: ORDER-OBJECT
  obj()->next()->m1(a()); out("obj-chain");          // class: ORDER-OBJECT
  obj()->sm(a()); out("obj-static");                 // class: ORDER-OBJECT
  (obj()->*pmf())(a()); out("obj-pmf");              // class: ORDER-OBJECT
  ref() << a(); out("op-shl-member");                // class: ORDER-OPERATOR
  ref() >> a(); out("op-shr-member");                // class: ORDER-OPERATOR
  ref() = a(); out("op-assign-member");              // class: ORDER-OPERATOR
  ref()[a()]; out("op-index-member");                // class: ORDER-OPERATOR
  ref() + a(); out("op-add-member");                 // class: ORDER-OPERATOR
  ref() == a(); out("op-eq-member");                 // class: ORDER-OPERATOR
  ref() += a(); out("op-addassign-member");          // class: ORDER-OPERATOR
  ref()(a(), b()); out("op-call-member");            // class: ORDER-OPERATOR
  yref() << a(); out("op-shl-free");                 // class: ORDER-OPERATOR
  yref() + a(); out("op-add-free");                  // class: ORDER-OPERATOR
  a() - yref(); out("op-sub-free");                  // class: ORDER-OPERATOR
  yref() += a(); out("op-addassign-free");           // class: ORDER-OPERATOR
  *ptr() = a(); out("assign-builtin");               // class: ORDER-ASSIGN
  *ptr() += a(); out("addassign-builtin");           // class: ORDER-ASSIGN
  arr()[idx()] = b(); out("index-assign-builtin");   // class: ORDER-ASSIGN
  g_int = a() + b(); out("add-builtin");             // class: ORDER-BINOP
  g_int = a() << b(); out("shl-builtin");            // class: ORDER-BINOP
  g_int = a() * b() - c(); out("mixed-builtin");     // class: ORDER-BINOP
  x0.m2(a(), b()); out("args-member");               // class: ORDER-ARGS (calibration: the plugin's case)
  return 0;
}
