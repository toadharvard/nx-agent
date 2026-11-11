/* Fixture for import_escape.py: one function per escape class. Bridge letters in fx_bridge.tsv. */
#include <string.h>
#include <stdlib.h>
#include <stdarg.h>

extern void ext_unknown(void *p);       /* no definition anywhere */
static int *g_saved;
struct node { struct node *next; int *p; };
static struct node *g_list;

int fx_read(const int *p) { return *p; }
void fx_write(int *p) { *p = 5; }
void fx_global(int *p) { g_saved = p; }
static int __attribute__((noinline)) helper_read(const int *p) { return *p + 1; }
int fx_helper_read(const int *p) { return helper_read(p); }
static void __attribute__((noinline)) helper_store(int *p) { g_saved = p; }
void fx_helper_store(int *p) { helper_store(p); }
void fx_extern(void *p) { ext_unknown(p); }
void fx_memcpy_src(void *dst, const void *src, unsigned n) { memcpy(dst, src, n); }
int *fx_ret(int *p) { return p; }
void fx_atomic(volatile long *p) { __atomic_fetch_add(p, 1, __ATOMIC_SEQ_CST); }
void fx_list(int *p) {
    struct node *n = malloc(sizeof *n);
    n->p = p; n->next = g_list; g_list = n;
}
int fx_int(int x) { return x + 1; }
long long fx_q(long long x) { return x * 3; }
static int use_local(const struct node *s) { return *s->p; }
static int __attribute__((noinline)) use_local_ni(const struct node *s) { return *s->p; }
/* LLVM's function-attrs does not reason about a pointer stored into a local that escapes to a noinline callee: e (conservative) */
int fx_local_noinline(int *p) { struct node s; s.p = p; s.next = 0; return use_local_ni(&s); }
int fx_local(int *p) {
    struct node s; s.p = p; s.next = 0;   /* pointer kept only in a local struct that dies with the call */
    return use_local(&s);
}
double fx_float(float a, double b, int *p) { return a + b + *p; }
int fx_vararg(int *p, const char *fmt, ...) { va_list a; va_start(a, fmt); int r = *p + va_arg(a, int); va_end(a); return r; }
int fx_mismatch(int a, int b) { return a + b; }

/* returned-only variants. LLVM 18 only proves `nocapture` through a discarding wrapper when the callee is inlined into it:
   fx_ret (small) -> r; fx_ret_big (not inlinable) -> e (soundness first, see import_escape.py); the others never get `returned` */
int *fx_ret_big(int *p, int n) {
    extern volatile int fx_sink;
    for (int i = 0; i < n; i++) { fx_sink += *p + i; if (fx_sink == 77) fx_sink = 3; }
    return p;
}
int *fx_ret_store(int *p) { g_saved = p; return p; }
int *fx_ret_derived(int *p) { return p + 1; }

/* writes-column cases */
static void __attribute__((noinline)) helper_write(int *p) { *p = 7; }
void fx_callee_write(int *p) { helper_write(p); }                  /* written only by a callee: writes y */
void fx_stash_write(int *q) { *g_saved = *q; }                     /* q only read; the write goes through a pointer stashed earlier by fx_global: q n/n (another alias is not claimed) */
void fx_unused(int *p) { (void)p; }                                /* never dereferenced: readnone: n/n */
int fx_gep_read(const int *p, int i) { return p[i] + p[i + 1]; }   /* derived pointers, loads only: n */
void fx_gep_write(int *p, int i) { p[i] = 1; }                     /* store through a derived pointer: y */
/* declared contracts (fx_contract.tsv): external declarations whose attributes come only from the contract */
extern void ext_hook(const char *n);       /* contract: nocapture, readonly */
extern void ext_hook_ro(const char *n);    /* contract: readonly only (may keep the pointer) */
void fx_contract(const char *n) { ext_hook(n); }
void fx_contract_ro(const char *n) { ext_hook_ro(n); }

/* null tests are not captures (hide_null_tests); every other use of the address still is */
int fx_nullcheck(const char *p) { if (!p) return 0; return *p; }                    /* n / n */
int fx_nullcheck_ne(const char *p) { return p != 0 && p[1] == 'x'; }                  /* n / n */
void fx_nullstore(int *p) { if (p) g_saved = p; }                                     /* e / y: the pointer is still stored */
int fx_ptrcmp(const char *p, const char *q) { return p < q; }                          /* e e / n n: address comparison of two pointers stays a capture; no write happens (but a copy exists: escape e) */
unsigned long fx_ptrtoint(const char *p) { return (unsigned long)p > 0xFFFF; }         /* e / n: pointer-to-integer stays a capture; no write through p */
