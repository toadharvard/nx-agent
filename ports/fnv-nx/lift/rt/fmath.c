/* fmath: correctly rounded x87 transcendentals and exact fprem/fprem1/fscale/fxtract (README "One reference CPU model", R8).
   Freestanding C (no libc, libm, malloc, errno, FPU instruction): doubles are only moved as bit patterns, every computation is integer arithmetic, so the
   host rounding mode and FPSR are irrelevant. Builds natively (x86-64, for test/fmathtest.py) and as aarch64 ILP32.

   RULE (one engine for sin cos tan atan2 2^x-1 y*log2(x) y*log2(x+1)): ZIV'S STRATEGY on multi-precision binary floats.
     1. Special operands (zeros, infinities, NaN, out of domain) and the EXPLICITLY ENUMERATED EXACT CASES are decided before the loop by the x87 table / by
        exact integer arithmetic (listed per function below).
     2. For every other input the true value v is irrational (even transcendental: Lindemann-Weierstrass for sin/cos/tan/2^x-1 of a non-zero algebraic,
        atan2 of a rational pair = argument of an algebraic number, Gelfond-Schneider for log2 of a rational that is not a power of two; times a non-zero
        rational y). So v is never a double and never a midpoint of two neighbouring doubles.
     3. For n = 6, 12, 24, ... limbs of 32 bits the evaluator produces A and an integer B with |A - v| <= 2^-B |v| (proved per evaluator, below).
        The interval [A(1-2^(2-B)), A(1+2^(2-B))] contains v. If it lies inside ONE half-ulp cell of the binary64 grid (same binade, same cell index of the
        half-ulp lattice; subnormals and the overflow boundary are part of the lattice), v rounds (nearest-even) to the double of that cell, which is the
        answer; otherwise n doubles. Termination: v is not on the lattice, so some finite n puts it strictly inside a cell with room to spare, and the
        interval shrinks to v (B grows with n). There is no cap and no give-up path.
   BIG FLOATS: value = (-1)^s m 2^(e-32n), m an n-limb integer with its top bit set (so |value| in [2^(e-1), 2^e)); e is an int (range far beyond binary64).
     Every primitive truncates (toward zero) and its absolute error is <= 2^(emax-32n+1), emax = the larger operand exponent (bf_mul, bf_div, bf_*_u32:
     relative error <= 2^(2-32n)); bf_add of numbers whose bits fit the window is exact. Series (Taylor/Euler/atanh/Machin) of N <= 32n terms with
     |argument| < 1: each term carries <= 3N primitive errors, so the sum has relative error <= 12 N^2 2^-32n * (sum of |terms| / |sum|) <= 2^(-32n+40)*2
     for n <= 2^15 (N <= 2^20); the constants pi, ln 2 inherit the same bound. We state every result with the guard G = 64 bits (> 42 + ceil(log2 of the
     amplification, always <= 8 here)); assumption recorded: n <= 2^15 limbs (4 MiB of mantissa), which no double input approaches (hard cases need ~ 200 bits).
   WIDE ARGUMENT STEPS are done in a second context cw with more limbs so that they are exact or carry an explicit absolute bound.
   Memory: VLAs on the stack, about 8*48*n bytes (n = 6: 2 KB), released every iteration. No static state: reentrant, thread safe. */
#pragma GCC optimize("no-tree-loop-distribute-patterns,no-tree-loop-distribute-patterns")
#include "fmath.h"
typedef long long s64;

#define EXPM 0x7ff0000000000000ull
#define MANM 0x000fffffffffffffull
#define SGNM 0x8000000000000000ull
#define FQ 0x0008000000000000ull
#define IE 1u
#define ZE 4u
#define OE 8u
#define UE 0x10u
#define PE 0x20u
static inline u64 d2u(double d) { union { double d; u64 u; } v; v.d = d; return v.u; }
static inline double u2d(u64 u) { union { double d; u64 u; } v; v.u = u; return v.d; }

/* ================= big floats ================= */
typedef struct { int n; u32 *p; u32 *end; } cx;
typedef struct { u32 *m; int e; int s; int z; } bf;
static bf bnew(cx *c) {
  bf r; r.m = c->p; c->p += c->n; r.e = 0; r.s = 0; r.z = 1;
#ifdef FM_DEBUG
  if (c->p > c->end) __builtin_trap();
#endif
  return r;
}
static void bzero_(bf *r) { r->z = 1; r->s = 0; r->e = 0; }
static void bf_cpy(cx *c, bf *r, const bf *a) { for (int i = 0; i < c->n; i++) r->m[i] = a->m[i]; r->e = a->e; r->s = a->s; r->z = a->z; }
static u32 getl(const u32 *t, int nt, int i) { return (i < 0 || i >= nt) ? 0 : t[i]; }

/* t: nt limbs (little endian), value t * 2^(ex - 32 nt). Normalise and truncate to n limbs. t is only read. */
static void bf_nw(cx *c, bf *r, const u32 *t, int nt, int ex, int s) {
  int n = c->n, i = nt - 1;
  while (i >= 0 && !t[i]) i--;
  if (i < 0) { bzero_(r); return; }
  int sh = (nt - 1 - i) * 32 + __builtin_clz(t[i]), ls = sh >> 5, bs = sh & 31;
  for (int j = 0; j < n; j++) {
    int q = nt - n + j - ls;
    u32 v = getl(t, nt, q) << bs;
    if (bs) v |= getl(t, nt, q - 1) >> (32 - bs);
    r->m[j] = v;
  }
  r->e = ex - sh; r->s = s; r->z = 0;
}
/* value v * 2^sh */
static void bf_u64(cx *c, bf *r, u64 v, int sh, int s) {
  u32 t[2];
  if (!v) { bzero_(r); return; }
  t[0] = (u32)v; t[1] = (u32)(v >> 32);
  bf_nw(c, r, t, 2, 64 + sh, s);
}
static int cmpmag(const bf *a, const bf *b, int n) {
  if (a->e != b->e) return a->e > b->e ? 1 : -1;
  for (int i = n - 1; i >= 0; i--) if (a->m[i] != b->m[i]) return a->m[i] > b->m[i] ? 1 : -1;
  return 0;
}
static void bf_mul(cx *c, bf *r, const bf *a, const bf *b) {
  int n = c->n;
  if (a->z || b->z) { bzero_(r); return; }
  u32 t[2 * n];
  for (int i = 0; i < 2 * n; i++) t[i] = 0;
  for (int i = 0; i < n; i++) {
    u64 carry = 0;
    for (int j = 0; j < n; j++) { u64 cur = (u64)a->m[i] * b->m[j] + t[i + j] + carry; t[i + j] = (u32)cur; carry = cur >> 32; }
    t[i + n] = (u32)carry;
  }
  bf_nw(c, r, t, 2 * n, a->e + b->e, a->s ^ b->s);
}
static u32 xl(const bf *a, int j) { return (j >= 1 && j <= 0x7fffffff) ? a->m[j - 1] : 0; }
/* a + b. The smaller operand is aligned into a window with one extra low limb (error <= 2^-32 ulp of the larger one), the sum is truncated to n limbs. */
static void bf_add(cx *c, bf *r, const bf *a, const bf *b) {
  int n = c->n;
  if (a->z) { bf_cpy(c, r, b); return; }
  if (b->z) { bf_cpy(c, r, a); return; }
  const bf *big = a, *sm = b;
  int cm = cmpmag(a, b, n);
  if (cm < 0) { big = b; sm = a; }
  long long d = (long long)big->e - sm->e;
  u32 t[n + 2], u[n + 2];
  t[0] = 0; for (int i = 0; i < n; i++) t[i + 1] = big->m[i]; t[n + 1] = 0;
  if (d >= 32LL * (n + 2)) { for (int i = 0; i < n + 2; i++) u[i] = 0; }
  else {
    int ls = (int)(d >> 5), bs = (int)(d & 31);
    for (int i = 0; i < n + 2; i++) {
      u32 lo = (i + ls <= n) ? xl(sm, i + ls) : 0, hi = (i + ls + 1 <= n) ? xl(sm, i + ls + 1) : 0;
      u[i] = bs ? (lo >> bs) | (hi << (32 - bs)) : lo;
    }
  }
  if (a->s == b->s) {
    u64 cr = 0;
    for (int i = 0; i < n + 2; i++) { u64 cur = (u64)t[i] + u[i] + cr; t[i] = (u32)cur; cr = cur >> 32; }
  } else {
    long long br = 0;
    for (int i = 0; i < n + 2; i++) { long long cur = (long long)t[i] - u[i] - br; br = cur < 0; t[i] = (u32)cur; }
  }
  bf_nw(c, r, t, n + 2, big->e + 32, big->s);
}
static void bf_sub(cx *c, bf *r, const bf *a, const bf *b) { bf nb = *b; if (!nb.z) nb.s ^= 1; bf_add(c, r, a, &nb); }
/* a / b (b != 0): Q = floor(ma 2^(32n) / mb) bit by bit, n+1 limbs */
static void bf_div(cx *c, bf *r, const bf *a, const bf *b) {
  int n = c->n;
  if (a->z) { bzero_(r); return; }
  u32 R[n + 1], Q[n + 1];
  for (int i = 0; i < n; i++) { R[i] = a->m[i]; Q[i] = 0; }
  R[n] = 0; Q[n] = 0;
  for (int step = 32 * n; step >= 0; step--) {
    if (step < 32 * n) { u32 cr = 0; for (int i = 0; i <= n; i++) { u32 nc = R[i] >> 31; R[i] = (R[i] << 1) | cr; cr = nc; } }
    int ge = R[n] != 0;
    if (!ge) { ge = 1; for (int i = n - 1; i >= 0; i--) if (R[i] != b->m[i]) { ge = R[i] > b->m[i]; break; } }
    if (ge) {
      long long br = 0;
      for (int i = 0; i <= n; i++) { long long cur = (long long)R[i] - (i < n ? b->m[i] : 0) - br; br = cur < 0; R[i] = (u32)cur; }
      Q[step >> 5] |= 1u << (step & 31);
    }
  }
  bf_nw(c, r, Q, n + 1, a->e - b->e + 32, a->s ^ b->s);
}
static void bf_mulu(cx *c, bf *r, const bf *a, u32 d) {
  int n = c->n;
  if (a->z || !d) { bzero_(r); return; }
  u32 t[n + 1]; u64 cr = 0;
  for (int i = 0; i < n; i++) { u64 cur = (u64)a->m[i] * d + cr; t[i] = (u32)cur; cr = cur >> 32; }
  t[n] = (u32)cr;
  bf_nw(c, r, t, n + 1, a->e + 32, a->s);
}
static void bf_divu(cx *c, bf *r, const bf *a, u32 d) {
  int n = c->n;
  if (a->z) { bzero_(r); return; }
  u32 t[n + 1]; u64 rem = 0;
  for (int i = n - 1; i >= 0; i--) { u64 cur = (rem << 32) | a->m[i]; t[i + 1] = (u32)(cur / d); rem = cur % d; }
  t[0] = (u32)((rem << 32) / d);
  bf_nw(c, r, t, n + 1, a->e, a->s);
}
/* floor of a >= 0 (clears the fractional bits) */
static void bf_floor(cx *c, bf *r, const bf *a) {
  int n = c->n;
  if (a->z || a->e <= 0) { bzero_(r); return; }
  bf_cpy(c, r, a);
  int p = 32 * n - a->e;                       /* number of fractional bits */
  for (int i = 0; i < n && p > 0; i++) {
    if (p >= 32) { r->m[i] = 0; p -= 32; } else { r->m[i] &= ~((1u << p) - 1); p = 0; }
  }
}
static u32 bf_intbit(const bf *a, int i, int n) {   /* bit i of the integer part of a (a integer valued, a.e >= 1) */
  int pos = 32 * n - a->e + i;
  if (a->z || pos < 0 || pos >= 32 * n) return 0;
  return (a->m[pos >> 5] >> (pos & 31)) & 1;
}
static u32 bf_small(const bf *a, int n) {         /* integer valued a < 2^32 */
  if (a->z || a->e <= 0) return 0;
  return a->m[n - 1] >> (32 - a->e);
}

/* ================= constants and series ================= */
/* atan(1/m) = sum (-1)^k m^(-2k-1)/(2k+1), m <= 65535 */
static void ser_atan_inv(cx *c, bf *out, u32 m) {
  u32 *mk = c->p; int n = c->n;
  bf p = bnew(c), sum = bnew(c), t = bnew(c), one = bnew(c);
  bf_u64(c, &one, 1, 0, 0); bf_divu(c, &p, &one, m); bf_cpy(c, &sum, &p);
  for (u32 k = 1;; k++) {
    bf_divu(c, &p, &p, m * m);
    if (p.z || p.e < sum.e - 32 * n - 4) break;
    bf_divu(c, &t, &p, 2 * k + 1);
    if (k & 1) bf_sub(c, &sum, &sum, &t); else bf_add(c, &sum, &sum, &t);
  }
  bf_cpy(c, out, &sum); c->p = mk;
}
/* pi (Machin: 16 atan 1/5 - 4 atan 1/239) */
static void bf_pi(cx *c, bf *out) {
  u32 *mk = c->p;
  bf a = bnew(c), b = bnew(c);
  ser_atan_inv(c, &a, 5); ser_atan_inv(c, &b, 239);
  a.e += 4; b.e += 2;
  bf_sub(c, out, &a, &b); c->p = mk;
}
/* 2 sum_{j>=0} s^(2j+1)/(2j+1) = 2 atanh(s) = ln((1+s)/(1-s)), |s| <= 1/3 */
static void ser_atanh2(cx *c, bf *out, const bf *s) {
  u32 *mk = c->p; int n = c->n;
  bf s2 = bnew(c), p = bnew(c), sum = bnew(c), t = bnew(c);
  bf_mul(c, &s2, s, s); bf_cpy(c, &p, s); bf_cpy(c, &sum, s);
  for (u32 j = 1;; j++) {
    bf_mul(c, &p, &p, &s2);
    if (p.z || p.e < sum.e - 32 * n - 4) break;
    bf_divu(c, &t, &p, 2 * j + 1);
    bf_add(c, &sum, &sum, &t);
  }
  bf_cpy(c, out, &sum); out->e += 1; c->p = mk;
}
static void bf_ln2(cx *c, bf *out) {
  u32 *mk = c->p;
  bf one = bnew(c), s = bnew(c);
  bf_u64(c, &one, 1, 0, 0); bf_divu(c, &s, &one, 3);
  ser_atanh2(c, out, &s); c->p = mk;
}
/* atan(w) = sum (-1)^j w^(2j+1)/(2j+1), |w| < 1/8 */
static void ser_atan(cx *c, bf *out, const bf *w) {
  u32 *mk = c->p; int n = c->n;
  bf w2 = bnew(c), p = bnew(c), sum = bnew(c), t = bnew(c);
  bf_mul(c, &w2, w, w); bf_cpy(c, &p, w); bf_cpy(c, &sum, w);
  for (u32 j = 1;; j++) {
    bf_mul(c, &p, &p, &w2);
    if (p.z || p.e < sum.e - 32 * n - 4) break;
    bf_divu(c, &t, &p, 2 * j + 1);
    if (j & 1) bf_sub(c, &sum, &sum, &t); else bf_add(c, &sum, &sum, &t);
  }
  bf_cpy(c, out, &sum); c->p = mk;
}
/* atan(j/8), 1 <= j <= 7 (Euler: sum_m a_m c^(2m+1)/(1+c^2)^(m+1), a_m = a_(m-1) 2m/(2m+1); ratio c^2/(1+c^2) = j^2/(64+j^2) <= 49/113) */
static void ser_atan_c(cx *c, bf *out, u32 j) {
  u32 *mk = c->p; int n = c->n;
  bf t = bnew(c), sum = bnew(c);
  bf_u64(c, &t, 8 * j, 0, 0); bf_divu(c, &t, &t, 64 + j * j); bf_cpy(c, &sum, &t);
  for (u32 m = 1;; m++) {
    bf_mulu(c, &t, &t, 2 * m); bf_mulu(c, &t, &t, j * j); bf_divu(c, &t, &t, 2 * m + 1); bf_divu(c, &t, &t, 64 + j * j);
    if (t.z || t.e < sum.e - 32 * n - 4) break;
    bf_add(c, &sum, &sum, &t);
  }
  bf_cpy(c, out, &sum); c->p = mk;
}
/* sin r (cs = 0) and cos r (cs = 1), |r| < 1: sums of alternating decreasing terms, sum >= 0.54 (cos) / 0.84 |r| (sin): no cancellation beyond 2 bits */
static void ser_sincos(cx *c, bf *out, const bf *r, int cs) {
  u32 *mk = c->p; int n = c->n;
  bf r2 = bnew(c), t = bnew(c), sum = bnew(c);
  bf_mul(c, &r2, r, r);
  if (cs) { bf_u64(c, &t, 1, 0, 0); } else bf_cpy(c, &t, r);
  bf_cpy(c, &sum, &t);
  for (u32 k = 1;; k++) {
    bf_mul(c, &t, &t, &r2);
    u32 a = cs ? 2 * k - 1 : 2 * k, b = cs ? 2 * k : 2 * k + 1;
    bf_divu(c, &t, &t, a); bf_divu(c, &t, &t, b);
    if (t.z || t.e < sum.e - 32 * n - 4) break;
    if (k & 1) bf_sub(c, &sum, &sum, &t); else bf_add(c, &sum, &sum, &t);
  }
  bf_cpy(c, out, &sum); c->p = mk;
}
/* expm1(t) = sum_{j>=1} t^j/j!, |t| <= 0.35 */
static void ser_expm1(cx *c, bf *out, const bf *t) {
  u32 *mk = c->p; int n = c->n;
  bf p = bnew(c), sum = bnew(c);
  bf_cpy(c, &p, t); bf_cpy(c, &sum, t);
  for (u32 j = 2;; j++) {
    bf_mul(c, &p, &p, t); bf_divu(c, &p, &p, j);
    if (p.z || p.e < sum.e - 32 * n - 4) break;
    bf_add(c, &sum, &sum, &p);
  }
  bf_cpy(c, out, &sum); c->p = mk;
}

/* ================= rounding: the decision of the Ziv loop ================= */
/* bits [lo, lo+cnt) of the n-limb integer m, cnt <= 63 */
static u64 getbits(const u32 *m, int n, int lo, int cnt) {
  u64 v = 0;
  if (lo >= 32 * n) return 0;
  for (int i = cnt - 1; i >= 0; i--) { int pos = lo + i; v = (v << 1) | (pos < 32 * n ? (m[pos >> 5] >> (pos & 31)) & 1 : 0); }
  return v;
}
static int anybelow(const u32 *m, int n, int lo) {
  for (int pos = 0; pos < lo && pos < 32 * n; pos += 32) { int k = lo - pos; u32 mask = k >= 32 ? 0xffffffffu : ((1u << k) - 1); if (m[pos >> 5] & mask) return 1; }
  return 0;
}
/* cell of |v| in the half-ulp lattice of binary64: v in [q, q+1) * 2^h, h = u - 1, u the exponent of the unit of the last place (-1074 in the subnormal range);
   u = 0x7fffffff when |v| >= 2^1024. rem: bits below the cell are not all zero */
static void cell(const bf *v, int n, u64 *q, int *u, int *rem) {
  int L = v->e - 1;
  if (L > 1023) { *q = 0; *u = 0x7fffffff; *rem = 1; return; }
  *u = L >= -1022 ? L - 52 : -1074;
  int need = 32 * n - ((v->e) - (*u - 1));     /* v / 2^h = m 2^(e-32n-h): drop `need` low bits */
  if (need < 0) need = 0;
  *q = getbits(v->m, n, need, 56); *rem = anybelow(v->m, n, need);
}
/* A with relative error <= 2^-B (exact: A is v). On success *out = the double (with sign) and the exception bits are ORed into *fl. */
static int bf_pack(cx *c, const bf *A, int B, int exact, u64 *out, u32 *fl) {
  int n = c->n;
  if (A->z) return 0;
  u32 *mk = c->p;
  bf am = *A; am.s = 0;
  bf lo = bnew(c), hi = bnew(c);
  if (exact) { bf_cpy(c, &lo, &am); bf_cpy(c, &hi, &am); }
  else {
    if (B < 8) { c->p = mk; return 0; }
    bf d = bnew(c); bf_cpy(c, &d, &am); d.e -= B - 2;
    bf_sub(c, &lo, &am, &d); bf_add(c, &hi, &am, &d);
  }
  u64 ql, qh; int ul, uh, rl, rh;
  cell(&lo, n, &ql, &ul, &rl); cell(&hi, n, &qh, &uh, &rh);
  c->p = mk;
  if (ul != uh || ql != qh) return 0;
  u64 M; u32 f = 0; u64 bits;
  if (ul == 0x7fffffff) { bits = EXPM; f = OE | PE; }
  else {
    int inexact = !exact || !(((ql & 1) == 0) && !rl);
    if (ql & 1) {
      if (exact && !rl) { M = (ql + 1) >> 1; if (M & 1) M -= 1; }       /* tie: the even neighbour */
      else M = (ql + 1) >> 1;
    } else M = ql >> 1;
    bits = ((u64)(ul + 1074) << 52) + M;
    if (bits >= EXPM) { bits = EXPM; f = OE | PE; }
    else if (inexact) { f = PE; if (A->e - 1 < -1022) f |= UE; }
  }
  *fl |= f;
  *out = bits | (A->s ? SGNM : 0);
  return 1;
}

/* exact double of (-1)^s M 2^q (M an integer < 2^64): used where the value is exactly known (fscale, fprem) */
static double exact_double(u64 M, int q, int s, u32 *fl) {
  u32 arena[40]; cx c; c.n = 4; c.p = arena; c.end = arena + 40;
  bf A = bnew(&c); u64 out;
  if (!M) return u2d(s ? SGNM : 0);
  bf_u64(&c, &A, M, q, s);
  bf_pack(&c, &A, 0, 1, &out, fl);
  return u2d(out);
}

/* ================= operands ================= */
static double invalid(u32 *fl) { *fl |= IE; return u2d(0xFFF8000000000000ull); }
static double nan1(double x, u32 *fl) { u64 u = d2u(x); if (!(u & FQ)) *fl |= IE; return u2d(u | FQ); }
/* x87 rule for two operands, at least one a NaN: both NaN -> the larger significand (tie: the positive one), one NaN -> it; the result is quieted */
static double nan2(double a, double b, u32 *fl) {
  u64 x = d2u(a), y = d2u(b);
  int na = (x & ~SGNM) > EXPM, nb = (y & ~SGNM) > EXPM;
  if ((na && !(x & FQ)) || (nb && !(y & FQ))) *fl |= IE;
  if (na && nb) { u64 ma = x & (FQ * 2 - 1), mb = y & (FQ * 2 - 1); return u2d((ma > mb ? x : mb > ma ? y : (x >> 63 ? y : x)) | FQ); }
  return u2d((na ? x : y) | FQ);
}
static int isnan_(u64 a) { return (a & ~SGNM) > EXPM; }
static int isinf_(u64 a) { return (a & ~SGNM) == EXPM; }
static int iszero_(u64 a) { return !(a & ~SGNM); }
/* |x| = mant 2^e2 (mant < 2^53; zero -> mant 0) */
static void unpack(u64 a, u64 *mant, int *e2) {
  int f = (int)((a >> 52) & 0x7ff);
  if (f) { *mant = (a & MANM) | (1ull << 52); *e2 = f - 1075; } else { *mant = a & MANM; *e2 = -1074; }
}

/* ================= evaluators ================= */
enum { W_SIN, W_COS, W_TAN, W_2XM1, W_YL2X, W_YL2XP1, W_ATAN2, W_PIQ };
typedef struct { double x, y; int w; int q; } za;

/* pi/2 into out (n limbs of context c) */
static void bf_pi2(cx *c, bf *out) { bf_pi(c, out); out->e -= 1; }

/* sin/cos/tan of x (finite, nonzero). Reduction: |x| = k pi/2 + r with the exact integer k = floor(|x|/(pi/2) + 1/2) taken from an approximation (any
   k with |r| < 1 serves: |r| <= pi/4 + 2^-90). In cw (nw limbs, 32 nw >= 32 n + e_x + 129; e_x: |x| < 2^e_x): pi/2 has relative error <= 2^(64-32nw), the product
   k pi/2 absolute error <= 2^(e_x+1) 2^(64-32 nw) <= 2^(-32n-64), the subtraction adds 2^(e_x+1-32nw) <= 2^(-32n-128): so |r_computed - r| <= 2^(-32n-63) and
   the relative error of r is <= 2^-(32n+62+r.e) (r.e <= 0: |r| < 2^r.e). Evaluating the odd/even series on r keeps relative error (cond(sin r)<=1, cond(cos r)<=0.7). */
static int ev_trig(cx *c, cx *cw, const za *a, bf *A, int *B) {
  int n = c->n, nw = cw->n;
  u64 u = d2u(a->x), mant; int e2, sx = (int)(u >> 63);
  unpack(u, &mant, &e2);
  u32 *mk = c->p, *mkw = cw->p;
  bf R = bnew(c); int br; int q = 0;
  bf Xm = bnew(c); bf_u64(c, &Xm, mant, e2, 0);
  if (Xm.e <= 0) { bf_cpy(c, &R, &Xm); br = 32 * n; }
  else {
    bf X = bnew(cw), pi2 = bnew(cw), Z = bnew(cw), H = bnew(cw), K = bnew(cw), P = bnew(cw), Rw = bnew(cw), half = bnew(cw);
    bf_u64(cw, &X, mant, e2, 0); bf_pi2(cw, &pi2);
    bf_div(cw, &Z, &X, &pi2);
    bf_u64(cw, &half, 1, -1, 0); bf_add(cw, &H, &Z, &half); bf_floor(cw, &K, &H);
    q = (int)(bf_intbit(&K, 0, nw) | (bf_intbit(&K, 1, nw) << 1));
    bf_mul(cw, &P, &K, &pi2); bf_sub(cw, &Rw, &X, &P);
    if (Rw.z) { c->p = mk; cw->p = mkw; return 0; }
    br = 32 * n + 62 + Rw.e;
    bf_nw(c, &R, Rw.m, nw, Rw.e, Rw.s);
    cw->p = mkw;
  }
  if (br > 32 * n - 64) br = 32 * n - 64;
  if (br < 16) { c->p = mk; return 0; }
  bf S = bnew(c), C = bnew(c);
  int ws = a->w, neg;
  int needs = (ws == W_SIN) ? !(q & 1) : (ws == W_COS) ? (q & 1) : 1, needc = (ws == W_SIN) ? (q & 1) : (ws == W_COS) ? !(q & 1) : 1;
  if (needs) ser_sincos(c, &S, &R, 0);
  if (needc) ser_sincos(c, &C, &R, 1);
  if (ws == W_TAN) {
    if (q & 1) { bf_div(c, A, &C, &S); neg = 1; } else { bf_div(c, A, &S, &C); neg = 0; }
    neg ^= sx;
    *B = br - 10;
  } else {
    int useS = (ws == W_SIN) ? !(q & 1) : (q & 1);
    bf_cpy(c, A, useS ? &S : &C);
    /* sin|x| = [S, C, -S, -C][q]; cos|x| = [C, -S, -C, S][q] */
    neg = (ws == W_SIN) ? (q >= 2) ^ sx : (q == 1 || q == 2);
    *B = br - 8;
  }
  if (!A->z) A->s ^= neg;
  c->p = mk; /* keep A and R? A is allocated by the caller */
  return 1;
}

/* 2^x - 1 for non-integer x in (-60, 1024) or integer -60 <= x < 64. 2^x - 1 = 2^k expm1(f ln2) + (2^k - 1), k = nearest integer, |f| <= 1/2 */
static int ev_exp(cx *c, cx *cw, const za *a, bf *A, int *B) {
  (void)cw;
  u64 u = d2u(a->x), mant; int e2, sx = (int)(u >> 63);
  unpack(u, &mant, &e2);
  s64 ip, fnum; int fe = e2;
  if (e2 >= 0) { ip = (s64)(mant << e2); fnum = 0; }
  else {
    int s = -e2;
    if (s >= 64) { ip = 0; fnum = (s64)mant; }
    else {
      u64 ipu = mant >> s, fr = mant & ((1ull << s) - 1), half = 1ull << (s - 1);
      if (fr > half || (fr == half && (ipu & 1))) ipu++;
      ip = (s64)ipu; fnum = (s64)mant - (s64)(ipu << s);
    }
  }
  int k = sx ? -(int)ip : (int)ip;
  u32 *mk = c->p;
  bf P2 = bnew(c), one = bnew(c), W = bnew(c);
  bf_u64(c, &P2, 1, k, 0); bf_u64(c, &one, 1, 0, 1);
  if (k) bf_add(c, &W, &P2, &one); else bzero_(&W);
  if (!fnum) { bf_cpy(c, A, &W); c->p = mk; return 2; }                 /* integer x: 2^k - 1 exactly (n >= 6 limbs hold 64 bits) */
  bf F = bnew(c), T = bnew(c), L2 = bnew(c), S = bnew(c);
  bf_u64(c, &F, (u64)(fnum < 0 ? -fnum : fnum), fe, (fnum < 0) ^ sx);
  bf_ln2(c, &L2); bf_mul(c, &T, &F, &L2);
  ser_expm1(c, &S, &T);
  S.e += k;
  bf_add(c, A, &S, &W);
  *B = 32 * c->n - 64 - 8;
  c->p = mk;
  return 1;
}

/* y log2(u), u = x (W_YL2X, x > 0) or 1 + x (W_YL2XP1, x > -1), y finite non-zero, u not 1. U = u is formed EXACTLY in cw (70+ limbs hold every bit
   between 2^-1074 and 2^1025). u = 2^k w, w in [3/4, 3/2); z = w - 1 = (U - 2^k)/2^k exact in cw. ln w = 2 atanh(z/(2+z)), |z/(2+z)| <= 1/5. log2 u = k + ln w/ln 2:
   |k + ln w/ln2| >= 0.4 for k != 0, so the cancellation costs <= 2 bits. u a power of two <=> z = 0: result y k, exactly (<= 64 bits). */
static int ev_log(cx *c, cx *cw, const za *a, bf *A, int *B) {
  u64 ux = d2u(a->x), mx, uy = d2u(a->y), my; int ex, ey;
  unpack(ux, &mx, &ex); unpack(uy, &my, &ey);
  u32 *mk = c->p, *mkw = cw->p;
  bf U = bnew(cw), P2 = bnew(cw), N = bnew(cw), one = bnew(cw);
  bf_u64(cw, &U, mx, ex, (int)(ux >> 63));
  if (a->w == W_YL2XP1) { bf_u64(cw, &one, 1, 0, 0); bf_add(cw, &U, &U, &one); }
  int k = U.e - 1;
  if ((U.m[cw->n - 1] >> 30) == 3) k = U.e;
  bf_u64(cw, &P2, 1, k, 0);
  bf_sub(cw, &N, &U, &P2);
  bf Y = bnew(c), Kb = bnew(c);
  bf_u64(c, &Y, my, ey, (int)(uy >> 63));
  bf_u64(c, &Kb, (u64)(k < 0 ? -k : k), 0, k < 0);
  if (N.z) { bf_mul(c, A, &Y, &Kb); cw->p = mkw; c->p = mk; return 2; }
  bf Z = bnew(c), D = bnew(c), Sx = bnew(c), Lw = bnew(c), L2 = bnew(c), Q = bnew(c), V = bnew(c), two = bnew(c);
  N.e -= k;
  bf_nw(c, &Z, N.m, cw->n, N.e, N.s);
  bf_u64(c, &two, 1, 1, 0); bf_add(c, &D, &Z, &two); bf_div(c, &Sx, &Z, &D);
  ser_atanh2(c, &Lw, &Sx);
  bf_ln2(c, &L2); bf_div(c, &Q, &Lw, &L2);
  bf_add(c, &V, &Kb, &Q);
  bf_mul(c, A, &Y, &V);
  *B = 32 * c->n - 64 - 12;
  cw->p = mkw; c->p = mk;
  return 1;
}

/* atan2(y, x) of finite non-zero y, x. a = |y|, b = |x|; P/Q = min/max, z = P/Q in (0,1]; j = round(8 z) in 0..8, c = j/8,
   atan z = atan c + atan w, w = (z - c)/(1 + z c) = (8P - jQ)/(8Q + jP): numerator and denominator are formed EXACTLY in cw (jQ: Q has 53 significant bits).
   |w| <= 1/16 + 2^-30. atan c: Euler series (j < 8), pi/4 (j = 8), 0 (j = 0). If |y| > |x| the angle is pi/2 - atan(x/y). x < 0: pi - angle. The sign is that of y. */
static int ev_atan(cx *c, cx *cw, const za *a, bf *A, int *B) {
  u64 ux = d2u(a->x), mx, uy = d2u(a->y), my; int ex, ey;
  unpack(ux, &mx, &ex); unpack(uy, &my, &ey);
  u32 *mk = c->p, *mkw = cw->p;
  bf ya = bnew(cw), xa = bnew(cw);
  bf_u64(cw, &ya, my, ey, 0); bf_u64(cw, &xa, mx, ex, 0);
  int swap = cmpmag(&ya, &xa, cw->n) > 0;
  const bf *Pw = swap ? &xa : &ya, *Qw = swap ? &ya : &xa;
  bf Pm = bnew(c), Qm = bnew(c), Zm = bnew(c), t8 = bnew(c), half = bnew(c), Hm = bnew(c), Jf = bnew(c);
  bf_nw(c, &Pm, Pw->m, cw->n, Pw->e, 0); bf_nw(c, &Qm, Qw->m, cw->n, Qw->e, 0);
  bf_div(c, &Zm, &Pm, &Qm);
  t8 = Zm; t8.e += 3;
  bf_u64(c, &half, 1, -1, 0); bf_add(c, &Hm, &t8, &half); bf_floor(c, &Jf, &Hm);
  u32 j = bf_small(&Jf, c->n);
  if (j > 8) j = 8;
  bf Nj = bnew(cw), P8 = bnew(cw), Q8 = bnew(cw), Num = bnew(cw), Den = bnew(cw), Jp = bnew(cw);
  bf_mulu(cw, &Nj, Qw, j); P8 = *Pw; P8.e += 3;
  bf_sub(cw, &Num, &P8, &Nj);
  Q8 = *Qw; Q8.e += 3; bf_mulu(cw, &Jp, Pw, j); bf_add(cw, &Den, &Q8, &Jp);
  bf Wn = bnew(c), Wd = bnew(c), Wv = bnew(c), Ac = bnew(c), Aw = bnew(c), Th = bnew(c), pi = bnew(c), pi2 = bnew(c);
  bf_nw(c, &Wn, Num.m, cw->n, Num.e, Num.s); bf_nw(c, &Wd, Den.m, cw->n, Den.e, Den.s);
  bf_div(c, &Wv, &Wn, &Wd);
  bf_pi(c, &pi); pi2 = pi; pi2.e -= 1;
  if (j == 0) bzero_(&Ac); else if (j == 8) { Ac = pi; Ac.e -= 2; } else ser_atan_c(c, &Ac, j);
  if (Wv.z) bzero_(&Aw); else ser_atan(c, &Aw, &Wv);
  bf_add(c, &Th, &Ac, &Aw);
  if (swap) { bf T2 = bnew(c); bf_sub(c, &T2, &pi2, &Th); Th = T2; }
  if (ux >> 63) { bf T3 = bnew(c); bf_sub(c, &T3, &pi, &Th); Th = T3; }
  bf_cpy(c, A, &Th); if (!A->z) A->s = (int)(uy >> 63);
  *B = 32 * c->n - 64 - 12;
  cw->p = mkw; c->p = mk;
  return 1;
}
/* q pi / 4 (q = 1..4) with the sign of y (the infinite / zero operand cases of fpatan) */
static int ev_piq(cx *c, cx *cw, const za *a, bf *A, int *B) {
  (void)cw;
  u32 *mk = c->p;
  bf pi = bnew(c), t = bnew(c);
  bf_pi(c, &pi);
  if (a->q == 3) { bf_mulu(c, &t, &pi, 3); t.e -= 2; } else { t = pi; t.e -= (a->q == 1) ? 2 : (a->q == 2) ? 1 : 0; }
  bf_cpy(c, A, &t);
  A->s = (int)(d2u(a->y) >> 63);
  *B = 32 * c->n - 64 - 4;
  c->p = mk;
  return 1;
}

static double ziv(int w, double x, double y, int q, u32 *fl) {
  u64 ux = d2u(x);
  int ex = (int)((ux >> 52) & 0x7ff) - 1022, exx = ex > 0 ? ex : 0;
  for (int n = 6;; n *= 2) {
    int nw = (w <= W_TAN) ? n + (exx >> 5) + 5 : 72;
    u32 arena[48 * n + 24 * nw];
    cx c, cw; c.n = n; c.p = arena; c.end = arena + 48 * n; cw.n = nw; cw.p = arena + 48 * n; cw.end = arena + 48 * n + 24 * nw;
    za a; a.x = x; a.y = y; a.w = w; a.q = q;
    bf A = bnew(&c); int B = 0, r;
    switch (w) {
    case W_SIN: case W_COS: case W_TAN: r = ev_trig(&c, &cw, &a, &A, &B); break;
    case W_2XM1: r = ev_exp(&c, &cw, &a, &A, &B); break;
    case W_YL2X: case W_YL2XP1: r = ev_log(&c, &cw, &a, &A, &B); break;
    case W_ATAN2: r = ev_atan(&c, &cw, &a, &A, &B); break;
    default: r = ev_piq(&c, &cw, &a, &A, &B); break;
    }
    if (r) { u64 out; if (bf_pack(&c, &A, B, r == 2, &out, fl)) return u2d(out); }
  }
}

/* ================= public: trigonometry ================= */
u32 fnvl_m_trig_big(double x) { u64 f = (d2u(x) >> 52) & 0x7ff; return f >= 1023 + 63 && f != 0x7ff; }
/* sin x, tan x for |x| < 2^-27 round to x: sin x in (x(1 - x^2/6), x), tan x in (x, x(1 + x^2/3)), and x^2/3 < 2^-55 < the half spacing/x of the neighbour
   on that side (>= 2^-54 relative); cos x in (1 - 2^-55, 1) rounds to 1 (spacing below 1 is 2^-53). */
static double small_arg(double x, u64 a, u32 *fl) { *fl |= PE | (a < 0x0010000000000000ull ? UE : 0); return x; }
double fnvl_m_sin(double x, u32 *fl) {
  u64 a = d2u(x) & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (a == EXPM) return invalid(fl);
  if (!a) return x;
  if (a < 0x3e40000000000000ull) return small_arg(x, a, fl);
  return ziv(W_SIN, x, 0.0, 0, fl);
}
double fnvl_m_tan(double x, u32 *fl) {
  u64 a = d2u(x) & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (a == EXPM) return invalid(fl);
  if (!a) return x;
  if (a < 0x3e40000000000000ull) return small_arg(x, a, fl);
  return ziv(W_TAN, x, 0.0, 0, fl);
}
double fnvl_m_cos(double x, u32 *fl) {
  u64 a = d2u(x) & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (a == EXPM) return invalid(fl);
  if (!a) return 1.0;
  if (a < 0x3e40000000000000ull) { *fl |= PE; return u2d(0x3ff0000000000000ull); }
  return ziv(W_COS, x, 0.0, 0, fl);
}
void fnvl_m_sincos(double x, double *s, double *cs, u32 *fl) { *s = fnvl_m_sin(x, fl); *cs = fnvl_m_cos(x, fl); }

/* ================= public: atan2 ================= */
/* x87 FPATAN table: the results of zero / infinite operands are 0 and multiples of pi/4, the others come from the engine */
double fnvl_m_atan2(double y, double x, u32 *fl) {
  u64 uy = d2u(y), ux = d2u(x), sy = uy & SGNM;
  if (isnan_(uy) || isnan_(ux)) return nan2(y, x, fl);
  int q;                                                    /* result = sign(y) q pi/4, q = 0 -> +-0 */
  int xneg = (int)(ux >> 63);
  if (iszero_(uy)) q = xneg ? 4 : 0;
  else if (iszero_(ux)) q = 2;
  else if (isinf_(uy)) q = isinf_(ux) ? (xneg ? 3 : 1) : 2;
  else if (isinf_(ux)) q = xneg ? 4 : 0;
  else return ziv(W_ATAN2, x, y, 0, fl);
  if (!q) return u2d(sy);
  return ziv(W_PIQ, x, y, q, fl);
}

/* ================= public: 2^x - 1 ================= */
double fnvl_m_2xm1(double x, u32 *fl) {
  u64 u = d2u(x), a = u & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (!a) return x;
  if (a == EXPM) return (u >> 63) ? u2d(0xBFF0000000000000ull) : x;     /* 2^-inf - 1 = -1, 2^inf - 1 = +inf */
  if (!(u >> 63) && a >= 0x4090000000000000ull) { *fl |= OE | PE; return u2d(EXPM); }                /* x >= 1024: >= 2^1024 - 1, past the half-way point to infinity */
  if (!(u >> 63) && a >= 0x4050000000000000ull) {                      /* x >= 64: integer: 2^x - 1 rounds to 2^x (1 < half the spacing 2^(x-54)); else the engine */
    u64 mant; int e2; unpack(a, &mant, &e2);
    if (e2 >= 0 || !(mant & ((1ull << -e2) - 1))) { int k = (int)(e2 >= 0 ? (mant << e2) : (mant >> -e2)); *fl |= PE; return u2d((u64)(k + 1023) << 52); }
  }
  if ((u >> 63) && a > 0x404e000000000000ull) { *fl |= PE; return u2d(0xBFF0000000000000ull); }   /* x < -60: -1 + 2^x rounds to -1 (2^x < 2^-60 < 2^-54) */
  return ziv(W_2XM1, x, 0.0, 0, fl);
}

/* ================= public: y log2(x), y log2(x+1) ================= */
/* x87 FYL2X / FYL2XP1 tables as y * log2(u) with log2(1) = +0, log2(0) = -inf (divide by zero when y is finite), log2(+inf) = +inf, u < 0 invalid; the
   sign of a zero log2(1+x) is that of x (as in the FYL2XP1 table) */
static double ylog_common(u64 uy, int lsgn, int linf, u32 *fl) {
  u64 sg = ((uy >> 63) ^ (u64)lsgn) ? SGNM : 0;
  if (linf) return iszero_(uy) ? invalid(fl) : u2d(sg | EXPM);        /* y * (+-inf) */
  return isinf_(uy) ? invalid(fl) : u2d(sg);                          /* y * (+-0) */
}
double fnvl_m_yl2x(double y, double x, u32 *fl) {
  u64 uy = d2u(y), ux = d2u(x);
  if (isnan_(uy) || isnan_(ux)) return nan2(y, x, fl);
  int xn = (int)(ux >> 63);
  if (!iszero_(ux) && xn) return invalid(fl);                     /* x < 0 incl. -inf */
  if (iszero_(ux)) { double r = ylog_common(uy, 1, 1, fl); if (!isinf_(uy) && !iszero_(uy)) *fl |= ZE; return r; }
  if (isinf_(ux)) return ylog_common(uy, 0, 1, fl);
  if (ux == 0x3ff0000000000000ull) return ylog_common(uy, 0, 0, fl);   /* x = 1 */
  int lneg = (ux >> 52) < 0x3ff;                                  /* x < 1 */
  if (isinf_(uy)) return ylog_common(uy, lneg, 1, fl);
  if (iszero_(uy)) return ylog_common(uy, lneg, 0, fl);
  return ziv(W_YL2X, x, y, 0, fl);
}
double fnvl_m_yl2xp1(double y, double x, u32 *fl) {
  u64 uy = d2u(y), ux = d2u(x), ax = ux & ~SGNM;
  if (isnan_(uy) || isnan_(ux)) return nan2(y, x, fl);
  int xn = (int)(ux >> 63);
  if (isinf_(ux)) return xn ? invalid(fl) : ylog_common(uy, 0, 1, fl);
  if (iszero_(ux)) return ylog_common(uy, xn, 0, fl);       /* log2(1 + +-0) = +-0 */
  if (xn && ax > 0x3ff0000000000000ull) return invalid(fl);        /* x < -1 */
  if (xn && ax == 0x3ff0000000000000ull) { double r = ylog_common(uy, 1, 1, fl); if (!isinf_(uy) && !iszero_(uy)) *fl |= ZE; return r; }
  if (isinf_(uy)) return ylog_common(uy, xn, 1, fl);
  if (iszero_(uy)) return ylog_common(uy, xn, 0, fl);
  return ziv(W_YL2XP1, x, y, 0, fl);
}

/* ================= public: fscale ================= */
/* ST0 * 2^trunc(ST1): x87 FSCALE table = IEEE scaleB with 0 * 2^+inf and inf * 2^-inf invalid */
double fnvl_m_scale(double x, double e, u32 *fl) {
  u64 ux = d2u(x), ue = d2u(e);
  if (isnan_(ux) || isnan_(ue)) return nan2(x, e, fl);
  int einf = isinf_(ue), eneg = (int)(ue >> 63);
  if (einf) {
    if ((iszero_(ux) && !eneg) || (isinf_(ux) && eneg)) return invalid(fl);
    if (iszero_(ux) || isinf_(ux)) return x;
    return eneg ? u2d(ux & SGNM) : u2d((ux & SGNM) | EXPM);          /* finite * 2^-inf = +-0, 2^+inf = +-inf (not tiny/overflow flags: the x87 reports none) */
  }
  if (iszero_(ux) || isinf_(ux)) return x;
  /* trunc(e) saturated to +-2^20 (any |e| >= 4400 already over/underflows every double) */
  u64 mant; int e2; unpack(ue & ~SGNM, &mant, &e2);
  long long t;
  if (!mant) t = 0;
  else if (e2 >= 0) t = (e2 >= 20 || (mant << e2) >= (1ull << 20)) ? (1 << 20) : (long long)(mant << e2);
  else if (-e2 >= 64) t = 0;
  else t = (long long)(mant >> -e2);
  if (t > (1 << 20)) t = 1 << 20;
  if (eneg) t = -t;
  u64 m; int xe; unpack(ux & ~SGNM, &m, &xe);
  return exact_double(m, xe + (int)t, (int)(ux >> 63), fl);
}

/* ================= public: fxtract ================= */
static double int2d(int v) {
  if (!v) return 0.0;
  u64 s = 0, a = (u64)v;
  if (v < 0) { s = SGNM; a = (u64)-v; }
  int L = 63 - __builtin_clzll(a);
  return u2d(s | ((u64)(L + 1023) << 52) | ((a << (52 - L)) & MANM));
}
void fnvl_m_xtract(double x, double *expo, double *sig, u32 *fl) {
  u64 u = d2u(x), a = u & ~SGNM;
  if (isnan_(u)) { double r = nan1(x, fl); *expo = r; *sig = r; return; }
  if (a == EXPM) { *expo = u2d(EXPM); *sig = x; return; }
  if (!a) { *expo = u2d(SGNM | EXPM); *sig = x; *fl |= ZE; return; }
  u64 m; int e2; unpack(a, &m, &e2);
  int L = 63 - __builtin_clzll(m);
  *expo = int2d(L + e2);
  *sig = u2d((u & SGNM) | ((u64)1023 << 52) | ((m << (52 - L)) & MANM));
}

/* ================= public: fprem, fprem1 ================= */
/* a = Ma 2^(ea-52), b = Mb 2^(eb-52) (53-bit normalised mantissas, ea, eb the exponents of the leading bits), D = ea - eb.
   D < 64 (complete, C2 = 0): Q = trunc (fprem) or round-to-nearest-even (fprem1) of a/b, a - Q b exact; C0 = Q2, C3 = Q0, C1 = Q1.
   D >= 64 (partial, C2 = 1, C0 = C1 = C3 = 0): the reference CPU (Intel family 6 model 207, verified in test/fmathtest.py) reduces by exactly K = 32 floor(D/32) - 32 bits:
   Q' = trunc(a / (b 2^K)), result a - b Q' 2^K = (Ma 2^N mod Mb) 2^(K + eb - 52), N = D - K = 32 + D mod 32 in [32, 63] (SDM: "N between 32 and 63"). The truncation also
   holds for fprem1 when partial. Special operands: NaN -> nan2; a infinite or b zero -> invalid; b infinite or a zero -> a. */
double fnvl_m_fprem(double a, double b, u32 near, u32 *cf, u32 *fl) {
  u64 ua = d2u(a), ub = d2u(b);
  *cf = 0;
  if (isnan_(ua) || isnan_(ub)) return nan2(a, b, fl);
  if (isinf_(ua) || iszero_(ub)) return invalid(fl);
  if (isinf_(ub) || iszero_(ua)) return a;
  u64 Ma, Mb; int ea2, eb2;
  unpack(ua & ~SGNM, &Ma, &ea2); unpack(ub & ~SGNM, &Mb, &eb2);
  int la = 63 - __builtin_clzll(Ma), lb = 63 - __builtin_clzll(Mb);
  int ea = ea2 + la, eb = eb2 + lb;                         /* exponents of the leading bits */
  Ma <<= 52 - la; Mb <<= 52 - lb;
  int D = ea - eb, sa = (int)(ua >> 63), neg = sa;
  u64 q = 0, r;
  if (D < 0) {
    if (!near || D < -1 || Ma <= Mb) return a;                /* |a/b| < 1/2 or a tie (even quotient 0) */
    q = 1; r = 2 * Mb - Ma; neg ^= 1;                         /* |b| - |a| in units 2^(ea-52) */
    *cf = (q & 1 ? 0x4000u : 0) | (q & 2 ? 0x200u : 0) | (q & 4 ? 0x100u : 0);
    return exact_double(r, ea - 52, neg, fl);
  }
  int N = D < 64 ? D : 32 + (D & 31), K = D < 64 ? 0 : D - N;
  r = Ma;
  for (int i = 0; i <= N; i++) {
    int ge = r >= Mb;
    q = (q << 1) | (u64)ge;
    if (ge) r -= Mb;
    if (i < N) r <<= 1;
  }
  if (D >= 64) { *cf = 0x400; return exact_double(r, K + eb - 52, sa, fl); }
  if (near && (2 * r > Mb || (2 * r == Mb && (q & 1)))) { q++; r = Mb - r; neg ^= 1; }
  *cf = (q & 1 ? 0x4000u : 0) | (q & 2 ? 0x200u : 0) | (q & 4 ? 0x100u : 0);
  return exact_double(r, eb - 52, neg, fl);
}
