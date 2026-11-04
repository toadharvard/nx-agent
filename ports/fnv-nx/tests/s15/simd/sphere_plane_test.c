/* sphere_plane_test.c - tools/link/overrides/sphere_plane_004b6100.c against a scalar reference in the decomp's order
 * (0x004b6100 -> 0x004b6160 -> 0x004b6190): random, unit-normal and edge inputs (tests/s15/simd/run.py). Our own
 * code: no decomp text. Prints "sphere_plane: <n> cases, <m> mismatches". */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct fnvnx_sphere { float c[3], r; };
struct fnvnx_plane { float n[3], k; };
int fnvnx_sphere_plane_004b6100(const struct fnvnx_sphere *s, const struct fnvnx_plane *p);

static __attribute__((noinline)) float ref_dot(const float *a, const float *b)
{
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
static __attribute__((noinline)) float ref_dist(const struct fnvnx_plane *p, const struct fnvnx_sphere *s)
{
    return ref_dot(p->n, s->c) - p->k;
}
static __attribute__((noinline)) int ref_classify(const struct fnvnx_sphere *s, const struct fnvnx_plane *p)
{
    float d = ref_dist(p, s);
    if (d <= -s->r)
        return 2;
    else if (d >= s->r)
        return 1;
    return 0;
}

static uint64_t rng = 0x2545f4914f6cdd1dull;
static uint32_t next(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)(rng >> 16); }
static float frand(float lo, float hi) { return lo + (hi - lo) * (float)(next() & 0xffffff) / 16777216.0f; }
static float fbits(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static const uint32_t EDGE[] = {0x00000000, 0x80000000, 0x3f800000, 0xbf800000, 0x7f800000, 0xff800000, 0x7fc00000,
                                0x00000001, 0x7f7fffff, 0x3f000000};
static float pick(int mode)
{
    switch (mode) {
    case 0: return frand(-1.0f, 1.0f);
    case 1: return fbits(next());
    case 2: return fbits(EDGE[next() % (sizeof EDGE / sizeof EDGE[0])]);
    default: return frand(-5000.0f, 5000.0f);
    }
}

int main(void)
{
    long cases = 0, bad = 0;
    for (int i = 0; i < 400000; i++) {
        int mode = (int)(next() % 4);
        struct fnvnx_sphere s;
        struct fnvnx_plane p;
        for (int k = 0; k < 3; k++) { s.c[k] = pick(mode); p.n[k] = pick(mode ? mode : 0); }
        s.r = next() % 4 ? frand(0.0f, 300.0f) : pick(mode);
        p.k = pick(mode);
        if (mode == 0) {   /* the cull's case: unit normals, world positions, plane distances near the sphere */
            float l = __builtin_sqrtf(ref_dot(p.n, p.n));
            if (l > 0)
                for (int k = 0; k < 3; k++) p.n[k] /= l;
            for (int k = 0; k < 3; k++) s.c[k] = frand(-5000.0f, 5000.0f);
            p.k = ref_dot(p.n, s.c) + frand(-2.0f, 2.0f) * s.r;
        }
        int x = fnvnx_sphere_plane_004b6100(&s, &p), y = ref_classify(&s, &p);
        cases++;
        if (x != y && bad++ < 8)
            printf("mismatch case %d: %d vs %d\n", i, x, y);
    }
    printf("sphere_plane: %ld cases, %ld mismatches\n", cases, bad);
    return bad != 0;
}
