/* fnvnx_update_world_loc_00a68bf0 - NiAVObject::UpdateWorldLoc (0x00a68bf0): world = parent world * local, in one
 * leaf function with NEON lanes (stream S15-simd, docs/PERF.md 11.6, tests/s15/simd).
 *
 * The original (FalloutNV.exe 0x00a68bf0 -> 0x0062c250 NiTransform::operator*) composes:
 *   scale = P.s * L.s                                   (float)
 *   R     = P.R * L.R   (0x0043f8d0: r[i][j] = (P[i][0] L[0][j] + P[i][1] L[1][j]) + P[i][2] L[2][j])
 *   v     = P.R * L.t   (0x004b4500: v[i] = (P[i][0] t0 + P[i][1] t1) + P[i][2] t2)
 *   t     = P.t + P.s * v                               (0x004a3760, then 0x00439e90: P.t[i] + s v[i])
 * into a temporary that is then copied to the world transform. With the 0x200 flag the parent's world is copied,
 * without a parent the local transform. Our decomp build reaches neither matrix helper: NiTransform::operator* is
 * linked natively, but it calls ThreeByThreeMatrix::operator*(matrix) and (point), names the link leaves
 * unresolved (out/refs.tsv), and 0x0062c250 has no definition by address. This override does the whole compose
 * itself, each value with the original's float operations in the original's order (no fused multiply-add:
 * build.sh), the independent outputs in NEON lanes; bit-identical lane by lane to a scalar transcription
 * (tests/s15/simd/xform_test.c).
 *
 * Layout (ILP32 native object and x86 alike): NiAVObject +0x18 parent, +0x30 flags (bit 9: world follows the
 * parent's world only), +0x34 local NiTransform, +0x68 world NiTransform; NiTransform {rotation 3x3 row-major,
 * translation at +36, scale at +48}, 52 bytes. Every input is read before the world is written. */
#include <arm_neon.h>
#include <stdint.h>
#include <string.h>

struct fnvnx_xform { float m[13]; };   /* rotation[9], translation[3], scale */

struct fnvnx_avobject {
    uint8_t pad0[0x18];
#ifdef __ILP32__
    const struct fnvnx_avobject *parent;   /* NiNode *: 32 bits in the game (ILP32) */
#else
    uint32_t parent;                   /* LP64 unit test: objects below 4 GiB */
#endif
    uint8_t pad1[0x30 - 0x1c];
    uint32_t flags;
    struct fnvnx_xform local;          /* +0x34 */
    struct fnvnx_xform world;          /* +0x68 */
};

void fnvnx_update_world_loc_00a68bf0(struct fnvnx_avobject *self)
{
    const struct fnvnx_xform *src;
    const struct fnvnx_avobject *par = (const struct fnvnx_avobject *)(uintptr_t)self->parent;
    if (!par)
        src = &self->local;
    else if ((self->flags >> 9) & 1)
        src = &par->world;
    else
        goto compose;
    memcpy(&self->world, src, sizeof self->world);
    return;
compose:;
    const float *p = par->world.m, *l = self->local.m;
    float32x4_t p0 = vld1q_f32(p), p1 = vld1q_f32(p + 3), p2 = vld1q_f32(p + 6);   /* rows; lane 3 unused */
    float32x4_t l0 = vld1q_f32(l), l1 = vld1q_f32(l + 3), l2 = vld1q_f32(l + 6);
    float32x4x3_t pc = vld3q_f32(p);   /* pc.val[k] lanes 0-2: column k of P.R */
    float32x4_t pts = vld1q_f32(p + 9);   /* P.t, P.s */
    float ls = l[12], ps = vgetq_lane_f32(pts, 3);

    float32x4_t r0 = vaddq_f32(vaddq_f32(vmulq_laneq_f32(l0, p0, 0), vmulq_laneq_f32(l1, p0, 1)),
                               vmulq_laneq_f32(l2, p0, 2));
    float32x4_t r1 = vaddq_f32(vaddq_f32(vmulq_laneq_f32(l0, p1, 0), vmulq_laneq_f32(l1, p1, 1)),
                               vmulq_laneq_f32(l2, p1, 2));
    float32x4_t r2 = vaddq_f32(vaddq_f32(vmulq_laneq_f32(l0, p2, 0), vmulq_laneq_f32(l1, p2, 1)),
                               vmulq_laneq_f32(l2, p2, 2));
    float32x4_t v = vaddq_f32(vaddq_f32(vmulq_n_f32(pc.val[0], l[9]), vmulq_n_f32(pc.val[1], l[10])),
                              vmulq_n_f32(pc.val[2], l[11]));
    float32x4_t t = vaddq_f32(pts, vmulq_n_f32(v, ps));
    t = vsetq_lane_f32(ps * ls, t, 3);

    float *w = self->world.m;
    vst1q_f32(w, r0);        /* lane 3 lands on w[3], rewritten next */
    vst1q_f32(w + 3, r1);
    vst1q_f32(w + 6, r2);
    vst1q_f32(w + 9, t);     /* translation and scale */
}
