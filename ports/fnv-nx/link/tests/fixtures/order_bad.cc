// invented fixture: built WITHOUT the order plugin (lifted-origin toy root, no plugin): plain accesses the order gate must name
extern "C" void sink(void*);
int g;
void pstore(int* p) { *p = 1; }
int pload(int* p) { return p[2]; }
char* dyn(int n) { char* p = (char*)__builtin_alloca(n); sink(p); p[1] = 2; return p; }
int gload2(int** pp) { return **pp; }
int lse(int* p) { return __atomic_fetch_add(p, 3, __ATOMIC_RELAXED); }
void gstore(int v) { g = v; }
