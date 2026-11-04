/* fnvnx_cull_worker_00a694e0 - the culling worker's per-object frustum test (0x00a694e0) with the six planes in NEON
 * lanes (stream S15-simd, docs/PERF.md 11.6, tests/s15/simd/cull_test.c).
 *
 * The decomp walks the six planes; for each active one (bit i of the worker's mask at +0x8c) it calls the bound
 * getter 0x0043d450 and the sphere/plane classification 0x004b6100 (which calls 0x004b6160 and 0x004b6190): about
 * 55 A64 instructions per active plane. It stops at the first plane the sphere is fully behind (no callback); a
 * plane the sphere is fully in front of has its bit cleared for the children; if no plane rejects, the object's
 * virtual slot 53 is called with the worker; the mask is restored afterwards.
 *
 * Here: one ld4 per four planes puts each plane in a lane, the distance is ((n.x c.x + n.y c.y) + n.z c.z) - k per
 * lane (the decomp's order, float, no fused multiply-add: build.sh), and the two compares give the behind and front
 * bits of all six planes. The sequential walk is then exactly a function of those bits: plane i's bit is distinct
 * from the others and clearing it never changes a later plane's test, so "an active plane rejects" is
 * (active & behind) != 0, and the mask the callback sees is mask & ~(active & front). The intermediate mask stores
 * of the walk are not observable (the mask is restored before return, nothing runs in between but these pure
 * callees). NaN distances neither reject nor clear, as in the decomp.
 *
 * Layouts (ILP32 and x86 alike): worker planes at +0x2c (6 x {normal x, y, z; constant}), mask at +0x8c; object:
 * vtable at +0, bound pointer at +0x20 (sphere {center x, y, z; radius}). The getter's null fallback (0x011f4288)
 * is never reached: the decomp returns before the loop when the bound is null. The override inlines copies of
 * 0x0043d450, 0x004b6100, 0x004b6160 and 0x004b6190. Reads: the 96 bytes of planes (two ld4 of 64 bytes: the
 * second starts at plane 2, so it ends at the last plane's end). */
#include <arm_neon.h>
#include <stdint.h>

struct fnvnx_sphere { float c[3], r; };
struct fnvnx_cull_object;
typedef void (*fnvnx_cull_cb)(struct fnvnx_cull_object *, void *);
struct fnvnx_cull_object {
    fnvnx_cull_cb *vtable;
    uint32_t pad[7];
    struct fnvnx_sphere *bound;           /* +0x20 */
};
struct fnvnx_cull_worker {
    uint32_t pad[11];
    float planes[6][4];                   /* +0x2c */
    uint32_t mask;                        /* +0x8c */
};

/* per lane: ((n.x c.x + n.y c.y) + n.z c.z) - k */
static inline float32x4_t plane_dist(float32x4x4_t p, float32x4_t c)
{
    float32x4_t d = vmulq_laneq_f32(p.val[0], c, 0);
    d = vaddq_f32(d, vmulq_laneq_f32(p.val[1], c, 1));
    d = vaddq_f32(d, vmulq_laneq_f32(p.val[2], c, 2));
    return vsubq_f32(d, p.val[3]);
}

void fnvnx_cull_worker_00a694e0(struct fnvnx_cull_worker *self, struct fnvnx_cull_object *obj)
{
    const struct fnvnx_sphere *b = obj->bound;
    if (!b)
        return;
    float r = b->r;
    if (r == 0.0f)
        return;
    uint32_t saved = self->mask;
    uint32_t active = saved & 0x3f;
    if (active) {
        float32x4_t c = vld1q_f32(b->c);
        float32x4_t d03 = plane_dist(vld4q_f32(self->planes[0]), c);   /* planes 0-3 */
        float32x4_t d25 = plane_dist(vld4q_f32(self->planes[2]), c);   /* planes 2-5; lanes 0-1 unused */
        float32x4_t nr = vdupq_n_f32(-r), pr = vdupq_n_f32(r);
        static const uint32_t w03[4] = {1, 2, 4, 8}, w25[4] = {0, 0, 16, 32};
        uint32x4_t k03 = vld1q_u32(w03), k25 = vld1q_u32(w25);
        uint32_t behind = vaddvq_u32(vandq_u32(vcleq_f32(d03, nr), k03)) |
                          vaddvq_u32(vandq_u32(vcleq_f32(d25, nr), k25));
        if (active & behind)
            return;   /* the mask was restored by the decomp: unchanged */
        uint32_t front = vaddvq_u32(vandq_u32(vcgeq_f32(d03, pr), k03)) |
                         vaddvq_u32(vandq_u32(vcgeq_f32(d25, pr), k25));
        self->mask = saved & ~(active & front);
    }
    obj->vtable[53](obj, self);
    self->mask = saved;
}
