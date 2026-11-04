/* nibound_test.c - bit-exact check of tools/link/overrides/nibound_update_00a7fdd0.c against a scalar reference that
 * follows the decomp's NiBound::Update (0x00a7fdd0) operation by operation (tests/s15/simd/run.py runs it under
 * qemu-aarch64). Random, rotation-like and edge inputs; `self` == `bound` aliasing. Our own code: no decomp text.
 * Prints "nibound: <n> cases, <m> mismatches". */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct fnvnx_bound { float c[4]; };
struct fnvnx_transform { float m[13]; };
void fnvnx_nibound_update_00a7fdd0(struct fnvnx_bound *self, const struct fnvnx_bound *bound,
                                   const struct fnvnx_transform *tf);

/* reference: center = t + (R c) * s, row dot products summed left to right; radius = r * s */
static __attribute__((noinline)) void ref_update(float *self, const float *bound, const float *tf)
{
    float c0 = bound[0], c1 = bound[1], c2 = bound[2];
    float r0 = tf[0] * c0 + tf[1] * c1 + tf[2] * c2;
    float r1 = tf[3] * c0 + tf[4] * c1 + tf[5] * c2;
    float r2 = tf[6] * c0 + tf[7] * c1 + tf[8] * c2;
    float s = tf[12];
    self[0] = tf[9] + r0 * s;
    self[1] = tf[10] + r1 * s;
    self[2] = tf[11] + r2 * s;
    self[3] = bound[3] * s;
}

static uint64_t rng = 0x2545f4914f6cdd1dull;
static uint32_t next(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)(rng >> 16); }
static float frand(float lo, float hi) { return lo + (hi - lo) * (float)(next() & 0xffffff) / 16777216.0f; }
static float fbits(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static uint32_t ubits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }

static const uint32_t EDGE[] = {0x00000000, 0x80000000, 0x3f800000, 0xbf800000, 0x7f800000, 0xff800000, 0x7fc00000,
                                0x7fa00001, 0x00000001, 0x807fffff, 0x7f7fffff, 0x3f7fffff, 0x4b800000};

static float pick(int mode)
{
    switch (mode) {
    case 0: return frand(-1.0f, 1.0f);
    case 1: return fbits(next());
    case 2: return fbits(EDGE[next() % (sizeof EDGE / sizeof EDGE[0])]);
    default: return frand(-5000.0f, 5000.0f);
    }
}

static int same(float a, float b)
{
    uint32_t x = ubits(a), y = ubits(b);
    int xn = (x & 0x7fffffff) > 0x7f800000, yn = (y & 0x7fffffff) > 0x7f800000;
    return xn == yn && (xn || x == y);   /* any NaN matches any NaN (operand order picks the payload) */
}

int main(void)
{
    long cases = 0, bad = 0;
    for (int i = 0; i < 400000; i++) {
        int mode = (int)(next() % 4);
        struct fnvnx_transform tf;
        struct fnvnx_bound b, out;
        float rb[4], rout[4];
        for (int k = 0; k < 13; k++)
            tf.m[k] = pick(k < 9 ? mode : (int)(next() % 4));
        for (int k = 0; k < 4; k++)
            b.c[k] = rb[k] = pick(next() % 3 ? 3 : (int)(next() % 4));
        if (next() % 2) {
            out = b;
            memcpy(rout, rb, 16);
            fnvnx_nibound_update_00a7fdd0(&out, &out, &tf);
            ref_update(rout, rout, tf.m);
        } else {
            fnvnx_nibound_update_00a7fdd0(&out, &b, &tf);
            ref_update(rout, rb, tf.m);
        }
        cases++;
        int ok = 1;
        for (int k = 0; k < 4; k++)
            ok &= same(out.c[k], rout[k]);
        if (!ok && bad++ < 8)
            printf("mismatch case %d: %08x %08x %08x %08x vs %08x %08x %08x %08x\n", i, ubits(out.c[0]),
                   ubits(out.c[1]), ubits(out.c[2]), ubits(out.c[3]), ubits(rout[0]), ubits(rout[1]),
                   ubits(rout[2]), ubits(rout[3]));
    }
    printf("nibound: %ld cases, %ld mismatches\n", cases, bad);
    return bad != 0;
}
