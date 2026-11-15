/* fmath: correctly rounded x87 transcendentals and exact fprem/fprem1/fscale/fxtract (README "One reference CPU model", R8), and the msvcrt math library (acos asin atan cosh sinh tanh exp log pow, exact fmod sqrt).
   Freestanding C (no libc, libm, malloc, errno, FPU instruction): doubles are only moved as bit patterns, every computation is integer arithmetic, so the
   host rounding mode and FPSR are irrelevant. Builds natively (x86-64, for test/fmathtest.py) and as aarch64 ILP32.

   RULE (one engine for sin cos tan atan2 2^x-1 y*log2(x) y*log2(x+1)): ZIV'S STRATEGY on multi-precision binary floats.
     1. Special operands (zeros, infinities, NaN, out of domain) and the EXPLICITLY ENUMERATED EXACT CASES are decided before the loop by the x87 table / by
        exact integer arithmetic (listed per function below).
     2. For every other input the true value v is irrational (even transcendental: Lindemann-Weierstrass for sin/cos/tan/2^x-1 of a non-zero algebraic,
        atan2 of a rational pair = argument of an algebraic number, Gelfond-Schneider for log2 of a rational that is not a power of two; times a non-zero
        rational y). So v is never a double and never a midpoint of two neighbouring doubles.
     3. For n = 4, 8, 16, ... limbs of 32 bits the evaluator produces A and an integer B with |A - v| <= 2^-B |v| (proved per evaluator, below).
        The interval [A(1-2^(2-B)), A(1+2^(2-B))] contains v. If it lies inside ONE half-ulp cell of the binary64 grid (same binade, same cell index of the
        half-ulp lattice; subnormals and the overflow boundary are part of the lattice), v rounds (nearest-even) to the double of that cell, which is the
        answer; otherwise n doubles. Termination: v is not on the lattice, so some finite n puts it strictly inside a cell with room to spare, and the
        interval shrinks to v (B grows with n). There is no cap and no give-up path.
   BIG FLOATS: value = (-1)^s m 2^(e-32n), m an n-limb integer with its top bit set (so |value| in [2^(e-1), 2^e)); e is an int (range far beyond binary64).
     Every primitive truncates (toward zero) and its absolute error is <= 2^(emax-32n+1), emax = the larger operand exponent (bf_mul, bf_div, bf_*_u32:
     relative error <= 2^(2-32n)); bf_add of numbers whose bits fit the window is exact.
     Series (Taylor, Euler, atanh, Machin) are summed term by term until the term is below 2^-(32n+4) of the sum (tail <= 2 terms): error bound GUARD(n) below.
     The constants pi and ln 2 come from a generated table (n <= 40 limbs, truncated: relative error < 2^(2-32n): ASM-230) or from the series (n > 40). ASM-232
     recorded: n <= 2^15 limbs (4 MiB of mantissa), which no double input approaches (hard cases need ~ 200 bits).
   WIDE ARGUMENT STEPS are done in a second context cw with more limbs so that they are exact or carry an explicit absolute bound.
   PER FUNCTION (exact cases handled before the loop / evaluator and its error budget):
     sin cos tan: x = 0, |x| < 2^-27 (sin, tan -> x; cos -> 1: proved at fnvl_m_sin) and non-finite operands by the table; else |x| = k pi/2 + r with the exact
       integer k and pi/2 to 32 nw bits (nw = n + e_x/32 + 5, so the absolute error of r is <= 2^(-32n-63) for EVERY finite x, whatever |x|; r.e from the computed r
       gives the relative error, r = 0 -> retry); sin/cos series of r (|r| < 1, no cancellation), tan = sin/cos; quadrant from k mod 4.
     atan2: zeros / infinities (+-0, +-pi, +-pi/2, +-pi/4, +-3pi/4 by the SDM table, pi from the engine); else z = min/max in (0,1], j = round(8z), atan z = atan(j/8) +
       atan(w) with w formed from EXACT numerator 8P - jQ and denominator 8Q + jP (cw holds every bit of a double), |w| <= 1/16 + 2^-30.
     2^x - 1: x = 0, +-inf, x >= 1024 (inf), integer x >= 64 (2^x - 1 rounds to 2^x), x < -60 (-1), integer -60 <= x < 64 (exact bf of 2^x - 1, no series); else
       2^k expm1(f ln 2) + (2^k - 1), k = nearest integer, f = x - k exact.
     y log2(x), y log2(x+1): the table of FYL2X / FYL2XP1 for zero / infinite / NaN / out-of-domain operands and x = 1 (or x = +-0); u = x or 1 + x is formed EXACTLY in cw
       (every bit between 2^-1074 and 2^1025 fits in 72 limbs), u = 2^k w, z = w - 1 exact; u a power of two (z = 0) gives y k exactly; else ln w = 2 atanh(z/(2+z)).
   MSVCRT LIBRARY FUNCTIONS (the 15 _CI* intrinsics, rt/msvcrt_ci.cc): acos asin atan cosh sinh tanh exp log pow by the same engine (start limb count n >= 2), fmod and sqrt exact. Same rule, same steps 1-3.
     Irrationality (step 2): for a non-zero algebraic x, e^x (Hermite-Lindemann) and so exp, sinh, cosh, tanh (sinh x = s algebraic gives e^x a root of E^2 - 2 s E - 1, cosh and tanh likewise) are transcendental;
       ln x for algebraic x > 0, x != 1 is transcendental (e^(ln x) = x algebraic); asin, acos, atan: sin / cos / tan of a non-zero algebraic number is transcendental, and tan(atan x) = x, sin(asin x) = x, cos(acos x) = x
       are algebraic, so the angle is not algebraic: never a double or half-way point (the lattice of step 3 contains only dyadic rationals). Exact cases decided before the loop: exp 0 -> 1, log 1 -> +0, acos 1 -> +0,
       asin / atan / sinh / tanh 0 -> x, cosh 0 -> 1 (the only arguments with an algebraic value; everything else is transcendental, hence off the lattice).
     Out-of-range and tiny arguments are decided by proved bounds, never by sampling: sin-like |x| < 2^-27: atan x, tanh x in (x (1 - x^2/3), x) and asin x, sinh x in (x, x (1 + x^2/3)) round to x (x^2/3 < 2^-55 is below
       the half-spacing 2^-54 |x| of the nearer neighbour); cosh |x| < 2^-26: 1 < cosh x < 1 + 2^-53, rounds to 1 (below the half-way point); exp: 0 < x < 2^-54: 1 < e^x < 1 + 2^-53 -> 1; -2^-55 < x < 0: 1 - 2^-55 < e^x < 1 -> 1
       (above the half-way point 1 - 2^-54); x >= 710: e^x > 2^1024 -> inf; x <= -746: e^x < 2^-1076 -> +0; sinh / cosh |x| >= 711 > asinh(2^1024) = 710.4758: inf; tanh |x| >= 20: 1 - |tanh x| < 2 e^-40 < 2^-54: rounds to 1.
     atan: atan x = atan2(x, 1) of the existing evaluator. asin / acos (ev_asin, ev_acos): asin |x| = atan(|x| / sqrt((1 - |x|)(1 + |x|))), acos x = 2 atan(sqrt((1 - x)/(1 + x))), asin(+-1) = +-pi/2, acos(-1) = pi; the
       bf_sqrt (Newton from a 32-bit integer square root, fixed iteration count, relative error <= 2^(4-32n)) and atan_core (pi/2 - atan(1/z) for z >= 1, atan(j/8) + atan(w), j = round(8 z), |w| <= 1/16 + d otherwise) carry
       the error budget derived at each function: B = 32 n - GUARD(n) - 12, worst measured error / budget ~ 2e-10.
     exp sinh cosh tanh (ev_hyp): X = |x| (tanh: 2|x|), k = round(X / ln 2) and R = X - k ln 2 in a context with n + 4 limbs (abs error <= 2^(-32n-51), then 2^(1-32n) from the truncation to n limbs), e^R by the series expm1;
       exp = 2^(+-k)(1 + expm1(+-R)); for k = 0 the hyperbolics use E = expm1(X) in positive-term forms (sinh = E(E+2)/(2(E+1)), cosh = 1 + E^2/(2(E+1)), tanh = E/(E+2)), for k >= 1 Y = e^X >= 1.41 and
       sinh = (Y - 1/Y)/2, cosh = (Y + 1/Y)/2, tanh = (Y - 1)/(Y + 1) (cancellation <= 2 bits). Overflow / underflow / subnormal results: the lattice of step 3.
     log (ln_core): |x| = 2^k w, w in [3/4, 3/2), z = w - 1 = N 2^-s formed EXACTLY from the 53-bit mantissa (no wide context), ln w = 2 atanh(z/(2+z)), ln|x| = k ln 2 + ln w (cancellation <= 2.5 bits).
     pow (ev_pow, pow_exact): C99 Annex F table for zero / infinite / NaN operands, |x| = 1, y = 0 and a negative base (sign by the parity of the integer y, non-integer y: invalid); then
       EXACT CASES. x = m 2^e (m odd), |y| = a 2^f (a odd), j = max(-f, 0). x^y is a rational number iff 2^j | e and m = r^(2^j) (x^y = (x^a')^(1/2^j) with a' = a or |y|; a q-th root of an integer power is rational iff every
       prime exponent of the radicand is divisible by q: the exponent of 2 is e a', of an odd prime p it is a' v_p(m), and gcd(a', 2^j) = 1); then x^y = r^(+-a') 2^(+-e a'/2^j). It is a dyadic rational iff y > 0 or r = 1
       (r > 1 and y < 0 gives an odd denominator > 1). Points of the rounding lattice (doubles, half-way points, the overflow / underflow boundaries) are dyadic rationals whose odd part has <= 54 bits (r^a' < 2^64 is tested
       with an overflow-checked product; j <= 5 since r >= 3 and m < 2^53; a' <= 64), so every other value (irrational, non-dyadic rational, r^a' >= 2^64) is off the lattice and the loop terminates for it. The enumerated cases
       are therefore exactly: r = 1 (x = 2^e, 2^j | e: v = 2^N, N = (e/2^j) y exact, saturated to +-2^30 where the result is inf / 0 anyway) and r >= 3, y > 0 (v = r^a' 2^(e a'/2^j) < 2^64 2^S); each is rounded once by
       exact_double (ties, subnormals, overflow, flags). Others: y ln|x| = T with relative error <= 2^-(B_L - 1) (ev_pow comment), |T| >= 2^11 decided by the region (inf / 0), |T| < 2^-59 -> 1 (round down / up), else
       exp(T) as above with B = 32 n - GUARD(n) - 15 - max(T.e, 0).
     fmod: exact long division (the remainder is a double: |r| < |y| and r a multiple of the ulp of y or x). sqrt: integer square root of m 2^56 (55 bits) with a sticky bit, rounded once (comment at fnvl_m_sqrt).
   EXACT FORMS (no engine): fscale = integer exponent shift with one rounding (exact_double); fxtract = bit fields; fprem / fprem1 = long division of 53-bit
     mantissas, C0 = Q2, C3 = Q1, C1 = Q0, C2 = 0 for D = exponent difference < 64; D >= 64: C2 = 1 and the reduction by exactly 32 floor(D/32) - 32 bits (the reference CPU, below).
   FLAGS: IE, ZE, OE, UE (tiny before rounding and inexact), PE and C1 (= rounded up in magnitude) are derived from the same cell the rounding uses.
   Memory: VLAs on the stack, 4 (48 n + 24 nw) bytes (n = 4, nw = 72: 7.6 KB), released every iteration. No static state: reentrant, thread safe. */
#pragma GCC optimize("no-tree-loop-distribute-patterns")      /* no memset/memcpy calls: this file links without libc */
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
#define C1 0x200u                  /* x87 C1 = the result was rounded up in magnitude (set with PE) */
static inline u64 d2u(double d) { union { double d; u64 u; } v; v.d = d; return v.u; }
static inline double u2d(u64 u) { union { double d; u64 u; } v; v.u = u; return v.d; }

/* ================= big floats ================= */
typedef struct { int n; u32 *p; u32 *end; } cx;
typedef struct { u32 *m; int e; int s; int z; } bf;
static bf bnew(cx *c) {
  bf r; r.m = c->p; c->p += c->n; r.e = 0; r.s = 0; r.z = 1;
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
/* GUARD(n): bits lost to the primitive errors of a series evaluation at n limbs. Each series has N <= 32 n terms (every term gains >= 1.2 bits: the slowest
   are Euler's atan(j/8), ratio <= 49/113, and atanh(1/3)); term k carries <= 4k primitive errors of 2^(2-32n) relative and the sum <= N additions of 2^(1-32n), and the
   terms fall geometrically, so the total relative error is <= (2N + 64) 2^-32n times the cancellation factor (<= 8, counted separately in each evaluator);
   2N + 64 <= 2^(bitlen(32 n) + 7), so 12 + 2 bitlen(32 n) bits are a safe (twice too generous) guard. test/fmathtest.py checks the claimed bounds against MPFR. */
static int bitlen_(unsigned v) { int b = 0; while (v) { b++; v >>= 1; } return b; }
#define GUARD(n) (12 + 2 * bitlen_(32u * (unsigned)(n)))
/* convert a bf of context cw (nw limbs) into context c */
static void bf_conv(cx *c, int nw, bf *r, const bf *a) { if (a->z) bzero_(r); else bf_nw(c, r, a->m, nw, a->e, a->s); }
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
/* pi/4 and ln 2 as N-limb normalised mantissas (little endian limbs, value = m 2^-(32 N) times 2^2 resp. 2^0), TRUNCATED, N = 40 (1280 bits).
   Generated by test/fmathtest.py --gen-consts (MPFR); test/fmathtest.py checks them (and the Machin / atanh series that replace them for n > 40) against MPFR at every limb count.
   Sufficiency: for n <= 40 the table gives the constant with relative error < 2^(2-32n), the same bound as a computed one; for n > 40 the series are used. */
#define FM_TN 40
static const u32 PI4_T[FM_TN] = {
  0x7096966du, 0x9ed52907u, 0x208552bbu, 0x1c62f356u, 0xdca3ad96u, 0x83655d23u,
  0xfd24cf5fu, 0x69163fa8u, 0x1c55d39au, 0x98da4836u, 0xa163bf05u, 0xc2007cb8u,
  0xece45b3du, 0x49286651u, 0x7c4b1fe6u, 0xae9f2411u, 0x5a899fa5u, 0xee386bfbu,
  0xf406b7edu, 0x0bff5cb6u, 0xa637ed6bu, 0xf44c42e9u, 0x625e7ec6u, 0xe485b576u,
  0x6d51c245u, 0x4fe1356du, 0xf25f1437u, 0x302b0a6du, 0xcd3a431bu, 0xef9519b3u,
  0x8e3404ddu, 0x514a0879u, 0x3b139b22u, 0x020bbea6u, 0x8a67cc74u, 0x29024e08u,
  0x80dc1cd1u, 0xc4c6628bu, 0x2168c234u, 0xc90fdaa2u,
};
static const u32 LN2_T[FM_TN] = {
  0x1400b396u, 0x7d2e23deu, 0xfc1efa15u, 0xee569d6du, 0x8fe551a2u, 0x610d30f8u,
  0xfb5bfb90u, 0x07f4ca11u, 0x0f3fd5c6u, 0xda2d97c5u, 0x2f20e3a2u, 0x655fa187u,
  0x38303248u, 0xf5dfa6bdu, 0x9d6548cau, 0x72ce87b1u, 0x7657f74bu, 0x256fa0ecu,
  0xb136603bu, 0xb9ea9bc3u, 0x317c387eu, 0x1acbda11u, 0x224ae8c5u, 0x3e96ca16u,
  0x1169b825u, 0x27573b29u, 0xc1382144u, 0xed2eae35u, 0x4afa1b10u, 0x559552fbu,
  0x6debac98u, 0xe7b87620u, 0x8baafa2bu, 0x8a0d175bu, 0x7298b62du, 0x40f34326u,
  0x03f2f6afu, 0xc9e3b398u, 0xd1cf79abu, 0xb17217f7u,
};
#ifdef FM_DEBUG
int fm_notable;
#else
#define fm_notable 0
#endif
static void bf_table(cx *c, bf *out, const u32 *T, int e) {
  for (int i = 0; i < c->n; i++) out->m[i] = T[FM_TN - c->n + i];
  out->e = e; out->s = 0; out->z = 0;
}
/* pi (table for n <= 40 limbs, else Machin: 16 atan 1/5 - 4 atan 1/239) */
static void bf_pi(cx *c, bf *out) {
  if (c->n <= FM_TN && !fm_notable) { bf_table(c, out, PI4_T, 2); return; }
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
  if (c->n <= FM_TN && !fm_notable) { bf_table(c, out, LN2_T, 0); return; }
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
  int tiny = lo.e - 1 < -1022;
  if (tiny != (hi.e - 1 < -1022)) return 0;                  /* the interval straddles 2^-1022: the underflow flag is not decided yet */
  u64 M; u32 f = 0; u64 bits;
  if (ul == 0x7fffffff) { bits = EXPM; f = OE | PE | C1; }
  else {
    int inexact = !exact || !(((ql & 1) == 0) && !rl);
    if (ql & 1) {
      if (exact && !rl) { M = (ql + 1) >> 1; if (M & 1) M -= 1; }       /* tie: the even neighbour */
      else M = (ql + 1) >> 1;
    } else M = ql >> 1;
    bits = ((u64)(ul + 1074) << 52) + M;
    if (bits >= EXPM) { bits = EXPM; f = OE | PE | C1; }
    else if (inexact) { f = PE | (((ql & 1) && M == ((ql + 1) >> 1)) ? C1 : 0); if (tiny) f |= UE; }
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
  x |= FQ; y |= FQ;                                          /* both quieted first (an x87 register holds the quiet NaN of a loaded signalling one) */
  if (na && nb) { u64 ma = x & (FQ * 2 - 1), mb = y & (FQ * 2 - 1); return u2d(ma > mb ? x : mb > ma ? y : (x >> 63 ? y : x)); }
  return u2d(na ? x : y);
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
enum { W_SIN, W_COS, W_TAN, W_2XM1, W_YL2X, W_YL2XP1, W_ATAN2, W_PIQ, W_ACOS, W_ASIN, W_EXP, W_SINH, W_COSH, W_TANH, W_LOG, W_POW };
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
    bf_conv(c, nw, &R, &Rw);
    cw->p = mkw;
  }
  if (br > 32 * n - GUARD(n)) br = 32 * n - GUARD(n);
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
  *B = 32 * c->n - GUARD(c->n) - 8;
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
  bf_conv(c, cw->n, &Z, &N);
  bf_u64(c, &two, 1, 1, 0); bf_add(c, &D, &Z, &two); bf_div(c, &Sx, &Z, &D);
  ser_atanh2(c, &Lw, &Sx);
  bf_ln2(c, &L2); bf_div(c, &Q, &Lw, &L2);
  bf_add(c, &V, &Kb, &Q);
  bf_mul(c, A, &Y, &V);
  *B = 32 * c->n - GUARD(c->n) - 12;
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
  bf_conv(c, cw->n, &Pm, Pw); bf_conv(c, cw->n, &Qm, Qw);
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
  bf_conv(c, cw->n, &Wn, &Num); bf_conv(c, cw->n, &Wd, &Den);
  bf_div(c, &Wv, &Wn, &Wd);
  bf_pi(c, &pi); pi2 = pi; pi2.e -= 1;
  if (j == 0) bzero_(&Ac); else if (j == 8) { Ac = pi; Ac.e -= 2; } else ser_atan_c(c, &Ac, j);
  if (Wv.z) bzero_(&Aw); else ser_atan(c, &Aw, &Wv);
  bf_add(c, &Th, &Ac, &Aw);
  if (swap) { bf T2 = bnew(c); bf_sub(c, &T2, &pi2, &Th); Th = T2; }
  if (ux >> 63) { bf T3 = bnew(c); bf_sub(c, &T3, &pi, &Th); Th = T3; }
  bf_cpy(c, A, &Th); if (!A->z) A->s = (int)(uy >> 63);
  *B = 32 * c->n - GUARD(c->n) - 12;
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
  *B = 32 * c->n - GUARD(c->n) - 4;
  c->p = mk;
  return 1;
}

/* ================= msvcrt library functions: evaluators (acos asin atan cosh sinh tanh exp log pow) ================= */
/* floor(sqrt(v)) */
static u32 isqrt64(u64 v) {
  u64 res = 0, bit = 1ull << 62;
  while (bit > v) bit >>= 2;
  while (bit) { if (v >= res + bit) { v -= res + bit; res = (res >> 1) + bit; } else res >>= 1; bit >>= 2; }
  return (u32)res;
}
/* sqrt(a), a > 0. a = f 2^g with g even and f in [1/4, 1). y0 = isqrt(top 63..64 bits of f) 2^-32 has relative error <= 2^-30; Newton y <- (y + f/y)/2 squares the error
   (e' <= e^2/2) so after the iterations below (accuracy bits acc -> 2 acc - 1 from 30 until acc >= 32 n + 8) the quadratic remainder is <= 2^(-32n-8); every iteration
   adds the primitive errors of one bf_div and one bf_add (<= 2^(3-32n)) which later iterations square away, the last one stays: relative error of the result <= 2^(4-32n). */
static void bf_sqrt(cx *c, bf *out, const bf *a) {
  int n = c->n; u32 *mk = c->p;
  bf f = bnew(c), y = bnew(c), t = bnew(c), s = bnew(c);
  int g = (a->e & 1) ? a->e + 1 : a->e;
  bf_cpy(c, &f, a); f.e = a->e - g;
  u64 T = ((u64)f.m[n - 1] << 32) | f.m[n - 2], V = (a->e & 1) ? T >> 1 : T;
  bf_u64(c, &y, isqrt64(V), -32, 0);
  for (int acc = 30; acc < 32 * n + 8; acc = 2 * acc - 1) {
    bf_div(c, &t, &f, &y); bf_add(c, &s, &y, &t); s.e -= 1; bf_cpy(c, &y, &s);
  }
  bf_cpy(c, out, &y); out->e += g / 2; c->p = mk;
}
/* atan(z) of a bf z > 0 with relative error d: z >= 1 -> pi/2 - atan(1/z); z in (0,1): j = round(8 z), atan z = atan(j/8) + atan(w), w = (8z - j)/(8 + j z), |w| <= 1/16 + d.
   Errors: 1/z: 2^(2-32n); 8z - j has absolute error <= 8 z d' + 2^(4-32n) (d' = d + 2^(2-32n)), so w has absolute error <= d' + 2^(5-32n) and so has atan w; the sum atan(c) + atan(w) is >= atan(1/16) > 2^-4
   for j >= 1 and equals atan w = atan z (relative error <= d') for j = 0, and pi/2 - atan(1/z) >= pi/4. Result relative error <= 16 d' + 2^(10-32n) + the series (GUARD). */
static void atan_core(cx *c, bf *out, const bf *z0) {
  u32 *mk = c->p;
  bf z = bnew(c), one = bnew(c), t8 = bnew(c), half = bnew(c), H = bnew(c), Jf = bnew(c), Jb = bnew(c), Wn = bnew(c), Wd = bnew(c), Jp = bnew(c), eight = bnew(c);
  bf Wv = bnew(c), Ac = bnew(c), Aw = bnew(c), Th = bnew(c), pi = bnew(c);
  int inv = z0->e >= 1;
  bf_u64(c, &one, 1, 0, 0);
  if (inv) bf_div(c, &z, &one, z0); else bf_cpy(c, &z, z0);
  t8 = z; t8.e += 3;
  bf_u64(c, &half, 1, -1, 0); bf_add(c, &H, &t8, &half); bf_floor(c, &Jf, &H);
  u32 j = bf_small(&Jf, c->n);
  if (j > 8) j = 8;
  bf_u64(c, &Jb, j, 0, 0); bf_sub(c, &Wn, &t8, &Jb);
  bf_u64(c, &eight, 8, 0, 0); bf_mulu(c, &Jp, &z, j); bf_add(c, &Wd, &eight, &Jp);
  bf_div(c, &Wv, &Wn, &Wd);
  bf_pi(c, &pi);
  if (j == 0) bzero_(&Ac); else if (j == 8) { bf_cpy(c, &Ac, &pi); Ac.e -= 2; } else ser_atan_c(c, &Ac, j);
  if (Wv.z) bzero_(&Aw); else ser_atan(c, &Aw, &Wv);
  bf_add(c, &Th, &Ac, &Aw);
  if (inv) { bf T2 = bnew(c); pi.e -= 1; bf_sub(c, &T2, &pi, &Th); bf_cpy(c, out, &T2); }
  else bf_cpy(c, out, &Th);
  c->p = mk;
}
/* asin x, 0 < |x| <= 1. |x| = 1: pi/2. else u = |x| / sqrt((1 - |x|)(1 + |x|)), asin x = sign atan u (u = tan(asin |x|)). 1 -+ |x| carry relative error <= 2^(2-32n) (bf_add: both operands are
   doubles, so the sum / difference of numbers of binade distance < 2^7 is exact and otherwise the larger operand is 1), the product 2^(2-32n), bf_sqrt 2^(4-32n) + half the input's, the division
   2^(2-32n): d <= 2^(6-32n) in u; atan_core turns that into <= 2^(11-32n) + the series: B = 32 n - GUARD(n) - 12 covers it with 24+ bits to spare. */
static int ev_asin(cx *c, cx *cw, const za *a, bf *A, int *B) {
  (void)cw;
  u64 ux = d2u(a->x), mant; int e2, n = c->n;
  unpack(ux & ~SGNM, &mant, &e2);
  u32 *mk = c->p;
  bf X = bnew(c), one = bnew(c), om = bnew(c), op = bnew(c), pr = bnew(c), sq = bnew(c), u = bnew(c), th = bnew(c);
  if ((ux & ~SGNM) == 0x3ff0000000000000ull) { bf_pi(c, &th); th.e -= 1; }
  else {
    bf_u64(c, &X, mant, e2, 0); bf_u64(c, &one, 1, 0, 0);
    bf_sub(c, &om, &one, &X); bf_add(c, &op, &one, &X); bf_mul(c, &pr, &om, &op);
    bf_sqrt(c, &sq, &pr); bf_div(c, &u, &X, &sq);
    atan_core(c, &th, &u);
  }
  bf_cpy(c, A, &th); A->s = (int)(ux >> 63);
  *B = 32 * n - GUARD(n) - 12;
  c->p = mk;
  return 1;
}
/* acos x, -1 <= x < 1 (x = 1 is 0, decided before). x = -1: pi. else acos x = 2 atan(sqrt((1 - x)/(1 + x))) (tan(acos(x)/2)); same error budget as asin. */
static int ev_acos(cx *c, cx *cw, const za *a, bf *A, int *B) {
  (void)cw;
  u64 ux = d2u(a->x), mant; int e2, n = c->n;
  unpack(ux & ~SGNM, &mant, &e2);
  u32 *mk = c->p;
  bf X = bnew(c), one = bnew(c), om = bnew(c), op = bnew(c), q = bnew(c), s = bnew(c), th = bnew(c);
  if (ux == 0xBFF0000000000000ull) { bf_pi(c, &th); }
  else {
    bf_u64(c, &X, mant, e2, (int)(ux >> 63)); bf_u64(c, &one, 1, 0, 0);
    bf_sub(c, &om, &one, &X); bf_add(c, &op, &one, &X); bf_div(c, &q, &om, &op);
    bf_sqrt(c, &s, &q);
    atan_core(c, &th, &s); th.e += 1;
  }
  bf_cpy(c, A, &th);
  *B = 32 * n - GUARD(n) - 12;
  c->p = mk;
  return 1;
}
/* e^X for X > 0 (X < 2^13): k = round(X / ln 2) and R = X - k ln 2 in cw (nw = n + 4 limbs). Any integer k with |R| < 1 would do (the series below only need it). Errors: K < 2^13 and
   ln 2 (table or series: relative error <= 2^(64-32nw)) give an absolute error of K ln 2 <= 2^(13+64-32nw) = 2^(-32n-51), the product and the subtraction add <= 2^(15-32nw); then R is truncated to n limbs
   (absolute error <= 2^(1-32n)). e^R carries the same absolute error, i.e. relative error <= 2^(2-32n) after the factor e^|R| <= 1.45. k = 0: R = X exactly. */
static int exp_reduce(cx *c, cx *cw, const bf *X, bf *R) {
  int nw = cw->n; u32 *mkw = cw->p;
  bf Xw = bnew(cw), L2 = bnew(cw), Z = bnew(cw), H = bnew(cw), K = bnew(cw), half = bnew(cw), P = bnew(cw), Rw = bnew(cw);
  bf_conv(cw, c->n, &Xw, X);
  bf_ln2(cw, &L2); bf_div(cw, &Z, &Xw, &L2);
  bf_u64(cw, &half, 1, -1, 0); bf_add(cw, &H, &Z, &half); bf_floor(cw, &K, &H);
  int k = (int)bf_small(&K, nw);
  bf_mul(cw, &P, &K, &L2); bf_sub(cw, &Rw, &Xw, &P);
  bf_conv(c, nw, R, &Rw);
  cw->p = mkw;
  return k;
}
/* exp x, sinh x, cosh x, tanh x for finite x (|x| < 746 exp, < 711 sinh/cosh, < 20 tanh; sinh/tanh |x| >= 2^-27, cosh |x| >= 2^-26: the tiny arguments are decided before).
   X = |x| (tanh: 2|x|), k, R as above. exp: e^x = 2^(+-k) (1 + expm1(+-R)) (sign of x), a sum of terms 1 and expm1(R) >= -0.30: no cancellation beyond 1 bit.
   Hyperbolics with X = |x| (tanh: X = 2|x|, tanh = (e^X - 1)/(e^X + 1)): k = 0 (X < 0.35): E = expm1(X) > 0 and sinh = E (E + 2) / (2 (E + 1)), cosh = 1 + E^2 / (2 (E + 1)), tanh = E / (E + 2): only
   products, quotients and sums of positive numbers (error <= 8 primitive errors). k >= 1 (e^X = Y = 2^k (1 + expm1(R)) >= 1.41): sinh = (Y - 1/Y)/2, cosh = (Y + 1/Y)/2, tanh = (Y - 1)/(Y + 1); Y - 1/Y >= 0.7 Y
   and Y - 1 >= 0.41 Y: cancellation <= 2 bits. Every relative error <= (series 2^(GUARD - 32n)/16 + 40 primitive errors 2^(2-32n)): B = 32 n - GUARD(n) - 12 (exp: -8). */
static int ev_hyp(cx *c, cx *cw, const za *a, bf *A, int *B) {
  int n = c->n, w = a->w;
  u64 u = d2u(a->x), mant; int e2, sx = (int)(u >> 63);
  unpack(u & ~SGNM, &mant, &e2);
  if (w == W_TANH) e2 += 1;
  u32 *mk = c->p;
  bf X = bnew(c), R = bnew(c), E = bnew(c), one = bnew(c), Y = bnew(c), t1 = bnew(c), t2 = bnew(c), t3 = bnew(c);
  bf_u64(c, &X, mant, e2, 0); bf_u64(c, &one, 1, 0, 0);
  int k = exp_reduce(c, cw, &X, &R);
  if (w == W_EXP) {
    if (sx) { k = -k; if (!R.z) R.s ^= 1; }
    ser_expm1(c, &E, &R); bf_add(c, &Y, &E, &one); Y.e += k;
    bf_cpy(c, A, &Y); *B = 32 * n - GUARD(n) - 8;
    c->p = mk; return 1;
  }
  if (k == 0) {
    ser_expm1(c, &E, &X);
    if (w == W_TANH) { bf_u64(c, &t2, 2, 0, 0); bf_add(c, &t1, &E, &t2); bf_div(c, A, &E, &t1); }
    else {
      bf_add(c, &t1, &E, &one); t1.e += 1;                                   /* 2 (E + 1) */
      if (w == W_SINH) { bf_u64(c, &t2, 2, 0, 0); bf_add(c, &t3, &E, &t2); bf_mul(c, &t2, &E, &t3); bf_div(c, A, &t2, &t1); }
      else { bf_mul(c, &t2, &E, &E); bf_div(c, &t3, &t2, &t1); bf_add(c, A, &one, &t3); }
    }
  } else {
    ser_expm1(c, &E, &R); bf_add(c, &Y, &E, &one); Y.e += k;
    if (w == W_TANH) { bf_sub(c, &t1, &Y, &one); bf_add(c, &t2, &Y, &one); bf_div(c, A, &t1, &t2); }
    else {
      bf_div(c, &t1, &one, &Y);
      if (w == W_SINH) bf_sub(c, &t2, &Y, &t1); else bf_add(c, &t2, &Y, &t1);
      bf_cpy(c, A, &t2); A->e -= 1;
    }
  }
  if (!A->z) A->s = (w == W_COSH) ? 0 : sx;
  *B = 32 * n - GUARD(n) - 12;
  c->p = mk;
  return 1;
}
/* ln |x| for a finite double x, |x| != 1 into *L (n limbs): |x| = M 2^e2 with M in [2^52, 2^53) normalised; |x| = 2^k w with w in [3/4, 3/2) (k = floor(log2|x|) or one more), z = w - 1 = N 2^-s
   (N = M - 2^s, s = 52 or 53) is an exact small integer times a power of two, ln w = 2 atanh(z / (2 + z)), |z/(2+z)| <= 1/5; ln|x| = k ln 2 + ln w. For k != 0, |ln|x|| >= ln 2 - ln(4/3) >= 0.4 against
   |k ln 2| <= 0.7 |k|... (k = -1: w < 3/2 so ln|x| <= -0.28; the sum k ln2 + ln w has |.| >= 0.28 and the terms <= 0.7 |k|, k = 1: >= 0.40; larger |k|: ln w is below 0.41, the sum is >= 0.28 |k|): cancellation <= 2.5 bits.
   N = 0 (|x| a power of two, k != 0): ln|x| = k ln 2. Relative error <= 2^-(32 n - GUARD(n) - 12) (the existing ev_log budget). */
static void ln_core(cx *c, u64 ux, bf *L) {
  int n = c->n; (void)n;
  u64 mant; int e2; unpack(ux & ~SGNM, &mant, &e2);
  int lz = __builtin_clzll(mant) - 11;
  mant <<= lz; e2 -= lz;
  int Lx = e2 + 52, k, s;
  if (mant >= 0x18000000000000ull) { k = Lx + 1; s = 53; } else { k = Lx; s = 52; }
  s64 N = (s64)mant - (s64)(1ull << s);
  u32 *mk = c->p;
  bf Z = bnew(c), D = bnew(c), Sx = bnew(c), Lw = bnew(c), L2 = bnew(c), Kb = bnew(c), Q = bnew(c), two = bnew(c);
  bf_ln2(c, &L2);
  bf_u64(c, &Kb, (u64)(k < 0 ? -k : k), 0, k < 0);
  bf_mul(c, &Q, &Kb, &L2);
  if (N == 0) { bf_cpy(c, L, &Q); c->p = mk; return; }
  bf_u64(c, &Z, (u64)(N < 0 ? -N : N), -s, N < 0);
  bf_u64(c, &two, 1, 1, 0); bf_add(c, &D, &Z, &two); bf_div(c, &Sx, &Z, &D);
  ser_atanh2(c, &Lw, &Sx);
  bf_add(c, L, &Q, &Lw);
  c->p = mk;
}
static int ev_ln(cx *c, cx *cw, const za *a, bf *A, int *B) {
  (void)cw;
  ln_core(c, d2u(a->x), A);
  *B = 32 * c->n - GUARD(c->n) - 12;
  return 1;
}
/* x^y = exp(y ln|x|) for finite x != 0, |x| != 1 and finite y != 0 whose result is NOT an exact case (pow_exact); sign of the result: a->q = 1 means negative. T = y ln|x| has relative error <= 2^-(B_L - 1)
   (B_L = 32n - GUARD - 12 of ln_core, plus the product 2^(2-32n)) hence absolute error <= 2^(T.e - B_L + 1) (|T| < 2^T.e); e^T~ / e^T - 1 <= 1.01 2^(T.e - B_L + 1); with the exponential's own error (ev_hyp exp)
   B = 32 n - GUARD(n) - 15 - max(T.e, 0) covers both. |T| >= 2^11 (T.e >= 12): e^T > 2^1024 resp. < 2^-1075 (e^2048 > 2^2954): the result is inf resp. 0 whatever the digits: A = 2^(+-4096), r = 4 (decided by the
   region, not an approximation: the test checks the bound only for r = 1). |T| < 2^-59 (T.e <= -59; |T| <= 2^-58 after the 2^-8 relative error): e^T in (1 - 2^-57, 1 + 2^-57) rounds to 1 (the neighbours are
   2^-53 / 2^-52 away, the half-way points 2^-54 / 2^-53): r = 3, the result 1 rounded down (T > 0) or up (T < 0). */
static int ev_pow(cx *c, cx *cw, const za *a, bf *A, int *B) {
  int n = c->n;
  u64 uy = d2u(a->y), my; int ey; unpack(uy & ~SGNM, &my, &ey);
  u32 *mk = c->p;
  bf L = bnew(c), Y = bnew(c), T = bnew(c), X = bnew(c), R = bnew(c), E = bnew(c), one = bnew(c), S = bnew(c);
  ln_core(c, d2u(a->x), &L);
  bf_u64(c, &Y, my, ey, (int)(uy >> 63));
  bf_mul(c, &T, &Y, &L);
  bf_u64(c, &one, 1, 0, 0);
  int r = 1;
  if (T.e >= 12) { bf_u64(c, A, 1, T.s ? -4096 : 4096, 0); *B = 32 * n - 8; r = 4; }
  else if (T.e <= -59) { bf_cpy(c, A, &one); *B = T.s; r = 3; }
  else {
    bf_cpy(c, &X, &T); X.s = 0;
    int k = exp_reduce(c, cw, &X, &R);
    if (T.s) { k = -k; if (!R.z) R.s ^= 1; }
    ser_expm1(c, &E, &R); bf_add(c, &S, &E, &one); S.e += k;
    bf_cpy(c, A, &S);
    *B = 32 * n - GUARD(n) - 15 - (T.e > 0 ? T.e : 0);
  }
  if (!A->z) A->s = a->q;
  c->p = mk;
  return r;
}

/* x^y exactly representable or a half-way point? x = m 2^e (m odd), |y| = a 2^f (a odd). x^y is rational iff 2^j | e and m = r^(2^j) (j = max(-f, 0)): x^y = r^(+-a') 2^(e a'/2^j) with a' = a (j > 0) or the
   integer |y| (j = 0). It is a dyadic rational iff y > 0 or r = 1 (r > 1, y < 0 gives an odd denominator > 1). Only dyadic rationals lie on the rounding lattice (doubles and half-way points, the overflow and underflow
   boundaries included), and the odd part r^a' of a lattice point has <= 54 bits, so r^a' >= 2^64 is off the lattice; every other (irrational or non-dyadic rational) value is not on it either, so the Ziv loop
   terminates for it. Here: r = 1 (x a power of two): v = 2^N, N = (e / 2^j) y' exact (saturated to +-2^30: the lattice test of bf_pack then gives inf / 0); r >= 3 (j <= 5 as 3^(2^6) > 2^53): v = O 2^S, O = r^a' < 2^64
   computed with overflow check, S = (e / 2^j) a'; rounded once by exact_double (flags included). Returns 0 when v is not an exact case. neg = sign of the result. */
static int pow_exact(u64 ax, u64 uy, int neg, double *res, u32 *fl) {
  u64 mx, my; int ex, ey;
  unpack(ax, &mx, &ex); unpack(uy & ~SGNM, &my, &ey);
  int t = __builtin_ctzll(mx); mx >>= t; ex += t;
  t = __builtin_ctzll(my); my >>= t; ey += t;
  int ys = (int)(uy >> 63), j = ey < 0 ? -ey : 0;
  u64 O; s64 S;
  if (mx == 1) {                                                     /* x = 2^ex, ex != 0 */
    if (j >= 31 || (j > 0 && (ex & ((1 << j) - 1)))) return 0;
    u64 ae = ex < 0 ? (u64)-(s64)ex : (u64)ex, mag;                  /* |ex| <= 1074 */
    if (j > 0) mag = (ae >> j) * my;                                 /* N = (ex / 2^j) my: < 2^11 * 2^53 */
    else { mag = ae * my; if (ey > 0) mag = (ey >= 32 || (mag >> 32)) ? (1ull << 40) : (mag << ey); }
    if (mag > (1ull << 30)) mag = 1ull << 30;
    S = (s64)mag; if ((ex < 0) != (ys != 0)) S = -S;
    O = 1;
  } else {
    if (ys || j > 5 || (j > 0 && (ex & ((1 << j) - 1)))) return 0;
    u64 r = mx;
    for (int i = 0; i < j; i++) { u64 s = isqrt64(r); if (s * s != r) return 0; r = s; }
    u64 ap;                                                          /* the exponent a' >= 1 */
    if (j > 0) ap = my; else { if (ey > 6) return 0; ap = my << ey; if (ap > 64) return 0; }
    O = 1;
    for (u64 i = 0; i < ap; i++) { if (O > 0xffffffffffffffffull / r) return 0; O *= r; }
    S = (s64)(ex >> j) * (s64)ap;                                    /* ex divisible by 2^j */
  }
  *res = exact_double(O, (int)S, neg, fl);
  return 1;
}

#ifdef FM_DEBUG
/* constants at n limbs (which = 0: pi, 1: ln 2), from the table or (notable) from the series: for test/fmathtest.py */
int fm_dbg_const(int which, int n, int notable, u32 *m, int *e) {
  u32 arena[40 * n]; cx c; c.n = n; c.p = arena; c.end = arena + 40 * n; fm_notable = notable;
  bf r = bnew(&c);
  if (which) bf_ln2(&c, &r); else bf_pi(&c, &r);
  for (int i = 0; i < n; i++) m[i] = r.m[i];
  *e = r.e; fm_notable = 0; return 1;
}
#endif
#ifdef FM_DEBUG                  /* test/fmathtest.py: start limb count and a hook that reports every evaluation (A, B) so the error bounds can be checked against MPFR */
int fm_nstart = 4;
void (*fm_hook)(int n, const u32 *m, int e, int s, int z, int B, int r);
#define FM_NSTART fm_nstart
#else
#define FM_NSTART 4
#endif
static double ziv(int w, double x, double y, int q, u32 *fl) {
  u64 ux = d2u(x);
  int ex = (int)((ux >> 52) & 0x7ff) - 1022, exx = ex > 0 ? ex : 0;
  for (int n = FM_NSTART;; n *= 2) {
    int nw = (w <= W_TAN) ? n + (exx >> 5) + 5 : (w >= W_ACOS) ? n + 4 : 72;
    u32 arena[48 * n + 24 * nw];
#ifdef FM_DEBUG
    for (int i = 0; i < 48 * n + 24 * nw; i++) arena[i] = 0x9e3779b9u * (u32)(i + 1);
#endif
    cx c, cw; c.n = n; c.p = arena; c.end = arena + 48 * n; cw.n = nw; cw.p = arena + 48 * n; cw.end = arena + 48 * n + 24 * nw;
    za a; a.x = x; a.y = y; a.w = w; a.q = q;
    bf A = bnew(&c); int B = 0, r;
    switch (w) {
    case W_SIN: case W_COS: case W_TAN: r = ev_trig(&c, &cw, &a, &A, &B); break;
    case W_2XM1: r = ev_exp(&c, &cw, &a, &A, &B); break;
    case W_YL2X: case W_YL2XP1: r = ev_log(&c, &cw, &a, &A, &B); break;
    case W_ATAN2: r = ev_atan(&c, &cw, &a, &A, &B); break;
    case W_ACOS: r = ev_acos(&c, &cw, &a, &A, &B); break;
    case W_ASIN: r = ev_asin(&c, &cw, &a, &A, &B); break;
    case W_EXP: case W_SINH: case W_COSH: case W_TANH: r = ev_hyp(&c, &cw, &a, &A, &B); break;
    case W_LOG: r = ev_ln(&c, &cw, &a, &A, &B); break;
    case W_POW: r = ev_pow(&c, &cw, &a, &A, &B); break;
    default: r = ev_piq(&c, &cw, &a, &A, &B); break;
    }
#ifdef FM_DEBUG
    if (fm_hook) fm_hook(n, A.m, A.e, A.s, A.z, B, r);
#endif
    if (r == 3) { u64 out; u32 f0 = 0; bf_pack(&c, &A, 0, 1, &out, &f0); *fl |= PE | (B ? C1 : 0); return u2d(out | (A.s ? SGNM : 0)); }     /* pow: A = 1 exactly is the rounded value, B = 1 when it is above the true value */
    if (r) { u64 out; if (bf_pack(&c, &A, B, r == 2, &out, fl)) return u2d(out); }
  }
}

/* ================= public: trigonometry ================= */
u32 fnvl_m_trig_big(double x) { u64 f = (d2u(x) >> 52) & 0x7ff; return f >= 1023 + 63 && f != 0x7ff; }
/* sin x, tan x for |x| < 2^-27 round to x: sin x in (x(1 - x^2/6), x), tan x in (x, x(1 + x^2/3)), and x^2/3 < 2^-55 < the half spacing/x of the neighbour
   on that side (>= 2^-54 relative); cos x in (1 - 2^-55, 1) rounds to 1 (spacing below 1 is 2^-53). */
static double small_arg(double x, u64 a, u32 up, u32 *fl) { *fl |= PE | (up ? C1 : 0) | (a < 0x0010000000000000ull + up ? UE : 0); return x; }   /* sin: |true| < |x| (rounded up, tiny up to 2^-1022 itself); tan: |true| > |x| */
double fnvl_m_sin(double x, u32 *fl) {
  u64 a = d2u(x) & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (a == EXPM) return invalid(fl);
  if (!a) return x;
  if (a < 0x3e40000000000000ull) return small_arg(x, a, 1, fl);
  return ziv(W_SIN, x, 0.0, 0, fl);
}
double fnvl_m_tan(double x, u32 *fl) {
  u64 a = d2u(x) & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (a == EXPM) return invalid(fl);
  if (!a) return x;
  if (a < 0x3e40000000000000ull) return small_arg(x, a, 0, fl);
  return ziv(W_TAN, x, 0.0, 0, fl);
}
double fnvl_m_cos(double x, u32 *fl) {
  u64 a = d2u(x) & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (a == EXPM) return invalid(fl);
  if (!a) return 1.0;
  if (a < 0x3e40000000000000ull) { *fl |= PE | C1; return u2d(0x3ff0000000000000ull); }
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
  if (!(u >> 63) && a >= 0x4090000000000000ull) { *fl |= OE | PE | C1; return u2d(EXPM); }                /* x >= 1024: >= 2^1024 - 1, past the half-way point to infinity */
  if (!(u >> 63) && a >= 0x4050000000000000ull) {                      /* x >= 64: integer: 2^x - 1 rounds to 2^x (1 < half the spacing 2^(x-54)); else the engine */
    u64 mant; int e2; unpack(a, &mant, &e2);
    if (e2 >= 0 || !(mant & ((1ull << -e2) - 1))) { int k = (int)(e2 >= 0 ? (mant << e2) : (mant >> -e2)); *fl |= PE | C1; return u2d((u64)(k + 1023) << 52); }
  }
  if ((u >> 63) && a > 0x404e000000000000ull) { *fl |= PE | C1; return u2d(0xBFF0000000000000ull); }   /* x < -60: -1 + 2^x rounds to -1 (2^x < 2^-60 < 2^-54) */
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
  u64 emant; int e2, ef = (int)((ue >> 52) & 0x7ff);
  unpack(ue, &emant, &e2);
  long long t;
  if (ef < 1023) t = 0;                                              /* |e| < 1 */
  else if (ef >= 1023 + 20) t = 1 << 20;
  else t = (long long)(emant >> (52 - (ef - 1023)));                 /* trunc(|e|) < 2^20 */
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
   D < 64 (complete, C2 = 0): Q = trunc (fprem) or round-to-nearest-even (fprem1) of a/b, a - Q b exact; C0 = Q2, C3 = Q1, C1 = Q0.
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
    *cf = (q & 1 ? 0x200u : 0) | (q & 2 ? 0x4000u : 0) | (q & 4 ? 0x100u : 0);
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
  *cf = (q & 1 ? 0x200u : 0) | (q & 2 ? 0x4000u : 0) | (q & 4 ? 0x100u : 0);
  return exact_double(r, eb - 52, neg, fl);
}

/* ================= public: msvcrt math library (acos asin atan cosh sinh tanh exp log pow fmod sqrt) ================= */
/* C99 Annex F values (what msvcrt / Wine's libm return), decided before the loop; the results of the engine are correctly rounded. NaN operand: the quiet NaN (IE for a signalling one). Invalid: the default NaN
   (0xFFF8...). The rounding of the "tiny argument" rules is proved at each function. */
double fnvl_m_atan(double x, u32 *fl) {
  u64 u = d2u(x), a = u & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (!a) return x;
  if (a < 0x3e40000000000000ull) return small_arg(x, a, 1, fl);   /* |x| < 2^-27: atan x in (x (1 - x^2/3), x) rounds to x as sin does */
  return fnvl_m_atan2(x, 1.0, fl);                                /* atan x = atan2(x, 1) (+-inf: +-pi/2 by the table) */
}
double fnvl_m_asin(double x, u32 *fl) {
  u64 u = d2u(x), a = u & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (!a) return x;
  if (a > 0x3ff0000000000000ull) return invalid(fl);              /* |x| > 1 (infinities included) */
  if (a < 0x3e40000000000000ull) return small_arg(x, a, 0, fl);   /* asin x in (x, x (1 + x^2/3)) */
  return ziv(W_ASIN, x, 0.0, 0, fl);
}
double fnvl_m_acos(double x, u32 *fl) {
  u64 u = d2u(x), a = u & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (a > 0x3ff0000000000000ull) return invalid(fl);
  if (u == 0x3ff0000000000000ull) return 0.0;                     /* acos 1 = +0 exactly */
  return ziv(W_ACOS, x, 0.0, 0, fl);
}
/* cosh x in (1 + x^2/2, 1 + x^2/2 + x^4/20): for |x| < 2^-26 it is in (1, 1 + 2^-53), below the half-way point to 1 + 2^-52: rounds to 1 (down). cosh x >= 2^1024 (1 - 2^-54) for |x| >= 711 > 710.4758. */
double fnvl_m_cosh(double x, u32 *fl) {
  u64 u = d2u(x), a = u & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (!a) return 1.0;
  if (a == EXPM) return u2d(EXPM);
  if (a < 0x3e50000000000000ull) { *fl |= PE; return 1.0; }
  if (a >= 0x4086380000000000ull) { *fl |= OE | PE | C1; return u2d(EXPM); }
  return ziv(W_COSH, x, 0.0, 0, fl);
}
double fnvl_m_sinh(double x, u32 *fl) {
  u64 u = d2u(x), a = u & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (!a || a == EXPM) return x;
  if (a < 0x3e40000000000000ull) return small_arg(x, a, 0, fl);   /* sinh x in (x, x (1 + x^2/5)) */
  if (a >= 0x4086380000000000ull) { *fl |= OE | PE | C1; return u2d((u & SGNM) | EXPM); }
  return ziv(W_SINH, x, 0.0, 0, fl);
}
/* tanh |x| in (|x| (1 - x^2/3), |x|) for tiny x; 1 - tanh |x| < 2 e^(-2|x|) < 2^-54 for |x| >= 20 (e^-40 * 2 = 8.5e-18 < 5.5e-17): the value is inside the last half-ulp cell below 1 and rounds to 1 (up in magnitude). */
double fnvl_m_tanh(double x, u32 *fl) {
  u64 u = d2u(x), a = u & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (!a) return x;
  if (a == EXPM) return u2d((u & SGNM) | 0x3ff0000000000000ull);
  if (a < 0x3e40000000000000ull) return small_arg(x, a, 1, fl);
  if (a >= 0x4034000000000000ull) { *fl |= PE | C1; return u2d((u & SGNM) | 0x3ff0000000000000ull); }
  return ziv(W_TANH, x, 0.0, 0, fl);
}
/* exp x: x > 0, x < 2^-54: 1 < e^x < 1 + 2^-53: rounds to 1 (down). x < 0, |x| < 2^-55: 1 - 2^-55 < e^x < 1, above the half-way point 1 - 2^-54: rounds to 1 (up). x >= 710: e^x > 2^1024. x <= -746: e^x < 2^-1076 < half the
   smallest subnormal: rounds to +0 (tiny, inexact). */
double fnvl_m_exp(double x, u32 *fl) {
  u64 u = d2u(x), a = u & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (u == EXPM) return x;
  if (u == (SGNM | EXPM)) return 0.0;
  if (!a) return 1.0;
  if (!(u >> 63)) {
    if (a >= 0x4086300000000000ull) { *fl |= OE | PE | C1; return u2d(EXPM); }
    if (a < 0x3c90000000000000ull) { *fl |= PE; return 1.0; }
  } else {
    if (a >= 0x4087500000000000ull) { *fl |= PE | UE; return 0.0; }
    if (a < 0x3c80000000000000ull) { *fl |= PE | C1; return 1.0; }
  }
  return ziv(W_EXP, x, 0.0, 0, fl);
}
double fnvl_m_log(double x, u32 *fl) {
  u64 u = d2u(x), a = u & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (!a) { *fl |= ZE; return u2d(SGNM | EXPM); }
  if (u >> 63) return invalid(fl);
  if (a == EXPM) return x;
  if (u == 0x3ff0000000000000ull) return 0.0;
  return ziv(W_LOG, x, 0.0, 0, fl);
}
/* pow(x, y), C99 Annex F: y = +-0 -> 1 (also for NaN x); x = 1 -> 1 (also for NaN y; no exception even for a signalling NaN, as Annex F words it); other NaNs propagate (nan2); y = +-inf: |x| = 1 -> 1, (|x| < 1) == (y > 0) -> +0 else +inf;
   x = +-inf / +-0: the sign of the result is negative iff x < 0 and y is an odd integer; x = +-0, y < 0: +-inf with ZE; x < 0 finite and y not an integer: invalid; |x| = 1: +-1. Otherwise pow_exact (exact cases) or the loop. */
double fnvl_m_pow(double x, double y, u32 *fl) {
  u64 ux = d2u(x), uy = d2u(y), ax = ux & ~SGNM, ay = uy & ~SGNM;
  int xs = (int)(ux >> 63);
  if (!ay || ux == 0x3ff0000000000000ull) return 1.0;
  if (ax > EXPM || ay > EXPM) return nan2(x, y, fl);
  int yint = 0, yodd = 0;
  if (ay != EXPM) {
    u64 my; int ey; unpack(ay, &my, &ey);
    int f = ey + __builtin_ctzll(my);
    yint = f >= 0; yodd = f == 0;
  }
  if (ay == EXPM) {
    if (ax == 0x3ff0000000000000ull) return 1.0;
    int xlt = ax < 0x3ff0000000000000ull, ypos = !(uy >> 63);
    return xlt == ypos ? 0.0 : u2d(EXPM);
  }
  int neg = xs && yodd, ypos = !(uy >> 63);
  if (ax == EXPM) return u2d((neg ? SGNM : 0) | (ypos ? EXPM : 0));
  if (!ax) { if (ypos) return u2d(neg ? SGNM : 0); *fl |= ZE; return u2d((neg ? SGNM : 0) | EXPM); }
  if (xs && !yint) return invalid(fl);
  if (ax == 0x3ff0000000000000ull) return u2d(neg ? 0xBFF0000000000000ull : 0x3ff0000000000000ull);
  double r;
  if (pow_exact(ax, uy, neg, &r, fl)) return r;
  return ziv(W_POW, u2d(ax), y, neg, fl);
}

/* ================= public: fmod, sqrt (exact, IEEE: one rounding, none for fmod) ================= */
/* fmod(x, y) = x - trunc(x/y) y is exactly representable: |x| = Ma 2^(ea-52), |y| = Mb 2^(eb-52) (53-bit normalised mantissas), D = ea - eb: D < 0 -> x; else the remainder is (Ma 2^D mod Mb) 2^(eb-52) by the
   bitwise long division (r < 2 Mb < 2^54, D + 1 steps), sign of x (also for a zero remainder). x infinite or y zero: invalid; y infinite or x zero: x; NaN: nan2. */
double fnvl_m_fmod(double x, double y, u32 *fl) {
  u64 ua = d2u(x), ub = d2u(y);
  if (isnan_(ua) || isnan_(ub)) return nan2(x, y, fl);
  if (isinf_(ua) || iszero_(ub)) return invalid(fl);
  if (isinf_(ub) || iszero_(ua)) return x;
  u64 Ma, Mb; int ea2, eb2;
  unpack(ua & ~SGNM, &Ma, &ea2); unpack(ub & ~SGNM, &Mb, &eb2);
  int la = 63 - __builtin_clzll(Ma), lb = 63 - __builtin_clzll(Mb);
  int ea = ea2 + la, eb = eb2 + lb;
  Ma <<= 52 - la; Mb <<= 52 - lb;
  int D = ea - eb;
  if (D < 0) return x;
  u64 r = Ma;
  for (int i = 0; i <= D; i++) { if (r >= Mb) r -= Mb; if (i < D) r <<= 1; }
  return exact_double(r, eb - 52, (int)(ua >> 63), fl);
}
/* sqrt: |x| = m 2^e with m in [2^52, 2^54) and e even (one doubling of m for an odd exponent): N = m 2^56 in [2^108, 2^110), q = floor(sqrt N) (55 bits) by the bitwise method, sqrt|x| = (q + f) 2^((e-56)/2) with
   0 <= f < 1, f = 0 iff q^2 = N. (2q + [q^2 != N]) 2^((e-56)/2 - 1) lies on the same side of every rounding boundary of binary64 as the true value (the boundaries are multiples of 2 units of q since q has
   55 bits, and q + 1/2 is strictly between q and q+1), so one exact_double of it is the correctly rounded root with the right flags. x < 0: invalid, -0 -> -0, +inf -> +inf, NaN -> nan1. */
double fnvl_m_sqrt(double x, u32 *fl) {
  u64 u = d2u(x), a = u & ~SGNM;
  if (a > EXPM) return nan1(x, fl);
  if (!a) return x;
  if (u >> 63) return invalid(fl);
  if (a == EXPM) return x;
  u64 m; int e; unpack(a, &m, &e);
  int lz = __builtin_clzll(m) - 11;
  m <<= lz; e -= lz;
  if (e & 1) { m <<= 1; e -= 1; }
  unsigned __int128 N = (unsigned __int128)m << 56;
  u64 q = 0;
  for (int b = 54; b >= 0; b--) { u64 t = q | (1ull << b); if ((unsigned __int128)t * t <= N) q = t; }
  u64 sticky = (unsigned __int128)q * q != N;
  return exact_double((q << 1) | sticky, (e - 56) / 2 - 1, 0, fl);
}
