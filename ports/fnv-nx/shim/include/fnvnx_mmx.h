/* fnvnx_mmx.h - MMX (<mmintrin.h>) intrinsics the decomp calls that sse2neon does not provide (hkp3axissweep's
 * broadphase), on its __m64 (int64x1_t). Intel semantics: wrapping 16-bit add / sub, all-ones lane masks for the
 * compares (signed for cmpgt), logical shifts that give 0 for counts past the lane width, low-half interleaves.
 * Included by fnvnx_compat.h after sse2neon.h. */
#ifndef FNVNX_MMX_H
#define FNVNX_MMX_H

#define FNVNX_MMX_FN static inline __attribute__((always_inline))

FNVNX_MMX_FN __m64 _mm_setzero_si64(void) { return vcreate_s64(0); }
FNVNX_MMX_FN __m64 _mm_set_pi32(int i1, int i0)
{
    return vcreate_s64((long long)(((unsigned long long)(unsigned int)i1 << 32) | (unsigned int)i0));
}
FNVNX_MMX_FN __m64 _mm_add_pi16(__m64 a, __m64 b)
{
    return vreinterpret_m64_s16(vadd_s16(vreinterpret_s16_m64(a), vreinterpret_s16_m64(b)));
}
FNVNX_MMX_FN __m64 _mm_sub_pi16(__m64 a, __m64 b)
{
    return vreinterpret_m64_s16(vsub_s16(vreinterpret_s16_m64(a), vreinterpret_s16_m64(b)));
}
FNVNX_MMX_FN __m64 _mm_and_si64(__m64 a, __m64 b) { return vand_s64(a, b); }
FNVNX_MMX_FN __m64 _mm_or_si64(__m64 a, __m64 b) { return vorr_s64(a, b); }
FNVNX_MMX_FN __m64 _mm_cmpeq_pi16(__m64 a, __m64 b)
{
    return vreinterpret_m64_u16(vceq_s16(vreinterpret_s16_m64(a), vreinterpret_s16_m64(b)));
}
FNVNX_MMX_FN __m64 _mm_cmpgt_pi16(__m64 a, __m64 b)
{
    return vreinterpret_m64_u16(vcgt_s16(vreinterpret_s16_m64(a), vreinterpret_s16_m64(b)));
}
FNVNX_MMX_FN __m64 _mm_slli_pi16(__m64 a, int count)
{
    if (count < 0 || count > 15) return vcreate_s64(0);
    return vreinterpret_m64_u16(vshl_u16(vreinterpret_u16_m64(a), vdup_n_s16((short)count)));
}
FNVNX_MMX_FN __m64 _mm_srli_pi16(__m64 a, int count)
{
    if (count < 0 || count > 15) return vcreate_s64(0);
    return vreinterpret_m64_u16(vshl_u16(vreinterpret_u16_m64(a), vdup_n_s16((short)-count)));
}
FNVNX_MMX_FN __m64 _mm_srli_si64(__m64 a, int count)
{
    if (count < 0 || count > 63) return vcreate_s64(0);
    return vreinterpret_m64_u64(vshl_u64(vreinterpret_u64_m64(a), vdup_n_s64(-(long long)count)));
}
FNVNX_MMX_FN __m64 _mm_unpacklo_pi16(__m64 a, __m64 b)
{
    return vreinterpret_m64_s16(vzip1_s16(vreinterpret_s16_m64(a), vreinterpret_s16_m64(b)));
}
FNVNX_MMX_FN __m64 _mm_unpacklo_pi32(__m64 a, __m64 b)
{
    return vreinterpret_m64_s32(vzip1_s32(vreinterpret_s32_m64(a), vreinterpret_s32_m64(b)));
}

#undef FNVNX_MMX_FN
#endif
