/* decltype.c - the `.fnvnx.decltype` section of a C unit: one line per external symbol; each symbol's type is spelled in the grammar of
   link/ctypes_compat.py (the expected strings are in test_layout.py). */
struct S { int x; char *y; };
struct S v;
struct L;                                  /* incomplete */
extern struct L *il;
struct { int a; } p;                       /* unnamed tag */
struct B { unsigned int f : 3; int : 5; int g; } b;
int a[4];
extern int au[];
int f(int i, char *s) { return i + s[0]; }
int g(int i, ...);
int h();                                   /* no prototype */
int *const q;
const int *pc;
struct Lst { struct Lst *next; } l;
enum E { E0, E1 } e;
typedef int T;
typedef struct { T m; const T *n; } TS;
TS ts;
volatile unsigned long long vv;
int (*fp)(int (*cb)(void), const char *const name);
union U { int i; float f; } u;
const struct S cs;
int caller(void) { return implicit_fn(1) + g(1, 2) + h(); }   /* implicit_fn: declared by the call only */
static int internal_only;                  /* internal linkage: no line */
int use(void) { return internal_only; }
