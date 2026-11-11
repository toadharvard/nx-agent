/* fnv-nx L6 front: the rules of the CONVERT formats beyond upload (declared in d3d9_state.h): target write mask, clear value, read-back
 * conversion. Every function switches on the row's NxConv (front_fmt.c): the property of the storage decides, not the format name. */
#include "front_int.h"

void nx_d3d9_rt_rule(uint32_t d3dfmt, uint8_t* mask) {
    *mask = 15;
    switch (nxfront_fmt_row(d3dfmt).conv) {
    case NXCONV_A8: *mask = 8; break;           /* storage (0, 0, 0, A): colour channels stay 0 */
    case NXCONV_L8: *mask = 1; break;           /* storage (L, x, x, x), sampled through the swizzle (R, R, R, 1): only red is a channel */
    case NXCONV_R8G8B8: *mask = 7; break;       /* the x byte is not a channel */
    case NXCONV_NONE: case NXCONV_A8L8: case NXCONV_L16: case NXCONV_ATI1: case NXCONV_L6V5U5: case NXCONV_X8L8V8U8: break;
    }
}

uint32_t nx_d3d9_clear_rgba8(uint32_t d3dfmt, uint32_t c) {
    uint32_t r = c >> 16 & 255, g = c >> 8 & 255, b = c & 255, a = c >> 24 & 255; uint8_t m;
    nx_d3d9_rt_rule(d3dfmt, &m);
    if (!(m & 1)) r = 0;
    if (!(m & 2)) g = 0;
    if (!(m & 4)) b = 0;
    if (!(m & 8)) a = 255;
    return r | g << 8 | b << 16 | a << 24;
}

uint32_t nx_d3d9_unconvert(uint32_t d3dfmt, uint32_t w, uint32_t h, const void* src, uint32_t srcSize, void* dst, uint32_t dstCap) {
    NxFmtRow row = nxfront_fmt_row(d3dfmt); const uint8_t* s = (const uint8_t*)src; uint8_t* d = (uint8_t*)dst;
    uint32_t n = w * h, ob = row.info.srcBytes;
    if (!(row.info.flags & NXFMT_CONVERT) || !src || !dst || !n || srcSize < n * row.info.bytes || dstCap < n * ob) return 0;
    switch (row.conv) {
    case NXCONV_A8: for (uint32_t i = 0; i < n; i++) d[i] = s[i * 4 + 3]; return n;
    case NXCONV_L8: for (uint32_t i = 0; i < n; i++) d[i] = s[i * 4]; return n;
    case NXCONV_R8G8B8: for (uint32_t i = 0; i < n; i++) { d[i * 3] = s[i * 4]; d[i * 3 + 1] = s[i * 4 + 1]; d[i * 3 + 2] = s[i * 4 + 2]; } return n * 3;
    case NXCONV_A8L8: for (uint32_t i = 0; i < n; i++) { d[i * 2] = s[i * 4]; d[i * 2 + 1] = s[i * 4 + 3]; } return n * 2;
    case NXCONV_L16: for (uint32_t i = 0; i < n; i++) { d[i * 2] = s[i * 8]; d[i * 2 + 1] = s[i * 8 + 1]; } return n * 2;
    case NXCONV_L6V5U5: for (uint32_t i = 0; i < n; i++) {   /* the exact inverse of front_conv.c: every code, the most negative one by its side-word flag */
            float f[4]; memcpy(f, s + i * 16, 16); uint32_t side = (uint32_t)f[3];
            int u = (side & 1) ? -16 : nxfront_snorm_code(f[0], 15), v = (side & 2) ? -16 : nxfront_snorm_code(f[1], 15), l = nxfront_snorm_code(f[2], 63);
            uint32_t o = ((uint32_t)u & 31) | ((uint32_t)v & 31) << 5 | ((uint32_t)l & 63) << 10;
            d[i * 2] = (uint8_t)o; d[i * 2 + 1] = (uint8_t)(o >> 8);
        }
        return n * 2;
    case NXCONV_X8L8V8U8: for (uint32_t i = 0; i < n; i++) {
            float f[4]; memcpy(f, s + i * 16, 16); uint32_t side = (uint32_t)f[3];
            d[i * 4] = (side & 1) ? 0x80 : (uint8_t)nxfront_snorm_code(f[0], 127); d[i * 4 + 1] = (side & 2) ? 0x80 : (uint8_t)nxfront_snorm_code(f[1], 127);
            d[i * 4 + 2] = (uint8_t)nxfront_snorm_code(f[2], 255); d[i * 4 + 3] = (uint8_t)(side >> 2);
        }
        return n * 4;
    case NXCONV_NONE: case NXCONV_ATI1: return 0;   /* BC4 is decoded on upload: not invertible, and no format with it is a target or a blit destination */
    }
    return 0;
}
