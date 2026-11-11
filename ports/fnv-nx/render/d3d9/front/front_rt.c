/* fnv-nx L6 front: the rules of the CONVERT formats beyond upload (declared in d3d9_state.h): target write mask / replication, clear value, read-back
 * conversion, sampler fixup of the biased signed formats. Every function switches on the row's NxConv (front_fmt.c): the property of the storage decides, not the format name. */
#include "front_int.h"

void nx_d3d9_rt_rule(uint32_t d3dfmt, uint8_t* mask, uint8_t* replicate_r) {
    *mask = 15; *replicate_r = 0;
    switch (nxfront_fmt_row(d3dfmt).conv) {
    case NXCONV_A8: *mask = 8; break;           /* storage (0, 0, 0, A): colour channels stay 0 */
    case NXCONV_L8: *mask = 7; *replicate_r = 1; break;   /* storage (L, L, L, 1): alpha is never written */
    case NXCONV_R8G8B8: *mask = 7; break;       /* the x byte is not a channel */
    case NXCONV_NONE: case NXCONV_A8L8: case NXCONV_L16: case NXCONV_ATI1: case NXCONV_L6V5U5: case NXCONV_X8L8V8U8: break;
    }
}

uint32_t nx_d3d9_clear_rgba8(uint32_t d3dfmt, uint32_t c) {
    uint32_t r = c >> 16 & 255, g = c >> 8 & 255, b = c & 255, a = c >> 24 & 255; uint8_t m, rep;
    nx_d3d9_rt_rule(d3dfmt, &m, &rep);
    if (rep) g = b = r;
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
    case NXCONV_L6V5U5:   /* inverse of the biased codes (the clamped code -16 never occurs in storage) */
        for (uint32_t i = 0; i < n; i++) {
            uint32_t u = (uint32_t)(s[i * 4] - 16) & 31, v = (uint32_t)(s[i * 4 + 1] - 16) & 31, l = s[i * 4 + 2] & 63, o = u | v << 5 | l << 10;
            d[i * 2] = (uint8_t)o; d[i * 2 + 1] = (uint8_t)(o >> 8);
        }
        return n * 2;
    case NXCONV_X8L8V8U8: for (uint32_t i = 0; i < n; i++) { d[i * 4] = (uint8_t)(s[i * 4] - 128); d[i * 4 + 1] = (uint8_t)(s[i * 4 + 1] - 128); d[i * 4 + 2] = s[i * 4 + 2]; d[i * 4 + 3] = 0; } return n * 4;
    case NXCONV_NONE: case NXCONV_ATI1: return 0;   /* BC4 is decoded on upload: not invertible, and no format with it is a target or a blit destination */
    }
    return 0;
}

int nx_d3d9_sample_fixup(uint32_t d3dfmt, float scale[4], float offset[4]) {
    NxConv c = nxfront_fmt_row(d3dfmt).conv;
    for (int i = 0; i < 4; i++) { scale[i] = 1.0f; offset[i] = 0.0f; }
    switch (c) {
    case NXCONV_L6V5U5: scale[0] = scale[1] = 255.0f / 15.0f; offset[0] = offset[1] = -16.0f / 15.0f; scale[2] = 255.0f / 63.0f; return 1;
    case NXCONV_X8L8V8U8: scale[0] = scale[1] = 255.0f / 127.0f; offset[0] = offset[1] = -128.0f / 127.0f; return 1;
    case NXCONV_NONE: case NXCONV_A8: case NXCONV_L8: case NXCONV_A8L8: case NXCONV_L16: case NXCONV_ATI1: case NXCONV_R8G8B8: return 0;
    }
    return 0;
}
