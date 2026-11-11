/* fnv-nx L6 GL 4.5 backend v4: NxFormat -> GL. The switches are total over the enums (-Werror=switch): a new
 * enumerator is a compile error here, and a value outside the enum (never sent by the device) yields the zero
 * entry, which image creation reports as a failed create.
 *
 * Native byte layouts (what ImageUpdate writes and image_read returns), all little endian:
 *   colour formats: as nx/state.h NxFormat; BCn: 4x4 blocks of 8 (BC1, BC4) or 16 bytes;
 *   Z16 u16; Z32F f32 (little-endian bits); Z24S8 and Z24X8 one u32 = depth24 << 8 | stencil-or-x (GL_UNSIGNED_INT_24_8);
 *   Z32FS8 eight bytes: f32 depth, then u32 with the stencil in its low byte (GL_FLOAT_32_UNSIGNED_INT_24_8_REV).
 * Z24X8 is stored as GL DEPTH24_STENCIL8 so that its x byte round-trips exactly (an X format whose GL storage
 * has no x channel, BGRX8 / RGBX8 on GL_RGB8, reads x back as 0xFF: see the report). */
#include "gl4.h"

#ifndef GL_COMPRESSED_RGBA_S3TC_DXT1_EXT
#define GL_COMPRESSED_RGBA_S3TC_DXT1_EXT 0x83F1
#define GL_COMPRESSED_RGBA_S3TC_DXT3_EXT 0x83F2
#define GL_COMPRESSED_RGBA_S3TC_DXT5_EXT 0x83F3
#endif
#ifndef GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT1_EXT
#define GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT1_EXT 0x8C4D
#define GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT3_EXT 0x8C4E
#define GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT5_EXT 0x8C4F
#endif

#define C(i, fo, ty, s, b) { i, fo, ty, s, b, 0, 0, 0, 0 }
#define BC(i, s, b) { i, 0, 0, s, b, 1, 0, 0, 0 }
#define Z(i, fo, ty, b, st, ds) { i, fo, ty, 0, b, 0, 1, st, ds }

Gl4Fmt gl4_fmt(uint32_t nxfmt) {
    static const Gl4Fmt none = {0, 0, 0, 0, 0, 0, 0, 0, 0};
    switch ((enum NxFormat)nxfmt) {
    case NxFmt_RGBA8: return (Gl4Fmt)C(GL_RGBA8, GL_RGBA, GL_UNSIGNED_BYTE, GL_SRGB8_ALPHA8, 4);
    case NxFmt_BGRA8: return (Gl4Fmt)C(GL_RGBA8, GL_BGRA, GL_UNSIGNED_BYTE, GL_SRGB8_ALPHA8, 4);
    case NxFmt_BGRX8: return (Gl4Fmt)C(GL_RGB8, GL_BGRA, GL_UNSIGNED_BYTE, GL_SRGB8, 4);
    case NxFmt_RGBX8: return (Gl4Fmt)C(GL_RGB8, GL_RGBA, GL_UNSIGNED_BYTE, GL_SRGB8, 4);
    case NxFmt_R5G6B5: return (Gl4Fmt)C(GL_RGB565, GL_RGB, GL_UNSIGNED_SHORT_5_6_5, 0, 2);
    case NxFmt_A1R5G5B5: return (Gl4Fmt)C(GL_RGB5_A1, GL_BGRA, GL_UNSIGNED_SHORT_1_5_5_5_REV, 0, 2);
    case NxFmt_X1R5G5B5: return (Gl4Fmt)C(GL_RGB5_A1, GL_BGRA, GL_UNSIGNED_SHORT_1_5_5_5_REV, 0, 2);
    case NxFmt_A4R4G4B4: return (Gl4Fmt)C(GL_RGBA4, GL_BGRA, GL_UNSIGNED_SHORT_4_4_4_4_REV, 0, 2);
    case NxFmt_RGB10A2: return (Gl4Fmt)C(GL_RGB10_A2, GL_RGBA, GL_UNSIGNED_INT_2_10_10_10_REV, 0, 4);
    case NxFmt_BGR10A2: return (Gl4Fmt)C(GL_RGB10_A2, GL_BGRA, GL_UNSIGNED_INT_2_10_10_10_REV, 0, 4);
    case NxFmt_R8: return (Gl4Fmt)C(GL_R8, GL_RED, GL_UNSIGNED_BYTE, 0, 1);
    case NxFmt_RG8: return (Gl4Fmt)C(GL_RG8, GL_RG, GL_UNSIGNED_BYTE, 0, 2);
    case NxFmt_R16: return (Gl4Fmt)C(GL_R16, GL_RED, GL_UNSIGNED_SHORT, 0, 2);
    case NxFmt_RG16: return (Gl4Fmt)C(GL_RG16, GL_RG, GL_UNSIGNED_SHORT, 0, 4);
    case NxFmt_RGBA16: return (Gl4Fmt)C(GL_RGBA16, GL_RGBA, GL_UNSIGNED_SHORT, 0, 8);
    case NxFmt_RG8S: return (Gl4Fmt)C(GL_RG8_SNORM, GL_RG, GL_BYTE, 0, 2);
    case NxFmt_RGBA8S: return (Gl4Fmt)C(GL_RGBA8_SNORM, GL_RGBA, GL_BYTE, 0, 4);
    case NxFmt_RG16S: return (Gl4Fmt)C(GL_RG16_SNORM, GL_RG, GL_SHORT, 0, 4);
    case NxFmt_RGBA16S: return (Gl4Fmt)C(GL_RGBA16_SNORM, GL_RGBA, GL_SHORT, 0, 8);
    case NxFmt_R16F: return (Gl4Fmt)C(GL_R16F, GL_RED, GL_HALF_FLOAT, 0, 2);
    case NxFmt_RG16F: return (Gl4Fmt)C(GL_RG16F, GL_RG, GL_HALF_FLOAT, 0, 4);
    case NxFmt_RGBA16F: return (Gl4Fmt)C(GL_RGBA16F, GL_RGBA, GL_HALF_FLOAT, 0, 8);
    case NxFmt_R32F: return (Gl4Fmt)C(GL_R32F, GL_RED, GL_FLOAT, 0, 4);
    case NxFmt_RG32F: return (Gl4Fmt)C(GL_RG32F, GL_RG, GL_FLOAT, 0, 8);
    case NxFmt_RGBA32F: return (Gl4Fmt)C(GL_RGBA32F, GL_RGBA, GL_FLOAT, 0, 16);
    case NxFmt_BC1: return (Gl4Fmt)BC(GL_COMPRESSED_RGBA_S3TC_DXT1_EXT, GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT1_EXT, 8);
    case NxFmt_BC2: return (Gl4Fmt)BC(GL_COMPRESSED_RGBA_S3TC_DXT3_EXT, GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT3_EXT, 16);
    case NxFmt_BC3: return (Gl4Fmt)BC(GL_COMPRESSED_RGBA_S3TC_DXT5_EXT, GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT5_EXT, 16);
    case NxFmt_BC4: return (Gl4Fmt)BC(GL_COMPRESSED_RED_RGTC1, 0, 8);
    case NxFmt_BC5: return (Gl4Fmt)BC(GL_COMPRESSED_RG_RGTC2, 0, 16);
    case NxFmt_Z16: return (Gl4Fmt)Z(GL_DEPTH_COMPONENT16, GL_DEPTH_COMPONENT, GL_UNSIGNED_SHORT, 2, 0, 0);
    case NxFmt_Z24X8: return (Gl4Fmt)Z(GL_DEPTH24_STENCIL8, GL_DEPTH_STENCIL, GL_UNSIGNED_INT_24_8, 4, 0, 1);
    case NxFmt_Z24S8: return (Gl4Fmt)Z(GL_DEPTH24_STENCIL8, GL_DEPTH_STENCIL, GL_UNSIGNED_INT_24_8, 4, 1, 1);
    case NxFmt_Z32F: return (Gl4Fmt)Z(GL_DEPTH_COMPONENT32F, GL_DEPTH_COMPONENT, GL_FLOAT, 4, 0, 0);
    case NxFmt_Z32FS8:
        return (Gl4Fmt)Z(GL_DEPTH32F_STENCIL8, GL_DEPTH_STENCIL, GL_FLOAT_32_UNSIGNED_INT_24_8_REV, 8, 1, 1);
    case NxFmt_None:
    case NxFmt_Count: break;
    }
    return none;
}

GLint gl4_swz(uint32_t nibble) {
    switch ((enum NxSwizzle)nibble) {
    case NxSwz_R: return GL_RED;
    case NxSwz_G: return GL_GREEN;
    case NxSwz_B: return GL_BLUE;
    case NxSwz_A: return GL_ALPHA;
    case NxSwz_0: return GL_ZERO;
    case NxSwz_1: return GL_ONE;
    }
    return GL_ZERO;   /* nibble 6..15: never sent (the device builds swizzles from NxSwizzle) */
}

size_t gl4_row_bytes(const Gl4Fmt* f, uint32_t w) { return (f->compressed ? (size_t)(w + 3) / 4 : w) * f->bpp; }
size_t gl4_rows(const Gl4Fmt* f, uint32_t h) { return f->compressed ? (size_t)(h + 3) / 4 : h; }
