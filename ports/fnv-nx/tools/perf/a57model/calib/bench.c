/* bench.c - calibration kernels for the A57 model (docs/PERF.md 12). Bare metal (start.S), one core.
 *
 * Each kernel prints "K <name> <units>" through semihosting, runs once to warm caches and predictors, then runs
 * again between the region markers (hint #112 / #113) that a57trace.so and uctrace.py count. <units> is what the
 * truth in truth.json is per (iterations, bytes, branches). The kernels are written in inline assembly where the
 * instruction sequence is the point, so the compiler cannot change what is measured.
 */
#include <stdint.h>

void semi_write0(const char *s);
extern char __heap[];

#define MARK_START() __asm__ volatile("hint #112" ::: "memory")
#define MARK_STOP() __asm__ volatile("hint #113" ::: "memory")

static char *heap_p;
static void *alloc(uint64_t n) {
  if (!heap_p) heap_p = __heap;
  heap_p = (char *)(((uintptr_t)heap_p + 4095) & ~(uintptr_t)4095);
  void *p = heap_p;
  heap_p += n;
  return p;
}
static void put(const char *s) { semi_write0(s); }
static void putu(uint64_t v) {
  char b[24]; int i = 23; b[i] = 0;
  do { b[--i] = (char)('0' + v % 10); v /= 10; } while (v);
  put(b + i);
}
static void kernel(const char *name, uint64_t units) { put("K "); put(name); put(" "); putu(units); put("\n"); }

static uint64_t rng = 0x9e3779b97f4a7c15ull;
static uint64_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return rng; }

/* ---- pointer chase over `bytes`, one pointer per 64-byte line, random cyclic order ---- */
static void **chase_build(uint64_t bytes) {
  uint64_t n = bytes / 64;
  void **base = alloc(bytes);
  uint32_t *perm = alloc(n * 4);
  for (uint64_t i = 0; i < n; i++) perm[i] = (uint32_t)i;
  for (uint64_t i = n - 1; i > 0; i--) { uint64_t j = rnd() % (i + 1); uint32_t t = perm[i]; perm[i] = perm[j]; perm[j] = t; }
  for (uint64_t i = 0; i < n; i++) base[perm[i] * 8] = &base[perm[(i + 1) % n] * 8];
  return base;
}
static void *chase_run(void **p, uint64_t iters) {
  __asm__ volatile(
      "1: ldr %0, [%0]\n ldr %0, [%0]\n ldr %0, [%0]\n ldr %0, [%0]\n"
      " ldr %0, [%0]\n ldr %0, [%0]\n ldr %0, [%0]\n ldr %0, [%0]\n"
      " subs %1, %1, #8\n b.ne 1b\n"
      : "+r"(p), "+r"(iters)::"memory");
  return p;
}
static void k_chase(const char *name, uint64_t bytes, uint64_t iters) {
  void **p = chase_build(bytes);
  void *q = chase_run(p, iters > bytes / 64 ? iters : bytes / 64);
  kernel(name, iters);
  MARK_START();
  chase_run(q, iters);
  MARK_STOP();
}

/* ---- dependent chains and independent throughput, 8 instructions per loop iteration ---- */
#define CHAIN8(ins) ins "\n" ins "\n" ins "\n" ins "\n" ins "\n" ins "\n" ins "\n" ins "\n"
#define LOOP(body, iters, ...)                                                                                   \
  do {                                                                                                           \
    uint64_t n_ = (iters);                                                                                       \
    __asm__ volatile("1:\n" body " subs %[n], %[n], #8\n b.ne 1b\n" : [n] "+r"(n_) : __VA_ARGS__ : "x9", "x10", \
                     "x11", "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v16", "v17", "memory"); \
  } while (0)

static void k_alu(void) {
  uint64_t it = 80000;
  LOOP(CHAIN8("add x9, x9, #1"), 8000, "r"(0)); kernel("add_chain", it); MARK_START();
  LOOP(CHAIN8("add x9, x9, #1"), it, "r"(0)); MARK_STOP();
  LOOP(CHAIN8("mul x9, x9, x10"), 8000, "r"(0)); kernel("mul64_chain", it); MARK_START();
  LOOP(CHAIN8("mul x9, x9, x10"), it, "r"(0)); MARK_STOP();
  /* the chain runs through a multiplicand: through the accumulator the A57 forwards late (shorter) */
  LOOP(CHAIN8("fmla v0.4s, v0.4s, v1.4s"), 8000, "r"(0)); kernel("fmla_chain", it); MARK_START();
  LOOP(CHAIN8("fmla v0.4s, v0.4s, v1.4s"), it, "r"(0)); MARK_STOP();
  kernel("fmla_tput", it); MARK_START();
  LOOP("fmla v0.4s, v16.4s, v17.4s\n fmla v1.4s, v16.4s, v17.4s\n fmla v2.4s, v16.4s, v17.4s\n"
       "fmla v3.4s, v16.4s, v17.4s\n fmla v4.4s, v16.4s, v17.4s\n fmla v5.4s, v16.4s, v17.4s\n"
       "fmla v6.4s, v16.4s, v17.4s\n fmla v7.4s, v16.4s, v17.4s\n", it, "r"(0)); MARK_STOP();
  kernel("vadd_chain", it); MARK_START();
  LOOP(CHAIN8("add v0.4s, v0.4s, v1.4s"), it, "r"(0)); MARK_STOP();
  kernel("vmul_chain", it); MARK_START();
  LOOP(CHAIN8("mul v0.4s, v0.4s, v1.4s"), it, "r"(0)); MARK_STOP();
  kernel("vmul_tput", it); MARK_START();
  LOOP("mul v0.4s, v16.4s, v17.4s\n mul v1.4s, v16.4s, v17.4s\n mul v2.4s, v16.4s, v17.4s\n"
       "mul v3.4s, v16.4s, v17.4s\n mul v4.4s, v16.4s, v17.4s\n mul v5.4s, v16.4s, v17.4s\n"
       "mul v6.4s, v16.4s, v17.4s\n mul v7.4s, v16.4s, v17.4s\n", it, "r"(0)); MARK_STOP();
}

/* store-to-load forwarding: the loaded value feeds the next store's data */
static void k_stlf(void) {
  uint64_t *b = alloc(4096), it = 40000;
  kernel("stlf_chain", it); MARK_START();
  LOOP(CHAIN8("str x9, [%[b]]\n ldr x9, [%[b]]"), it, [b] "r"(b)); MARK_STOP();
}

/* read bandwidth: 128 bytes per iteration with ldp q, over a buffer of `bytes` */
static void bw_run(char *buf, uint64_t bytes, uint64_t passes) {
  for (uint64_t p = 0; p < passes; p++) {
    char *q = buf; uint64_t n = bytes / 128;
    __asm__ volatile("1: ldp q0, q1, [%0]\n ldp q2, q3, [%0, #32]\n ldp q4, q5, [%0, #64]\n ldp q6, q7, [%0, #96]\n"
                     " add %0, %0, #128\n subs %1, %1, #1\n b.ne 1b\n"
                     : "+r"(q), "+r"(n)::"v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7", "memory");
  }
}
static void k_bw(const char *name, uint64_t bytes, uint64_t passes) {
  char *b = alloc(bytes);
  bw_run(b, bytes, 1);
  kernel(name, bytes * passes);
  MARK_START(); bw_run(b, bytes, passes); MARK_STOP();
}

/* memcpy as glibc's AArch64 generic loop does it: 64 bytes per iteration, ldp/stp q */
static void copy_run(char *d, const char *s, uint64_t bytes) {
  uint64_t n = bytes / 64;
  __asm__ volatile("1: ldp q0, q1, [%1]\n ldp q2, q3, [%1, #32]\n stp q0, q1, [%0]\n stp q2, q3, [%0, #32]\n"
                   " add %0, %0, #64\n add %1, %1, #64\n subs %2, %2, #1\n b.ne 1b\n"
                   : "+r"(d), "+r"(s), "+r"(n)::"v0", "v1", "v2", "v3", "memory");
}
static void k_memcpy(void) {
  uint64_t bytes = 8u << 20;
  char *s = alloc(bytes), *d = alloc(bytes);
  copy_run(d, s, bytes);
  kernel("memcpy_8m", bytes); MARK_START(); copy_run(d, s, bytes); MARK_STOP();
}

/* conditional branch on a byte array: random (50 %) vs all zero; the difference prices a mispredict */
static uint64_t br_run(const uint8_t *a, uint64_t n) {
  uint64_t x = 0;
  __asm__ volatile("1: ldrb w9, [%1], #1\n cbz w9, 2f\n add %0, %0, #1\n 2: subs %2, %2, #1\n b.ne 1b\n"
                   : "+r"(x), "+r"(a), "+r"(n)::"x9", "memory");
  return x;
}
static void k_branch(void) {
  uint64_t n = 40000;
  uint8_t *r = alloc(n), *z = alloc(n);
  for (uint64_t i = 0; i < n; i++) { r[i] = (uint8_t)(rnd() >> 40 & 1); z[i] = 0; }
  br_run(z, n); kernel("branch_zero", n); MARK_START(); br_run(z, n); MARK_STOP();
  br_run(r, n); kernel("branch_random", n); MARK_START(); br_run(r, n); MARK_STOP();
}

/* chains of taken unconditional branches, 32 (inside the 64-entry L1 BTB with the loop branch) and 1024 (one per 16 bytes), each chain run 200 times */
#define B16 "b 3f\n nop\n nop\n nop\n 3:\n"
#define B64 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 \
            B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 \
            B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 \
            B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16
#define B32 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 \
            B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16 B16
#define B1024 B64 B64 B64 B64 B64 B64 B64 B64 B64 B64 B64 B64 B64 B64 B64 B64
static void tb32(uint64_t r) { __asm__ volatile("1:\n" B32 "subs %0, %0, #1\n b.ne 1b\n" : "+r"(r)); }
static void tb1024(uint64_t r) { __asm__ volatile("1:\n" B1024 "subs %0, %0, #1\n b.ne 1b\n" : "+r"(r)); }
static void k_taken(void) {
  tb32(10); kernel("taken_32", 32 * 200); MARK_START(); tb32(200); MARK_STOP();
  tb1024(10); kernel("taken_1024", 1024 * 200); MARK_START(); tb1024(200); MARK_STOP();
}

/* math kernels: dot product (L1-resident, 4 accumulators) and 4x4 float matrix multiply (NiMatrix-style) */
static void dot_run(const float *a, const float *b, uint64_t n) {
  __asm__ volatile("movi v0.4s, #0\n movi v1.4s, #0\n movi v2.4s, #0\n movi v3.4s, #0\n"
                   "1: ldp q4, q5, [%0], #32\n ldp q6, q7, [%1], #32\n ldp q8, q9, [%0], #32\n ldp q16, q17, [%1], #32\n"
                   " fmla v0.4s, v4.4s, v6.4s\n fmla v1.4s, v5.4s, v7.4s\n fmla v2.4s, v8.4s, v16.4s\n"
                   " fmla v3.4s, v9.4s, v17.4s\n subs %2, %2, #16\n b.ne 1b\n"
                   : "+r"(a), "+r"(b), "+r"(n)::"v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v16",
                     "v17", "memory");
}
static void mat4_run(float *o, const float *a, const float *b, uint64_t reps) {
  for (uint64_t r = 0; r < reps; r++)
    for (int i = 0; i < 4; i++)
      for (int j = 0; j < 4; j++) {
        float s = 0;
        for (int k = 0; k < 4; k++) s += a[i * 4 + k] * b[k * 4 + j];
        o[i * 4 + j] = s;
      }
}
static void k_math(void) {
  uint64_t n = 2048, reps = 100;
  float *a = alloc(n * 4), *b = alloc(n * 4), *m = alloc(256);
  for (uint64_t i = 0; i < n; i++) { a[i] = (float)i; b[i] = 1.0f; }
  dot_run(a, b, n); kernel("dot_l1", n * reps); MARK_START();
  for (uint64_t r = 0; r < reps; r++) dot_run(a, b, n);
  MARK_STOP();
  mat4_run(m + 32, m, m + 16, 10); kernel("mat4_scalar", 2000); MARK_START(); mat4_run(m + 32, m, m + 16, 2000); MARK_STOP();
}

/* Interlocked cost (PERF.md 6.7): the inlined InterlockedIncrement body (ldaxr/stlxr loop + dmb ish), alone and
 * after a store; predictions only, no published A57 number to calibrate against */
static void ilk_run(uint32_t *p, uint64_t n, int store) {
  if (store)
    __asm__ volatile("1: str wzr, [%0, #64]\n 2: ldaxr w9, [%0]\n add w9, w9, #1\n stlxr w10, w9, [%0]\n cbnz w10, 2b\n"
                     " dmb ish\n subs %1, %1, #1\n b.ne 1b\n" : "+r"(p), "+r"(n)::"x9", "x10", "memory");
  else
    __asm__ volatile("1: ldaxr w9, [%0]\n add w9, w9, #1\n stlxr w10, w9, [%0]\n cbnz w10, 1b\n"
                     " dmb ish\n subs %1, %1, #1\n b.ne 1b\n" : "+r"(p), "+r"(n)::"x9", "x10", "memory");
}
static void k_ilk(void) {
  uint32_t *p = alloc(4096);
  ilk_run(p, 1000, 0); kernel("ilk_inline", 20000); MARK_START(); ilk_run(p, 20000, 0); MARK_STOP();
  kernel("ilk_after_store", 20000); MARK_START(); ilk_run(p, 20000, 1); MARK_STOP();
}

int main(void) {
  k_chase("chase_4k", 4096, 40000);
  k_chase("chase_96k", 96 * 1024, 40000);
  k_chase("chase_1m5", 1536 * 1024, 40000);
  k_chase("chase_64m", 64u << 20, 20000);
  k_alu();
  k_stlf();
  k_bw("bw_l1_16k", 16 * 1024, 40);
  k_bw("bw_l2_1m", 1024 * 1024, 2);
  k_memcpy();
  k_branch();
  k_taken();
  k_math();
  k_ilk();
  put("done\n");
  return 0;
}
