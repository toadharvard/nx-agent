// test_sdd.cpp - plain C++ for toolchain/sdd/test_sdd.py: no macro, no rule; the plugin alone gives MSVC slot numbering.
extern "C" void other_free(void*); extern "C" void dtor_mark(int);
struct A { int a; virtual int f(); virtual ~A(); virtual int g(); };
struct B : A { int b; static void operator delete(void* p) { other_free(p); } ~B() { dtor_mark(2); } int g() override; };
struct S2 { int s; virtual ~S2(); virtual int k(); };
struct M : A, S2 { M(); ~M(); int k() override; virtual int last(); };
struct I : A { int i; };                       // implicit destructor
void del_a(A* p) { delete p; }
void del_b(B* p) { delete p; }
void del_s2(S2* p) { delete p; }
void destroy_a(A* p) { p->~A(); }
int call_g(A* p) { return p->g(); }
int call_k(S2* p) { return p->k(); }
int call_last(M* p) { return p->last(); }
A* mk_i() { return new I; }
M::M() { a = 1; }
M::~M() {} int M::k() { return 1; } int M::last() { return 3; }
int B::g() { return 2; }
typedef int (A::*PM)();
PM table[] = { &A::g, &A::f };
bool eq(PM m) { return m == &A::g; }
template <PM m> int tcall(A* p) { return (p->*m)(); }
int use_t(A* p) { return tcall<&A::g>(p); }
template <class T> struct TT { T t; virtual ~TT() {} virtual int z() { return t; } int inl(TT* o) { return o->z(); } };
int use_tt(TT<int>* p) { return p->inl(p); }
void del_tt(TT<int>* p) { delete p; }
struct J : A { int inl2() { return g(); } virtual int w(); int inl3(J* o) { return o->w(); } };
int use_j(J* p) { return p->inl2() + p->inl3(p); }
