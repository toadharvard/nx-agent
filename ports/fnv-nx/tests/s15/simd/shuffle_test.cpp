// shuffle_test.cpp - shim/include/fnvnx_sse_fast.h's _mm_shuffle_ps against the plain lane rule, for all 256
// immediates, with two different operands and with the same operand named twice (tests/s15/simd/run.py runs it
// under qemu-aarch64). Prints "shuffle: <n> cases, <m> mismatches".
#include <sse2neon.h>
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
    }
    printf("shuffle: %ld cases, %ld mismatches\n", cases, bad);
    return bad != 0;
}
