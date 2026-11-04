/* fnvnx_mat3_tmul_00a588a0 - the NiMatrix3 transpose product 0x00a588a0 (this^T * kMat, returned by value) with NEON
 * lanes (stream S15-simd, docs/PERF.md 11.6, tests/s15/simd).
 *
 * P[i][j] = (A[0][i] B[0][j] + A[1][i] B[1][j]) + A[2][i] B[2][j]: row i of the product is a sum of B's rows, each
 * scaled by one element of A's column i. Per row: a multiply by element, then two multiply + add pairs over the
 * row's lanes, in the decomp's order, float operations, no fused multiply-add (build.sh: -ffp-contract=off).
 * Bit-identical lane by lane for every non-NaN result (tests/s15/simd/mat3_test.c); 27 fmul + 18 fadd become 9 + 6. A NaN result is a NaN, but its sign and payload may differ from the decomp's
 * compiled code: when two NaN operands meet, AArch64 returns the first one, and GCC commutes operands freely in
 * either build (V1, SL1). The tests accept any NaN for any NaN; PERF.md 11.6 says why no new difference arises.
 *
 * ABI as GCC compiles the decomp's member: this in x0, kMat in x1, the 36-byte result through x8. The last row of
 * each input is loaded as 8 + 4 bytes (no read past the 36 bytes). Every input is read before the first store. */
#include <arm_neon.h>

struct fnvnx_mat3 { float m[9]; };

static inline float32x4_t row_last(const float *p)
{
    return vcombine_f32(vld1_f32(p), vld1_dup_f32(p + 2));
}

struct fnvnx_mat3 fnvnx_mat3_tmul_00a588a0(const struct fnvnx_mat3 *a, const struct fnvnx_mat3 *b)
{
    float32x4_t a0 = vld1q_f32(a->m), a1 = vld1q_f32(a->m + 3), a2 = row_last(a->m + 6);
    float32x4_t b0 = vld1q_f32(b->m), b1 = vld1q_f32(b->m + 3), b2 = row_last(b->m + 6);
    float32x4_t p0 = vmulq_laneq_f32(b0, a0, 0);
    float32x4_t p1 = vmulq_laneq_f32(b0, a0, 1);
    float32x4_t p2 = vmulq_laneq_f32(b0, a0, 2);
    p0 = vaddq_f32(p0, vmulq_laneq_f32(b1, a1, 0));
    p1 = vaddq_f32(p1, vmulq_laneq_f32(b1, a1, 1));
    p2 = vaddq_f32(p2, vmulq_laneq_f32(b1, a1, 2));
    p0 = vaddq_f32(p0, vmulq_laneq_f32(b2, a2, 0));
    p1 = vaddq_f32(p1, vmulq_laneq_f32(b2, a2, 1));
    p2 = vaddq_f32(p2, vmulq_laneq_f32(b2, a2, 2));
    struct fnvnx_mat3 r;
    r.m[0] = vgetq_lane_f32(p0, 0); r.m[1] = vgetq_lane_f32(p0, 1); r.m[2] = vgetq_lane_f32(p0, 2);
    r.m[3] = vgetq_lane_f32(p1, 0); r.m[4] = vgetq_lane_f32(p1, 1); r.m[5] = vgetq_lane_f32(p1, 2);
    r.m[6] = vgetq_lane_f32(p2, 0); r.m[7] = vgetq_lane_f32(p2, 1); r.m[8] = vgetq_lane_f32(p2, 2);
    return r;
}
