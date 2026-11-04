// args.cpp - call arguments in MSVC x86's order, right to left (fnvnx_order args=rtl; tests/s12/order/run.py).
// Each case logs the order in which the argument expressions ran and the values the callee received. Built
// freestanding AArch64 ILP32 with the plugin and run under qemu-aarch64; the expected lines are MSVC's order (the
// same as GCC x86-32's, which pushes too: run.py compares with it when the host can build -m32).
extern "C" void v1_puts(const char *);
static char g_log[64];
static int g_n;
static void mark(char c) { g_log[g_n++] = c; g_log[g_n] = 0; }
static void out(const char *name) {
  v1_puts(name); v1_puts(" "); v1_puts(g_log); v1_puts("\n");
  g_n = 0; g_log[0] = 0;
}
static void num(int v) { mark('0' + v); }

__attribute__((noinline)) int a() { mark('a'); return 1; }
__attribute__((noinline)) int b() { mark('b'); return 2; }
__attribute__((noinline)) int c() { mark('c'); return 3; }
__attribute__((noinline)) int x1(int v) { mark('x'); return v; }
__attribute__((noinline)) int y1(int v) { mark('y'); return v; }
__attribute__((noinline)) void f3(int p, int q, int r) { mark(':'); num(p); num(q); num(r); }
__attribute__((noinline)) void vf(int n, ...) { mark(':'); num(n); }
struct S {
  int v;
  __attribute__((noinline)) S(int p, int q) : v(p * 10 + q) { mark(':'); num(p); num(q); }
  __attribute__((noinline)) void m(int p, int q) { mark(':'); num(p); num(q); }
};
alignas(8) static char g_s[sizeof(S)];   // no static constructors in the freestanding harness
__attribute__((noinline)) S *obj() { mark('o'); return (S *)g_s; }
static int g_i;
__attribute__((noinline)) int set_i(int v) { mark('s'); g_i = v; return v; }
struct T {
  __attribute__((noinline)) T() { mark('T'); }
  __attribute__((noinline)) T(const T &) { mark('C'); }
  __attribute__((noinline)) ~T() { mark('~'); }
};
__attribute__((noinline)) void ft(T, int p, int q) { mark(':'); num(p); num(q); }
struct R { int v; };
__attribute__((noinline)) R mk(int p, int q) { mark(':'); num(p); num(q); R r = {p + q}; return r; }

extern "C" int main() {
  f3(a(), b(), c()); out("call");                 // cba:123
  { S s(a(), b()); } out("ctor");                 // ba:12
  obj()->m(a(), b()); out("member");             // oba:12 (the object first, as GCC does now: ORDER-OBJECT)
  vf(2, a(), b()); out("varargs");               // ba:2
  g_i = 0; f3(g_i++, g_i++, 0); out("postinc");  // :100
  f3(x1(a()), y1(b()), 1); out("nested");        // byax:121
  g_i = 0; f3(g_i, set_i(5), 7); out("read");    // s:557
  ft(T(), a(), b()); out("byvalue");             // baT:12~ (x86 GCC: the temporary after the scalars)
  R r = mk(a(), b()); num(r.v); out("retslot");  // ba:123
  return 0;
}
