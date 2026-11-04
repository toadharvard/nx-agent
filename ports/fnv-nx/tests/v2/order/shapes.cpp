#include <stdio.h>
static char buf[64]; static int n;
static void T(char c) { buf[n++] = c; buf[n] = 0; }
static void flush(const char *name) { printf("%s %s\n", name, buf); n = 0; buf[0] = 0; }
struct B { virtual int f(int x) { return x; } int d(int x, int y) { return x + y; } virtual int v(int x, int y) { return x - y; }
           static int sf(int x) { return x; } int &r(); };
struct D : B { int f(int x) { return x + 1; } };
static D g_d;
B *o() { T('o'); return &g_d; }
B &ref() { T('o'); return g_d; }
D make() { T('o'); return D(); }
int a() { T('a'); return 1; }
int b() { T('b'); return 2; }
typedef int (*FP)(int);
int callee(int x) { return x; }
FP fp() { T('p'); return &callee; }
int (B::*pm())(int) { T('m'); return &B::f; }
int main() {
  o()->B::f(a()); flush("qualified-virtual");
  ref().f(a()); flush("ref-virtual");
  make().f(a()); flush("prvalue-virtual");
  o()->sf(a()); flush("static-via-object");
  fp()(a()); flush("fnptr-callee");
  o()->d(a(), b()); flush("direct-2args");
  o()->v(a(), b()); flush("virtual-2args");
  (o()->*pm())(a()); flush("ptm-callee");
  return 0;
}
