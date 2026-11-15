// builtins on shared operands are calls, not inline accesses (fnvnx_order FOLD SET). The sizes are known only after inlining,
// i.e. after the order pass: the front end cannot fold these, the middle end would.
struct P { int a[4]; };
static inline unsigned sixteen() { return 16; }
static inline unsigned four() { return 4; }
void copy16(P *d, const P *s) { __builtin_memcpy(d, s, sixteen()); }
void zero8(char *d) { __builtin_memset(d, 0, four() * 2); }
int cmp4(const char *a, const char *b) { return __builtin_memcmp(a, b, four()) == 0; }
unsigned len(const char *s) { return __builtin_strlen(s); }
static const int tbl[8] = {1, 2, 3, 4, 5, 6, 7, 8};
int ro_index(unsigned i) { return tbl[i & 7]; }
// an address that only reaches a non-escaping builtin argument keeps its local unshared (the vslot pattern of combridge)
template <class MP> int vslot(MP mp) { unsigned w[2]; __builtin_memcpy(w, &mp, sizeof w); return (int)w[0]; }
struct Q { virtual void f(); };
int slot_f() { return vslot(&Q::f); }
// a by-value parameter whose address is taken, inlined into its caller: the inliner's parameter store must not stay plain
extern "C" void use_ptr(const unsigned *);
static inline int pcopy(unsigned a) { use_ptr(&a); return (int)a; }
int pcaller(unsigned x) { return pcopy(x) + pcopy(x + 1); }
// aggregate copies with a shared side are copied unit-wise, every shared unit ordered
struct T12 { int a, b, c; };
void copy12(T12 *d, const T12 *s) { *d = *s; }
struct Big { int v[100]; };
void copybig(Big *d, const Big *s) { *d = *s; }
extern T12 gsrc;
T12 retg() { return gsrc; }
void takes(T12 v);
void passes(const T12 *s) { takes(*s); }
// atomics: every memory order is raised to seq_cst
int rmw(int *p) { return __atomic_fetch_add(p, 1, __ATOMIC_RELAXED); }
int cas(int *p, int *e) { return __atomic_compare_exchange_n(p, e, 1, false, __ATOMIC_RELAXED, __ATOMIC_RELAXED); }
// a CAS on shared memory with a shared `expected`: the builtin works on a private copy, the failure path stores back ordered
bool cas_sh(int *p, int *e) { return __atomic_compare_exchange_n(p, e, 1, false, __ATOMIC_RELAXED, __ATOMIC_RELAXED); }
bool cas_loc(int *p) { int e = 0; return __atomic_compare_exchange_n(p, &e, 1, false, __ATOMIC_RELAXED, __ATOMIC_RELAXED); }
// bit-fields: an access of the allocation unit, one ldar / stlr of the unit
struct BF { unsigned a : 3; unsigned b : 5; int c : 7; unsigned d : 17; };
unsigned bf_get(const BF *p) { return p->b; }
int bf_gets(const BF *p) { return p->c; }
void bf_set(BF *p, unsigned v) { p->b = v; }
void bf_copy(BF *d, const BF *s) { d->b = s->b; }
// a shared aggregate copied into an unshared local (the destination is no ordered side: the last unit replaces the assignment in place)
int ld_priv(const Big *s, int i) { Big l = *s; return l.v[i]; }
// a folded memcmp of two shared GUID-like members reads parts of aggregates as BIT_FIELD_REF (platform dsound.cpp guid_eq)
struct G { unsigned d1; unsigned short d2, d3; unsigned char d4[8]; };
bool geq(const G &a, const G &b) { return a.d1 == b.d1 && a.d2 == b.d2 && !__builtin_memcmp(a.d4, b.d4, 8); }
// NO-MIX: a scope-local shared object (its address leaves) dies, then a private copy (order's temporary of a by-value aggregate) is built:
// GCC would give both one stack slot
struct Gd { unsigned a; unsigned short b, c; unsigned char d[8]; };
int esc_use2(Big *);
int use3(Gd);
int nomix2(int c, const Gd *s) { { Big e; e.v[0] = c; esc_use2(&e); } return use3(*s); }
int nomix3(int c, const Gd *s, const Gd *t) { { Big e; e.v[0] = c; esc_use2(&e); } Gd l = *s; if (c) l = *t; return use3(l); }

// va_list builtins on the guest ABI (aarch64: va_list is a record): va_copy is an ordered aggregate copy, va_start/va_end touch only the object
#include <stdarg.h>
int va_sock(int);
int va_fcntl(int fd, int cmd, ...) {
    if (!va_sock(fd)) return -1;
    va_list ap; va_start(ap, cmd); int arg = va_arg(ap, int); va_end(ap);
    return arg + cmd;
}
int va_cp(const char *f, va_list ap) { va_list ap2; va_copy(ap2, ap); int r = va_sock(va_arg(ap2, int)); va_end(ap2); return r; }

// an aggregate array element copied between a shared object and a private local array (freetype ttgload.c shape): the private units are
// MEM[&decl + offset], not a MEM_REF over the address of an array reference (that form crashed RTL expansion)
struct TV { long x, y; };
struct TL { TV pp1, pp2, pp3, pp4; int linear, vadvance; };
int tt_load(TL *, unsigned, int);
int tt_sub(TL *loader, unsigned *flags, int num, int rec) {
  int error = 0;
  for (int n = 0; n < num; n++) {
    TV pp[4]; int la = loader->linear, va = loader->vadvance;
    pp[0] = loader->pp1; pp[1] = loader->pp2; pp[2] = loader->pp3; pp[3] = loader->pp4;
    error = tt_load(loader, flags[n], rec + 1);
    if (error) return error;
    if (!(flags[n] & 4)) { loader->pp1 = pp[0]; loader->pp2 = pp[1]; loader->pp3 = pp[2]; loader->pp4 = pp[3]; loader->linear = la; loader->vadvance = va; }
  }
  return error;
}
