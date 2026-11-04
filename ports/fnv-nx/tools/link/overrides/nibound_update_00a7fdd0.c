/* fnvnx_nibound_update_00a7fdd0 - NiBound::Update (0x00a7fdd0) with NEON lanes (stream S15-simd, docs/PERF.md 11.6,
 * tests/s15/simd).
 *
 * center = translation + (rotation * bound.center) * scale; radius = bound.radius * scale. GCC compiles the decomp
 * as 45 scalar instructions. Here one ld3 of the transform's first 48 bytes gives the rotation's columns in lanes
 * 0-2 (lane 3 holds the translation and is not used), so the three row dot products run as three lanes:
 * (m[i][0] c0 + m[i][1] c1) + m[i][2] c2, the decomp's order, each product and sum a float operation, no fused
 * multiply-add (build.sh: -ffp-contract=off). Then x scale and + translation per lane, and radius x scale in lane 3.
 * Bit-identical lane by lane for every non-NaN result (tests/s15/simd/nibound_test.c).
 * A NaN result is a NaN, but its sign and payload may differ from the decomp's compiled code: when two NaN
 * operands meet, AArch64 returns the first one, and GCC commutes operands freely in either build (V1, SL1).
 * The tests accept any NaN for any NaN; PERF.md 11.6 says why no new difference arises.
 *
 * Layouts (ILP32 and x86 alike): NiBound {center x, y, z; radius} 16 bytes; NiTransform {rotation 3x3 row-major at
 * 0, translation at 36, scale at 48} 52 bytes. The 16-byte load at +36 reads the translation and the scale, inside
 * the object. Aliasing: `self` may be `bound`; every input is read before the single store. */
#include <arm_neon.h>

struct fnvnx_bound { float c[4]; };      /* center x, y, z; radius */
struct fnvnx_transform { float m[13]; }; /* rotation[9], translation[3], scale */

void fnvnx_nibound_update_00a7fdd0(struct fnvnx_bound *self, const struct fnvnx_bound *bound,
                                   const struct fnvnx_transform *tf)
{
    float32x4_t b = vld1q_f32(bound->c);
    float32x4x3_t col = vld3q_f32(tf->m);          /* col.val[k] = {m[0][k], m[1][k], m[2][k], t[k]} */
    float32x4_t ts = vld1q_f32(tf->m + 9);          /* translation x, y, z; scale */
    float scale = vgetq_lane_f32(ts, 3);
    float32x4_t r = vmulq_laneq_f32(col.val[0], b, 0);
    r = vaddq_f32(r, vmulq_laneq_f32(col.val[1], b, 1));
    r = vaddq_f32(r, vmulq_laneq_f32(col.val[2], b, 2));
    float32x4_t out = vaddq_f32(ts, vmulq_n_f32(r, scale));
    out = vsetq_lane_f32(vgetq_lane_f32(b, 3) * scale, out, 3);
    vst1q_f32(self->c, out);
}
