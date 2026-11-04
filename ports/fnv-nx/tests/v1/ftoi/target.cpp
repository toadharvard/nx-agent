// target.cpp - V1's FTOI2 fuzz target (AArch64, compiled with tools/link/compile.sh's flags and the fnvnx_ftoi
// plugin, run under qemu-aarch64): the same conversions as ref.c written as the decomp writes them (casts, implicit
// conversions, compound assignments, enums, bit-fields, the sse2neon intrinsics). Lines equal ref.c's.
#include <stdio.h>
#include "gen.h"
#ifdef WITH_SSE2NEON
#include "fnvnx_sse2neon.h"   // the port's sse2neon (shim/include): sse2neon with its x86 fixes
#endif

enum E { E0, E1 = 5, E2 = 1000 };   // GCC: underlying unsigned int (all enumerators >= 0); MSVC: int
enum ES { ES_NEG = -1, ES1 = 1 };     // GCC: underlying int
enum EF : unsigned int { EF0, EF1 };  // a fixed unsigned base: unsigned int on both (MSVC 2008 extension)
struct BF { int a : 5; unsigned b : 20; unsigned long long c : 40; };
#define NI __attribute__((noinline))
#define CONV(name, T) NI unsigned long long name##_d(double x) { return (T)x; } NI unsigned long long name##_f(float x) { return (T)x; }
CONV(i8, signed char) CONV(ch, char) CONV(u8, unsigned char) CONV(i16, short) CONV(u16, unsigned short)
CONV(wc, wchar_t) CONV(i32, int) CONV(en, E) CONV(ens, ES) CONV(enf, EF) CONV(u32, unsigned int) CONV(ul, unsigned long) CONV(i64, long long)
CONV(u64, unsigned long long)
template <class X> NI unsigned long long bf5(X x) { BF s{}; s.a = x; return (unsigned long long)s.a; }
template <class X> NI unsigned long long bfu20(X x) { BF s{}; s.b = x; return s.b; }
template <class X> NI unsigned long long bfu40(X x) { BF s{}; s.c = x; return s.c; }
template <class X> NI unsigned long long ca32(X x) { int i = 7; i += x; return (unsigned)i; }
template <class X> NI unsigned long long cau32(X x) { unsigned u = 7; u += x; return u; }
template <class X> NI unsigned long long cas16(X x) { short s = 7; s -= x; return (unsigned short)s; }
#define M(w, v) ((unsigned long long)(v) & ((w) == 64 ? ~0ull : ((1ull << (w)) - 1)))

static inline int conv_inl(double x) { return (int)x; }
NI int k0() { return conv_inl(1e10); }
NI int k1() { return conv_inl(-1e10); }
NI int k2() { return conv_inl(3e9); }
NI int k3() { return conv_inl(1e19); }
NI int k4() { return conv_inl(-1.5); }
NI int k5() { return conv_inl(2147483647.5); }

#define ONE(X, x)                                                                                                    \
  P("i8", 8, i8_##X(x)); P("ch", 8, ch_##X(x)); P("u8", 8, u8_##X(x)); P("i16", 16, i16_##X(x));                 \
  P("u16", 16, u16_##X(x)); P("wc", 16, wc_##X(x)); P("i32", 32, i32_##X(x)); P("en", 32, en_##X(x));            \
  P("ens", 32, ens_##X(x)); P("enf", 32, enf_##X(x));                                                            \
  P("u32", 32, u32_##X(x)); P("ul", 32, ul_##X(x)); P("i64", 64, i64_##X(x)); P("u64", 64, u64_##X(x));          \
  P("bf5", 5, bf5(x)); P("bfu20", 20, bfu20(x)); P("bfu40", 40, bfu40(x));                                        \
  P("ca32", 32, ca32(x)); P("cau32", 32, cau32(x)); P("cas16", 16, cas16(x))

#ifdef WITH_SSE2NEON
#define MM(x) P("mm_cvtss", 32, (unsigned)_mm_cvtss_si32(_mm_set_ss(x))); P("mm_cvttss", 32, (unsigned)_mm_cvttss_si32(_mm_set_ss(x)))
#else
#define MM(x)
#endif

int main() {
  static double d[GEN_MAX];
  static float f[GEN_MAX];
  unsigned nd = gen_doubles(d), nf = gen_floats(f);
  for (unsigned i = 0; i < nd; i++) {
    double x = d[i];
    unsigned long long b = d_bits(x);
    const char *s = "d";
#define P(n, w, v) printf("%s %s %llx %llx\n", n, s, b, M(w, v))
    ONE(d, x);
    float g = (float)x;
    b = f_bits(g); s = "f";
    ONE(f, g); MM(g);
  }
  for (unsigned i = 0; i < nf; i++) {
    float g = f[i];
    unsigned long long b = f_bits(g);
    const char *s = "f";
    ONE(f, g); MM(g);
  }
  static const double k[] = {1e10, -1e10, 3e9, 1e19, -1.5, 2147483647.5};
  int (*kf[])() = {k0, k1, k2, k3, k4, k5};
  for (unsigned i = 0; i < 6; i++) printf("inl d %llx %llx\n", d_bits(k[i]), M(32, kf[i]()));
  return 0;
}
