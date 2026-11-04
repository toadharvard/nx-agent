// cases.cpp - one instance of every fnvcheck finding (tests/s12/run.sh checks the expected rows, `// expect:` lines).
// Parsed by clang with the static gate's gnu configuration; never compiled into anything.
#include <stdio.h>
#include <wchar.h>
typedef long LONG;
extern "C" LONG InterlockedIncrement(volatile LONG *);
extern "C" LONG InterlockedExchange(volatile LONG *, LONG);
extern "C" void _ReadWriteBarrier(void);
extern "C" void Sleep(unsigned long);
int ReadByte();
int Other();
struct Stream { int Read(); };
void Use(int, int);
void Log(const char *fmt, ...);
struct Str { const char *p; int n; };

unsigned char f2u(float f) { return (unsigned char)f; }            // expect: F2U
int f2s(float f) { int i = f; return i; }                          // expect: F2S
unsigned long long f2u64(double d) { return (unsigned long long)d; } // expect: F2U64
unsigned char f2c() { return (unsigned char)200.0f; }              // constant in range: nothing
long long p2i64(void *p) { return (long long)p; }                  // expect: P2I64
bool psign(void *p, void *q) { return (int)p < (int)q; }           // expect: P2I-SIGNED
long long sh64(long long x, int n) { return x << n; }              // expect: SHIFT64-VAR
int sh32(int x, int n) { return x << n; }                          // expect: SHIFT32-VAR
#include "shimlike/shiftmacro.h"
int shm(int x, int n) { return FNVS_SHL(x, n) + fnvs_rotl(x, n); } // excluded macro and inline code: nothing
int idiv(int a, int b) { return a / b; }                           // expect: DIV-VAR
void order1() { Use(ReadByte(), ReadByte()); }                     // expect: ORDER-ARGS stream
void order2(Stream *s) { Use(s->Read(), s->Read()); }              // expect: ORDER-ARGS stream
void order3() { Use(ReadByte(), Other()); }                        // expect: ORDER-ARGS -
int Compute();
void order6() { Use(Compute(), Compute()); }                       // expect: ORDER-ARGS same-callee
struct G { int v; int Get() { return v; } };
void order7(G *a, G *b) { Use(a->Get(), b->Get()); }               // trivial getters: nothing
struct Obj { Obj *Next(); void Put(int); };
void order4(Obj *o) { o->Next()->Put(ReadByte()); }                // expect: ORDER-OBJECT
int order5() { return ReadByte() - ReadByte(); }                   // expect: ORDER-BINOP
void fmt1(long long v) { Log("%d %s", v, "x"); }                   // expect: FMT-SIZE
void fmt2(Str s) { Log("%s", s); }                                 // expect: FMT-STRUCT VA-STRUCT
void fmt3(char *b, long long v) { sprintf(b, "%I64d", v); }        // expect: FMT-MSVC
void fmt4(char *b, int v) { sprintf(b, "%f", v); }                 // expect: FMT-CLASS
void va1(const char *fmt, ...) { const char **p = &fmt; (void)p; } // expect: VA-ADDR-PARAM
LONG g_lock;
void lock() { while (InterlockedExchange(&g_lock, 1)) {} }         // expect: ATOMIC-USE
void unlock() { g_lock = 0; }
#pragma pack(push, 1)
struct Packed { char c; LONG v; };
#pragma pack(pop)
void pk(Packed *p) { InterlockedIncrement(&p->v); }                // expect: ATOMIC-UNALIGNED ATOMIC-USE
void bar() { _ReadWriteBarrier(); }                                // expect: BARRIER
volatile int g_flag;                                               // expect: VOLATILE-DECL
int g_ready;
char *g_text;
void scan() { int i = 0; while (g_text[i++] != '>') {} }           // a scan, not a poll: nothing
void poll1() { while (!g_flag) {} }                                // expect: POLL-VOLATILE
void poll2() { while (!g_ready) Sleep(0); }                        // expect: POLL-PLAIN
__thread int t_val;                                                // expect: TLS
enum Mode { M0, M1, M2, M3 };
struct BF { Mode m : 2; unsigned char a : 4; unsigned short b : 10; }; // expect: ENUM-BITFIELD
enum Big { BIG_FLAG = 0x80000000 };                                // expect: ENUM-UNSIGNED
struct Cls { void f(); };
struct HasMp { void (Cls::*mp)(); };                               // expect: MEMPTR-FIELD
struct E1 {}; struct E2 {};
struct TwoEmpty : E1, E2 { int x; };                               // expect: EMPTY-BASE
size_t wl(const wchar_t *s) { return wcslen(s); }                  // expect: WCHAR-LIB
struct Thing { bool isnull() { return this == 0; } };              // expect: THIS-NULL
long double ld = 1.0L;                                             // expect: LDBL
// S13 causes that had no class (bool-repr, nan-bits, uninit-* of the harness, pointer-difference garbage)
struct Obj2 { int a; bool b; float f; };
bool bpun(Obj2 *o) { return *(bool *)((char *)o + 4); }           // expect: BOOL-PUN
bool bpunc(Obj2 *o) { return *(const bool *)((char *)o + 4); }    // expect: BOOL-PUN
void bpunw(Obj2 *o) { *(bool *)((char *)o + 4) = false; }          // a store of 0/1: nothing
bool bfield(Obj2 *o) { return o->b; }                              // typed bool: nothing
unsigned fbits(float f) { return *(unsigned *)&f; }                // expect: FLOAT-BITS
union FU { float f; unsigned u; };
unsigned fbits2(FU x) { return x.u; }                              // expect: FLOAT-BITS
float fbits3(FU x) { return x.f; }                                 // the float member: nothing
#include <xmmintrin.h>
int fbits4(__m128 a, __m128 b) { return _mm_movemask_ps(_mm_mul_ps(a, b)); } // expect: FLOAT-BITS
int fbits5(__m128 a, __m128 b) { return _mm_movemask_ps(_mm_cmplt_ps(a, b)); } // a compare mask: nothing
struct V12 { int x, y, z; };
int pdiff(V12 *a, V12 *b) { return a - b; }                        // expect: PTRDIFF-EXACT
int pdiff4(int *a, int *b) { return a - b; }                       // power of two: nothing
void Fill(int *out);
int outarg() { int v; Fill(&v); return v; }                        // expect: OUTARG-UNINIT
int outarg2() { int v = 0; Fill(&v); return v; }                   // initialized: nothing
int outarg3() { int v; v = 1; Fill(&v); return v; }                // written first: nothing
struct Acc { int s; void Init(); };
int outarg4() { Acc a; a.Init(); return a.s; }                     // expect: OUTARG-UNINIT
void outarg5(char *dst) { char buf[16]; sprintf(buf, "%d", 1); dst[0] = buf[0]; } // expect: OUTARG-UNINIT
int outarg6() { int v; int n = sizeof(v); Fill(&v); return v + n; } // expect: OUTARG-UNINIT
void Range(int &lo, int &hi);
void Show(const int &v);
int outarg7() { int lo, hi; Range(lo, hi); return lo - hi; }       // expect: OUTARG-UNINIT
int outarg8() { int v; Show(v); return 0; }                        // a const reference reads it: nothing
