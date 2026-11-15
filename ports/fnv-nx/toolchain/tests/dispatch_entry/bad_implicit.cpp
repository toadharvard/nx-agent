typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef int (*plain_t)(int);
entry_t f(plain_t p) { entry_t e = p; return e; }
