/* slerp_test.c - bit-exact check of tools/link/overrides/slerp_00a6e330.c against a scalar reference that follows
 * the decomp's NiQuaternion::Slerp (0x00a6e330), its weight 0x00a6dc30 and its normalisation 0x00a4ff00 operation
 * by operation (tests/s15/simd/run.py builds and runs it under qemu-aarch64).
 *
 * The image constants are variables here; every round draws new ones in the ranges the engine uses, then random
 * and edge-case quaternions and t. Our own code: no decomp text. Prints "slerp: <n> cases, <m> mismatches". */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct fnvnx_quat { float c[4]; };
void fnvnx_slerp_00a6e330(struct fnvnx_quat *self, float t, const struct fnvnx_quat *a, const struct fnvnx_quat *b);

double __fnv_0109c9d0, __fnv_0109c9d8;
float __fnv_011f3fec, __fnv_011f3ff0;

/* --- reference: scalar, written as the decomp reads (float unless a double constant widens it) --- */
static __attribute__((noinline)) float ref_weight(float t, float fCos)
{
    float fFactor = 1.0f - __fnv_0109c9d8 * fCos;
    fFactor *= fFactor;
    float fK = __fnv_0109c9d0 * fFactor;
    return t * (fK * t * (2.0f * t - 3.0) + 1.0f + fK);
}

static __attribute__((noinline)) float ref_isqrt(float s)
{
    return __fnv_011f3ff0 + __fnv_011f3fec * (s - 0.9590659737586975);
}

static __attribute__((noinline)) void ref_normalize(float *q)
{
    float s = q[2] * q[2] + q[1] * q[1] + q[3] * q[3] + q[0] * q[0];
    float fScale = ref_isqrt(s);
    if (s <= 0.9152119755744934) {
        fScale *= ref_isqrt(fScale * fScale * s);
        if (s <= 0.6521196961402893f)
            fScale *= ref_isqrt(fScale * fScale * s);
    }
    q[1] = q[1] * fScale;
    q[2] = fScale * q[2];
    q[3] = q[3] * fScale;
    q[0] = q[0] * fScale;
}

static __attribute__((noinline)) void ref_slerp(float *self, float t, const float *a, const float *b)
{
    float cosine = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
    float warped;
    if (t <= 0.5f)
        warped = ref_weight(t, cosine);
    else
        warped = 1.0f - ref_weight(1.0f - t, cosine);
    for (int i = 0; i < 4; i++) {
        float fStart = a[i];
        self[i] = fStart + warped * (b[i] - fStart);
    }
    ref_normalize(self);
}

/* --- inputs --- */
static uint64_t rng = 0x9e3779b97f4a7c15ull;
static uint32_t next(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)(rng >> 16); }
static float frand(float lo, float hi) { return lo + (hi - lo) * (float)(next() & 0xffffff) / 16777216.0f; }
static float fbits(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static uint32_t ubits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }

static const uint32_t EDGE[] = {0x00000000, 0x80000000, 0x3f800000, 0xbf800000, 0x3f000000, 0x7f800000, 0xff800000,
                                0x7fc00000, 0x7fa00001, 0x00000001, 0x807fffff, 0x7f7fffff, 0x3f7fffff, 0x3f000001,
                                0x3effffff, 0x3f6a4b55, 0x3f26f151, 0x3f75851f};

static float pick(int mode)
{
    switch (mode) {
    case 0: return frand(-1.0f, 1.0f);
    case 1: return fbits(next());
    case 2: return fbits(EDGE[next() % (sizeof EDGE / sizeof EDGE[0])]);
    default: return frand(-4.0f, 4.0f);
    }
}

int main(void)
{
    long cases = 0, bad = 0;
    for (int round = 0; round < 64; round++) {
        __fnv_0109c9d0 = frand(0.0f, 1.5f);
        __fnv_0109c9d8 = frand(0.0f, 1.5f);
        __fnv_011f3fec = round & 1 ? frand(-2.0f, 0.0f) : fbits(next());
        __fnv_011f3ff0 = frand(0.5f, 1.5f);
        for (int i = 0; i < 4000; i++) {
            int mode = (int)(next() % 4);
            struct fnvnx_quat a, b, out;
            float ra[4], rb[4], rout[4];
            for (int k = 0; k < 4; k++) {
                a.c[k] = ra[k] = pick(mode);
                b.c[k] = rb[k] = pick(next() % 3 ? mode : 2);
            }
            if (mode == 0) {   /* unit-length quaternions, the engine's case */
                float s = 0, s2 = 0;
                for (int k = 0; k < 4; k++) { s += a.c[k] * a.c[k]; s2 += b.c[k] * b.c[k]; }
                for (int k = 0; k < 4; k++) {
                    a.c[k] = ra[k] = s > 0 ? a.c[k] / __builtin_sqrtf(s) : a.c[k];
                    b.c[k] = rb[k] = s2 > 0 ? b.c[k] / __builtin_sqrtf(s2) : b.c[k];
                }
            }
            float t = next() % 8 ? frand(0.0f, 1.0f) : pick((int)(next() % 4));
            int alias = (int)(next() % 4);   /* 0: out separate, 1: out == a, 2: out == b */
            if (alias == 1) {
                out = a;
                memcpy(rout, ra, 16);
                fnvnx_slerp_00a6e330(&out, t, &out, &b);
                ref_slerp(rout, t, rout, rb);
            } else if (alias == 2) {
                out = b;
                memcpy(rout, rb, 16);
                fnvnx_slerp_00a6e330(&out, t, &a, &out);
                ref_slerp(rout, t, ra, rout);
            } else {
                fnvnx_slerp_00a6e330(&out, t, &a, &b);
                ref_slerp(rout, t, ra, rb);
            }
            cases++;
            int differ = 0;
            for (int k = 0; k < 4; k++) {
                uint32_t x = ubits(out.c[k]), y = ubits(rout[k]);
                /* any NaN matches any NaN: with two NaN operands the payload follows operand order, which GCC
                 * may commute in either build; everything else must be the same bits */
                int xn = (x & 0x7fffffff) > 0x7f800000, yn = (y & 0x7fffffff) > 0x7f800000;
                if (xn != yn || (!xn && x != y))
                    differ = 1;
            }
            if (differ && bad++ < 8)
                printf("mismatch t=%08x a=%08x %08x %08x %08x b=%08x %08x %08x %08x: %08x %08x %08x %08x vs "
                       "%08x %08x %08x %08x\n", ubits(t), ubits(ra[0]), ubits(ra[1]), ubits(ra[2]), ubits(ra[3]),
                       ubits(rb[0]), ubits(rb[1]), ubits(rb[2]), ubits(rb[3]), ubits(out.c[0]), ubits(out.c[1]),
                       ubits(out.c[2]), ubits(out.c[3]), ubits(rout[0]), ubits(rout[1]), ubits(rout[2]),
                       ubits(rout[3]));
        }
    }
    printf("slerp: %ld cases, %ld mismatches\n", cases, bad);
    return bad != 0;
}
