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
int idiv(int a, int b) { return a / b; }                           // expect: DIV-VAR
void order1() { Use(ReadByte(), ReadByte()); }                     // expect: ORDER-ARGS same-callee
void order2(Stream *s) { Use(s->Read(), s->Read()); }              // expect: ORDER-ARGS same-callee
void order3() { Use(ReadByte(), Other()); }                        // expect: ORDER-ARGS -
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
