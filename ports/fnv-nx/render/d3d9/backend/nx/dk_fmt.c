/* fnv-nx L6 deko3d backend: the 35 NxFormat rows (see dk_fmt.h for the rule and the assumptions). The switch is total
 * over NxFormat (-Werror=switch-enum): a new enumerator is a compile error. A value outside the enum yields the zero row.
 * Rows with BGR10A2 and A4R4G4B4 sample through a swizzle B,G,R,A, so they have no DKFMT_RENDER: a render target has no
 * view swizzle in deko3d, and RGB10A2 / RGBA4 would write the channels crosswise. */
#include "dk_fmt.h"

/* The values of the pinned headers (dk_facts.tsv, sha in the TSV): the build fails when a row's enumerator moves. */
_Static_assert(DkImageFormat_RGBA8_Unorm == 28, "DkImageFormat_RGBA8_Unorm");
_Static_assert(DkImageFormat_RGBA8_Unorm_sRGB == 47, "DkImageFormat_RGBA8_Unorm_sRGB");
_Static_assert(DkImageFormat_BGRA8_Unorm == 117, "DkImageFormat_BGRA8_Unorm");
_Static_assert(DkImageFormat_BGRA8_Unorm_sRGB == 119, "DkImageFormat_BGRA8_Unorm_sRGB");
_Static_assert(DkImageFormat_BGRX8_Unorm == 116, "DkImageFormat_BGRX8_Unorm");
_Static_assert(DkImageFormat_BGRX8_Unorm_sRGB == 118, "DkImageFormat_BGRX8_Unorm_sRGB");
_Static_assert(DkImageFormat_RGBX8_Unorm == 72, "DkImageFormat_RGBX8_Unorm");
_Static_assert(DkImageFormat_RGBX8_Unorm_sRGB == 46, "DkImageFormat_RGBX8_Unorm_sRGB");
_Static_assert(DkImageFormat_RGBA_BC1 == 57, "DkImageFormat_RGBA_BC1");
_Static_assert(DkImageFormat_RGBA_BC1_sRGB == 61, "DkImageFormat_RGBA_BC1_sRGB");
_Static_assert(DkImageFormat_RGBA_BC2 == 58, "DkImageFormat_RGBA_BC2");
_Static_assert(DkImageFormat_RGBA_BC2_sRGB == 62, "DkImageFormat_RGBA_BC2_sRGB");
_Static_assert(DkImageFormat_RGBA_BC3 == 59, "DkImageFormat_RGBA_BC3");
_Static_assert(DkImageFormat_RGBA_BC3_sRGB == 63, "DkImageFormat_RGBA_BC3_sRGB");
_Static_assert(DkImageFormat_R_BC4_Unorm == 64, "DkImageFormat_R_BC4_Unorm");
_Static_assert(DkImageFormat_RG_BC5_Unorm == 66, "DkImageFormat_RG_BC5_Unorm");
_Static_assert(DkImageFormat_R8_Unorm == 1, "DkImageFormat_R8_Unorm");
_Static_assert(DkImageFormat_RG8_Unorm == 13, "DkImageFormat_RG8_Unorm");
_Static_assert(DkImageFormat_BGR565_Unorm == 112, "DkImageFormat_BGR565_Unorm");
_Static_assert(DkImageFormat_BGR5A1_Unorm == 114, "DkImageFormat_BGR5A1_Unorm");
_Static_assert(DkImageFormat_BGR5_Unorm == 113, "DkImageFormat_BGR5_Unorm");
_Static_assert(DkImageFormat_RGBA4_Unorm == 48, "DkImageFormat_RGBA4_Unorm");
_Static_assert(DkImageFormat_RGB10A2_Unorm == 52, "DkImageFormat_RGB10A2_Unorm");
_Static_assert(DkImageFormat_RG16_Unorm == 18, "DkImageFormat_RG16_Unorm");
_Static_assert(DkImageFormat_RGBA16_Unorm == 33, "DkImageFormat_RGBA16_Unorm");
_Static_assert(DkImageFormat_R16_Unorm == 6, "DkImageFormat_R16_Unorm");
_Static_assert(DkImageFormat_RG8_Snorm == 14, "DkImageFormat_RG8_Snorm");
_Static_assert(DkImageFormat_RGBA8_Snorm == 29, "DkImageFormat_RGBA8_Snorm");
_Static_assert(DkImageFormat_RG16_Snorm == 19, "DkImageFormat_RG16_Snorm");
_Static_assert(DkImageFormat_RGBA16_Snorm == 34, "DkImageFormat_RGBA16_Snorm");
_Static_assert(DkImageFormat_R16_Float == 5, "DkImageFormat_R16_Float");
_Static_assert(DkImageFormat_RG16_Float == 17, "DkImageFormat_RG16_Float");
_Static_assert(DkImageFormat_RGBA16_Float == 32, "DkImageFormat_RGBA16_Float");
_Static_assert(DkImageFormat_R32_Float == 10, "DkImageFormat_R32_Float");
_Static_assert(DkImageFormat_RG32_Float == 22, "DkImageFormat_RG32_Float");
_Static_assert(DkImageFormat_RGBA32_Float == 37, "DkImageFormat_RGBA32_Float");
_Static_assert(DkImageFormat_Z16 == 41, "DkImageFormat_Z16");
_Static_assert(DkImageFormat_Z24X8 == 42, "DkImageFormat_Z24X8");
_Static_assert(DkImageFormat_Z24S8 == 44, "DkImageFormat_Z24S8");
_Static_assert(DkImageFormat_ZF32 == 43, "DkImageFormat_ZF32");
_Static_assert(DkImageFormat_ZF32_X24S8 == 45, "DkImageFormat_ZF32_X24S8");
_Static_assert(DkImageSwizzle_Zero == 0 && DkImageSwizzle_One == 1 && DkImageSwizzle_Red == 2 && DkImageSwizzle_Green == 3 &&
               DkImageSwizzle_Blue == 4 && DkImageSwizzle_Alpha == 5, "DkImageSwizzle");
/* dk_fmt_view_swizzle indexes by NxSwizzle: R G B A 0 1 */
_Static_assert(NxSwz_R == 0 && NxSwz_G == 1 && NxSwz_B == 2 && NxSwz_A == 3 && NxSwz_0 == 4 && NxSwz_1 == 5, "NxSwizzle");
_Static_assert(NxFmt_Count == 36, "NxFormat count: add a row for the new format");

#define RGBA DkImageSwizzle_Red, DkImageSwizzle_Green, DkImageSwizzle_Blue, DkImageSwizzle_Alpha
#define BGRA DkImageSwizzle_Blue, DkImageSwizzle_Green, DkImageSwizzle_Red, DkImageSwizzle_Alpha
/* a row: Dk format, sRGB view format, swizzle (4 values), bytes per texel/block, flags */
#define ROW(f, s, sw, b, fl) { DkImageFormat_##f, s, { sw }, b, fl }
#define NS DkImageFormat_None
#define CR (DKFMT_RENDER)

DkFmt dk_fmt(uint32_t nxfmt) {
    static const DkFmt none = { DkImageFormat_None, DkImageFormat_None, { 0, 0, 0, 0 }, 0, 0 };
    switch ((enum NxFormat)nxfmt) {
    case NxFmt_RGBA8: return (DkFmt)ROW(RGBA8_Unorm, DkImageFormat_RGBA8_Unorm_sRGB, RGBA, 4, CR);
    case NxFmt_BGRA8: return (DkFmt)ROW(BGRA8_Unorm, DkImageFormat_BGRA8_Unorm_sRGB, RGBA, 4, CR);
    case NxFmt_BGRX8: return (DkFmt)ROW(BGRX8_Unorm, DkImageFormat_BGRX8_Unorm_sRGB, RGBA, 4, CR | DKFMT_XFILL);
    case NxFmt_RGBX8: return (DkFmt)ROW(RGBX8_Unorm, DkImageFormat_RGBX8_Unorm_sRGB, RGBA, 4, CR | DKFMT_XFILL);
    case NxFmt_R5G6B5: return (DkFmt)ROW(BGR565_Unorm, NS, RGBA, 2, CR);
    case NxFmt_A1R5G5B5: return (DkFmt)ROW(BGR5A1_Unorm, NS, RGBA, 2, CR);
    case NxFmt_X1R5G5B5: return (DkFmt)ROW(BGR5_Unorm, NS, RGBA, 2, CR | DKFMT_XFILL);
    case NxFmt_A4R4G4B4: return (DkFmt)ROW(RGBA4_Unorm, NS, BGRA, 2, 0);
    case NxFmt_RGB10A2: return (DkFmt)ROW(RGB10A2_Unorm, NS, RGBA, 4, CR);
    case NxFmt_BGR10A2: return (DkFmt)ROW(RGB10A2_Unorm, NS, BGRA, 4, 0);
    case NxFmt_R8: return (DkFmt)ROW(R8_Unorm, NS, RGBA, 1, CR);
    case NxFmt_RG8: return (DkFmt)ROW(RG8_Unorm, NS, RGBA, 2, CR);
    case NxFmt_R16: return (DkFmt)ROW(R16_Unorm, NS, RGBA, 2, CR);
    case NxFmt_RG16: return (DkFmt)ROW(RG16_Unorm, NS, RGBA, 4, CR);
    case NxFmt_RGBA16: return (DkFmt)ROW(RGBA16_Unorm, NS, RGBA, 8, CR);
    case NxFmt_RG8S: return (DkFmt)ROW(RG8_Snorm, NS, RGBA, 2, CR);
    case NxFmt_RGBA8S: return (DkFmt)ROW(RGBA8_Snorm, NS, RGBA, 4, CR);
    case NxFmt_RG16S: return (DkFmt)ROW(RG16_Snorm, NS, RGBA, 4, CR);
    case NxFmt_RGBA16S: return (DkFmt)ROW(RGBA16_Snorm, NS, RGBA, 8, CR);
    case NxFmt_R16F: return (DkFmt)ROW(R16_Float, NS, RGBA, 2, CR);
    case NxFmt_RG16F: return (DkFmt)ROW(RG16_Float, NS, RGBA, 4, CR);
    case NxFmt_RGBA16F: return (DkFmt)ROW(RGBA16_Float, NS, RGBA, 8, CR);
    case NxFmt_R32F: return (DkFmt)ROW(R32_Float, NS, RGBA, 4, CR);
    case NxFmt_RG32F: return (DkFmt)ROW(RG32_Float, NS, RGBA, 8, CR);
    case NxFmt_RGBA32F: return (DkFmt)ROW(RGBA32_Float, NS, RGBA, 16, CR);
    case NxFmt_BC1: return (DkFmt)ROW(RGBA_BC1, DkImageFormat_RGBA_BC1_sRGB, RGBA, 8, DKFMT_COMPRESSED);
    case NxFmt_BC2: return (DkFmt)ROW(RGBA_BC2, DkImageFormat_RGBA_BC2_sRGB, RGBA, 16, DKFMT_COMPRESSED);
    case NxFmt_BC3: return (DkFmt)ROW(RGBA_BC3, DkImageFormat_RGBA_BC3_sRGB, RGBA, 16, DKFMT_COMPRESSED);
    case NxFmt_BC4: return (DkFmt)ROW(R_BC4_Unorm, NS, RGBA, 8, DKFMT_COMPRESSED);
    case NxFmt_BC5: return (DkFmt)ROW(RG_BC5_Unorm, NS, RGBA, 16, DKFMT_COMPRESSED);
    case NxFmt_Z16: return (DkFmt)ROW(Z16, NS, RGBA, 2, DKFMT_DEPTH);
    case NxFmt_Z24X8: return (DkFmt)ROW(Z24X8, NS, RGBA, 4, DKFMT_DEPTH | DKFMT_ROT8 | DKFMT_XFILL);
    case NxFmt_Z24S8: return (DkFmt)ROW(Z24S8, NS, RGBA, 4, DKFMT_DEPTH | DKFMT_STENCIL | DKFMT_ROT8);
    case NxFmt_Z32F: return (DkFmt)ROW(ZF32, NS, RGBA, 4, DKFMT_DEPTH);
    case NxFmt_Z32FS8: return (DkFmt)ROW(ZF32_X24S8, NS, RGBA, 8, DKFMT_DEPTH | DKFMT_STENCIL);
    case NxFmt_None:
    case NxFmt_Count: break;
    }
    return none;
}

void dk_fmt_view_swizzle(const DkFmt* f, const uint8_t info_swizzle[4], DkImageSwizzle out[4]) {
    for (int i = 0; i < 4; i++) {
        uint32_t s = info_swizzle[i];
        out[i] = s == NxSwz_0 ? DkImageSwizzle_Zero : s == NxSwz_1 ? DkImageSwizzle_One : (DkImageSwizzle)f->swizzle[s & 3];
    }
}
