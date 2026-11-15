typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef int (*plain_t)(int);
int target(int a) { return a + 1; }
void f(entry_t *d, long *s) { __builtin_memcpy(d, s, 4); }
