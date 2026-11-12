/* fnvnx_slerp_00a6e330 - NiQuaternion::Slerp (0x00a6e330) in one leaf function with NEON lanes (stream S15-simd,
 * docs/PERF.md 11.5 row 5, tests/s15/simd).
 *
 * The decomp's Slerp computes the cosine of the two quaternions, calls the weight function 0x00a6dc30 (once, with t
 * or with 1 - t), interpolates the four components linearly by that weight and tail-calls the normalisation
 * 0x00a4ff00 (three-step polynomial inverse square root around 0.959). Compiled by GCC that is three functions,
 * 134 A64 instructions per call, with the components loaded twice, the stack frame of the
 * caller and the call/return.
 *
 * This replacement does the same arithmetic, operation by operation, in the same order and the same precision:
 * every float operation is a float operation there too, every double one a double, the sums are summed in the
 * decomp's order (lane extracts, never a pairwise tree beyond the first pair, which is the same a + b), no
 * fused multiply-add (build.sh: -ffp-contract=off). Only independent per-component operations share a NEON
 * instruction (b - a, x w, + a; the squares; the scale), which is bit-identical lane by lane. Every non-NaN result,
 * infinities included, is the decomp's to the bit (tests/s15/simd/slerp_test.c, random and edge inputs, against a
 * scalar reference in the same order; V1's tests/v1/test_slerp_decomp.py against the decomp's own bodies).
 * A NaN result is a NaN, but its sign and payload may differ from the decomp's compiled code: when two NaN
 * operands meet, AArch64 returns the first one, and GCC commutes operands freely in either build (V1, SL1).
 * The tests accept any NaN for any NaN; PERF.md 11.6 says why no new difference arises.
 *
 * The four constants are read from the image, as the decomp does: 0x0109c9d0 / 0x0109c9d8 (doubles of the weight)
 * and 0x011f3fec / 0x011f3ff0 (floats of the normalisation, written by a static initializer at run time).
 * Aliasing: `self` may be `a` or `b` (q.Slerp(t, q, r)); every component is read before any is written, which gives
 * the decomp's result, because there each written component is only read again for itself. */
#include <arm_neon.h>
#include <stdint.h>

struct fnvnx_quat { float c[4]; };   /* NiQuaternion: 16 bytes, components at +0, +4, +8, +12 */

extern const double __fnv_0109c9d0;   /* weight: K */
extern const double __fnv_0109c9d8;   /* weight: cosine factor */
extern const float __fnv_011f3fec;    /* normalisation: slope */
extern const float __fnv_011f3ff0;    /* normalisation: value at 0.959 */

/* 0x00a6dc30: t * (k t (2t - 3) + 1 + k) with k = K (1 - C cos)^2, mixed float/double exactly as compiled */
static inline float slerp_weight(float t, float cosine)
{
    float f = (float)(1.0 - __fnv_0109c9d8 * (double)cosine);
    f = f * f;
    float k = (float)(__fnv_0109c9d0 * f);
    double two_t_m3 = (double)(t + t) - 3.0;
    return (float)((double)t * (two_t_m3 * (double)(k * t) + 1.0 + (double)k));
}

/* one step of 0x00a4ff00's inverse square root: C0 + C1 (s - 0.9590659737586975), in double */
static inline float isqrt_step(float s, double c0, double c1)
{
    return (float)(((double)s - 0.9590659737586975) * c1 + c0);
}

void fnvnx_slerp_00a6e330(struct fnvnx_quat *self, float t, const struct fnvnx_quat *a, const struct fnvnx_quat *b)
{
    float32x4_t va = vld1q_f32(a->c), vb = vld1q_f32(b->c);
    float32x4_t p = vmulq_f32(va, vb);
    float cosine = vgetq_lane_f32(p, 0) + vgetq_lane_f32(p, 1);
    cosine = cosine + vgetq_lane_f32(p, 2);
    cosine = cosine + vgetq_lane_f32(p, 3);

    float w;
    if (t <= 0.5f)
        w = slerp_weight(t, cosine);
    else
        w = 1.0f - slerp_weight(1.0f - t, cosine);

    /* a + w (b - a), as (b - a) * w + a per lane */
    float32x4_t q = vaddq_f32(vmulq_n_f32(vsubq_f32(vb, va), w), va);

    /* 0x00a4ff00: s = ((c2^2 + c1^2) + c3^2) + c0^2 */
    float32x4_t sq = vmulq_f32(q, q);
    float s = vgetq_lane_f32(sq, 2) + vgetq_lane_f32(sq, 1);
    s = s + vgetq_lane_f32(sq, 3);
    s = s + vgetq_lane_f32(sq, 0);
    double c0 = (double)__fnv_011f3ff0, c1 = (double)__fnv_011f3fec;
    float scale = isqrt_step(s, c0, c1);
    if (s <= 0.9152119755744934f) {
        scale = scale * isqrt_step(scale * scale * s, c0, c1);
        if (s <= 0.6521196961402893f)
            scale = scale * isqrt_step(scale * scale * s, c0, c1);
    }
    vst1q_f32(self->c, vmulq_n_f32(q, scale));
}
