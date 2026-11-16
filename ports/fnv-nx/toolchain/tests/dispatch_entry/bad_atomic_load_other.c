// an entry loaded by a generic atomic load into an integer object
typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef struct site_t { entry_t f; int n; } site_t;
void f(site_t *s, unsigned long long *v) { __atomic_load(s, v, __ATOMIC_ACQUIRE); }
