// shuffle_test.cpp - shim/include/fnvnx_sse_fast.h: _mm_shuffle_ps against the plain lane rule, for all 256
// immediates, with two different operands and with the same operand named twice; the scalar forms against
// sse2neon's own (rsqrt_ss, set_ss, load_ss: the same bits; add_ss: lane 0 the same, lanes 1-3 a's bits as on x86) (tests/s15/simd/run.py runs it
// under qemu-aarch64). Prints "shuffle: <n> cases, <m> mismatches".
#include <sse2neon.h>
// sse2neon's own scalar forms, captured before fnvnx_sse_fast.h redefines the names
static __attribute__((noinline)) __m128 orig_rsqrt_ss(__m128 a) { return _mm_rsqrt_ss(a); }
static __attribute__((noinline)) __m128 orig_add_ss(__m128 a, __m128 b) { return _mm_add_ss(a, b); }
static __attribute__((noinline)) __m128 orig_set_ss(float a) { return _mm_set_ss(a); }
static __attribute__((noinline)) __m128 orig_load_ss(const float *p) { return _mm_load_ss(p); }
#include "fnvnx_sse_fast.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <utility>

static long cases, bad;

static void cmp(int imm, int same, __m128 got, const float *a, const float *b)
{
    float want[4] = {a[imm & 3], a[(imm >> 2) & 3], b[(imm >> 4) & 3], b[(imm >> 6) & 3]};
    float g[4];
    memcpy(g, &got, 16);
    cases++;
    if (memcmp(g, want, 16) && bad++ < 8)
        printf("mismatch imm=0x%02x same=%d\n", imm, same);
}

template <int I> static __attribute__((noinline)) void one(const float *pa, const float *pb)
{
    __m128 a = _mm_loadu_ps(pa), b = _mm_loadu_ps(pb);
    cmp(I, 0, _mm_shuffle_ps(a, b, I), pa, pb);
    cmp(I, 1, _mm_shuffle_ps(a, a, I), pa, pa);
}

template <int... I> static void all(const float *a, const float *b, std::integer_sequence<int, I...>)
{
    (one<I>(a, b), ...);
}

int main()
{
    uint32_t s = 12345;
    for (int r = 0; r < 64; r++) {
        float a[4], b[4];
        for (int k = 0; k < 4; k++) {
            s = s * 1664525u + 1013904223u; uint32_t u = s; memcpy(&a[k], &u, 4);   // any bits, NaNs included
            s = s * 1664525u + 1013904223u; u = s; memcpy(&b[k], &u, 4);
        }
        all(a, b, std::make_integer_sequence<int, 256>{});
        for (int k = 0; k < 4; k++) {   // scalar forms, on each lane value as lane 0 and random/edge values
            static const uint32_t edge[] = {0, 0x80000000u, 0x7f800000u, 0xff800000u, 0x7fc00000u, 0x7fa00001u,
                                            1, 0x807fffffu, 0x3f800000u};
            float va[4], vb[4];
            memcpy(va, a, 16); memcpy(vb, b, 16);
            if (r & 1) memcpy(&va[0], &edge[(r / 2 + k) % 9], 4);
            va[0] = k ? va[k] : va[0];
            __m128 x = _mm_loadu_ps(va), y = _mm_loadu_ps(vb);
            __m128 g1 = _mm_rsqrt_ss(x), o1 = orig_rsqrt_ss(x);
            __m128 g2 = _mm_add_ss(x, y), o2 = orig_add_ss(x, y);
            __m128 g3 = _mm_set_ss(va[0]), o3 = orig_set_ss(va[0]);
            __m128 g4 = _mm_load_ss(&va[0]), o4 = orig_load_ss(&va[0]);
            float w2[4];
            memcpy(w2, &o2, 4); memcpy(w2 + 1, va + 1, 12);   // x86: lanes 1-3 of a unchanged
            cases += 4;
            if (memcmp(&g1, &o1, 16) && bad++ < 8) printf("mismatch rsqrt_ss %08x\n", *(uint32_t *)va);
            if (memcmp(&g2, w2, 16) && bad++ < 8) printf("mismatch add_ss\n");
            if (memcmp(&g3, &o3, 16) && bad++ < 8) printf("mismatch set_ss\n");
            if (memcmp(&g4, &o4, 16) && bad++ < 8) printf("mismatch load_ss\n");
        }
    }
    printf("shuffle: %ld cases, %ld mismatches\n", cases, bad);
    return bad != 0;
}
