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
// fact: vwrite  !stlr|dmb                          (volatile store: plain str, no release; MSVC: release, x86 TSO)
void vwrite(int v) { g_data = v; g_flag = 1; }
// fact: uninit  mov\tw0, 5\n\tret                  (uninitialized path folded away)
int uninit(int c) { int x; if (c) x = 5; return x; }
extern void use(char *);
// fact: dangle  mov\tw0, 0                         (address of a dead local returned as NULL)
const char *dangle(int c) { const char *s; { char buf[16]; buf[0] = c; s = buf; } char o[16]; o[0] = 0; use(o); return s; }
// fact: divzero  brk                            (division by a provable zero: the path traps - x86 div raises #DE there)
struct Geo { unsigned count; unsigned strides[4]; unsigned stride() { return count > 0 ? strides[0] : 0; } };
unsigned divzero(unsigned bytes, Geo *g) { return bytes / g->stride(); }   // UploadQuads 0x00e723a0's shape
int noret(int x) { if (x) return 1; }
// fact: noret  !brk                                (missing return: the other path is assumed unreachable)
// (the build rejects this with -Werror=return-type; FALL1 puts __builtin_trap() where the decomp lacks the return)
// -- classes the D10/D16 flags close (each line: what the flag guarantees in the generated code)
// fact: ldc  ldrsb\tw0                             (-fsigned-char: plain char loads sign-extend, as MSVC)
int ldc(char *p) { return *p; }
// fact: cneg  ubfx\tw0, w0, 7, 1                   (-fsigned-char: c < 0 tests the sign bit)
int cneg(char c) { return c < 0; }
// fact: wrapv  cmp\tw0, w1                         (-fwrapv: x + 1 > x is computed, not folded to true)
bool wrapv(int x) { return x + 1 > x; }
// fact: contract  fmul.*fadd                       (-ffp-contract=off: no fused multiply-add)
// fact: contract  !fmadd
float contract(float a, float b, float c) { return a * b + c; }
// fact: alias  str\twzr.*ldr\tw0, \[x0\]           (-fno-strict-aliasing: reloaded after the float store)
int alias(int *i, float *f) { *i = 1; *f = 0; return *i; }
extern int DAT[16];
// fact: gotref  :got:DAT                           (-fPIC, D16: a global through its own GOT slot)
int gotref() { return DAT[0]; }
// -- bool bytes (S13 bool-repr, BOOL-PUN): GCC assumes 0/1, MSVC tests the whole byte (test al, al)
// fact: bnot  eor\tw0, w0, 1                       (!b is b ^ 1: 0x22 -> 0x23, MSVC -> 0)
bool bnot(bool *p) { return !*p; }
// fact: btest  tst\tx0, 1                          (bit 0 only: 0x22 is false, MSVC true)
int btest(bool *p, int a, int b) { return *p ? a : b; }
// -- ILP32 addressing (S13 ilp32-pointer): zero-extended base + sign-extended 32-bit offset, computed in 64 bits;
// equal to x86's address mod 2^32 whenever that address is below 4 GiB, i.e. for every access x86 could make
// fact: sidx  ldr\tw0, \[x1, w0, sxtw\]            (a negative index: base - 4n, as on x86)
int sidx(int i) { return DAT[i]; }
// fact: uidx  ldr\tw0, \[x1, w0, sxtw\]           (an unsigned index too: 0xfffffff0 bytes is -16, as on x86)
int uidx(unsigned i) { return DAT[i]; }
// fact: i2p  uxtw\tx0, w0                          (an int used as a pointer is zero-extended)
int i2p(int a) { return *(int *)(a + 8); }
