// a local object of another type stored into storage that holds entry values by the generic atomic store (the object form of the C front end): not a same-type copy
typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef struct site_t { entry_t f; int n; } site_t;
void f(site_t *s) { unsigned long long v = 0; __atomic_store(s, &v, __ATOMIC_RELEASE); }
