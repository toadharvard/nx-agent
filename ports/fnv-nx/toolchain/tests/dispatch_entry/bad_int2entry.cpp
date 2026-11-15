typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef int (*plain_t)(int);
entry_t f(long x) { return (entry_t)x; }
