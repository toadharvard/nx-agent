// test_names.cpp - declarations whose MSVC 2008 names toolchain/names/test_names.py checks (test_names.expect is
// the pinned cl's own symbol table for this file). Compiles as C++ with cl and with g++ -std=gnu++17.
#ifndef _MSC_VER
#define __cdecl __attribute__((fnvnx_cc("cdecl")))
#define __stdcall __attribute__((fnvnx_cc("std")))
#define __fastcall __attribute__((fnvnx_cc("fast")))
#define __thiscall __attribute__((fnvnx_cc("this")))
#define __int64 long long
typedef __SIZE_TYPE__ size_t;
#define EXPORT
#define KEEP __attribute__((used))        // g++ emits this inline member, as cl's dllexport emits every member
#else
#define EXPORT __declspec(dllexport)        // cl then emits every member
#define KEEP
#endif
typedef unsigned int uint;
struct S { int a; };
class C { public: int a; };
union U { int a; float b; };
enum E { E0, E1 };
namespace N { struct S2 { enum E2 { X }; struct In { int v; }; }; class K; namespace M { struct D { int q; }; } }
struct Fwd1; class Fwd1 { public: int a; };          // first seen as struct, defined as class
class Fwd2; struct Fwd2 { int a; };                  // first seen as class, defined as struct
struct Fwd8; class Fwd8; void fw8(Fwd8 *) {} class Fwd9; struct Fwd9; void fw9(Fwd9 *) {}
struct Fwd3; class Fwd4;                             // never defined
struct Fwd5; void fw5(Fwd5 *) {} class Fwd5 { public: int a; }; void fw5b(Fwd5 *) {}   // named before its definition
class Fwd6; void fw6(Fwd6 *) {} struct Fwd6 { int a; }; struct Fwd7 { int a; }; class Fwd7; void fw7(Fwd7 *) {}
typedef struct { int a; } TD;                        // unnamed, named by its typedef
typedef int I32; typedef const char *CStr; typedef S *SP;

// ---- plain functions: builtin types, pointers, references, qualifiers
void f0() {}
int f1(int, unsigned, long, unsigned long, short, unsigned short, char, signed char, unsigned char) { return 0; }
bool f2(bool, wchar_t, float, double, long double, __int64, unsigned __int64) { return false; }
void f3(int *, const int *, int *const, const int *const, volatile int *, const volatile int *) {}
void f4(int &, const int &, int *&, int *const &, const char **, const char *const *, char **const) {}
void f5(const int, const S, S, const S &, S *, C, C *, U, U *, E, E *, const E) {}
void f6b(int a[], int *const b, int *c, int d[], int *const e, const int f[], const int *const g) {}
void f6(int a[], int b[3], const int c[], int d[][3], int e[2][3], S f[4]) {}
void f7(int (&a)[3], int (*b)[3], const int (&c)[2][4], int (*d)[2][4]) {}
void f8(void (*)(int), int (*)(S, C *), void g(int), void (*)(int[]), void (*)(int *const), void (*const)(int)) {}
void f9(void (__stdcall *)(int), int (__fastcall *)(int, int), void (__cdecl *)(), ...) {}
void f10(int S::*, int (C::*)(int), void (C::*)() const, const int S::*, int S::*const) {}
void f11(N::S2, N::S2::E2, N::S2::In *, N::K *, N::M::D &, Fwd1 *, Fwd2 *, Fwd3 *, Fwd4 *, TD, TD *) {}
void f12(I32, CStr, SP, uint, S, S *, S *, C *, C *, const S &, const S &, N::S2, N::S2) {}
S r0() { S s = {0}; return s; }
const S r1() { S s = {0}; return s; }
C *r2() { return 0; }
const C *const r3() { return 0; }
E r4() { return E0; }
const int r5() { return 0; }
S &r6(S &s) { return s; }
void (*r7())(int) { return 0; }
int (*r8())[3] { return 0; }
int S::*r9() { return 0; }
int __stdcall cs(int, char, double) { return 0; }
int __fastcall cf(int, S) { return 0; }
int __cdecl cc(int) { return 0; }
void va(int, ...) {}
extern "C" { int ec(int) { return 0; } int __stdcall es(int, double, S) { return 0; } int __fastcall ef(int, char) { return 0; } int ev = 1; }

// ---- variables
int g_i; const int *g_pc; int *const g_cp = 0; extern const int g_ci = 3; int g_arr[3]; const char *g_tab[2];
extern const int g_carr[2] = {1, 2}; int g_arr2[2][3]; S g_s; S *g_sp; S &g_sr = g_s; volatile int g_v;
void (*g_fp)(int); int S::*g_mp; int (C::*g_mfp)(int); E g_e; bool g_b; N::S2::In g_in; const S g_cs = {1};
extern const S g_ecs = {1}; char g_str[4]; unsigned char *volatile g_vp; int *g_parr[2]; float g_f; double g_d;
namespace N { int n_i; namespace M { D n_d; const D *n_pd; } }

// ---- members
struct EXPORT Mem {
  KEEP Mem() {} KEEP Mem(int) {} KEEP Mem(const Mem &) {} KEEP ~Mem() {}
  KEEP void m0() {} KEEP void m1() const {} KEEP void m2() volatile {} KEEP void m3() const volatile {}
  KEEP static void s0() {} static int s_i; static const int s_ci; static S s_s; static const char *s_p; static int s_arr[2];
  KEEP virtual void v0() {} KEEP virtual int v1(int) const { return 0; }
  KEEP int __stdcall ms(int) { return 0; } KEEP int __cdecl mc(int) { return 0; } KEEP int __fastcall mf(int) { return 0; }
  KEEP void mv(int, ...) {}
  KEEP Mem &operator=(const Mem &) { return *this; }
  KEEP bool operator==(const Mem &) const { return true; } KEEP bool operator!=(const Mem &) const { return false; }
  KEEP bool operator<(const Mem &) const { return false; } KEEP bool operator>(const Mem &) const { return false; }
  KEEP bool operator<=(const Mem &) const { return false; } KEEP bool operator>=(const Mem &) const { return false; }
  KEEP Mem operator+(const Mem &) const { return *this; } KEEP Mem operator-(const Mem &) const { return *this; }
  KEEP Mem operator-() const { return *this; } KEEP Mem operator+() const { return *this; }
  KEEP Mem operator*(const Mem &) const { return *this; } KEEP int operator*() const { return 0; }
  KEEP Mem operator/(const Mem &) const { return *this; } KEEP Mem operator%(const Mem &) const { return *this; }
  KEEP Mem *operator&() { return this; } KEEP Mem operator&(const Mem &) const { return *this; }
  KEEP Mem operator|(const Mem &) const { return *this; } KEEP Mem operator^(const Mem &) const { return *this; }
  KEEP Mem operator~() const { return *this; } KEEP bool operator!() const { return false; }
  KEEP bool operator&&(const Mem &) const { return false; } KEEP bool operator||(const Mem &) const { return false; }
  KEEP Mem &operator++() { return *this; } KEEP Mem operator++(int) { return *this; }
  KEEP Mem &operator--() { return *this; } KEEP Mem operator--(int) { return *this; }
  KEEP Mem &operator+=(int) { return *this; } KEEP Mem &operator-=(int) { return *this; } KEEP Mem &operator*=(int) { return *this; }
  KEEP Mem &operator/=(int) { return *this; } KEEP Mem &operator%=(int) { return *this; } KEEP Mem &operator&=(int) { return *this; }
  KEEP Mem &operator|=(int) { return *this; } KEEP Mem &operator^=(int) { return *this; } KEEP Mem &operator<<=(int) { return *this; }
  KEEP Mem &operator>>=(int) { return *this; } KEEP Mem operator<<(int) const { return *this; } KEEP Mem operator>>(int) const { return *this; }
  KEEP int operator[](int) const { return 0; } KEEP int operator()(int, int) const { return 0; } KEEP Mem *operator->() { return this; }
  KEEP int operator->*(int) { return 0; } KEEP Mem operator,(int) { return *this; }
  KEEP operator int() const { return 0; } KEEP operator const char *() const { return 0; } KEEP operator S() const { S s = {0}; return s; }
  KEEP void *operator new(size_t) { return 0; } KEEP void operator delete(void *) {}
  KEEP void *operator new[](size_t) { return 0; } KEEP void operator delete[](void *) {}
  KEEP void *operator new(size_t, int) { return 0; } KEEP void operator delete(void *, int) {}
protected: KEEP void p0() {} KEEP static void ps0() {} KEEP virtual void pv0() {} static int ps_i;
private: KEEP void q0() {} KEEP static void qs0() {} KEEP virtual void qv0() {} static int qs_i;
public: void callall();
};
int Mem::s_i; const int Mem::s_ci = 1; S Mem::s_s; const char *Mem::s_p; int Mem::s_arr[2]; int Mem::ps_i; int Mem::qs_i;
void Mem::callall() { p0(); ps0(); q0(); qs0(); }
Mem operator+(int, const Mem &m) { return m; }
bool operator==(const S &, const S &) { return true; }

// ---- inheritance: vftables, thunks, virtual bases
struct B1 { virtual void a() {} virtual ~B1() {} int x; };
struct B2 { virtual void b() {} virtual void b2(int) {} virtual ~B2() {} int y; };
struct B3 { virtual void c() {} int z; };
struct D1 : B1, B2, B3 { virtual void a() {} virtual void b() {} virtual void b2(int) {} virtual void c() {} virtual ~D1() {} };
struct VB { virtual void vb() {} virtual ~VB() {} int w; };
struct V1 : virtual VB { virtual void vb() {} virtual void v1() {} };
struct V2 : virtual VB { virtual void v2() {} };
struct VD : V1, V2 { virtual void vb() {} KEEP virtual ~VD() {} };
struct NV : virtual S { int n; };
namespace N { struct ND : B1, B2 { virtual void b() {} }; }

// ---- templates
struct C2 { int mf(int) { return 0; } };
template <class T> struct EXPORT T1 { T v; void m(T) {} static T s; T1() {} ~T1() {} template <class X> X tm(X x) { return x; } struct In { void im() {} }; };
template <class T> T T1<T>::s;
template <class T, int N> struct T2 { T a[N]; void m() {} };
template <class A, class B> struct T3 { void m(A, B, A, T1<A>, T1<B>, T1<A>) {} };
template <bool b, E e, char c, unsigned u, long l> struct T4 { void m() {} };
template <int *p> struct T5 { void m() {} };
template <void (*f)()> struct T6 { void m() {} };
template <int S::*mp> struct T7 { void m() {} };
template <int (C2::*mf)(int)> struct T8 { void m() {} };
template <const int &r> struct T9 { void m() {} };
template <class T> T tf(T t) { return t; }
template <class T, class X> X tf2(T, X x, T *, const X &) { return x; }
template <int N> int tn() { return N; }
template <class T> struct T10 { T1<T1<T> > m(T1<T>, T1<T1<T> >, T1<T> *) { return T1<T1<T> >(); } };
template <template <class> class TT> struct T11 { void m() {} };
template <class T> void tfa(T (&)[3], T1<T> &) {}


// ---- local scopes, local classes
int ls0() { static int a; { static int b; { static int c; b += c; } a += b; } { static int d; a += d; } return a; }
KEEP inline int ls1(int x) { static S s; struct L { L() {} int lm() { static int q; return q; } virtual void lv() {} }; L l; for (int i = 0; i < x; i++) { static int e; e++; } if (x) { static int g; g++; } else { static int h; h++; } return l.lm() + s.a; }
int Mem_ls(Mem *m);
struct LS { int m() { static int a; { static int b; a += b; } return a; } static int sm() { static E e; return e; } };
template <class T> struct TLS { int m() { static T a; return (int)a; } };
struct EXPORT LC { KEEP LC() { static int a; a++; } KEEP ~LC() { static int b; b++; } };
namespace { int anon_i; void anon_f() {} struct AnonS { void m() {} }; }
static int st_i; static void st_f() {}
struct Anon { union { int a; float b; }; struct { int c; } s; enum { AX } e; void m() {} };
enum { UE0 }; void ue(int) {}

// ---- many names and arguments (back-references)
namespace n0 { namespace n1 { namespace n2 { namespace n3 { namespace n4 { namespace n5 { namespace n6 { namespace n7 { namespace n8 { namespace n9 { namespace n10 { namespace n11 {
struct Deep { void m(Deep *, n0::n1::n2::n3::n4::n5::n6::n7::n8::n9::n10::n11::Deep &) {} };
} } } } } } } } } } } }
struct A0 {}; struct A1 {}; struct A2 {}; struct A3 {}; struct A4 {}; struct A5 {}; struct A6 {}; struct A7 {}; struct A8 {}; struct A9 {}; struct A10 {}; struct A11 {};
void br(A0 *, A1 *, A2 *, A3 *, A4 *, A5 *, A6 *, A7 *, A8 *, A9 *, A10 *, A11 *, A0 *, A9 *, A10 *, A11 *, A11 *, int, int *, int *) {}
void br2(T1<int>, T1<int> *, T1<T1<int> >, T3<int, S>, T3<int, S> *, T1<S>, S, T2<S, 3>, T2<S, 3>) {}

int g_tp; void g_tf() {} extern const int g_tr = 5;
void use() {
  T1<int> a; a.m(1); T1<int>::s = 1; a.tm(1.0f); a.tm<S *>(0); T1<int>::In i; i.im();
  T1<S> as; S s0 = {0}; as.m(s0); T1<T1<S> > aa; T2<char, 5> b; b.m(); T2<S, 3> b2; b2.m();
  T3<int, S> c; c.m(1, s0, 1, a, as, a); T3<S *, const char *> c2; c2.m(0, 0, 0, T1<S *>(), T1<const char *>(), T1<S *>());
  T4<true, E1, 'a', 7u, -5L> d; d.m(); T4<false, E0, 0, 0xFFFFFFFFu, 100000L> d2; d2.m();
  C2 cc2; cc2.mf(1);
  T5<&g_tp> e; e.m(); T6<&g_tf> f; f.m(); T7<&S::a> g; g.m(); T8<&C2::mf> *h8 = 0; (void)h8; T9<g_tr> r; r.m();
  tf(1); tf(s0); tf<const char *>(0); tf2(1, s0, (int *)0, s0); tn<3>(); tn<-1>(); tn<0>(); tn<10>(); tn<11>(); tn<255>(); tn<65536>();
  T10<int> t10; t10.m(a, T1<T1<int> >(), &a); T11<T1> t11; t11.m(); int arr[3]; tfa(arr, a);
  LS l; l.m(); LS::sm(); TLS<int> tl; tl.m(); ls1(1); anon_i = 1; anon_f(); AnonS an; an.m(); st_i = 1; st_f(); Anon ann; ann.m();
  D1 d1; VD vd; NV nv; N::ND nd; Mem m; m.callall(); (void)d1; (void)vd; (void)nv; (void)nd;
  n0::n1::n2::n3::n4::n5::n6::n7::n8::n9::n10::n11::Deep dp; dp.m(&dp, dp);
  D1 *pd = new D1; delete pd; D1 *pa = new D1[2]; delete[] pa; Mem *pm = new Mem; delete pm; Mem *pma = new Mem[2]; delete[] pma;
}
template struct T8<&C2::mf>;

// ---- NAMES1 rules found by the whole-tree gate (each checked against cl's own names)
#ifdef _MSC_VER
#include <emmintrin.h>
#else
typedef float __m128 __attribute__((vector_size(16)));
typedef long long __m128i __attribute__((vector_size(16)));
typedef double __m128d __attribute__((vector_size(16)));
#endif
// `I` (__restrict) after the pointer letter, part of the argument back-reference key
void na_r1(int * __restrict p) {}
void na_r2(int * __restrict p, int * __restrict q) {}
void na_r3(int * __restrict p, int *q) {}
void na_r4(const int * __restrict p, S * __restrict q, S *r, S * __restrict t) {}
void na_r5(int ** __restrict p) {}
void na_r6(int * __restrict * p) {}
void na_r7(int * const __restrict p) {}
void na_r8(int * __restrict a[]) {}
void na_r9(int * __restrict &a) {}
void na_r11(int & __restrict a, S & __restrict b, S &c) {}     // cl: qualifiers on a reference are ignored
int * __restrict na_r10() { return 0; }
int * __restrict na_rv;
int * __restrict * na_rv2;
// the name of __m128 / __m128i / __m128d is a name like any other (back-references)
void na_v1(__m128 *a, __m128 *b) {}
void na_v2(__m128 *a, __m128i *b, __m128d *c, __m128 *d, __m128d *e, __m128i *f) {}
void na_v3(float, __m128 *, __m128i *, __m128i &, S, const __m128 *) {}
__m128 na_v4(__m128 *a) { return *a; }
__m128i na_v5(__m128i *a, const __m128i &b) { return *a; }
const __m128 *na_v6(__m128 &a, __m128 &b) { return 0; }
// the class key is the definition's, whatever a later `typename ::C` does to the front end's flag
class NaK1 { public: int a; };
#ifndef _MSC_VER      // g++ only: it makes the front end forget the key (cl has no such construct)
static unsigned na_k1 = alignof(typename ::NaK1);
#endif
void na_k1f(NaK1 *) {}
struct NaK2 { int a; };
#ifndef _MSC_VER      // g++ only: it makes the front end forget the key (cl has no such construct)
static unsigned na_k2 = alignof(typename ::NaK2);
#endif
void na_k2f(NaK2 *) {}
// the parameters' top-level qualifiers and array form are the first declaration's
struct NaP { void f(int *p); void g(int *const p); void s(int *volatile p); void a(int p[]); void b(int *p); void c(int p[3]);
  void d(int *p, int *q); void e(int * const p); };
void NaP::f(int *const p) {}
void NaP::g(int *p) {}
void NaP::s(int *p) {}
void NaP::a(int *p) {}
void NaP::b(int p[]) {}
void NaP::c(int *p) {}
void NaP::d(int *const p, int *q) {}
void NaP::e(int *volatile p) {}
void na_u1(int *p);
void na_u1(int *const p) {}
void na_u2(int *const p);
void na_u2(int *p) {}
void na_u3(int *p);
void na_u3(int *const p);
void na_u3(int *volatile p) {}
void na_u4(int *const p);
void na_u4(int *p);
void na_u4(int *volatile p) {}
void na_u5(int *p);
void na_u5(int p[]) {}
void na_u6(int p[]);
void na_u6(int *p) {}
void na_u7(int *q, int *r);
void na_u7(int *const q, int *const r) {}
void na_u8(int *const q, int *const r);
void na_u8(int *q, int *r) {}
