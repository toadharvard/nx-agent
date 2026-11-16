typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef struct site_t { unsigned fp; entry_t f; } site_t;
entry_t g(unsigned fp, entry_t e, site_t *site) { site_t rec = { fp, e }; __atomic_store(site, &rec, __ATOMIC_RELEASE); return e; }
