extern "C" void sink(void*);
int r1(unsigned x) { return *(int*)(x + 0x40u); }
int r3(int* p, int i) { return p[i]; }
int r6(char* b, unsigned o) { return *(int*)(b + o); }
int r9() { return *(int*)0x1234; }
struct A { virtual void f(); int a; }; struct B { virtual void g(); int b; };
struct C : A, B { void g() override; int c; };
void C::g() { sink(this); }
struct E { E(); ~E(); int x; }; E::E() { x = 1; } E::~E() { sink(this); }
