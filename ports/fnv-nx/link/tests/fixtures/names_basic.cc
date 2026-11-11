// invented fixture: free functions, a class with two constructors, a global (names plugin rows: func, ctor.c1/c2, data)
struct A { int v; A(); ~A(); virtual int f(); };
int g_data = 3;
void f1() {}
int f2(int x) { return x + g_data; }
A::A() : v(1) {}
A::~A() {}
int A::f() { return v; }
