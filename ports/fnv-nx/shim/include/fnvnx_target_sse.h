/* fnvnx_target_sse.h - the SSE names IFX1 counts as existing (TARGET_NAMES: __m128, _mm_*, ...), included where
 * IFX1 drops a decomp's own `__if_not_exists(_mm_...) { declaration }` because of them. On the target they come
 * from sse2neon, which fnvnx_compat.h gives every unit, so this header adds nothing. It exists so that the S11 gate
 * can say the same (tools/equiv/msvc/fnvnx_target_sse.h: MSVC's own <emmintrin.h>), where the dropped declarations
 * were the unit's only ones (IFX1-SSE). */
#ifndef FNVNX_TARGET_SSE_H
#define FNVNX_TARGET_SSE_H
#endif
