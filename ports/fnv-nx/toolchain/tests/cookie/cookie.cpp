// S29 fixture: array new and delete expressions of the four element classes, one call each.
// nontrivial: a class with a non-trivial destructor (cookie)   trivial: no destructor (no cookie)
// aligned: a class with a non-trivial destructor and a 16-byte alignment (the cookie stays 4 bytes)
// base: delete[] through a pointer to a class with a virtual destructor
typedef unsigned int size_t_;
void *operator new[](size_t_);
void operator delete[](void *);
void operator delete[](void *, size_t_);

struct Nontrivial { int x; ~Nontrivial(); };
struct Trivial { int x; };
struct alignas(16) Aligned { int x; ~Aligned(); };
struct Base { virtual ~Base(); int y; };

Nontrivial *n_new(size_t_ n) { return new Nontrivial[n]; }
void n_del(Nontrivial *p) { delete[] p; }
Trivial *t_new(size_t_ n) { return new Trivial[n]; }
void t_del(Trivial *p) { delete[] p; }
Aligned *a_new(size_t_ n) { return new Aligned[n]; }
void a_del(Aligned *p) { delete[] p; }
Base *b_new(size_t_ n) { return new Base[n]; }
void b_del(Base *p) { delete[] p; }
