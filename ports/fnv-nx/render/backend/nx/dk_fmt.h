/* fnv-nx L6 deko3d backend: NxFormat -> DkImageFormat. Host side (LP64, links deko3d.h; the guest never sees it).
 *
 * Rule: a row maps an NxFormat (nx/state.h: a real memory layout) to a DkImageFormat only when deko3d has a format with
 * the same bit layout in memory, after an optional view swizzle over the format's output channels (DkImageView.swizzle,
 * applied on sampling only). Each row's DkImageFormat value is a _Static_assert in dk_fmt.c (values of dk_facts.tsv, the
 * compiler's own evaluation of the pinned headers) and the host test tests/l6/dkfmt/test_dkfmt.py checks bits, bytes per
 * block, block size, depth/stencil and sRGB against dk_facts.tsv (the pinned library's format_traits.inc) and against the
 * GL backend's table (gl4_fmt.c).
 *
 * Where deko3d has no equal layout, the conversion class of the GL backend applies (a transfer-time conversion of the
 * guest data), recorded as a flag: DKFMT_ROT8. Nothing else is invented.
 *
 * Assumptions (stated, not proven here):
 *   A1 the swizzle of a DkImageView applies to the format's output channels (format_traits.inc swizzle already folded in);
 *   A2 Z32FS8 = ZF32_X24S8: f32 depth in the first word, stencil in the low byte of the second (the Maxwell Z32_S8_X24
 *      layout, same as the GL_FLOAT_32_UNSIGNED_INT_24_8_REV layout of gl4_fmt.c). */
#ifndef FNVNX_DK_FMT_H
#define FNVNX_DK_FMT_H
#include <stdint.h>
#include <deko3d.h>
#include "state.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DKFMT_COMPRESSED 0x01   /* 4x4 blocks; bpb is per block */
#define DKFMT_DEPTH      0x02
#define DKFMT_STENCIL    0x04   /* the NxFormat has stencil bits */
#define DKFMT_ROT8       0x08   /* Nx word = rotl32(Dk word, 8): Nx depth24<<8|s8, Dk s8<<24|depth24 (upload and read-back convert) */
#define DKFMT_XFILL      0x10   /* the NxFormat has an x channel that deko3d does not store as data (render writes a constant) */
#define DKFMT_RENDER     0x20   /* usable as a colour render target: deko3d renders it and the view swizzle is the identity */

typedef struct {
    DkImageFormat format;      /* DkImageFormat_None: not an NxFormat */
    DkImageFormat srgb;        /* the sRGB view's DkImageFormat, DkImageFormat_None when the NxFormat has none */
    uint8_t swizzle[4];        /* DkImageSwizzle for the sampled r,g,b,a over the Dk format's output channels */
    uint8_t bpb;               /* bytes per texel, per 4x4 block when compressed */
    uint8_t flags;             /* DKFMT_* */
} DkFmt;

DkFmt dk_fmt(uint32_t nxfmt);
/* The view swizzle of an NxFormatInfo: out[i] = the row swizzle selected by info->swizzle[i] (0 / 1 stay constants). */
void dk_fmt_view_swizzle(const DkFmt* f, const uint8_t info_swizzle[4], DkImageSwizzle out[4]);

#ifdef __cplusplus
}
#endif
#endif
