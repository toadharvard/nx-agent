/* gen.h - inputs of V1's FTOI2 fuzz (tests/v1/test_ftoi_fuzz.py), generated identically by ref.c (x86-64 host) and
 * target.cpp (AArch64): boundary values of every integer width (+-2^7, 2^8, 2^15, 2^16, 2^31, 2^32, 2^63, 2^64 and
 * k ulps around them, +-0.5 and +-1 around them), specials (+-0, +-inf, quiet and signalling NaNs with payloads,
 * denormals, +-DBL_MAX, +-FLT_MAX), then xorshift-random doubles (all bit patterns, and magnitudes 2^-2..2^70) and
 * random float bit patterns. Synthetic numbers only. */
#include <stdint.h>
#include <string.h>

static uint64_t g_rs = 0x9E3779B97F4A7C15ull;
static uint64_t rnd64(void) { g_rs ^= g_rs << 13; g_rs ^= g_rs >> 7; g_rs ^= g_rs << 17; return g_rs; }
static double bits_d(uint64_t b) { double d; memcpy(&d, &b, 8); return d; }
static uint64_t d_bits(double d) { uint64_t b; memcpy(&b, &d, 8); return b; }
static float bits_f(uint32_t b) { float f; memcpy(&f, &b, 4); return f; }
static uint32_t f_bits(float f) { uint32_t b; memcpy(&b, &f, 4); return b; }

#define GEN_MAX 8192
/* doubles -> out (returns the count) */
static unsigned gen_doubles(double *out) {
  unsigned n = 0;
  static const double base[] = {0.0, 0.5, 1.0, 128.0, 256.0, 32768.0, 65536.0, 2147483648.0, 4294967296.0,
                                9223372036854775808.0, 18446744073709551616.0, 1e10, 3e9, 1e19, 1e30};
  for (unsigned i = 0; i < sizeof base / sizeof base[0]; i++)
    for (int s = 0; s < 2; s++) {
      double b = s ? -base[i] : base[i];
      for (int k = -4; k <= 4; k++)
        if (b != 0.0) out[n++] = bits_d(d_bits(b) + (int64_t)k);   /* k ulps away (same sign, finite) */
      out[n++] = b + 0.5; out[n++] = b - 0.5; out[n++] = b + 1.0; out[n++] = b - 1.0;
    }
  static const uint64_t special[] = {0x0000000000000000ull, 0x8000000000000000ull, 0x7ff0000000000000ull,
                                     0xfff0000000000000ull, 0x7ff8000000000000ull, 0xfff8000000000000ull,
                                     0x7ff4000000000000ull, 0x7ff0000000000001ull, 0xfff0000000000001ull,
                                     0x7fffffffffffffffull, 0x0000000000000001ull, 0x800fffffffffffffull,
                                     0x7fefffffffffffffull, 0xffefffffffffffffull, 0x47efffffe0000000ull,
                                     0xc7efffffe0000000ull, 0x47effffff0000000ull};
  for (unsigned i = 0; i < sizeof special / sizeof special[0]; i++) out[n++] = bits_d(special[i]);
  while (n < 2048) out[n++] = bits_d(rnd64());                                                /* any bits */
  while (n < 6144) {                                                                         /* 2^-2 .. 2^70 */
    uint64_t r = rnd64(), e = 1021 + (r >> 52) % 73;
    out[n++] = bits_d((r & 0x800fffffffffffffull) | e << 52);
  }
  return n;
}
/* float bit patterns -> out */
static unsigned gen_floats(float *out) {
  unsigned n = 0;
  static const float base[] = {0.5f, 1.0f, 128.0f, 256.0f, 32768.0f, 65536.0f, 2147483648.0f, 4294967296.0f,
                               9223372036854775808.0f, 18446744073709551616.0f};
  for (unsigned i = 0; i < sizeof base / sizeof base[0]; i++)
    for (int s = 0; s < 2; s++)
      for (int k = -4; k <= 4; k++) out[n++] = bits_f(f_bits(s ? -base[i] : base[i]) + k);
  static const uint32_t special[] = {0x00000000u, 0x80000000u, 0x7f800000u, 0xff800000u, 0x7fc00000u, 0xffc00000u,
                                     0x7fa00000u, 0x7f800001u, 0x00000001u, 0x7f7fffffu, 0xff7fffffu};
  for (unsigned i = 0; i < sizeof special / sizeof special[0]; i++) out[n++] = bits_f(special[i]);
  while (n < 1024) out[n++] = bits_f((uint32_t)rnd64());
  while (n < 3072) {
    uint32_t r = (uint32_t)rnd64(), e = 125 + (r >> 23) % 73;
    out[n++] = bits_f((r & 0x807fffffu) | e << 23);
  }
  return n;
}
