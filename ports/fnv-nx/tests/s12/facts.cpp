// facts.cpp - GCC AArch64 ILP32 code generation facts the static gate's verdicts rest on (docs/STATIC_CHECKS.md).
// tests/s12/run.sh compiles this with the real build flags at -O2 and checks each function's instructions
// (`// fact:` lines: function, then a regex its body must match, or `!regex` it must not).
int g(); int h();
void f(int, int);
// fact: order_args  bl\t_Z1gv.*bl\t_Z1hv           (left to right; MSVC x86 pushes right to left: h first)
void order_args() { f(g(), h()); }
// fact: f2uc  fcvtzu\tw0, s0                       (saturating: -1.0f -> 0; MSVC _ftol2 -> 0xFF)
unsigned char f2uc(float x) { return (unsigned char)x; }
// fact: f2ui  fcvtzu\tw0, s0
unsigned int f2ui(float x) { return (unsigned int)x; }
// fact: f2ui_x86  fcvtzs\tx0, s0                   (the FTOI1 rewrite: _ftol2 semantics below 2^63)
unsigned int f2ui_x86(float x) { return (unsigned int)(long long)x; }
// fact: f2i  fcvtzs\tw0, s0                        (saturating; NaN -> 0; MSVC 0x80000000)
int f2i(float x) { return (int)x; }
// fact: p2ll  sxtw\tx0, w0                         (pointer -> 64-bit integer sign-extends)
long long p2ll(void *p) { return (long long)p; }
// fact: sh32  lsl\tw0, w0, w1                      (count & 31, as x86)
int sh32(int x, int n) { return x << n; }
// fact: sh64  lsl\tx0, x0, x1                      (count & 63; MSVC __allshl: 0 for count >= 64)
long long sh64(long long x, int n) { return x << n; }
// fact: ptrwrap  sub\tw0, w0, #1                   (ILP32 pointer arithmetic wraps at 32 bits)
char *ptrwrap(char *p) { return p + (unsigned)-1; }
struct T { int a; bool isnull() { return this == 0; } };
// fact: thisnull  cmp\tw0, 0                       (kept: -fno-delete-null-pointer-checks)
bool thisnull(T *t) { return t->isnull(); }
// fact: ptroverflow  !cmp                          (p + n < p folded: pointer overflow UB, -fwrapv does not cover it)
// fixed: ptroverflow  cmp                          (-fwrapv-pointer)
bool ptroverflow(char *p, unsigned n) { return p + n < p; }
// fact: finite  ^\tret$                            (side-effect-free loop deleted: C++ forward progress, -ffinite-loops)
// fixed: finite  add\tw1, w1, 2                   (-fno-finite-loops)
void finite(unsigned n) { for (unsigned i = 1; i != n; i += 2) {} }
volatile int g_flag; int g_data;
// fact: vread  !ldar                               (volatile read: plain ldr, no acquire; MSVC: acquire)
int vread() { while (!g_flag) {} return g_data; }
// fact: vwrite  str\tw\d+, \[x1, #:lo12:\.LANCHOR0\].*str\tw0,  (flag stored before the data; MSVC: release)
void vwrite(int v) { g_data = v; g_flag = 1; }
// fact: uninit  mov\tw0, 5\n\tret                  (uninitialized path folded away)
int uninit(int c) { int x; if (c) x = 5; return x; }
extern void use(char *);
// fact: dangle  mov\tw0, 0                         (address of a dead local returned as NULL)
// fixed: dangle  !mov\tw0, 0$                     (-fno-isolate-erroneous-paths-dereference: the address, as MSVC)
const char *dangle(int c) { const char *s; { char buf[16]; buf[0] = c; s = buf; } char o[16]; o[0] = 0; use(o); return s; }
int noret(int x) { if (x) return 1; }
// fact: noret  !brk                                (missing return: the other path is assumed unreachable)
// fixed: noret  brk                                (-funreachable-traps: that path traps instead)
