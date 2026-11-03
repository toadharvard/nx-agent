// layouts.cpp - records whose MSVC x86 and GCC AArch64 ILP32 layouts are known (tests/s12/run.py).
// `// layout: <record> msvc=<size> gnu=<size>`: sizes from clang's MSVC layout engine and from real
// aarch64-linux-gnu-g++ -mabi=ilp32 (the gnu proxy of tools/static must agree with GCC: run.py compiles this file
// with GCC too and compares sizeof/offsetof of every record with the proxy's rows).
#include <xmmintrin.h>
struct D1 { int a; double b; };                                   // layout: D1 msvc=16 gnu=16
struct D2 { int a; long long b; };                                // layout: D2 msvc=16 gnu=16
#pragma pack(push, 4)
struct D3 { int a; double b; };                                   // layout: D3 msvc=12 gnu=12
struct P1 { char c; __m128 v; };                                  // layout: P1 msvc=32 gnu=20
struct A16 { float x; } __attribute__((aligned(16)));
struct P2 { char c; A16 m; };                                     // layout: P2 msvc=32 gnu=20
#pragma pack(pop)
#pragma pack(push, 1)
struct P4 { char c; short s; int i; };                            // layout: P4 msvc=7 gnu=7
#pragma pack(pop)
struct B1 { unsigned char a : 4; unsigned short b : 10; unsigned char c : 4; };  // layout: B1 msvc=6 gnu=4
struct B2 { unsigned int a : 4; unsigned char b; unsigned int c : 4; };          // layout: B2 msvc=12 gnu=4
struct B3 { char x; int a : 3; };                                 // layout: B3 msvc=8 gnu=4
struct B4 { unsigned short a : 4; unsigned int b : 20; };         // layout: B4 msvc=8 gnu=4
struct B6 { unsigned int a : 4; unsigned int b : 28; unsigned int c : 1; };      // layout: B6 msvc=8 gnu=8
struct E1 {}; struct E2 {};
struct TwoEmpty : E1, E2 { int x; };                              // layout: TwoEmpty msvc=8 gnu=4
struct Cls { virtual void f(); };
struct VEmpty : E1 { virtual void g(); int y; };                  // layout: VEmpty msvc=8 gnu=8
struct Base { virtual ~Base(); int a; char b; };
struct Derived : Base { FNVNX_MSVC_BASE_END(Base) char c; };      // layout: Derived msvc=16 gnu=16
int sizes[] = {sizeof(D1), sizeof(D2), sizeof(D3), sizeof(P1), sizeof(P2), sizeof(P4), sizeof(B1), sizeof(B2),
               sizeof(B3), sizeof(B4), sizeof(B6), sizeof(TwoEmpty), sizeof(VEmpty), sizeof(Derived)};
