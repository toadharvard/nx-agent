/* Exact x87 arithmetic core: integer-only, no libc/libm/libgcc. See x87.h. Matches QEMU 5.0 softfloat floatx80
   (precision 24/53/64 significand rounding, extended exponent range) for supported encodings. */
#include "x87.h"
typedef unsigned __int128 u128;
enum { IE = 1, DE = 2, ZE = 4, OE = 8, UE = 16, PE = 32, C1 = 0x200, BIAS = 16383 };
enum { K_ZERO, K_NUM, K_INF, K_QNAN, K_SNAN, K_BAD };
#define ISNAN(k) ((k) == K_QNAN || (k) == K_SNAN)
#define INTBIT (1ull << 63)
typedef struct { u32 k, s, den; int e; u64 m; } un_t;   /* m normalized, e biased (<= 0 for denormals) */

static fnvl_f80 mk(u32 s, u32 e, u64 m) { fnvl_f80 r; r.m = m; r.se = (u16)(s << 15 | e); return r; }
static fnvl_f80 inf(u32 s) { return mk(s, 0x7FFF, INTBIT); }
static fnvl_f80 indef(u32 *fsw) { *fsw |= IE; return mk(1, 0x7FFF, 3ull << 62); }
static int clz128(u128 x) { u64 h = (u64)(x >> 64); return h ? __builtin_clzll(h) : 64 + __builtin_clzll((u64)x); }

static un_t unpack(fnvl_f80 a)
{
	un_t u; u32 e = a.se & 0x7FFF; int z;
	u.s = a.se >> 15; u.m = a.m; u.e = (int)e; u.den = 0; u.k = K_NUM;
	if (e == 0x7FFF) u.k = !(a.m >> 63) ? K_BAD : !(a.m << 1) ? K_INF : (a.m >> 62 & 1) ? K_QNAN : K_SNAN;
	else if (e == 0) {
		if (!a.m) u.k = K_ZERO;
		else { u.den = 1; z = __builtin_clzll(a.m); u.m = a.m << z; u.e = 1 - z; }
	} else if (!(a.m >> 63)) u.k = K_BAD;
	return u;
}

/* x >> N (N >= 64) rounded per rc (0 even, 1 down, 2 up, 3 zero) for sign s; sets inexact / incremented */
static u128 rnd(u128 x, int N, u32 s, u32 rc, u32 *inex, u32 *inc)
{
	u128 q; u32 g, rest;
	if (N > 128) { q = 0; g = 0; rest = x != 0; }
	else if (N == 128) { q = 0; g = (u32)(x >> 127); rest = (x << 1) != 0; }
	else { q = x >> N; g = (u32)(x >> (N - 1)) & 1; rest = (x & (((u128)1 << (N - 1)) - 1)) != 0; }
	*inex = g | rest;
	*inc = rc == 0 ? (g && (rest || (q & 1))) : rc == 1 ? (s && *inex) : rc == 2 ? (!s && *inex) : 0;
	return q + *inc;
}

/* Underflow is tininess after rounding: tiny (sh > 0 = exponent below the normal range) unless rounding the
   p-bit significand with an unbounded exponent carries it up to the minimum normal. */
static int tiny(u64 m, u64 lo, int d0, int sh, int p, u32 s, u32 rc)
{
	u32 i, c;
	if (!sh) return 0;
	return !(sh == 1 && (rnd((u128)m << 64 | lo, 64 + d0, s, rc, &i, &c) >> p));
}

/* Round (m:lo) * 2^(e-BIAS-63) (m normalized, lo = extra low bits, sticky folded into lo) to the control word's
   precision with the extended exponent range; denormal results round on the precision grid (as QEMU/x87). */
static fnvl_f80 rpack(u32 s, int e, u64 m, u64 lo, u32 fcw, u32 *fsw)
{
	u32 pc = fcw >> 8 & 3, rc = fcw >> 10 & 3, inex, inc;
	int p = pc == 0 ? 24 : pc == 2 ? 53 : 64, d0 = 64 - p, sh = e < 1 ? 1 - e : 0;
	u128 q;
	*fsw &= ~C1;
	if (e >= 0x7FFF) goto ovf;
	q = rnd((u128)m << 64 | lo, 64 + d0 + sh, s, rc, &inex, &inc);
	if (inex) *fsw |= PE | (tiny(m, lo, d0, sh, p, s, rc) ? UE : 0);
	if (inc) *fsw |= C1;
	if (sh) { m = (u64)q << d0; return mk(s, m >> 63, m); }
	if (q >> p) { q >>= 1; e++; }
	if (e >= 0x7FFF) goto ovf;
	return mk(s, (u32)e, (u64)q << d0);
ovf:
	*fsw = (*fsw & ~C1) | OE | PE;
	if (rc == 3 || (rc == 1 && !s) || (rc == 2 && s)) return mk(s, 0x7FFE, ~0ull << d0);
	*fsw |= C1;
	return inf(s);
}

/* x87 NaN propagation (QEMU): SNaN quieted + IE; QNaN beats SNaN; both same kind: larger significand */
static fnvl_f80 nan2(fnvl_f80 a, un_t x, fnvl_f80 b, un_t y, u32 *fsw)
{
	a.m |= 1ull << 62; b.m |= 1ull << 62;
	if (x.k == K_SNAN || y.k == K_SNAN) *fsw |= IE;
	if (!ISNAN(y.k)) return a;
	if (!ISNAN(x.k)) return b;
	if (x.k == K_SNAN && y.k != K_SNAN) return b;
	if (x.k != K_SNAN && y.k == K_SNAN) return a;
	if (a.m != b.m) return a.m < b.m ? b : a;
	return a.se < b.se ? a : b;
}

static fnvl_f80 addsub(fnvl_f80 a, fnvl_f80 b, u32 sub, u32 fcw, u32 *fsw)
{
	un_t x = unpack(a), y = unpack(b), t; u32 rc = fcw >> 10 & 3, sa, sb, ts; int d, lz; u128 A, B, R;
	*fsw &= ~C1;
	if (x.k == K_BAD || y.k == K_BAD) return indef(fsw);
	if (ISNAN(x.k) || ISNAN(y.k)) return nan2(a, x, b, y, fsw);
	sa = x.s; sb = y.s ^ sub;
	if (x.den | y.den) *fsw |= DE;
	if (x.k == K_INF || y.k == K_INF) {
		if (x.k == K_INF && y.k == K_INF && sa != sb) return indef(fsw);
		return inf(x.k == K_INF ? sa : sb);
	}
	if (x.k == K_ZERO && y.k == K_ZERO) return mk(sa == sb ? sa : rc == 1, 0, 0);
	if (x.k == K_ZERO) return rpack(sb, y.e, y.m, 0, fcw, fsw);
	if (y.k == K_ZERO) return rpack(sa, x.e, x.m, 0, fcw, fsw);
	if (x.e < y.e || (x.e == y.e && x.m < y.m)) { t = x; x = y; y = t; ts = sa; sa = sb; sb = ts; }
	d = x.e - y.e;
	A = (u128)x.m << 63; B = (u128)y.m << 63;
	if (d >= 127) B = 1;
	else if (d > 0) B = (B >> d) | ((B & (((u128)1 << d) - 1)) != 0);
	R = sa == sb ? A + B : A - B;
	if (!R) return mk(rc == 1, 0, 0);
	lz = clz128(R); R <<= lz;
	return rpack(sa, x.e + 1 - lz, (u64)(R >> 64), (u64)R, fcw, fsw);
}

/* floor(r * 2^64 / d) for r < d, remainder in *rem */
static u64 divq(u64 r, u64 d, u64 *rem)
{
	u64 q = 0; int i;
	for (i = 0; i < 64; i++) {
		u64 c = r >> 63; r <<= 1; q <<= 1;
		if (c || r >= d) { r -= d; q |= 1; }
	}
	*rem = r; return q;
}

static fnvl_f80 muldiv(fnvl_f80 a, fnvl_f80 b, u32 dv, u32 fcw, u32 *fsw)
{
	un_t x = unpack(a), y = unpack(b); u32 s; int e, lz; u128 P; u64 r, q1, q2, r2, m, lo, hi;
	*fsw &= ~C1;
	if (x.k == K_BAD || y.k == K_BAD) return indef(fsw);
	if (ISNAN(x.k) || ISNAN(y.k)) return nan2(a, x, b, y, fsw);
	s = x.s ^ y.s;
	if (dv && y.k == K_ZERO && x.k != K_INF) { if (x.k == K_ZERO) return indef(fsw); *fsw |= ZE; return inf(s); }
	if (x.den | y.den) *fsw |= DE;
	if (x.k == K_INF || y.k == K_INF) {
		if (dv ? (x.k == y.k) : (x.k == K_ZERO || y.k == K_ZERO)) return indef(fsw);
		return dv && y.k == K_INF ? mk(s, 0, 0) : inf(s);
	}
	if (x.k == K_ZERO || y.k == K_ZERO) return mk(s, 0, 0);
	if (!dv) {
		P = (u128)x.m * y.m; lz = (P >> 127) ? 0 : 1; P <<= lz;
		return rpack(s, x.e + y.e - 16382 - lz, (u64)(P >> 64), (u64)P, fcw, fsw);
	}
	hi = x.m >= y.m; r = hi ? x.m - y.m : x.m;
	q1 = divq(r, y.m, &r); q2 = divq(r, y.m, &r2);
	e = x.e - y.e + BIAS;
	if (hi) { m = INTBIT | q1 >> 1; lo = q1 << 63 | q2 >> 1 | (q2 & 1); }
	else { m = q1; lo = q2; e--; }
	return rpack(s, e, m, lo | (r2 != 0), fcw, fsw);
}

fnvl_f80 fnvl_x_add(fnvl_f80 a, fnvl_f80 b, u32 fcw, u32 *fsw) { return addsub(a, b, 0, fcw, fsw); }
fnvl_f80 fnvl_x_sub(fnvl_f80 a, fnvl_f80 b, u32 fcw, u32 *fsw) { return addsub(a, b, 1, fcw, fsw); }
fnvl_f80 fnvl_x_mul(fnvl_f80 a, fnvl_f80 b, u32 fcw, u32 *fsw) { return muldiv(a, b, 0, fcw, fsw); }
fnvl_f80 fnvl_x_div(fnvl_f80 a, fnvl_f80 b, u32 fcw, u32 *fsw) { return muldiv(a, b, 1, fcw, fsw); }

/* digit-by-digit square root: 65 root digits (1 integer + 64 fraction), remainder as sticky */
fnvl_f80 fnvl_x_sqrt(fnvl_f80 a, u32 fcw, u32 *fsw)
{
	un_t x = unpack(a); int E, odd, k, i; u128 rem = 0, root = 0, t;
	*fsw &= ~C1;
	if (x.k == K_BAD) return indef(fsw);
	if (ISNAN(x.k)) return nan2(a, x, a, x, fsw);
	if (x.k == K_ZERO || x.k == K_INF) return x.s && x.k == K_INF ? indef(fsw) : a;
	if (x.s) return indef(fsw);
	if (x.den) *fsw |= DE;
	E = x.e - BIAS; odd = E & 1;
	for (k = 0; k < 65; k++) {
		u32 two = 0;
		for (i = 0; i < 2; i++) {
			int q = 2 * k + i - (odd ? 0 : 1);
			two = two << 1 | (q >= 0 && q < 64 ? (u32)(x.m >> (63 - q)) & 1 : 0);
		}
		rem = rem << 2 | two; t = root << 2 | 1; root <<= 1;
		if (rem >= t) { rem -= t; root |= 1; }
	}
	return rpack(0, BIAS + (E >> 1), (u64)(root >> 1), (u64)(root & 1) << 63 | (rem != 0), fcw, fsw);
}

fnvl_f80 fnvl_x_rndint(fnvl_f80 a, u32 fcw, u32 *fsw)
{
	un_t x = unpack(a); u32 inex, inc; int E, z; u64 q;
	*fsw &= ~C1;
	if (x.k == K_BAD) return indef(fsw);
	if (ISNAN(x.k)) return nan2(a, x, a, x, fsw);
	E = x.e - BIAS;
	if (x.den) *fsw |= DE;
	if (x.k == K_ZERO || x.k == K_INF || E >= 63) return a;
	q = (u64)rnd((u128)x.m << 64, 64 + 63 - E, x.s, fcw >> 10 & 3, &inex, &inc);
	if (inex) *fsw |= PE;
	if (inc) *fsw |= C1;
	if (!q) return mk(x.s, 0, 0);
	z = __builtin_clzll(q);
	return mk(x.s, BIAS + 63 - z, q << z);
}

fnvl_f80 fnvl_x_from_i64(long long v)
{
	u32 s = v < 0; u64 mag = s ? 0 - (u64)v : (u64)v; int z;
	if (!mag) return mk(0, 0, 0);
	z = __builtin_clzll(mag);
	return mk(s, BIAS + 63 - z, mag << z);
}

static fnvl_f80 from_ieee(u64 b, int eb, int mb, u32 *fsw)
{
	u32 s = (u32)(b >> (eb + mb)) & 1, ex = (u32)(b >> mb) & ((1u << eb) - 1); u64 f = b & ((1ull << mb) - 1);
	int bi = (1 << (eb - 1)) - 1, z;
	if (ex == (1u << eb) - 1) {
		if (!f) return inf(s);
		if (!(f >> (mb - 1))) *fsw |= IE;
		return mk(s, 0x7FFF, 3ull << 62 | f << (63 - mb));
	}
	if (ex == 0) {
		if (!f) return mk(s, 0, 0);
		*fsw |= DE; z = __builtin_clzll(f);
		return mk(s, BIAS + 64 - bi - mb - z, f << z);
	}
	return mk(s, ex - bi + BIAS, INTBIT | f << (63 - mb));
}
fnvl_f80 fnvl_x_from_f32(u32 bits, u32 *fsw) { return from_ieee(bits, 8, 23, fsw); }
fnvl_f80 fnvl_x_from_f64(u64 bits, u32 *fsw) { return from_ieee(bits, 11, 52, fsw); }

/* NaN conversion follows QEMU: payload = top mantissa bits, no quieting; all-zero payload -> default NaN */
static u64 to_ieee(fnvl_f80 a, u32 fcw, u32 *fsw, int eb, int mb)
{
	un_t x = unpack(a); u32 rc = fcw >> 10 & 3, inex, inc, s = x.s; u64 emax = (1ull << eb) - 1, bits, mant;
	int bi = (1 << (eb - 1)) - 1, E, emin, sh; u128 q;
	*fsw &= ~C1;
	if (x.k == K_BAD) { *fsw |= IE; return 1ull << (eb + mb) | emax << mb | 1ull << (mb - 1); }
	if (ISNAN(x.k)) {
		if (x.k == K_SNAN) *fsw |= IE;
		mant = ((a.m | 1ull << 62) << 1) >> (64 - mb);     /* quieted */
		return (u64)s << (eb + mb) | emax << mb | mant;
	}
	if (x.k == K_ZERO) return (u64)s << (eb + mb);
	E = x.e - BIAS; emin = 1 - bi;
	if (x.k == K_INF || E > bi) goto ovf;
	sh = E < emin ? emin - E : 0;
	q = rnd((u128)x.m << 64, 64 + (63 - mb) + sh, s, rc, &inex, &inc);
	if (inex) *fsw |= PE | (tiny(x.m, 0, 63 - mb, sh, mb + 1, s, rc) ? UE : 0);
	if (inc) *fsw |= C1;
	bits = (sh ? 0 : (u64)(E + bi - 1) << mb) + (u64)q;
	if ((bits >> mb) < emax) return (u64)s << (eb + mb) | bits;
ovf:
	if (x.k == K_INF) return (u64)s << (eb + mb) | emax << mb;
	*fsw = (*fsw & ~C1) | OE | PE;
	if (rc == 3 || (rc == 1 && !s) || (rc == 2 && s)) return (u64)s << (eb + mb) | (emax - 1) << mb | ((1ull << mb) - 1);
	*fsw |= C1;
	return (u64)s << (eb + mb) | emax << mb;
}
u32 fnvl_x_to_f32(fnvl_f80 a, u32 fcw, u32 *fsw) { return (u32)to_ieee(a, fcw, fsw, 8, 23); }
u64 fnvl_x_to_f64(fnvl_f80 a, u32 fcw, u32 *fsw) { return to_ieee(a, fcw, fsw, 11, 52); }

long long fnvl_x_to_i64(fnvl_f80 a, u32 rc, u32 bits, u32 *fsw)
{
	un_t x = unpack(a); u64 lim = 1ull << (bits - 1), mag; u32 inex, inc; int E;
	*fsw &= ~C1;
	if (x.k == K_ZERO) return 0;
	E = x.e - BIAS;
	if (x.k != K_NUM || E >= 64) goto bad;
	mag = (u64)rnd((u128)x.m << 64, 64 + 63 - E, x.s, rc, &inex, &inc);
	if (x.s ? mag > lim : mag >= lim) goto bad;
	if (inex) *fsw |= PE;
	if (inc) *fsw |= C1;
	return x.s ? (long long)(0 - mag) : (long long)mag;
bad:
	*fsw |= IE;
	return (long long)(0 - lim);
}

/* Unsupported encodings: unordered + IE. Otherwise a numeric compare (pseudo-denormals have exponent 1). */
u32 fnvl_x_cmp(fnvl_f80 a, fnvl_f80 b, u32 quiet, u32 *fsw)
{
	un_t x = unpack(a), y = unpack(b); u32 r; int xe, ye;
	*fsw &= ~(C1 | 0x4500u);
	if (x.k == K_BAD || y.k == K_BAD) { *fsw |= IE; r = 0x4500; }
	else if (ISNAN(x.k) || ISNAN(y.k)) {
		if (!quiet || x.k == K_SNAN || y.k == K_SNAN) *fsw |= IE;
		r = 0x4500;
	} else {
		if (x.den | y.den) *fsw |= DE;
		xe = x.k == K_ZERO ? -999999 : x.e; ye = y.k == K_ZERO ? -999999 : y.e;
		if (x.k == K_ZERO && y.k == K_ZERO) r = 0x4000;
		else if (x.s != y.s) r = x.s ? 0x100 : 0;
		else if (xe == ye && x.m == y.m) r = 0x4000;
		else r = ((xe < ye || (xe == ye && x.m < y.m)) ^ x.s) ? 0x100 : 0;
	}
	*fsw |= r;
	return r;
}

u32 fnvl_x_xam(fnvl_f80 a, u32 empty)
{
	u32 e = a.se & 0x7FFF, r = a.se >> 15 ? C1 : 0;
	if (empty) return 0x4100;
	if (unpack(a).k == K_BAD) return r;                /* unsupported encoding: C3 C2 C0 = 000 */
	if (e == 0x7FFF) return r | (a.m == INTBIT ? 0x500 : 0x100);
	if (e == 0) return r | (a.m ? 0x4400 : 0x4000);
	return r | 0x400;
}

/* ---- packed BCD (fbld / fbstp): 18 digits, nibble i = digit 10^i, bit 79 = sign ----
   ASM-216 (assumption): a nibble above 9 in fbld is not an error on the hardware (SDM: "undefined"); the value is the arithmetic sum of nibble * 10^i, which is what the loop below
   computes (at most 15 * (10^18 - 1) / 9 < 2^63, so the integer is exact). Bits 78:72 of the sign byte are ignored. fbstp: round per RC, NaN / infinity / unsupported / |x| >= 10^18 store the
   packed-BCD indefinite FFFF C000 0000 0000 0000 and set IE. */
fnvl_f80 fnvl_x_from_bcd(const u8 *p)
{
	u64 v = 0, w = 1; int i;
	for (i = 0; i < 9; i++, w *= 100) v += (u64)((p[i] & 15) + 10 * (p[i] >> 4)) * w;
	return p[9] & 0x80 ? (v ? fnvl_x_from_i64(-(long long)v) : mk(1, 0, 0)) : fnvl_x_from_i64((long long)v);
}

void fnvl_x_to_bcd(fnvl_f80 a, u32 fcw, u8 *out, u32 *fsw)
{
	un_t x = unpack(a); u32 inex, inc; int E, i; u64 mag = 0;
	*fsw &= ~C1;
	for (i = 0; i < 10; i++) out[i] = 0;
	if (x.k == K_ZERO) { out[9] = x.s << 7; return; }
	E = x.e - BIAS;
	if (x.k != K_NUM || E >= 64) goto bad;
	mag = (u64)rnd((u128)x.m << 64, 64 + 63 - E, x.s, fcw >> 10 & 3, &inex, &inc);
	if (mag >= 1000000000000000000ull) goto bad;
	if (inex) *fsw |= PE;
	if (inc) *fsw |= C1;
	for (i = 0; i < 9; i++, mag /= 100) out[i] = (u8)((mag % 100 / 10) << 4 | mag % 10);
	out[9] = x.s << 7;
	return;
bad:
	*fsw |= IE;
	out[7] = 0xC0; out[8] = out[9] = 0xFF;
}
