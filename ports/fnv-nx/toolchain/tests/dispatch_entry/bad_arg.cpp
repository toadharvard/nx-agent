typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef int (*plain_t)(int);
int g(entry_t e);
int h(plain_t p) { return g(p); }
