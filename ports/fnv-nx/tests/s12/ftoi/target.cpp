// target.cpp - the same conversions as plain C++ casts, an implicit conversion and a compound assignment, compiled
// for AArch64 with the fnvnx_ftoi plugin and run under qemu (tests/s12/run.py): the output must equal ref.c's.
// Without the plugin GCC's fcvtzs / fcvtzu saturate and the outputs differ.
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "cases.h"

#define CONV(name, T)                                                                \
  __attribute__((noinline)) unsigned long long name##_d(double x) { return (T)x; } \
  __attribute__((noinline)) unsigned long long name##_f(float x) { return (T)x; }
CONV(i8, int8_t)
CONV(u8, uint8_t)
CONV(i16, int16_t)
CONV(u16, uint16_t)
CONV(i32, int32_t)
CONV(u32, uint32_t)
CONV(i64, int64_t)
CONV(u64, uint64_t)
#define MASK(T, v) ((unsigned long long)(sizeof(T) == 8 ? (v) : ((v) & ((1ull << (8 * sizeof(T))) - 1))))
__attribute__((noinline)) int32_t implicit_f(float x) { int32_t r = x; return r; }
__attribute__((noinline)) int32_t compound_d(double x) { int32_t i = 7; i += x; return i; }
__attribute__((noinline)) int32_t compound_f(float x) { int32_t i = 7; i += x; return i; }

int main() {
  for (unsigned i = 0; i < FTOI_N; i++) {
    double d;
    memcpy(&d, &FTOI_CASES[i], 8);
    float f = (float)d;
    unsigned long long b = FTOI_CASES[i];
    for (int s = 0; s < 2; s++) {
      const char *src = s ? "f" : "d";
#define OUT(name, T) printf(#name " %s %016llx %llx\n", src, b, MASK(T, s ? name##_f(f) : name##_d(d)))
      OUT(i8, int8_t); OUT(u8, uint8_t); OUT(i16, int16_t); OUT(u16, uint16_t);
      OUT(i32, int32_t); OUT(u32, uint32_t); OUT(i64, int64_t); OUT(u64, uint64_t);
      printf("ca32 %s %016llx %llx\n", src, b, (unsigned long long)(uint32_t)(s ? compound_f(f) : compound_d(d)));
      if (s && (uint32_t)implicit_f(f) != (uint32_t)i32_f(f)) printf("implicit differs %016llx\n", b);
    }
  }
  return 0;
}
