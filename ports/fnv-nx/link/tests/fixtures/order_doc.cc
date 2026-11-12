// invented fixture: the two documented red forms of plugin output (alloca; a by-value aggregate argument copy) and a constructor with its C1/C2 alias
extern "C" void sink(void*);
struct S { int a, b, c, d, e; };
S gs;
void takes(S s);
void pass() { takes(gs); }
char* dyn(int n) { char* p = (char*)__builtin_alloca(n); sink(p); p[1] = 2; return p; }
struct A { int v; A(); };
A::A() : v(1) {}
