typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef int (*plain_t)(int);
int target(int a) { return a + 1; }
void f(entry_t *d) { __builtin_memset(d, 0, 4); }
