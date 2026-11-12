// invented fixture: code forms the order rule certifies (plugin output): ordered accesses, frame locals, an escaped local, fences, read-only table
extern "C" void sink(void*); extern "C" void sink2(int); extern "C" void ext(void);
struct S { int a, b, c, d, e; };
struct B { unsigned a : 3, b : 5; };
S gs, gs2; B gb; int g, *gptr;
volatile int vg;
const int tab[4] = {1, 2, 3, 4};
int nonesc(int x) { sink2(1); return x + 1; }
int esc() { int x = 0; gptr = &x; x = 5; sink2(x); return x; }
int esc2() { int x[4]; gptr = x; for (int i = 0; i < 4; i++) x[i] = i; sink(x); return x[2]; }
S ret() { return gs; }
int agg() { S l = gs2; return l.a + l.e; }
void stb() { gb.b = 3; }
int ldb() { return gb.b; }
int cpy() { gs = gs2; return g; }
int vol(int v) { int r = vg; vg = v; return r; }
int ro(int i) { return tab[i & 3]; }
int glob() { return g; }
void gset(int v) { g = v; }
int atom(int* p) { return __atomic_exchange_n(p, 1, __ATOMIC_SEQ_CST); }
