/* ref.c - what MSVC 2008's float -> integer conversions give on an x86 PC with SSE2, computed with the very
 * instructions it uses (host x86-64): cvttsd2si for __ftol2_sse (int, long, short, signed char), x87 fisttp DWORD
 * for the inline fistp DWORD (unsigned short / char), x87 fisttp QWORD for the inline fistp QWORD (unsigned int)
 * and __ftol2 (__int64, unsigned __int64). fisttp truncates like fistp under MSVC's truncating control word and
 * stores the same integer indefinite. Output: `<type> <d|f> <input bits> <result>` (tests/s12/run.py). */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "cases.h"

static int32_t sse32(double x) { int32_t r; __asm__("cvttsd2si %1, %0" : "=r"(r) : "x"(x)); return r; }
static int32_t x87_32(double x) { int32_t r; __asm__("fisttpl %0" : "=m"(r) : "t"(x) : "st"); return r; }
static int64_t x87_64(double x) { int64_t r; __asm__("fisttpll %0" : "=m"(r) : "t"(x) : "st"); return r; }

static void one(const char *src, unsigned long long bits, double x, double sum) {
  printf("i8 %s %016llx %llx\n", src, bits, (unsigned long long)(uint8_t)(int8_t)sse32(x));
  printf("u8 %s %016llx %llx\n", src, bits, (unsigned long long)(uint8_t)x87_32(x));
  printf("i16 %s %016llx %llx\n", src, bits, (unsigned long long)(uint16_t)(int16_t)sse32(x));
  printf("u16 %s %016llx %llx\n", src, bits, (unsigned long long)(uint16_t)x87_32(x));
  printf("i32 %s %016llx %llx\n", src, bits, (unsigned long long)(uint32_t)sse32(x));
  printf("u32 %s %016llx %llx\n", src, bits, (unsigned long long)(uint32_t)x87_64(x));
  printf("i64 %s %016llx %llx\n", src, bits, (unsigned long long)x87_64(x));
  printf("u64 %s %016llx %llx\n", src, bits, (unsigned long long)x87_64(x));
  printf("ca32 %s %016llx %llx\n", src, bits, (unsigned long long)(uint32_t)sse32(sum));   /* int i = 7; i += x */
}

int main(void) {
  for (unsigned i = 0; i < FTOI_N; i++) {
    double d;
    memcpy(&d, &FTOI_CASES[i], 8);
    float f = (float)d;
    one("d", FTOI_CASES[i], d, 7.0 + d);
    one("f", FTOI_CASES[i], (double)f, (double)(7.0f + f));
  }
  return 0;
}
