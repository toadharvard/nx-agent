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
