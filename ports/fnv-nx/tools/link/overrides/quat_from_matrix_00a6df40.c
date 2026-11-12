/* fnvnx_quat_from_matrix_00a6df40 - NiQuaternion::FromRotationMatrix (0x00a6df40) without its stack frame (stream
 * S15-simd, docs/PERF.md 11.6, tests/s15/simd).
 *
 * The decomp builds the quaternion from the trace when it is positive, else from the largest diagonal element (the
 * next and last axes come from the image table 0x011a9e98, {1, 2, 0}). Its square root goes through a volatile
 * float (a store and a reload), and the largest-diagonal branch writes three components through an array of
 * pointers on the stack. GCC compiles that to a 112-byte frame, four saved register pairs and indexed loads of the
 * array: 89 A64 instructions per call.
 *
 * This replacement has the same arithmetic, operation by operation and in float as compiled. The source's
 * `root * 0.5` and `0.5 / root` are double expressions, but GCC computes them in float, and both give the same bits:
 * a product by 0.5 is exact, and a quotient of floats rounded once through double is the float quotient. It writes
 * the same components in the same order. The square root keeps C's errno semantics (-fmath-errno: a negative
 * argument calls sqrtf, as the decomp's object does; here after the stores, which nothing can tell apart, and the
 * NaN is the same default NaN either way). The components are addressed by index (component k + 1 for the decomp's
 * apComponents[k]), so no frame is needed on the common paths.
 * Index arithmetic is unsigned: the table holds 0..2, so it is the decomp's signed indexing. */
#include <stdint.h>

struct fnvnx_quat { float c[4]; };      /* NiQuaternion: +0 X, +4 Y, +8 Z, +12 scalar */
struct fnvnx_mat3 { float m[3][3]; };   /* row-major, 36 bytes */

extern const int32_t __fnv_011a9e98[3];   /* next axis: {1, 2, 0} */

/* the square root without errno: one fsqrt (a negative argument gives the default NaN, as sqrtf does) */
static inline float fsqrt(float x)
{
    float r;
    __asm__("fsqrt %s0, %s1" : "=w"(r) : "w"(x));
    return r;
}

/* sqrtf of a negative argument, for its errno (EDOM) only: called last, after the stores, so the common path needs
 * no frame; nothing observes errno in between */
float sqrtf(float);
static __attribute__((noinline, cold)) void sqrtf_errno(float x)
{
    volatile float r = sqrtf(x);
    (void)r;
}

void fnvnx_quat_from_matrix_00a6df40(struct fnvnx_quat *self, const struct fnvnx_mat3 *mat)
{
    const float (*m)[3] = mat->m;
    float root = m[0][0] + m[1][1];
    root = root + m[2][2];
    if (root > 0.0f) {
        root = fsqrt(root + 1.0f);   /* > 1: never the errno case */
        self->c[0] = root * 0.5f;
        root = 0.5f / root;
        self->c[1] = (m[2][1] - m[1][2]) * root;
        self->c[2] = (m[0][2] - m[2][0]) * root;
        self->c[3] = (m[1][0] - m[0][1]) * root;
        return;
    }
    unsigned a = m[0][0] < m[1][1] ? 1 : 0;
    const float *e = &m[0][0];
    if (e[2 * 4] > e[a * 4])
        a = 2;
    unsigned n = (unsigned)__fnv_011a9e98[a];
    unsigned l = (unsigned)__fnv_011a9e98[n];
    float arg = e[a * 4] - e[n * 4];
    arg = arg - e[l * 4];
    arg = arg + 1.0f;
    root = fsqrt(arg);
    float *c = &self->c[1];
    c[a] = root * 0.5f;
    root = 0.5f / root;
    self->c[0] = (e[l * 3 + n] - e[n * 3 + l]) * root;
    c[n] = (e[a * 3 + n] + e[n * 3 + a]) * root;
    c[l] = (e[a * 3 + l] + e[l * 3 + a]) * root;
    if (arg < 0.0f)
        sqrtf_errno(arg);
}
