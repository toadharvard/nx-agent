/* fnvnx_asm.h - portable replacements for the decomp's MSVC x86 inline assembly (stream S8, docs/ASM.md).
 *
 * prepare.py rules in tools/rules/s8_asm.py rewrite every `__asm { ... }` site of the decomp into calls of the
 * functions below, and replace the decomp's x87 helper header with a two-line header that includes this one with
 * FNVNX_ASM_FNV_HELPERS defined (the helper names the decomp calls, fnv_SinCos, fnv_FloatToInt, ...).
 *
 * The aim is behavioural equivalence with the x86 code, not bit-exactness: same math in the same operation order,
 * same memory reads and writes in the same order where the operands may alias, same results on the edge cases the
 * x87 defines (out-of-range conversions, out-of-range fsincos operands). Precision notes are in docs/ASM.md.
 *
 * No standard headers on purpose: this file is included into decomp units that declare their own CRT prototypes,
 * so it only uses compiler builtins and fnvnx_-prefixed names (static inline, no link-visible symbols).
 */
#ifndef FNVNX_ASM_H
#define FNVNX_ASM_H

#if defined(__cplusplus)
#define FNVNX_ASM_INLINE static inline __attribute__((always_inline, unused))
#else
#define FNVNX_ASM_INLINE static inline __attribute__((always_inline, unused))
#endif

/* ------------------------------------------------------------------------------------------- memory stores
 * `mov dword ptr [v], eax` / `fistp qword ptr [v]` write a fixed number of bytes whatever v's declared type is;
 * these do the same (the decomp's targets are 32-bit integers of many typedef spellings, some volatile). */

FNVNX_ASM_INLINE void fnvnx_asm_store32(volatile void* p, unsigned int v) { __builtin_memcpy((void*)p, &v, 4); }
FNVNX_ASM_INLINE void fnvnx_asm_store64(volatile void* p, unsigned long long v) { __builtin_memcpy((void*)p, &v, 8); }

/* ------------------------------------------------------------------------------------------- time stamp counter
 * rdtsc: a free-running 64-bit tick counter. AArch64: the generic timer's physical count (CNTPCT_EL0, 19.2 MHz on the
 * Switch, the only count Horizon opens to EL0 - libnx armGetSystemTick() reads the same register). Every tick consumer in
 * the decomp either takes differences of these values or calibrates them against QueryPerformanceCounter (Havok
 * hkSystemClock), so only the rate changes. The "memory" clobber keeps the read ordered against surrounding memory
 * accesses like an MSVC __asm block (which the compiler does not move code across). */

FNVNX_ASM_INLINE unsigned long long fnvnx_rdtsc64(void)
{
#if defined(__aarch64__)
    unsigned long long v;
    /* CNTPCT_EL0, not CNTVCT_EL0: Horizon's kernel opens only the physical count to EL0 (Mesosphere writes
     * CNTKCTL_EL1 = EL0PCTEN, kern_k_hardware_timer.hpp), a CNTVCT_EL0 read traps; libnx's armGetSystemTick reads
     * the same register (NX1 check 3) */
    __asm__ __volatile__("mrs %0, cntpct_el0" : "=r"(v) : : "memory");
    return v;
#elif defined(__i386__) || defined(__x86_64__)
    unsigned long long v;
    __asm__ __volatile__("" : : : "memory");
    v = __builtin_ia32_rdtsc();
    __asm__ __volatile__("" : : : "memory");
    return v;
#else
#error "fnvnx_asm.h: no tick counter for this target"
#endif
}

FNVNX_ASM_INLINE unsigned int fnvnx_rdtsc32(void) { return (unsigned int)fnvnx_rdtsc64(); }

/* ------------------------------------------------------------------------------------------- cpuid
 * There is no CPUID on AArch64. The decomp asks it only to grade the CPU (MMX / CMOV / SSE checks of leaf 1 EDX, the
 * extended-leaf range). The port answers as an SSE2-class x86 (what sse2neon provides): FPU, TSC, CX8, CMOV, MMX,
 * FXSR, SSE, SSE2. Leaf 0x80000000 reports extended leaves up to 0x80000008; every other leaf reads as zero. */

#ifndef FNVNX_CPUID_1_EDX
#define FNVNX_CPUID_1_EDX 0x07808111u /* bits 0 FPU, 4 TSC, 8 CX8, 15 CMOV, 23 MMX, 24 FXSR, 25 SSE, 26 SSE2 */
#endif

FNVNX_ASM_INLINE unsigned int fnvnx_cpuid_eax(unsigned int leaf)
{
    return leaf == 0u ? 1u : leaf == 0x80000000u ? 0x80000008u : 0u;
}

FNVNX_ASM_INLINE unsigned int fnvnx_cpuid_edx(unsigned int leaf) { return leaf == 1u ? FNVNX_CPUID_1_EDX : 0u; }

/* ------------------------------------------------------------------------------------------- int 3
 * Havok HK_BREAKPOINT in release builds: a breakpoint trap after an error report. Overridable by the platform layer
 * (e.g. to log and continue) by defining FNVNX_ASM_BREAKPOINT before this header. */

#ifndef FNVNX_ASM_BREAKPOINT
#define FNVNX_ASM_BREAKPOINT() __builtin_trap()
#endif

FNVNX_ASM_INLINE void fnvnx_breakpoint(void) { FNVNX_ASM_BREAKPOINT(); }

/* ------------------------------------------------------------------------------------------- fistp
 * `fld x` + `fistp m` converts in the *current* rounding mode (round to nearest even unless the program changed the
 * x87 control word; the platform layer maps _controlfp's RC field onto FPCR.RMode so rint follows it). A NaN or a
 * value out of the destination's range stores the "integer indefinite" (the most negative value): the x87 response
 * with the invalid-operation exception masked, as Windows runs it. AArch64's FCVT* would saturate instead, hence the
 * explicit range check. */

FNVNX_ASM_INLINE int fnvnx_fistp32f(float v)
{
    float r = __builtin_rintf(v);
    if (!(r >= -2147483648.0f && r < 2147483648.0f)) return (int)0x80000000u;
    return (int)r;
}

FNVNX_ASM_INLINE int fnvnx_fistp32(double v)
{
    double r = __builtin_rint(v);
    if (!(r >= -2147483648.0 && r < 2147483648.0)) return (int)0x80000000u;
    return (int)r;
}

FNVNX_ASM_INLINE short fnvnx_fistp16(double v)
{
    double r = __builtin_rint(v);
    if (!(r >= -32768.0 && r < 32768.0)) return (short)0x8000u;
    return (short)r;
}

FNVNX_ASM_INLINE long long fnvnx_fistp64(double v)
{
    double r = __builtin_rint(v);
    if (!(r >= -9223372036854775808.0 && r < 9223372036854775808.0)) return (long long)0x8000000000000000ull;
    return (long long)r;
}

#ifdef __cplusplus
/* `fld src` + `fistp dst`: the store width is the width of dst (word / dword / qword), as MSVC infers it. */
template <class T> static inline __attribute__((always_inline)) void fnvnx_asm_fistp(T* dst, double v)
{
    static_assert(sizeof(T) == 2 || sizeof(T) == 4 || sizeof(T) == 8, "fistp stores 2, 4 or 8 bytes");
    if (sizeof(T) == 2) { short r = fnvnx_fistp16(v); __builtin_memcpy((void*)dst, &r, 2); }
    else if (sizeof(T) == 4) { int r = fnvnx_fistp32(v); __builtin_memcpy((void*)dst, &r, 4); }
    else { long long r = fnvnx_fistp64(v); __builtin_memcpy((void*)dst, &r, 8); }
}
template <class T> static inline __attribute__((always_inline)) void fnvnx_asm_fistp(T* dst, float v)
{
    if (sizeof(T) == 4) { int r = fnvnx_fistp32f(v); __builtin_memcpy((void*)dst, &r, 4); }
    else fnvnx_asm_fistp(dst, (double)v);
}
#endif

/* ------------------------------------------------------------------------------------------- fsincos
 * `fld x` + `fsincos` leaves cos(x) in ST0 and sin(x) in ST1, computed in extended precision and rounded once on
 * the float store. sin/cos in double rounded to float matches that to within the last bit (x87 reduces with a 66-bit
 * pi, double libm reduces exactly; they differ visibly only for |x| in the thousands of radians and above).
 * Edge cases as the x87 defines them:
 *   NaN        -> both results are that NaN (quieted);
 *   +-inf      -> invalid operation, both results are the default NaN;
 *   |x| >= 2^63 (finite) -> C2 is set and nothing is pushed: the first fstp stores x, the second underflows the
 *                 register stack and stores the default NaN.
 * FNVNX_ASM_FAST_TRIG uses sinf/cosf instead of the double functions (faster on the A57, < 1 ulp apart). */

FNVNX_ASM_INLINE void fnvnx_fsincos(float x, float* st0_cos, float* st1_sin)
{
    float ax = __builtin_fabsf(x);
    if (__builtin_expect(!(ax < 9223372036854775808.0f), 0)) {
        float dnan = -__builtin_nanf("");
        if (x != x) { *st0_cos = x + 0.0f; *st1_sin = x + 0.0f; }
        else if (ax == __builtin_inff()) { *st0_cos = dnan; *st1_sin = dnan; }
        else { *st0_cos = x; *st1_sin = dnan; }
        return;
    }
#ifdef FNVNX_ASM_FAST_TRIG
    *st0_cos = __builtin_cosf(x);
    *st1_sin = __builtin_sinf(x);
#else
    *st0_cos = (float)__builtin_cos((double)x);
    *st1_sin = (float)__builtin_sin((double)x);
#endif
}

FNVNX_ASM_INLINE void fnvnx_sincos(float x, float* s, float* c)
{
    float cs, sn;
    fnvnx_fsincos(x, &cs, &sn);
    *s = sn;
    *c = cs;
}

/* ------------------------------------------------------------------------------------------- x87 loops
 * The decomp's fixed x87 sequences from the PC executable, as float arithmetic in the same association order.
 * Memory accesses keep the original order where src/dst/matrix may alias (pointers are not restrict).
 * Products go through fnvnx_x87_mul so GCC cannot contract them with the following add into a fused multiply-add
 * (GNU mode defaults to -ffp-contract=fast, and an optimize attribute does not survive inlining): with the x87
 * precision control at 24 bits, which D3D9 sets on device creation unless D3DCREATE_FPU_PRESERVE, every x87 step
 * rounds to float and these functions give the same bits as the original (tests/s8). */

FNVNX_ASM_INLINE float fnvnx_x87_mul(float a, float b)
{
    float r = a * b;
#if defined(__aarch64__) && !defined(FNVNX_MSVC_NAMES)
    __asm__("" : "+w"(r));
#elif defined(__SSE__)
    __asm__("" : "+x"(r));
#endif
    return r;
}

/* 3x3 matrix (row-major, 9 floats) times n vectors of 3 floats:
 *   dst[r] = (s0 * m[3r] + s1 * m[3r+1]) + s2 * m[3r+2]
 * Order of the original: row 0 and row 1 products are read, dst[0] is stored, then s0 and m[6..8] are read again
 * for row 2 (so an in-place call sees the new dst[0] there), dst[1] is stored after row 2's products are read. */
FNVNX_ASM_INLINE void fnvnx_transform_vectors(unsigned int n, const float* src, float* dst, const float* m)
{
    for (; n != 0; --n, src += 3, dst += 3) {
        float x = (fnvnx_x87_mul(src[0], m[0]) + fnvnx_x87_mul(src[1], m[1])) + fnvnx_x87_mul(src[2], m[2]);
        float d = fnvnx_x87_mul(src[0], m[3]);
        float e = fnvnx_x87_mul(src[1], m[4]);
        float f = fnvnx_x87_mul(src[2], m[5]);
        dst[0] = x;
        float y = (d + e) + f;
        float g = fnvnx_x87_mul(src[0], m[6]);
        float h = fnvnx_x87_mul(src[1], m[7]);
        float i = fnvnx_x87_mul(src[2], m[8]);
        dst[1] = y;
        dst[2] = (g + h) + i;
    }
}

/* Same with a translation, every read before the first store:
 *   dst[r] = ((s0 * m[3r] + s1 * m[3r+1]) + s2 * m[3r+2]) + t[r] */
FNVNX_ASM_INLINE void fnvnx_transform_points(unsigned int n, const float* src, float* dst, const float* m,
                                             float tx, float ty, float tz)
{
    for (; n != 0; --n, src += 3, dst += 3) {
        float s0 = src[0], s1 = src[1], s2 = src[2];
        float x = ((fnvnx_x87_mul(s0, m[0]) + fnvnx_x87_mul(s1, m[1])) + fnvnx_x87_mul(s2, m[2])) + tx;
        float y = ((fnvnx_x87_mul(s0, m[3]) + fnvnx_x87_mul(s1, m[4])) + fnvnx_x87_mul(s2, m[5])) + ty;
        float z0 = (fnvnx_x87_mul(m[6], s0) + fnvnx_x87_mul(m[7], s1)) + fnvnx_x87_mul(m[8], s2);
        dst[0] = x;
        dst[1] = y;
        dst[2] = z0 + tz;
    }
}

/* out[k] = p[k] * w + out[k]; out[0] is stored before out[2] is read (as in the original). */
FNVNX_ASM_INLINE void fnvnx_add_weighted_point(float* out, const float* p, float w)
{
    float a = fnvnx_x87_mul(p[0], w) + out[0];
    float b = fnvnx_x87_mul(p[1], w) + out[1];
    float c = fnvnx_x87_mul(p[2], w);
    out[0] = a;
    c = c + out[2];
    out[1] = b;
    out[2] = c;
}

/* ------------------------------------------------------------------------------------------- decomp helper names
 * The names of the decomp's include/fnv_asm_helpers.h, which prepare (rule ASM1) replaces with a header that defines
 * FNVNX_ASM_FNV_HELPERS and includes this one. Signatures and macro arguments as in the decomp. */
#if defined(FNVNX_ASM_FNV_HELPERS) && defined(__cplusplus)
inline __attribute__((always_inline)) void fnv_SinCos(float fRadians, float& fSin, float& fCos)
{
    float s, c;
    fnvnx_sincos(fRadians, &s, &c);
    fSin = s;
    fCos = c;
}

inline __attribute__((always_inline)) int fnv_FloatToInt(float fValue) { return fnvnx_fistp32f(fValue); }

#define fnv_TransformVectors(n, src, dst, matrix)                                                                   \
    fnvnx_transform_vectors((unsigned int)(n), (const float*)(src), (float*)(dst), (const float*)(matrix))
#define fnv_TransformPoints(n, src, dst, matrix, tx, ty, tz)                                                        \
    fnvnx_transform_points((unsigned int)(n), (const float*)(src), (float*)(dst), (const float*)(matrix), (tx), (ty), \
                           (tz))
#define fnv_ReadTicks64(ticks) fnvnx_asm_store64(&(ticks), fnvnx_rdtsc64())
#define fnv_CpuidMaxExtended(result) fnvnx_asm_store32(&(result), fnvnx_cpuid_eax(0x80000000u))
/* the original loads EDX, not EAX, with 0x80000001 before cpuid, so the leaf it reads is whatever EAX held; the port
 * defines it as leaf 0x80000001, whose EAX (extended signature) reads as zero */
#define fnv_CpuidLegacyExtended(result) fnvnx_asm_store32(&(result), fnvnx_cpuid_eax(0x80000001u))
#define fnv_CpuidFeatures(result) fnvnx_asm_store32(&(result), fnvnx_cpuid_edx(1u))
#define fnv_Breakpoint() fnvnx_breakpoint()
#define fnv_AddWeightedPoint(out, point, weight) fnvnx_add_weighted_point((float*)(out), (const float*)(point), (weight))
#endif

#endif
