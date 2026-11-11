/* ref.c - V1's FTOI2 fuzz reference (x86-64 host): what MSVC 2008's code gives on an SSE2 PC, with the instructions
 * it uses (toolchain/ftoi/fnvnx_ftoi.cc's table): cvttsd2si (__ftol2_sse: int, short, signed char, char, enum, signed
 * bit-fields), x87 truncating store DWORD (inline fistp DWORD: unsigned short / char / wchar_t), QWORD (inline
 * fistp QWORD: unsigned int / long, __ftol2: __int64, unsigned __int64). A bit-field converts to its declared type
 * (int, unsigned int, unsigned __int64) and keeps the low bits, as C++ says and GCC's front end does (assumed for
 * MSVC: no MSVC object of a bit-field conversion was checked), cvtss2si / cvttss2si for the SSE intrinsics _mm_cvtss_si32 / _mm_cvttss_si32 (MXCSR default: nearest).
 * Same lines as target.cpp: `<conv> <d|f> <input bits> <result, masked to the type's width>`. */
#include <stdio.h>
#include "gen.h"

static int32_t sse32(double x) { int32_t r; __asm__("cvttsd2si %1, %0" : "=r"(r) : "x"(x)); return r; }
static int32_t x87_32(double x) { int32_t r; __asm__("fisttpl %0" : "=m"(r) : "t"(x) : "st"); return r; }
static int64_t x87_64(double x) { int64_t r; __asm__("fisttpll %0" : "=m"(r) : "t"(x) : "st"); return r; }
static int32_t cvtss(float x) { int32_t r; __asm__("cvtss2si %1, %0" : "=r"(r) : "x"(x)); return r; }
static int32_t cvttss(float x) { int32_t r; __asm__("cvttss2si %1, %0" : "=r"(r) : "x"(x)); return r; }
#define M(w, v) ((unsigned long long)(v) & ((w) == 64 ? ~0ull : ((1ull << (w)) - 1)))

static void one(const char *s, unsigned long long b, double x, double c7, double u7, double s7, int isf, float f) {
  printf("i8 %s %llx %llx\n", s, b, M(8, sse32(x)));
  printf("ch %s %llx %llx\n", s, b, M(8, sse32(x)));
  printf("u8 %s %llx %llx\n", s, b, M(8, x87_32(x)));
  printf("i16 %s %llx %llx\n", s, b, M(16, sse32(x)));
  printf("u16 %s %llx %llx\n", s, b, M(16, x87_32(x)));
  printf("wc %s %llx %llx\n", s, b, M(16, x87_32(x)));
  printf("i32 %s %llx %llx\n", s, b, M(32, sse32(x)));
  printf("en %s %llx %llx\n", s, b, M(32, sse32(x)));     /* (E)(int)x */
  printf("ens %s %llx %llx\n", s, b, M(32, sse32(x)));    /* (ES)(int)x */
  printf("enf %s %llx %llx\n", s, b, M(32, x87_64(x)));   /* (EF)(unsigned)x: fistp QWORD */
  printf("u32 %s %llx %llx\n", s, b, M(32, x87_64(x)));
  printf("ul %s %llx %llx\n", s, b, M(32, x87_64(x)));
  printf("i64 %s %llx %llx\n", s, b, M(64, x87_64(x)));
  printf("u64 %s %llx %llx\n", s, b, M(64, x87_64(x)));
  printf("bf5 %s %llx %llx\n", s, b, M(5, sse32(x)));
  printf("bfu20 %s %llx %llx\n", s, b, M(20, x87_64(x)));   /* unsigned int : 20 */
  printf("bfu40 %s %llx %llx\n", s, b, M(40, x87_64(x)));
  printf("ca32 %s %llx %llx\n", s, b, M(32, sse32(c7)));     /* int i = 7; i += x */
  printf("cau32 %s %llx %llx\n", s, b, M(32, x87_64(u7)));   /* unsigned u = 7; u += x */
  printf("cas16 %s %llx %llx\n", s, b, M(16, sse32(s7)));    /* short s = 7; s -= x */
  if (isf) {
    printf("mm_cvtss %s %llx %llx\n", s, b, M(32, cvtss(f)));
    printf("mm_cvttss %s %llx %llx\n", s, b, M(32, cvttss(f)));
  }
}

int main(void) {
  static double d[GEN_MAX];
  static float f[GEN_MAX];
  unsigned nd = gen_doubles(d), nf = gen_floats(f);
  for (unsigned i = 0; i < nd; i++) {
    double x = d[i];
    one("d", d_bits(x), x, 7.0 + x, 7.0 + x, 7.0 - x, 0, 0);
    float g = (float)x;
    one("f", f_bits(g), (double)g, (double)(7.0f + g), (double)(7.0f + g), (double)(7.0f - g), 1, g);
  }
  for (unsigned i = 0; i < nf; i++) {
    float g = f[i];
    one("f", f_bits(g), (double)g, (double)(7.0f + g), (double)(7.0f + g), (double)(7.0f - g), 1, g);
  }
  /* constants through inlining (the plugin rewrites conv_inl before it is inlined and folded) */
  static const double k[] = {1e10, -1e10, 3e9, 1e19, -1.5, 2147483647.5};
  for (unsigned i = 0; i < sizeof k / sizeof k[0]; i++)
    printf("inl %s %llx %llx\n", "d", d_bits(k[i]), M(32, sse32(k[i])));
  return 0;
}
