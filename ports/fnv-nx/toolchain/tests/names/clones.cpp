// clones.cpp - the cgraph clones of one unit, compiled at -O2 (-fipa-cp-clone): test_names.py checks the `.fnvnx.clones` record of each.
// Every body reaches an opaque call, so GCC cannot fold it into its callers.
extern int opaque(int);
extern const int *opaque_p(const int *);
struct Cfg { int a, b; };
static const Cfg g_cfg = { 3, 4 };

// 1. IPA-SRA: the unused parameter (index 1) is dropped from the clone: isra_f.isra.0(int a, int c) is isra_f's parameters 0 and 2
static __attribute__((noinline)) int isra_f(int a, int unused, int c) { return opaque(a) + opaque(c); }
int use_isra(int x, int y, int z) { return isra_f(x, y, z) + isra_f(z, y, x); }

// 2. IPA-CP: every call passes the same constant for parameter 1: cp_f.constprop.0 takes parameter 1 from the constant
static __attribute__((noinline)) int cp_f(int a, int b) { return opaque(a) * b + opaque(b + a); }
int use_cp(int x) { return cp_f(x, 7) + cp_f(x + 1, 7); }

// 3. ipa-split: the part of a function that runs after its early exit is split off: part_f.part.0
static int part_f(int *p, int n)
{
  if (!p) return -1;
  int s = 0;
  for (int i = 0; i < n; i++) s += opaque(p[i]) + opaque(p[i] * 3) + opaque(i);
  s += opaque(s) + opaque(s + 1) + opaque(s + 2);
  return s;
}
int use_part(int *p, int n) { return part_f(p, n) + part_f(p, n + 1); }

// 4. the first case of L1: a body that answers a constant object and ignores its argument, like render's dk_cfg.isra.0
static __attribute__((noinline)) const Cfg *dk_cfg(int unused) { return &g_cfg; }
int use_cfg(int u) { return dk_cfg(u)->a + dk_cfg(u + 1)->b; }

// 5. not a clone: no record
__attribute__((noinline)) int plain(int a) { return opaque(a) + 1; }

// 6. IPA-SRA split: a struct parameter of which one field is used is passed as that field by value: split_f.isra.0(int) is the int at byte 4 of parameter 0
struct Big { int a; int b; int c[4]; };
static __attribute__((noinline)) int split_f(const Big *p) { return opaque(p->b) + 5; }
int use_split(const Big *p) { return split_f(p) + split_f(p + 1); }
