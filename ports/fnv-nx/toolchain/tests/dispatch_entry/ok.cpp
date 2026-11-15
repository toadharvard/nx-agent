// ENTRY1: an entry-typed callee is not rewritten (no fnvnx_dispatch call, no descriptor); the address of a function converts to the entry type
typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef int (*plain_t)(int);
int target(int a) { return a + 1; }
entry_t table[2];
entry_t get() { return &target; }
int call_entry(entry_t e, int a) { return e(a); }
int call_loaded(int a) { return table[1](a); }
int call_via_get(int a) { return get()(a); }
int call_plain(plain_t p, int a) { return p(a); }
void store() { table[0] = &target; table[1] = target; }
