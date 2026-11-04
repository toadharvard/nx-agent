/* quat_from_matrix_test.c - bit-exact check of tools/link/overrides/quat_from_matrix_00a6df40.c against a scalar
 * reference that follows the decomp's NiQuaternion::FromRotationMatrix (0x00a6df40): double literals, the volatile
 * square root, the array of component pointers (tests/s15/simd/run.py runs it under qemu-aarch64).
 *
 * Inputs: rotation matrices (random axis and angle, so every branch and axis is taken), random matrices in [-2, 2],
 * random bit patterns and edge values (zero, negative zero, infinities, NaN, denormals, values that make the
 * square root's argument negative). errno must match too. Our own code: no decomp text. Prints
 * "quat_from_matrix: <n> cases, <m> mismatches, <e> errno differences". */
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct fnvnx_quat { float c[4]; };
struct fnvnx_mat3 { float m[3][3]; };
void fnvnx_quat_from_matrix_00a6df40(struct fnvnx_quat *self, const struct fnvnx_mat3 *mat);

int32_t __fnv_011a9e98[3] = {1, 2, 0};

static float ref_sqrt(float v) { volatile float r = sqrtf(v); return r; }

static __attribute__((noinline)) void ref(float *q, const float m[3][3])
{
    float root = m[0][0] + m[1][1] + m[2][2];
    if (root > 0.0f) {
        root = ref_sqrt(root + 1.0f);
        q[0] = root * 0.5;
        root = 0.5 / root;
        q[1] = (m[2][1] - m[1][2]) * root;
        q[2] = (m[0][2] - m[2][0]) * root;
        q[3] = (m[1][0] - m[0][1]) * root;
    } else {
        unsigned a = 0;
        if (m[1][1] > m[0][0])
            a = 1;
        if (m[2][2] > m[a][a])
            a = 2;
        unsigned n = __fnv_011a9e98[a], l = __fnv_011a9e98[n];
        root = ref_sqrt(m[a][a] - m[n][n] - m[l][l] + 1.0f);
        float *comp[3] = {&q[1], &q[2], &q[3]};
        *comp[a] = root * 0.5;
        root = 0.5 / root;
        q[0] = (m[l][n] - m[n][l]) * root;
        *comp[n] = (m[a][n] + m[n][a]) * root;
        *comp[l] = (m[a][l] + m[l][a]) * root;
    }
}

static uint64_t rng = 0x243f6a8885a308d3ull;
static uint32_t next(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)(rng >> 16); }
static float frand(float lo, float hi) { return lo + (hi - lo) * (float)(next() & 0xffffff) / 16777216.0f; }
static float fbits(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static uint32_t ubits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
static const uint32_t EDGE[] = {0x00000000, 0x80000000, 0x3f800000, 0xbf800000, 0x7f800000, 0xff800000, 0x7fc00000,
                                0x7fa00001, 0x00000001, 0x807fffff, 0x7f7fffff, 0x40000000, 0xc0000000, 0x3f000000};

static void rotation(float m[3][3])
{
    float x = frand(-1, 1), y = frand(-1, 1), z = frand(-1, 1), s = sqrtf(x * x + y * y + z * z);
    if (s == 0) { x = 1; s = 1; }
    x /= s; y /= s; z /= s;
    float ang = frand(-3.2f, 3.2f), c = cosf(ang), sn = sinf(ang), t = 1 - c;
    float r[3][3] = {{t * x * x + c, t * x * y - sn * z, t * x * z + sn * y},
                     {t * x * y + sn * z, t * y * y + c, t * y * z - sn * x},
                     {t * x * z - sn * y, t * y * z + sn * x, t * z * z + c}};
    memcpy(m, r, sizeof r);
}

int main(void)
{
    long cases = 0, bad = 0, errno_bad = 0;
    for (int i = 0; i < 400000; i++) {
        struct fnvnx_mat3 mat;
        int mode = (int)(next() % 4);
        if (mode == 0)
            rotation(mat.m);
        for (int k = 0; k < 9; k++) {
            float *e = &mat.m[k / 3][k % 3];
            if (mode == 1) *e = frand(-2, 2);
            else if (mode == 2) *e = fbits(next());
            else if (mode == 3) *e = next() % 3 ? frand(-1, 1) : fbits(EDGE[next() % (sizeof EDGE / 4)]);
        }
        struct fnvnx_quat out;
        float r[4];
        for (int k = 0; k < 4; k++) { out.c[k] = r[k] = fbits(0x55555555u); }
        errno = 0;
        fnvnx_quat_from_matrix_00a6df40(&out, &mat);
        int e1 = errno;
        errno = 0;
        ref(r, (const float (*)[3])mat.m);
        int e2 = errno;
        cases++;
        if (e1 != e2)
            errno_bad++;
        int differ = 0;
        for (int k = 0; k < 4; k++) {
            uint32_t x = ubits(out.c[k]), y = ubits(r[k]);
            int xn = (x & 0x7fffffff) > 0x7f800000, yn = (y & 0x7fffffff) > 0x7f800000;
            if (xn != yn || (!xn && x != y))
                differ = 1;
        }
        if (differ && bad++ < 8) {
            printf("mismatch m=");
            for (int k = 0; k < 9; k++) printf("%08x ", ubits(mat.m[k / 3][k % 3]));
            printf(": %08x %08x %08x %08x vs %08x %08x %08x %08x\n", ubits(out.c[0]), ubits(out.c[1]),
                   ubits(out.c[2]), ubits(out.c[3]), ubits(r[0]), ubits(r[1]), ubits(r[2]), ubits(r[3]));
        }
    }
    printf("quat_from_matrix: %ld cases, %ld mismatches, %ld errno differences\n", cases, bad, errno_bad);
    return bad != 0 || errno_bad != 0;
}
