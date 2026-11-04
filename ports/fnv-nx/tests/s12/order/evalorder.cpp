// evalorder.cpp - order of the object expression and of overloaded operator operands (fnvnx_order object= and
// operator=; tests/s12/order/run.py, docs/ORDERING.md "Open, not closed by this").
//
// Each case logs, one letter per expression, the order in which the parts of one call ran, then ':' (or a digit for
// an operator) when the callee ran. Three builds compare their output:
//   evalorder.gcc.expect         the plugin's defaults (object=first operator=cpp17): GCC's C++17 order
//   evalorder.msvc-guess.expect  object=last operator=rtl: arguments right to left, then the object, then the call
//   evalorder.msvc.expect        (not yet) what MSVC 2008 x86 itself prints: `cl /O2 /EHsc evalorder.cpp`
// Plain C++03 so MSVC 2008 builds it as a console program; run.py builds it freestanding AArch64 ILP32 for qemu with
// -DFNVNX_FREESTANDING (no C library: the harness's v1_puts, no static constructors).
#ifdef FNVNX_FREESTANDING
extern "C" void v1_puts(const char *);
static void put(const char *s) { v1_puts(s); }
#else
#include <stdio.h>
static void put(const char *s) { fputs(s, stdout); }
#endif
#if defined(_MSC_VER)
#define NOINLINE __declspec(noinline)
#else
#define NOINLINE __attribute__((noinline))
#endif

static char g_log[64];
static int g_n;
static void mark(char c) { g_log[g_n++] = c; g_log[g_n] = 0; }
static void out(const char *name) {
  put(name); put(" "); put(g_log); put("\n");
  g_n = 0; g_log[0] = 0;
}

NOINLINE int a() { mark('a'); return 1; }
NOINLINE int b() { mark('b'); return 2; }
NOINLINE int x() { mark('x'); return 3; }
NOINLINE int y() { mark('y'); return 4; }
NOINLINE int idx() { mark('i'); return 0; }
NOINLINE int val() { mark('v'); return 5; }

struct Big {   // returned through a hidden slot: a user copy constructor makes the call an AGGR_INIT_EXPR
  int v;
  Big() : v(0) {}
  Big(const Big &o) : v(o.v) {}
};

struct S {
  int v[4];
  NOINLINE void f(int) { mark(':'); }
  NOINLINE void m(int, int) { mark(':'); }
  NOINLINE Big mk(int p) { mark(':'); Big r; r.v = p; return r; }
  NOINLINE int &operator[](int i) { mark(':'); return v[i]; }
  NOINLINE S &operator=(int p) { mark(':'); v[0] = p; return *this; }
  NOINLINE S &operator+=(int p) { mark(':'); v[0] += p; return *this; }
  NOINLINE S &operator>>(int p) { mark('0' + (char)p); return *this; }
};
NOINLINE S &operator<<(S &s, int p) { mark('0' + (char)p); return s; }
NOINLINE S &operator-=(S &s, int p) { mark(':'); s.v[0] -= p; return s; }

struct V {
  virtual void vf(int) { mark(':'); }
  virtual void vm(int, int) { mark(':'); }
};

static S g_s;          // POD: no static constructor in the freestanding harness
static V *g_v;         // set in main
NOINLINE S *get() { mark('g'); return &g_s; }
NOINLINE S &obj() { mark('o'); return g_s; }
NOINLINE V *getv() { mark('g'); return g_v; }

// one function per case (run.py checks that the non-virtual ones stay direct calls: no blr)
NOINLINE void case_member() { get()->f(a()); }                 // object of a direct method call
NOINLINE void case_member2() { obj().m(x(), y()); }            // reference object, two arguments
NOINLINE void case_retslot() { Big r = get()->mk(a()); (void)r; }   // a method returning through a slot
NOINLINE void case_virtual() { getv()->vf(a()); }              // object of a virtual call (vtable load)
NOINLINE void case_virtual2() { getv()->vm(x(), y()); }
NOINLINE void case_shl() { S &s = g_s; s << a() << b(); }      // free operator<< chain
NOINLINE void case_shr() { obj() >> a() >> b(); }              // member operator>> chain
NOINLINE void case_index() { (*get())[idx()] = 1; }            // member operator[]
NOINLINE void case_assign() { obj() = val(); }                 // member operator=
NOINLINE void case_addeq() { obj() += val(); }                 // member compound assignment
NOINLINE void case_subeq() { obj() -= val(); }                 // free compound assignment

int main() {
  V v;
  g_v = &v;
  case_member(); out("member");
  case_member2(); out("member2");
  case_retslot(); out("retslot");
  case_virtual(); out("virtual");
  case_virtual2(); out("virtual2");
  case_shl(); out("shl");
  case_shr(); out("shr");
  case_index(); out("index");
  case_assign(); out("assign");
  case_addeq(); out("addeq");
  case_subeq(); out("subeq");
  return 0;
}
