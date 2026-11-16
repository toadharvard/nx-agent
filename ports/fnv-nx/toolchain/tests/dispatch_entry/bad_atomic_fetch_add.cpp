// arithmetic on an entry: an entry is no number
typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
void f(entry_t *p) { __atomic_fetch_add(p, 1, __ATOMIC_ACQ_REL); }
