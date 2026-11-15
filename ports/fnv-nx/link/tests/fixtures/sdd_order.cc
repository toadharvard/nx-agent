// delete-expressions and virtual destructor calls: the vptr and slot loads the sdd plugin builds are shared-memory loads (R9)
struct A { int a; virtual int f(); virtual ~A(); virtual int g(); };
struct B : A { int b; ~B(); };
struct S2 { int s; virtual ~S2(); virtual int k(); };
struct M : A, S2 { M(); ~M(); int k() override; };
void del_a(A* p) { delete p; }
void del_s2(S2* p) { delete p; }
void del_m(M* p) { delete p; }
void destroy_a(A* p) { p->~A(); }
void del_arr(B* p) { delete[] p; }
int call_g(A* p) { return p->g(); }
// control: a function with callee-saved spills and no virtual call
extern int ext(int);
int plain(int* p) { int a = *p; ext(a); return a + *p; }
