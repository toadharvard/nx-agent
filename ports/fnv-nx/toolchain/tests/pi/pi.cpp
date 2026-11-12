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
struct KD { ~KD(); }; KD::~KD() { two(ga(), gb()); }   // one row, for the emitted clone, none for the in-charge form
struct KE { ~KE(); }; KE::~KE() { two(ga(), ga()); }   // the verdict reaches the clones
// FALL2: the end of a non-void function
[[noreturn]] void die();
int fo1(int x) { if (x) return 1; }                               // reaches the end
int fo2(int x) { for (;;) { if (x) return 1; } }                  // every path returns: the end is not in the CFG
int fo3(int x) { switch (x) { case 0: return 1; default: return 2; } }
int fo4(int x) { if (x) return 1; die(); }                        // ends in a call that does not return
int fo5(int x) { while (x) { if (x == 3) return 1; x--; } }       // the loop can end
// SEH-PI: the forms are declared functions carrying fnvnx_lifted("seh")
// the declarations of the lifted forms: functions declared and never defined, carrying the attribute the plugin registers
extern "C" {
void __debugbreak(void) __attribute__((fnvnx_lifted("int3")));
int fnvnx_seh_try(void) __attribute__((fnvnx_lifted("seh")));
int fnvnx_seh_except(int) __attribute__((fnvnx_lifted("seh")));
int fnvnx_seh_finally(void) __attribute__((fnvnx_lifted("seh")));
}
#define __try if (fnvnx_seh_try())
#define __except(filter) else if (fnvnx_seh_except((int)(filter)))
#define __finally if (fnvnx_seh_finally())
int plain(int);
int sx1(int a) { int r = 0; __try { r = plain(a); } __except (1) { r = 2; } return r; }
int sx2(int a) { int r = 0; __try { r = plain(a); } __finally { r++; } return r; }
int sx3(int a) { return plain(a) + 1; }
static inline __attribute__((always_inline)) int sh1(int a) { __try { return plain(a); } __except (1) { return 2; } }
int sx4(int a) { return sh1(a); }
int db(int a) { if (a) __debugbreak(); return a; }
// ARG2: a side-effecting non-scalar argument and one that reads what it writes: the order plugin leaves GCC's order
struct S1 { int a; }; S1 mk(int&); void fs(S1, int);
void ao1(int x0) { int x = x0; fs(mk(x), x); }
// INL2: the inlined set follows GCC's inlining bookkeeping, not the scope blocks (which the optimizer may drop)
#define AI static inline __attribute__((always_inline))
AI int h2(int a) { int r = plain(a); return r + 2; }
int f2(int a) { return h2(a); }                                   // 1: a body with a variable
AI int i2(int a) { return a; }
int f2b(int a) { return i2(plain(a + 21)); }                           // 1: no statement of the body is left: no scope block
AI int g3(int a) { return plain(a + 3); }
AI int h3(int a) { return g3(a); }
int f3(int a) { return h3(a); }                                   // 2: always_inline chain
static inline int g4(int a) { return plain(a) + 1; }
static inline int h4(int a) { return g4(a) + plain(a + 1); }
int f4(int a) { return h4(a); }                                   // 3: h4 early-inlines g4, then goes into f4
static inline int h5(int a) { return plain(a) + plain(a + 2); }
int e5(int a) { return h5(a); }
int f5(int a) { return h5(a + 1) + plain(1); }
int (*p5)(int) = (int(*)(int))h5;                                 // 4: inlined into two callers and also emitted
AI int g6(int a) { return a * 0; }
__attribute__((noinline)) static int cp6(int a, int b) { return g6(a) + b * plain(b); }
int f6(int a) { return cp6(a, 3) + cp6(a + 1, 3); }               // 5: into a constprop clone
static int r7(int n) { return n ? plain(n) + r7(n - 1) : plain(7); }   // 6: recursion, inlined once into its callers
int f7(int n) { return r7(n); }
int f7b(int n) { return r7(n + 1) * 5; }
AI int z8(int a) { return a * 0; }
int f8(int a) { return z8(a) + plain(a + 8); }                        // 7: all of the code optimised away
static int ip9(int (*f)(int), int a) { return f(a) * 9; }
static int t9(int a) { return plain(a + 9) ^ 5; }
int f9(int a) { return ip9(t9, a) + ip9(t9, a + 1); }             // 8: indirect call made direct
// INL3: every record of the removal hook reaches a row (a receiver that is itself inlined: its record is read through the callee)
static inline int g10(int a) { return plain(a) + 10; }
static inline int h10(int a) { return g10(a) * plain(a + 1); }
int f10(int a) { return h10(a); }
// INL3: a callee that is a clone (a constructor's C2): its own inlinings are recorded under the clone's name
AI int i11(int a) { return plain(a + 11); }
struct Z { int z; Z(int a) : z(i11(a)) {} };
int f11(int a) { Z z(a); return z.z; }
// INL4: a receiver with no record of its own that reached no row (an implicit constructor chain inlined in place into a
// constructor that is itself removed unused): its record is inside code the callgraph removed
#define FI4 inline __attribute__((always_inline))
struct Va { float x[4]; FI4 Va() {} };
struct Ma { Va a, b, c; };
struct Ra : Ma {};
struct Ta { Ra r; Va t; FI4 Ta() {} };
float inl4(Ta* p) { Ta l; return l.t.x[0] + p->t.x[1]; }
// INL5: a receiver whose body this unit does not output: a member of an extern template, too big to inline. GCC inlines
// the small constructors into its (external, never written) body and the out-of-line call stays; the records of the
// inlinees belong to code in another object
template <class T> struct Ex5i { T v; Ex5i(const Ex5i &o) : v(o.v) {} Ex5i() : v() {} };
template <class T> struct Ex5 { Ex5i<T> i; T w; __attribute__((noinline)) Ex5(const T *p) : i(), w(*p) {} };
extern template struct Ex5<char>;
void use5(Ex5<char> *);
void f5(const char *p) { Ex5<char> e(p); use5(&e); }
