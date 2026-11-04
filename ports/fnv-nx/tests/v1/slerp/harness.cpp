// harness.cpp - V1 (D14) against S15-simd's NEON Slerp override (f0861e2, tools/link/overrides/slerp_00a6e330.c).
//
// The reference is not a transcription: test_slerp_decomp.py copies the decomp's own function bodies
// (niquaternion.cpp: FUN_00a6dc30, NiQuaternion::Slerp, isqrt_approx_in_neighborhood, NormalizeQuaternion) into a
// temporary decomp_slerp.inc at run time, so no decomp text is in git. This file supplies the stand-in class and
// constants, the inputs and the comparison.
//
//   -DWITH_OVERRIDE  (AArch64 build): also run fnvnx_slerp_00a6e330 and count bit mismatches against the decomp body
//   output: one line per case, "<decomp out x4 hex>" (the python compares the AArch64, x86-64 SSE and x87 builds)
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <math.h>

extern "C" {
double __fnv_0109c9d0, __fnv_0109c9d8;
float __fnv_011f3fec, __fnv_011f3ff0;
#ifdef WITH_OVERRIDE
struct fnvnx_quat { float c[4]; };
void fnvnx_slerp_00a6e330(struct fnvnx_quat *self, float t, const struct fnvnx_quat *a, const struct fnvnx_quat *b);
#endif
}
#define DAT_0109c9d0 __fnv_0109c9d0
#define DAT_0109c9d8 __fnv_0109c9d8
#define DAT_011F3FEC __fnv_011f3fec
#define DAT_011F3FF0 __fnv_011f3ff0

class NiQuaternion {
  public:
    float m_fVectorX, m_fVectorY, m_fVectorZ, m_fScalarPart;
    void EnsureCanonicalSign();
    void Slerp(float t, const NiQuaternion &a, const NiQuaternion &b);
    static __attribute__((noinline)) float CalculateSlerpWeight(float t, float cosine);
};
struct FUN_00a4ff00_shell : public NiQuaternion {
    void NormalizeQuaternion();
};

#include "decomp_slerp.inc"   // generated: the decomp's bodies, unchanged

float NiQuaternion::CalculateSlerpWeight(float t, float cosine) { return FUN_00a6dc30(t, cosine); }
void NiQuaternion::EnsureCanonicalSign() { static_cast<FUN_00a4ff00_shell *>(this)->NormalizeQuaternion(); }

static uint64_t rng = 0x243f6a8885a308d3ull;
static uint32_t nx(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)(rng >> 11); }
static float fb(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static uint32_t ub(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
static float fr(float lo, float hi) { return lo + (hi - lo) * (float)(nx() & 0xffffff) / 16777216.0f; }

static void unit(float *q) {
    double s = 0;
    for (int k = 0; k < 4; k++) s += (double)q[k] * q[k];
    if (s > 0) for (int k = 0; k < 4; k++) q[k] = (float)(q[k] / sqrt(s));
}

static const uint32_t EDGE[] = {0x00000000, 0x80000000, 0x00000001, 0x80000001, 0x007fffff, 0x00800000, 0x3f800000,
                                0xbf800000, 0x3f7fffff, 0x3f800001, 0x3f000000, 0x3effffff, 0x3f000001, 0x7f800000,
                                0xff800000, 0x7fc00000, 0xffc00001, 0x7f800001, 0x7f7fffff, 0x3f6a4b55, 0x3f26f151};

int main(void) {
    long cases = 0, bad = 0, bad_nan_only = 0;
    for (int round = 0; round < 48; round++) {
        if (round == 0) {   // J. Blow's published fast-slerp / isqrt-neighbourhood constants
            __fnv_0109c9d8 = 0.82279687;
            __fnv_0109c9d0 = 0.58549219;
            const double N = 0.959066, S = 1.000311;
            __fnv_011f3ff0 = (float)(S / sqrt(N));
            __fnv_011f3fec = (float)(S * (-0.5 / (N * sqrt(N))));
        } else {
            __fnv_0109c9d8 = fr(0.0f, 1.2f);
            __fnv_0109c9d0 = fr(0.0f, 1.2f);
            __fnv_011f3ff0 = fr(0.8f, 1.2f);
            __fnv_011f3fec = fr(-0.8f, -0.3f);
        }
        for (int i = 0; i < 3000; i++) {
            float a[4], b[4], t;
            int mode = (int)(nx() % 6);
            for (int k = 0; k < 4; k++) {
                a[k] = fr(-1, 1);
                b[k] = fr(-1, 1);
            }
            unit(a);
            unit(b);
            switch (mode) {
            case 1:   // antipodal-ish: dot < 0 (the decomp does not flip; neither may the override)
                for (int k = 0; k < 4; k++) b[k] = -a[k] + fr(-1e-3f, 1e-3f);
                break;
            case 2:   // nearly identical: cos -> 1
                for (int k = 0; k < 4; k++) b[k] = fb(ub(a[k]) ^ (nx() & 7));
                break;
            case 3:   // edge components (NaN, inf, denormal, signed zero)
                a[nx() % 4] = fb(EDGE[nx() % (sizeof EDGE / 4)]);
                b[nx() % 4] = fb(EDGE[nx() % (sizeof EDGE / 4)]);
                break;
            case 4:   // raw bits, unnormalised
                for (int k = 0; k < 4; k++) { a[k] = fb(nx() << 1 ^ nx()); b[k] = fb(nx() << 1 ^ nx()); }
                break;
            case 5:   // denormal quaternions (s far below every threshold)
                for (int k = 0; k < 4; k++) { a[k] = fb(nx() & 0x807fffff); b[k] = fb(nx() & 0x807fffff); }
                break;
            }
            switch (nx() % 6) {
            case 0: t = fb(EDGE[nx() % (sizeof EDGE / 4)]); break;
            case 1: t = fb(0x3f000000u + (int)(nx() % 5) - 2); break;   // around 0.5, the branch point
            default: t = fr(0, 1);
            }
            NiQuaternion qa, qb, qo;
            memcpy(&qa, a, 16);
            memcpy(&qb, b, 16);
            qo.Slerp(t, qa, qb);
            float o[4];
            memcpy(o, &qo, 16);
            printf("%08x %08x %08x %08x %d\n", ub(o[0]), ub(o[1]), ub(o[2]), ub(o[3]), mode);
            cases++;
#ifdef WITH_OVERRIDE
            fnvnx_quat fa, fbq, fo;
            memcpy(fa.c, a, 16);
            memcpy(fbq.c, b, 16);
            fnvnx_slerp_00a6e330(&fo, t, &fa, &fbq);
            int exact = 1, nanok = 1;
            for (int k = 0; k < 4; k++) {
                uint32_t x = ub(fo.c[k]), y = ub(o[k]);
                if (x != y) {
                    exact = 0;
                    if (!(isnan(fo.c[k]) && isnan(o[k]))) nanok = 0;
                }
            }
            if (!exact) {
                if (nanok) {
                    if (bad_nan_only++ < 3)
                    fprintf(stderr, "nan-payload t=%08x a=%08x,%08x,%08x,%08x b=%08x,%08x,%08x,%08x: override "
                            "%08x,%08x,%08x,%08x decomp %08x,%08x,%08x,%08x\n", ub(t), ub(a[0]), ub(a[1]), ub(a[2]),
                            ub(a[3]), ub(b[0]), ub(b[1]), ub(b[2]), ub(b[3]), ub(fo.c[0]), ub(fo.c[1]), ub(fo.c[2]),
                            ub(fo.c[3]), ub(o[0]), ub(o[1]), ub(o[2]), ub(o[3]));
                } else if (bad++ < 8)
                    fprintf(stderr, "mismatch t=%08x a=%08x,%08x,%08x,%08x b=%08x,%08x,%08x,%08x\n", ub(t), ub(a[0]),
                            ub(a[1]), ub(a[2]), ub(a[3]), ub(b[0]), ub(b[1]), ub(b[2]), ub(b[3]));
            }
#endif
        }
    }
#ifdef WITH_OVERRIDE
    fprintf(stderr, "override: %ld cases, %ld mismatches, %ld differ only in a NaN payload\n", cases, bad, bad_nan_only);
    return bad != 0;
#else
    return 0;
#endif
}
