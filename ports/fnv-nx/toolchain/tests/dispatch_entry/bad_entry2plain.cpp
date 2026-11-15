typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef int (*plain_t)(int);
plain_t f(entry_t e) { return (plain_t)e; }
