/* fnvnx_sse_fast.h - shorter NEON sequences for SSE intrinsics where sse2neon's expansion is long (stream
 * S15-simd, docs/PERF.md 11.6). Included by fnvnx_compat.h right after <sse2neon.h> (C++ units only).
 *
 * Only permutations and exact lane moves are replaced: their results are the same bits whatever instructions
 * produce them, so nothing here can change a value (tests/s15/simd/shuffle_test.cpp checks every immediate of every
 * replaced form, one and two sources, against the shim's original __builtin_shuffle form).
 *
 * _mm_shuffle_ps(a, b, imm). sse2neon gives GCC __builtin_shuffle, and GCC 13's AArch64 expander finds a single
 * permute instruction or else emits tbl with a 16-byte literal mask (adrp + ldr q + tbl, plus a register copy for
 * tbl's register pair when a == b): 3-4 instructions for the cross-product shuffles 0xc9 / 0xd2 and the two-source
 * broadcasts. Two-instruction sequences exist (searched exhaustively over zip/uzp/trn/ext/rev64/dup/ins), but GCC
 * folds any chain of permute intrinsics back into one VEC_PERM_EXPR and so into tbl again; the sequences are
 * therefore inline asm (pure, no memory, not volatile: GCC still CSEs, schedules around and deletes them).
 * Only the two-source forms are replaced. Whether the two operands are one value is decided from the macro's
 * argument text (__builtin_strcmp of the two stringified arguments folds to a constant). 93 % of the Havok calls
 * name the same vector twice; those keep GCC's single-source code (dup, ext, rev64, or tbl for 0xc9 / 0xd2).
 * Asm sequences for 0xc9 (3 instructions) and 0xd2 (2) measured worse on the harness: GSK 579 -> 585 / 591 A64
 * per query, because GCC can no longer merge them with neighbouring permutes or share the tbl mask. Arguments
 * are evaluated once each, as before. Same text means same value unless the argument has side effects, which an
 * SSE shuffle operand in the decomp never has (and which would be unsequenced in the original already). */
#pragma once
#if defined(__cplusplus) && defined(__aarch64__) && !defined(FNVNX_SSE_FAST_OFF)

#define FNVNX_ASM1(op, r, x) __asm__(op " %0.4s, %1.4s" : "=w"(r) : "w"(x))
#define FNVNX_ASM2(op, r, x, y) __asm__(op " %0.4s, %1.4s, %2.4s" : "=w"(r) : "w"(x), "w"(y))

/* two sources: lanes a0..a3, b0..b3 */
static inline __attribute__((always_inline)) float32x4_t fnvnx_shuf2_bcast(float32x4_t a, float32x4_t b, int lane)
{   /* imm 0x00 / 0x55 / 0xaa / 0xff: a_l a_l b_l b_l */
    float32x4_t t, r;
    if (lane < 2) FNVNX_ASM2("zip1", t, a, b); else FNVNX_ASM2("zip2", t, a, b);   /* a_k b_k a_k+1 b_k+1 */
    if (lane & 1) FNVNX_ASM2("zip2", r, t, t); else FNVNX_ASM2("zip1", r, t, t);
    return r;
}
static inline __attribute__((always_inline)) float32x4_t fnvnx_shuf2_c4(float32x4_t a, float32x4_t b)
{   /* a0 a1 b0 b3 */
    float32x4_t r;
    __asm__("zip1 %0.2d, %1.2d, %2.2d\n\tmov %0.s[3], %2.s[3]" : "=&w"(r) : "w"(a), "w"(b));
    return r;
}
static inline __attribute__((always_inline)) float32x4_t fnvnx_shuf2_d4(float32x4_t a, float32x4_t b)
{   /* a0 a1 b1 b3 */
    float32x4_t t, r;
    FNVNX_ASM2("zip1", t, a, a);                                     /* a0 a0 a1 a1 */
    FNVNX_ASM2("uzp2", r, t, b);                                     /* a0 a1 b1 b3 */
    return r;
}

/* the shim's original form: what sse2neon's _mm_shuffle_ps expands to under GCC */
#define FNVNX_SHUF_BUILTIN(a, b, imm)                                                                        \
    __builtin_shuffle((float32x4_t)(a), (float32x4_t)(b),                                                    \
                      (int32x4_t){(imm) & 3, ((imm) >> 2) & 3, 4 + (((imm) >> 4) & 3), 4 + (((imm) >> 6) & 3)})

#define FNVNX_SHUF1(x, imm) FNVNX_SHUF_BUILTIN(x, x, imm)
#define FNVNX_SHUF2(a, b, imm)                                                                               \
    ((imm) == 0x00 ? fnvnx_shuf2_bcast(a, b, 0) : (imm) == 0x55 ? fnvnx_shuf2_bcast(a, b, 1)                 \
     : (imm) == 0xaa ? fnvnx_shuf2_bcast(a, b, 2) : (imm) == 0xff ? fnvnx_shuf2_bcast(a, b, 3)               \
     : (imm) == 0xc4 ? fnvnx_shuf2_c4(a, b) : (imm) == 0xd4 ? fnvnx_shuf2_d4(a, b)                           \
     : FNVNX_SHUF_BUILTIN(a, b, imm))

#undef _mm_shuffle_ps
#define _mm_shuffle_ps(a, b, imm)                                                                            \
    __extension__({                                                                                          \
        SSE2NEON_REQUIRE_CONST_RANGE(imm, 0, 255);                                                           \
        float32x4_t _fnvnx_a = vreinterpretq_f32_m128(a), _fnvnx_b = vreinterpretq_f32_m128(b);              \
        vreinterpretq_m128_f32(__builtin_strcmp(#a, #b) == 0 ? FNVNX_SHUF1(_fnvnx_a, (imm))                  \
                                                             : FNVNX_SHUF2(_fnvnx_a, _fnvnx_b, (imm)));      \
    })

#endif
