// test_dispatch.cpp - call shapes for toolchain/dispatch/test_dispatch.py
struct A { virtual int f(int); virtual ~A(); };
typedef int (*FP)(int);
int c1(FP p, int x) { return p(x); }
int c2(A* a, int x) { return a->f(x) + 1; }
void c3(A* a) { delete a; }
int c4(FP p, int x) { return p(x); }          // a tail call
int c5(FP p, FP q, int x) { int s = 0; for (int i = 0; i < x; i++) s += p(i) + q(i); return s; }
int direct(int); int c6(int x) { return direct(x); }
// the call's shape (the descriptor)
#define __fastcall __attribute__((fnvnx_cc("fast")))
#define __stdcall __attribute__((fnvnx_cc("std")))
#define __cdecl __attribute__((fnvnx_cc("cdecl")))
struct V3 { float x, y, z; };
struct P2 { int a, b; };
struct Big { int a[8]; };
struct NT { int a; ~NT(); };
typedef void (__fastcall *FF)(int, int, float);
typedef long long (__stdcall *FS)(double, long long);
typedef V3 (__cdecl *FV)(V3, P2, Big, char);
typedef Big (*FB)(NT);
struct B { int g(float, double); };
typedef int (B::*PM)(float, double);
void d1(FF p) { p(1, 2, 3.f); }
long long d2(FS p) { return p(1.0, 2); }
V3 d3(FV p, V3 v, P2 q, Big b) { return p(v, q, b, 'c'); }
Big d4(FB p, NT& n) { return p(n); }
int d5(B* b, PM m) { return (b->*m)(1.f, 2.0); }
