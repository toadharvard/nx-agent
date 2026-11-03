// test_asm.cpp - host test of shim/include/fnvnx_asm.h against x87-free reference math (stream S8, docs/ASM.md).
//
//   tests/s8/run.sh            (host g++, runs it; also compiles the header for AArch64 ILP32 when that g++ exists)
//
// References are written from the documented x87 semantics (Intel SDM: FISTP, FSINCOS, register-stack underflow
// with masked exceptions) and from the operation order of the decomp's fixed sequences (docs/ASM.md), in long
// double, independent of the code under test.
#include <cfenv>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>

#define FNVNX_ASM_FNV_HELPERS 1
#include "fnvnx_asm.h"

static int failures = 0;
#define CHECK(cond, ...)                                                                                            \
    do {                                                                                                            \
        if (!(cond)) {                                                                                              \
            ++failures;                                                                                             \
            std::printf("FAIL %s:%d: ", __FILE__, __LINE__);                                                        \
            std::printf(__VA_ARGS__);                                                                               \
            std::printf("\n");                                                                                      \
        }                                                                                                           \
    } while (0)

static uint32_t bits(float f) { uint32_t u; std::memcpy(&u, &f, 4); return u; }
static const uint32_t DEFAULT_NAN = 0xffc00000u;  // x87 "real indefinite" as a float

static long long ulps(float a, float b)
{
    if (std::isnan(a) || std::isnan(b)) return (std::isnan(a) && std::isnan(b)) ? 0 : 1LL << 40;
    int32_t ia = (int32_t)bits(a), ib = (int32_t)bits(b);
    if (ia < 0) ia = (int32_t)0x80000000 - ia;
    if (ib < 0) ib = (int32_t)0x80000000 - ib;
    long long d = (long long)ia - ib;
    return d < 0 ? -d : d;
}

// ---- fistp: round in the current mode; NaN / out of range -> most negative value of the destination width
template <class T> static T ref_fistp(long double v)
{
    if (std::isnan(v)) return std::numeric_limits<T>::min();
    long double r = std::nearbyintl(v);
    if (r < (long double)std::numeric_limits<T>::min() || r > (long double)std::numeric_limits<T>::max())
        return std::numeric_limits<T>::min();
    return (T)r;
}

static void test_fistp()
{
    const float fs[] = {0.0f, -0.0f, 0.5f, 1.5f, 2.5f, -0.5f, -1.5f, -2.5f, 0.49999997f, 1e9f, -1e9f, 2147483520.0f,
                        2147483648.0f, -2147483648.0f, -2147483904.0f, 1e10f, -1e10f, 3.4e38f, 12345.678f, -98765.4f,
                        std::numeric_limits<float>::infinity(), -std::numeric_limits<float>::infinity(),
                        std::numeric_limits<float>::quiet_NaN(), 1e-40f};
    const int modes[] = {FE_TONEAREST, FE_UPWARD, FE_DOWNWARD, FE_TOWARDZERO};
    for (int mode : modes) {
        std::fesetround(mode);
        for (float f : fs) {
            int want = ref_fistp<int>(f);
            CHECK(fnvnx_fistp32f(f) == want, "fistp32f(%a) mode %d = %d, want %d", f, mode, fnvnx_fistp32f(f), want);
            CHECK(fnvnx_fistp32(f) == want, "fistp32(%a) mode %d", f, mode);
            CHECK(fnv_FloatToInt(f) == want, "fnv_FloatToInt(%a) mode %d", f, mode);
            short s16; fnvnx_asm_fistp(&s16, (double)f);
            CHECK(s16 == ref_fistp<short>(f), "fistp16(%a) mode %d = %d", f, mode, s16);
            long long s64; fnvnx_asm_fistp(&s64, (double)f);
            CHECK(s64 == ref_fistp<long long>(f), "fistp64(%a) mode %d = %lld", f, mode, s64);
        }
    }
    std::fesetround(FE_TONEAREST);
    // dword store of the template into a wider object touches 4 bytes only
    unsigned char buf[8]; std::memset(buf, 0xAA, 8);
    fnvnx_asm_fistp((int*)buf, 7.0f);
    CHECK(buf[4] == 0xAA && buf[7] == 0xAA && buf[0] == 7, "fistp dword store width");
}

// ---- fsincos
static void test_sincos()
{
    long long worst = 0;
    for (int i = -200000; i <= 200000; ++i) {
        float x = (float)i * 0.0005f;  // [-100, 100] rad, covers every quadrant many times
        float s, c;
        fnv_SinCos(x, s, c);
        long long es = ulps(s, (float)std::sin((long double)x)), ec = ulps(c, (float)std::cos((long double)x));
        if (es > worst) worst = es;
        if (ec > worst) worst = ec;
    }
    CHECK(worst <= 1, "fsincos worst error %lld ulp over [-100, 100]", worst);

    float s, c;
    fnv_SinCos(1e19f, s, c);  // finite, |x| >= 2^63: C2 set, nothing pushed
    CHECK(bits(c) == bits(1e19f) && bits(s) == DEFAULT_NAN, "fsincos out of range: c=%a s=%a", c, s);
    fnv_SinCos(-std::numeric_limits<float>::infinity(), s, c);
    CHECK(bits(c) == DEFAULT_NAN && bits(s) == DEFAULT_NAN, "fsincos(-inf)");
    fnv_SinCos(std::numeric_limits<float>::quiet_NaN(), s, c);
    CHECK(std::isnan(s) && std::isnan(c), "fsincos(nan)");
    fnv_SinCos(9.2233715e18f, s, c);  // just below 2^63: computed
    CHECK(!std::isnan(s) && !std::isnan(c) && std::fabs(s * s + c * c - 1.0f) < 1e-5f, "fsincos just below 2^63");

    float st0, st1;  // block form: fld x / fsincos / fstp a / fstp b -> a = cos, b = sin
    fnvnx_fsincos(0.5f, &st0, &st1);
    CHECK(ulps(st0, std::cos(0.5f)) <= 1 && ulps(st1, std::sin(0.5f)) <= 1, "fnvnx_fsincos order");
}

// ---- fixed x87 loops
static float frand(uint32_t& s) { s = s * 1664525u + 1013904223u; return ((int32_t)(s >> 8) - (1 << 23)) / 65536.0f; }

static void test_transforms()
{
    uint32_t seed = 12345;
    for (int trial = 0; trial < 2000; ++trial) {
        float m[9], src[12], dst[12], tx = frand(seed), ty = frand(seed), tz = frand(seed);
        for (float& v : m) v = frand(seed) / 64.0f;
        for (float& v : src) v = frand(seed);
        unsigned n = 1 + trial % 4;

        fnvnx_transform_vectors(n, src, dst, m);
        for (unsigned k = 0; k < n; ++k)
            for (int r = 0; r < 3; ++r) {
                long double want = (long double)src[3 * k] * m[3 * r] + (long double)src[3 * k + 1] * m[3 * r + 1] +
                                   (long double)src[3 * k + 2] * m[3 * r + 2];
                long double tol = 1e-6L * (std::fabs((long double)src[3 * k] * m[3 * r]) +
                                           std::fabs((long double)src[3 * k + 1] * m[3 * r + 1]) +
                                           std::fabs((long double)src[3 * k + 2] * m[3 * r + 2]) + 1e-30L);
                CHECK(std::fabs(dst[3 * k + r] - want) <= tol, "transform_vectors trial %d", trial);
            }

        fnvnx_transform_points(n, src, dst, m, tx, ty, tz);
        const float t[3] = {tx, ty, tz};
        for (unsigned k = 0; k < n; ++k)
            for (int r = 0; r < 3; ++r) {
                long double p0 = (long double)src[3 * k] * m[3 * r], p1 = (long double)src[3 * k + 1] * m[3 * r + 1],
                            p2 = (long double)src[3 * k + 2] * m[3 * r + 2];
                long double want = p0 + p1 + p2 + t[r];
                long double tol = 1e-6L * (std::fabs(p0) + std::fabs(p1) + std::fabs(p2) + std::fabs((long double)t[r]));
                CHECK(std::fabs(dst[3 * k + r] - want) <= tol, "transform_points trial %d", trial);
            }

        // in place: rows 0 and 1 read the old vector; row 2 reads the new x (the original stores dst[0] before it
        // reads s0 for row 2) and the old y, z; transform_points reads everything before storing.
        float v[3] = {src[0], src[1], src[2]}, old[3] = {src[0], src[1], src[2]};
        fnvnx_transform_vectors(1, v, v, m);
        long double nx = (long double)old[0] * m[0] + (long double)old[1] * m[1] + (long double)old[2] * m[2];
        long double nz = (long double)v[0] * m[6] + (long double)old[1] * m[7] + (long double)old[2] * m[8];
        long double tol = 1e-5L * (std::fabs(nx) + std::fabs(nz) + 1.0L);
        CHECK(std::fabs(v[0] - nx) <= tol && std::fabs(v[2] - nz) <= tol, "transform_vectors in place, trial %d", trial);
        float w[3] = {old[0], old[1], old[2]};
        fnvnx_transform_points(1, w, w, m, tx, ty, tz);
        long double pz = (long double)old[0] * m[6] + (long double)old[1] * m[7] + (long double)old[2] * m[8] + tz;
        CHECK(std::fabs(w[2] - pz) <= 1e-5L * (std::fabs(pz) + 1.0L) + 1e-5L * std::fabs((long double)tz),
              "transform_points in place, trial %d", trial);

        float out[3] = {dst[0], dst[1], dst[2]}, p[3] = {src[0], src[1], src[2]}, wt = m[0];
        float before[3] = {out[0], out[1], out[2]};
        fnv_AddWeightedPoint(out, p, wt);
        for (int r = 0; r < 3; ++r) {
            long double want = (long double)p[r] * wt + before[r];
            CHECK(std::fabs(out[r] - want) <= 1e-6L * (std::fabs((long double)p[r] * wt) + std::fabs((long double)before[r])) + 1e-30L,
                  "add_weighted_point trial %d", trial);
        }
    }
    float m[9] = {1, 2, 3, 4, 5, 6, 7, 8, 9}, src[3] = {1, 1, 1}, dst[3] = {-1, -1, -1};
    fnv_TransformVectors(0u, src, dst, m);  // n == 0: no access
    CHECK(dst[0] == -1 && dst[1] == -1 && dst[2] == -1, "transform n == 0");
    fnv_TransformVectors(1u, src, dst, m);
    CHECK(dst[0] == 6 && dst[1] == 15 && dst[2] == 24, "transform_vectors basic");
    float tx = 0.5f, ty = 1.0f, tz = 2.0f;
    fnv_TransformPoints(1u, src, dst, m, tx, ty, tz);
    CHECK(dst[0] == 6.5f && dst[1] == 16 && dst[2] == 26, "transform_points basic");
}

// ---- rdtsc / cpuid / stores
static void test_misc()
{
    unsigned long long a = fnvnx_rdtsc64(), b = fnvnx_rdtsc64();
    CHECK(b >= a, "rdtsc64 monotonic");
    unsigned char buf[8]; std::memset(buf, 0xAA, 8);
    fnvnx_asm_store32(buf, 0x11223344u);
    CHECK(buf[0] == 0x44 && buf[3] == 0x11 && buf[4] == 0xAA, "store32 width");
    unsigned long long t = 0;
    fnv_ReadTicks64(t);
    CHECK(t >= a, "fnv_ReadTicks64");
    volatile unsigned int stamped = 0;
    { fnvnx_asm_store32(&(stamped), fnvnx_rdtsc32()); }
    (void)stamped;

    int r = 0;
    fnv_CpuidMaxExtended(r);
    CHECK((unsigned)r == 0x80000008u, "cpuid 0x80000000");
    fnv_CpuidLegacyExtended(r);
    CHECK(r == 0, "cpuid 0x80000001");
    fnv_CpuidFeatures(r);
    CHECK((r & 0x800000) && (r & 0x8000) && (r & 0x2000000) && (r & 0x4000000), "cpuid 1 edx = %#x", r);
}

int main()
{
    test_fistp();
    test_sincos();
    test_transforms();
    test_misc();
    std::printf("%s (%d failures)\n", failures ? "FAILED" : "ok", failures);
    return failures != 0;
}
