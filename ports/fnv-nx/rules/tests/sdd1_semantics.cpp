// sdd1_semantics.cpp - synthetic classes for rules/tests/test_sdd1.py: what `delete`, __sdd and an explicit
// destructor call compile to under FNVNX_SDD (and that the same text compiles without it).

#include "fnvnx_sdd.h"
extern "C" void game_free(void*); extern "C" void other_free(void*); extern "C" void sized_free(void*, unsigned);
extern "C" void dtor_mark(int);
struct Mem { static void operator delete(void* p, std::size_t n) { sized_free(p, n); } };       // NiMemObject-like
struct A : Mem { int a; FNVNX_VDTOR(A, Mem) FNVNX_SDD_VIRTUAL ~A(); virtual int f(); };
struct B : A { int b; static void operator delete(void* p) { other_free(p); } FNVNX_VDTOR(B, A) FNVNX_SDD_VIRTUAL ~B() { dtor_mark(2); } };
struct B2 : B { int c; FNVNX_SDD_DERIVED(B2, B) };
struct V { int v; FNVNX_VDTOR(V) FNVNX_SDD_VIRTUAL ~V(); };
struct D : virtual V { int d; ~D(); FNVNX_SDD_DERIVED(D, V) virtual int h(); };
struct S2 { int s; virtual int k(); };
struct M : A, S2 { ~M(); int k() override; FNVNX_SDD_DERIVED(M, A, S2) };
template <class T> struct TT : A { T t; FNVNX_VDTOR(TT, A) FNVNX_SDD_VIRTUAL ~TT() {} };
class P { FNVNX_VDTOR(P) FNVNX_SDD_VIRTUAL ~P(); public: void kill() { delete this; } };
void del_a(A* p) { delete p; }
void del_b(B* p) { delete p; }
void del_b2(B2* p) { delete p; }
void del_d(D* p) { delete p; }
void del_m(M* p) { delete p; }
void del_s2(M* p) { S2* s = p; (void)s; }
void del_tt(TT<int>* p) { delete p; }
void del_ca(const A* p) { delete p; }
void kill_p(P* p) { p->kill(); }
int call_f(A* p) { return p->f(); }
void destroy_a(A* p) { FNVNX_DESTROY(p, A); }
void destroy_mem(Mem* p) { FNVNX_DESTROY(p, Mem); }
template <class T> void destroy_t(T* p) { FNVNX_DESTROY(p, T); }
void destroy_i(int* p, B* b) { destroy_t(p); destroy_t(b); }
void auto_b() { B b; b.a = 1; }
A* mk_b2() { return new B2; }
M::~M() {} int M::k() { return 1; }
D::~D() {} int D::h() { return 2; }
struct N0 { int n; ~N0(); };                                   // not polymorphic
struct N1 : N0, Mem { int m; ~N1(); FNVNX_SDD_DERIVED(N1, N0, Mem) };
struct N2 : N1 { int k; FNVNX_SDD_DERIVED(N2, N1) };
void del_n1(N1* p) { delete p; }
void del_n2(N2* p) { delete p; }
N2* mk_n2() { return new N2; }
static_assert(sizeof(N2) == 12 && sizeof(N1) == 8, "no vptr in a non-polymorphic class");
