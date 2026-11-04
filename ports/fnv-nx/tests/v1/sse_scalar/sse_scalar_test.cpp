// V1 (D14) against S15-simd 8fc5af8: the scalar forms of shim/include/fnvnx_sse_fast.h
// (_mm_rsqrt_ss, _mm_add_ss, _mm_set_ss, _mm_load_ss) against
//   sse2neon's own functions (captured before the header: the claim is "the same bits" for rsqrt/set/load), and
//   a software model of x86 SSE for the lanes: addss / movss keep or zero lanes 1-3 exactly as the Intel SDM says.
// _mm_load_ss is also exercised at addresses the "m" constraint may hand the asm with an offset (unaligned, negative,
// large), which `ldr s` must accept (GNU as turns an unscaled offset into ldur).
// Built by tests/v1/test_s15_sse_scalar.py at -O0, -O2 and -O3; synthetic inputs only.
#include <sse2neon.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static __attribute__((noinline)) __m128 orig_rsqrt_ss(__m128 a) { return _mm_rsqrt_ss(a); }
static __attribute__((noinline)) __m128 orig_rsqrt_ps(__m128 a) { return _mm_rsqrt_ps(a); }
static __attribute__((noinline)) __m128 orig_add_ss(__m128 a, __m128 b) { return _mm_add_ss(a, b); }
static __attribute__((noinline)) __m128 orig_set_ss(float a) { return _mm_set_ss(a); }
static __attribute__((noinline)) __m128 orig_load_ss(const float *p) { return _mm_load_ss(p); }

#include "fnvnx_sse_fast.h"

#ifndef FNVNX_SSE_FAST_OFF
#if !defined(_mm_rsqrt_ss) || !defined(_mm_add_ss) || !defined(_mm_set_ss) || !defined(_mm_load_ss)
#error "fnvnx_sse_fast.h does not replace the four scalar forms"
#endif
#endif

static uint64_t s = 0x9E3779B97F4A7C15ull;
static uint32_t rnd() { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return (uint32_t)(s >> 11); }
static float f(uint32_t u) { float x; memcpy(&x, &u, 4); return x; }
static uint32_t u(float x) { uint32_t v; memcpy(&v, &x, 4); return v; }

static const uint32_t EDGE[] = {0x00000000u, 0x80000000u, 0x7f800000u, 0xff800000u, 0x7fc00000u, 0xffc00001u,
                                0x7f800001u, 0xff800123u, 0x00000001u, 0x807fffffu, 0x00800000u, 0x3f800000u,
                                0xbf800000u, 0x7f7fffffu, 0x5f3759dfu, 0x3e800000u, 0x40800000u, 0x00400000u};

int main() {
    int bad = 0;
    long n = 0;
    alignas(16) static unsigned char buf[64 + 4096 + 4096];   // p - 7 .. p + 1000 floats stay inside
    for (int it = 0; it < 40000; it++) {
        uint32_t a[4], b[4];
        for (int i = 0; i < 4; i++) {
            a[i] = it < 18 * 18 ? EDGE[(it / 18 + i) % 18] : rnd();
            b[i] = it < 18 * 18 ? EDGE[(it + i) % 18] : rnd();
        }
        if (it % 3 == 0) a[0] = (rnd() & 0x80000000u) | (0x30000000u + (rnd() & 0x1fffffffu));   // ordinary floats
        __m128 va, vb;
        memcpy(&va, a, 16);
        memcpy(&vb, b, 16);
        uint32_t g[4], o[4], m[4];
        // rsqrt_ss: same bits as sse2neon's; lanes 1-3 are a's bits (x86 rsqrtss)
        __m128 r1 = _mm_rsqrt_ss(va), r2 = orig_rsqrt_ss(va), r3 = orig_rsqrt_ps(va);
        memcpy(g, &r1, 16); memcpy(o, &r2, 16); memcpy(m, &r3, 16);
        if ((memcmp(g, o, 16) || g[0] != m[0] || g[1] != a[1] || g[2] != a[2] || g[3] != a[3]) && bad++ < 10)
            printf("FAIL rsqrt_ss in %08x: %08x %08x %08x %08x, sse2neon %08x\n", a[0], g[0], g[1], g[2], g[3], o[0]);
        // add_ss: lane 0 = sse2neon's a0 + b0 (which NaN of two comes out is GCC's operand order in both; x86 takes
        // a's: not claimed), lanes 1-3 = a's bits exactly (x86 addss)
        __m128 r4 = _mm_add_ss(va, vb), r4o = orig_add_ss(va, vb);
        memcpy(g, &r4, 16); memcpy(o, &r4o, 16);
        // with two NaN operands either NaN is IEEE-correct and GCC picks the operand order of fadd in both forms:
        // accept sse2neon's or x86's (a's, quieted)
        bool nan2 = (a[0] & 0x7fffffffu) > 0x7f800000u && (b[0] & 0x7fffffffu) > 0x7f800000u;
        bool lane0 = g[0] == o[0] || (nan2 && g[0] == (a[0] | 0x00400000u));
        if ((!lane0 || g[1] != a[1] || g[2] != a[2] || g[3] != a[3]) && bad++ < 10)
            printf("FAIL add_ss %08x+%08x: %08x %08x %08x %08x (a %08x %08x %08x)\n", a[0], b[0], g[0], g[1], g[2], g[3],
                   a[1], a[2], a[3]);
        // set_ss / load_ss: {x, 0, 0, 0} with x's bits (no quieting), at any offset
        __m128 r5 = _mm_set_ss(f(a[1])), r6 = orig_set_ss(f(a[1]));
        memcpy(g, &r5, 16); memcpy(o, &r6, 16);
        if ((g[0] != a[1] || g[1] | g[2] | g[3] || memcmp(g, o, 16)) && bad++ < 10)
            printf("FAIL set_ss %08x: %08x %08x %08x %08x\n", a[1], g[0], g[1], g[2], g[3]);
        int off = (int)(rnd() % 4093);
        memcpy(buf + 32 + off, &a[2], 4);
        const float *p = (const float *)(buf + 32 + off);
        __m128 r7 = _mm_load_ss(p), r8 = orig_load_ss(p), r9 = _mm_load_ss(p - 7), r10 = _mm_load_ss(p + 1000);
        memcpy(g, &r7, 16); memcpy(o, &r8, 16);
        uint32_t w9[4], w10[4], e9, e10;
        memcpy(w9, &r9, 16); memcpy(w10, &r10, 16);
        memcpy(&e9, (const char *)(p - 7), 4);
        memcpy(&e10, (const char *)(p + 1000), 4);
        if ((g[0] != a[2] || g[1] | g[2] | g[3] || memcmp(g, o, 16) || w9[0] != e9 || w9[1] | w9[2] | w9[3] ||
             w10[0] != e10 || w10[1] | w10[2] | w10[3]) && bad++ < 10)
            printf("FAIL load_ss at +%d: %08x %08x %08x %08x (want %08x)\n", off, g[0], g[1], g[2], g[3], a[2]);
        n += 4;
    }
    printf("%ld cases, %d mismatches\n", n, bad);
    return bad != 0;
}
