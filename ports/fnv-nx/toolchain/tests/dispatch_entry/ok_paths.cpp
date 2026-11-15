typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef int (*plain_t)(int);
int target(int a) { return a + 1; }
struct H { entry_t f; int n; };
entry_t g1 = &target;
entry_t tab[2] = { &target, target };
H h = { &target, 1 };
H hs[2] = { { &target, 1 }, { target, 2 } };
entry_t tab2[2];
H copy(const H &a) { H b = a; return b; }
void cp() { __builtin_memcpy(tab2, tab, sizeof tab); }
