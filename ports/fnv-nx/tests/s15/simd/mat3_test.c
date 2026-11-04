/* mat3_test.c - bit-exact check of tools/link/overrides/mat3_tmul_00a588a0.c against a scalar reference that follows
 * the decomp's transpose product 0x00a588a0 (P[i][j] = A[0][i] B[0][j] + A[1][i] B[1][j] + A[2][i] B[2][j], left
 * to right) under qemu-aarch64 (tests/s15/simd/run.py). Random, rotation-like and edge inputs, a == b included.
 * Our own code: no decomp text. Prints "mat3: <n> cases, <m> mismatches". */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct fnvnx_mat3 { float m[9]; };
struct fnvnx_mat3 fnvnx_mat3_tmul_00a588a0(const struct fnvnx_mat3 *a, const struct fnvnx_mat3 *b);

static __attribute__((noinline)) void ref_tmul(float *p, const float *a, const float *b)
{
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++)
            p[3 * i + j] = a[0 * 3 + i] * b[0 * 3 + j] + a[1 * 3 + i] * b[1 * 3 + j] + a[2 * 3 + i] * b[2 * 3 + j];
}

static uint64_t rng = 0x853c49e6748fea9bull;
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
    default: return frand(-100.0f, 100.0f);
    }
}

static int same(float a, float b)
{
    uint32_t x = ubits(a), y = ubits(b);
    int xn = (x & 0x7fffffff) > 0x7f800000, yn = (y & 0x7fffffff) > 0x7f800000;
    return xn == yn && (xn || x == y);   /* any NaN matches any NaN (operand order picks the payload) */
}

static struct fnvnx_mat3 storage[2];

int main(void)
{
    long cases = 0, bad = 0;
    for (int i = 0; i < 400000; i++) {
        int mode = (int)(next() % 4);
        struct fnvnx_mat3 *a = &storage[0], *b = next() % 8 ? &storage[1] : &storage[0];
        for (int k = 0; k < 9; k++) {
            a->m[k] = pick(mode);
            if (b != a)
                b->m[k] = pick(next() % 3 ? mode : 2);
        }
        float ref[9];
        ref_tmul(ref, a->m, b->m);
        struct fnvnx_mat3 out = fnvnx_mat3_tmul_00a588a0(a, b);
        cases++;
        int ok = 1;
        for (int k = 0; k < 9; k++)
            ok &= same(out.m[k], ref[k]);
        if (!ok && bad++ < 8) {
            printf("mismatch case %d:", i);
            for (int k = 0; k < 9; k++)
                printf(" %08x/%08x", ubits(out.m[k]), ubits(ref[k]));
            printf("\n");
        }
    }
    printf("mat3: %ld cases, %ld mismatches\n", cases, bad);
    return bad != 0;
}
