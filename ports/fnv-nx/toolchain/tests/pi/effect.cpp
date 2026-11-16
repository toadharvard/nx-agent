// pi_effect.cpp - EFFECT cases of toolchain/tests/test_pi.py: which arguments make the order of evaluation matter (an effect: a store, a volatile access, a throwing or
// non-returning construct, a call not known side-effect-free by the compiler's own facts)
#include <arm_neon.h>
int ga(); int gb(); int two(int, int);
int ca(int) __attribute__((const)); int cb(int) __attribute__((const)); int pa(int) __attribute__((pure));
#define W static inline __attribute__((always_inline))
W int w1(int a) { int t = a * 2; return t + 1; }                      // locals and the result are private
W int w2(int a) { return w1(a) + w1(a + 1); }                          // a summary of a summary
W int wst(int *p) { *p = 1; return 2; }                                // a store through a pointer
W int wcx(int a) { return ga() + a; }                                  // a call of an unknown function
W int wlp(int n) { int s = 0; for (int i = 0; i < n; i++) s += i; return s; }   // termination is not judged
W int wcb(int a) { return cb(a) + ca(a); }                             // calls of const functions
int nr(int) __attribute__((noreturn));
int x1(int x) { return two(ca(x), cb(x)); }                            // two const calls
int x2(int x) { return two(pa(x), ca(x)); }                            // pure and const
int x3(int x) { return two(w1(x), w2(x)); }                            // always_inline bodies without effects
int x4(int x) { return two(wcb(x), w2(x)); }
int x5(int *p, int x) { return two(wst(p), ga() + x); }                // a store in a body; an unknown call
int x6(int x) { return two(wcx(x), gb()); }                            // an unknown call in a body
int x7(int x) { return two(wlp(x), ga()); }                            // a loop in a body
int x8(int x) { return two(wlp(x), ca(x)); }                           // one effect only
int x9(int i) { return two(pa(i++), ga()); }                           // a pure function whose argument has an effect
int x9b(int i) { return two(pa(i++), ca(i)); }                         // ... and the other argument has none
int x10(int i) { return two(pa(ga()), pa(gb())); }                     // ... and the same twice
int x11(int x) { return two(nr(x), ga()); }                            // a call that does not return
int x12(int x) { return two(__builtin_abs(x), __builtin_popcount(x)); }   // built-ins with the const flag
int x13(int x) { return two(__builtin_abs(x), ga()); }
int x14(int x) { return two(x + 1, x * 2); }                           // no calls at all
int (*fp)(int);
int x15(int x) { return two(fp(x), ca(x)); }                           // an indirect call: unknown
int x16(int x) { return two(fp(x), fp(x + 1)); }
int32x4_t n1(int32x4_t a, int32x4_t b, int32x4_t c) { return vaddq_s32(vmulq_s32(a, b), vmulq_s32(a, c)); }   // NEON intrinsics
int32x4_t n2(int32x4_t a, int32x4_t b, int32x4_t c) { return vbslq_s32(vcgtq_s32(a, b), vmulq_s32(a, c), vaddq_s32(b, c)); }
int32x4_t n3(int32x4_t a, int *p) { return vaddq_s32(vld1q_s32(p), vdupq_n_s32(ga())); }   // a load and an unknown call
int32x4_t n5(int32x4_t a) { return vaddq_s32(vdupq_n_s32(ga()), vdupq_n_s32(gb())); }   // wrappers around effects
void n4(int32x4_t a, int *p, int *q) { vst1q_s32(p, vaddq_s32(a, vld1q_s32(q))); }
