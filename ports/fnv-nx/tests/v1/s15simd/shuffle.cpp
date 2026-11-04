// shuffle.cpp - V1 (D14) against S15-simd e1e16cb (shim/include/fnvnx_sse_fast.h): _mm_shuffle_ps for every immediate
// 0..255, with the operands in both orders and the same operand named twice, on vectors holding signalling and quiet
// NaNs of either sign with payloads, signed zeros, denormals, infinities and raw bits. Built twice by
// test_s15simd_shuffle.py: for the host x86-64 (<xmmintrin.h>, the semantics of the original) and for AArch64 with
// sse2neon + fnvnx_sse_fast.h (qemu-aarch64); both print one line per case and the python compares them.
//   -DCOUNT (AArch64): also emits noinline two-source functions sh2_<imm> for the static instruction counts.
#ifdef __aarch64__
#include <sse2neon.h>
#include "fnvnx_sse_fast.h"
#else
#include <xmmintrin.h>
#endif
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <utility>

static void put(__m128 v)
{
    uint32_t u[4];
    _mm_storeu_ps((float *)u, v);
    printf(" %08x %08x %08x %08x", u[0], u[1], u[2], u[3]);
}

template <int I> static __attribute__((noinline)) void one(const float *pa, const float *pb)
{
    __m128 a = _mm_loadu_ps(pa), b = _mm_loadu_ps(pb);
    __m128 v[2] = {a, b};
    printf("%02x", I);
    put(_mm_shuffle_ps(a, b, I));         // two sources
    put(_mm_shuffle_ps(b, a, I));         // the other order
    put(_mm_shuffle_ps(a, a, I));         // one source, same text
    __m128 c = a;
    put(_mm_shuffle_ps(a, c, I));         // two names, one value
    put(_mm_shuffle_ps(v[1], v[0], I));   // expressions
    printf("\n");
}

template <int... I> static void all(const float *a, const float *b, std::integer_sequence<int, I...>)
{
    (one<I>(a, b), ...);
}

#ifdef COUNT
#define SH2(I) extern "C" __attribute__((noinline)) __m128 sh2_##I(__m128 a, __m128 b) { return _mm_shuffle_ps(a, b, I); }
SH2(0x00) SH2(0x55) SH2(0xaa) SH2(0xff) SH2(0xc4) SH2(0xd4) SH2(0xc9) SH2(0xd2) SH2(0x44) SH2(0xe4) SH2(0x1b)
// used by a following add: the result need not land in a's register (count - 1 = the shuffle)
#define SHU(I) extern "C" __attribute__((noinline)) __m128 shu_##I(__m128 a, __m128 b, __m128 c) { return _mm_add_ps(_mm_shuffle_ps(a, b, I), c); }
SHU(0x00) SHU(0x55) SHU(0xaa) SHU(0xff) SHU(0xc4) SHU(0xd4)
#endif

static const uint32_t SPECIAL[] = {0x7f800001, 0xff800001, 0x7fbfffff, 0xffa5a5a5, 0x7fc00000, 0xffc00000, 0x7fc12345,
                                   0xffd00bad, 0x00000000, 0x80000000, 0x00000001, 0x807fffff, 0x7f800000, 0xff800000,
                                   0x3f800000, 0xc0490fdb};

int main()
{
    uint32_t s = 2463534242u;
    for (int r = 0; r < 24; r++) {
        uint32_t ua[4], ub[4];
        for (int k = 0; k < 4; k++) {
            if (r < 4) {   // all lanes special, every lane different
                ua[k] = SPECIAL[(4 * r + k) % 16];
                ub[k] = SPECIAL[(4 * r + k + 7) % 16];
            } else {
                s ^= s << 13; s ^= s >> 17; s ^= s << 5; ua[k] = s;
                s ^= s << 13; s ^= s >> 17; s ^= s << 5; ub[k] = s;
                if (r % 3 == 0) { ua[k] = 0x7f800000u | (ua[k] & 0x807fffffu) | 1; ub[k] |= 0x7f800001u; }   // NaNs
            }
        }
        float a[4], b[4];
        memcpy(a, ua, 16);
        memcpy(b, ub, 16);
        all(a, b, std::make_integer_sequence<int, 256>{});
    }
    return 0;
}
