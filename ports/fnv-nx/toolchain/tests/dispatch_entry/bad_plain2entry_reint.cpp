typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef int (*plain_t)(int);
entry_t f(plain_t p) { return reinterpret_cast<entry_t>(p); }
