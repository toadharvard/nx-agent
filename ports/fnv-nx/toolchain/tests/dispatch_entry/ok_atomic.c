// ENTRY1-ATOMIC: a generic atomic builtin is a same-type copy of storage that holds entry values
typedef int (*__attribute__((fnvnx_entry)) entry_t)(int);
typedef struct site_t { entry_t f; int n; } site_t;
void st(site_t *s, site_t *r) { __atomic_store(s, r, __ATOMIC_RELEASE); }
void ld(site_t *s, site_t *r) { __atomic_load(s, r, __ATOMIC_ACQUIRE); }
void ex(site_t *s, site_t *v, site_t *r) { __atomic_exchange(s, v, r, __ATOMIC_ACQ_REL); }
_Bool cx(site_t *s, site_t *e, site_t *d) { return __atomic_compare_exchange(s, e, d, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE); }
// the _n forms and the __sync movers on a bare entry value
void stn(entry_t *p, entry_t v) { __atomic_store_n(p, v, __ATOMIC_RELEASE); }
entry_t ldn(entry_t *p) { return __atomic_load_n(p, __ATOMIC_ACQUIRE); }
entry_t exn(entry_t *p, entry_t v) { return __atomic_exchange_n(p, v, __ATOMIC_ACQ_REL); }
_Bool cxn(entry_t *p, entry_t *e, entry_t d) { return __atomic_compare_exchange_n(p, e, d, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE); }
entry_t vcs(entry_t *p, entry_t a, entry_t b) { return __sync_val_compare_and_swap(p, a, b); }
_Bool bcs(entry_t *p, entry_t a, entry_t b) { return __sync_bool_compare_and_swap(p, a, b); }
entry_t lts(entry_t *p, entry_t v) { return __sync_lock_test_and_set(p, v); }
