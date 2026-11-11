/* fnv-nx L6 front: texel conversion of the NXFMT_CONVERT formats (see d3d9_state.h). The kind of conversion is the row's
 * NxConv (front_fmt.c); the per-pixel switch has a case for every NxConv value and no default (-Werror=switch-enum). */
#include "front_int.h"

static uint32_t mip_pixels(uint32_t w, uint32_t h, uint32_t mips, int blocks) {
    uint32_t n = 0;
    for (uint32_t m = 0; m < mips; m++) {
        n += blocks ? ((w + 3) / 4) * ((h + 3) / 4) : w * h;
        w = w > 1 ? w / 2 : 1;
        h = h > 1 ? h / 2 : 1;
    }
    return n;
}

uint32_t nx_d3d9_converted_size(uint32_t d3dfmt, uint32_t w, uint32_t h, uint32_t mips, uint32_t faces) {
    NxFmtRow row = nxfront_fmt_row(d3dfmt);
    if (row.info.format == NxFmt_None || !w || !h) return 0;
    if (!mips) mips = 1;
    if (!faces) faces = 1;
    if (row.conv == NXCONV_ATI1) {   /* blocks in, pixels out (block-padded mips stay exact) */
        uint32_t n = 0;
        for (uint32_t m = 0, mw = w, mh = h; m < mips; m++, mw = mw > 1 ? mw / 2 : 1, mh = mh > 1 ? mh / 2 : 1) n += mw * mh;
        return n * 4 * faces;
    }
    int blocks = (row.info.flags & NXFMT_COMPRESSED) != 0;
    return mip_pixels(w, h, mips, blocks) * row.info.bytes * faces;
}

/* BC4 unorm block, decoded as Mesa's util_format_rgtc: integer division, truncating. */
static void bc4_block(const uint8_t* b, uint8_t out[16]) {
    uint8_t e[8];
    e[0] = b[0]; e[1] = b[1];
    if (e[0] > e[1]) for (int c = 2; c < 8; c++) e[c] = (uint8_t)(((8 - c) * e[0] + (c - 1) * e[1]) / 7);
    else { for (int c = 2; c < 6; c++) e[c] = (uint8_t)(((6 - c) * e[0] + (c - 1) * e[1]) / 5); e[6] = 0; e[7] = 255; }
    uint64_t bits = 0;
    for (int i = 0; i < 6; i++) bits |= (uint64_t)b[2 + i] << (8 * i);
    for (int t = 0; t < 16; t++) out[t] = e[(bits >> (3 * t)) & 7];
}

static uint32_t convert_ati1(uint32_t w, uint32_t h, uint32_t mips, uint32_t faces, const uint8_t* s, uint32_t srcSize,
                             uint8_t* d, uint32_t need) {
    if (srcSize < mip_pixels(w, h, mips, 1) * 8 * faces) return 0;
    for (uint32_t face = 0; face < faces; face++)
        for (uint32_t m = 0, mw = w, mh = h; m < mips; m++, mw = mw > 1 ? mw / 2 : 1, mh = mh > 1 ? mh / 2 : 1) {
            uint32_t bw = (mw + 3) / 4, bh = (mh + 3) / 4;
            for (uint32_t by = 0; by < bh; by++)
                for (uint32_t bx = 0; bx < bw; bx++, s += 8) {
                    uint8_t t[16];
                    bc4_block(s, t);
                    for (uint32_t y = 0; y < 4; y++)
                        for (uint32_t x = 0; x < 4; x++) {
                            uint32_t px = bx * 4 + x, py = by * 4 + y;
                            if (px >= mw || py >= mh) continue;
                            uint8_t* o = d + (py * mw + px) * 4;
                            o[0] = t[y * 4 + x]; o[1] = 0; o[2] = 0; o[3] = 255;
                        }
                }
            d += mw * mh * 4;
        }
    return need;
}

uint32_t nx_d3d9_convert(uint32_t d3dfmt, uint32_t w, uint32_t h, uint32_t mips, uint32_t faces, const void* src,
                         uint32_t srcSize, void* dst, uint32_t dstCap) {
    NxFmtRow row = nxfront_fmt_row(d3dfmt);
    if (!(row.info.flags & NXFMT_CONVERT) || !src || !dst) return 0;
    uint32_t need = nx_d3d9_converted_size(d3dfmt, w, h, mips, faces);
    if (!need || need > dstCap) return 0;
    if (!mips) mips = 1;
    if (!faces) faces = 1;
    const uint8_t* s = (const uint8_t*)src;
    uint8_t* d = (uint8_t*)dst;
    if (row.conv == NXCONV_ATI1) return convert_ati1(w, h, mips, faces, s, srcSize, d, need);
    uint32_t n = mip_pixels(w, h, mips, 0) * faces;
    if (srcSize < n * row.info.srcBytes) return 0;
    for (uint32_t i = 0; i < n; i++) {
        switch (row.conv) {
        case NXCONV_A8: d[0] = 0; d[1] = 0; d[2] = 0; d[3] = s[0]; s += 1; d += 4; break;
        case NXCONV_L8: d[0] = d[1] = d[2] = s[0]; d[3] = 255; s += 1; d += 4; break;
        case NXCONV_A8L8: d[0] = d[1] = d[2] = s[0]; d[3] = s[1]; s += 2; d += 4; break;
        case NXCONV_L16:
            d[0] = d[2] = d[4] = s[0]; d[1] = d[3] = d[5] = s[1]; d[6] = d[7] = 255; s += 2; d += 8; break;
        case NXCONV_A2R10G10B10: {
            uint32_t v = (uint32_t)s[0] | (uint32_t)s[1] << 8 | (uint32_t)s[2] << 16 | (uint32_t)s[3] << 24;
            v = (v & 0xC00FFC00u) | (v >> 20 & 0x3FF) | (v & 0x3FF) << 20;
            d[0] = (uint8_t)v; d[1] = (uint8_t)(v >> 8); d[2] = (uint8_t)(v >> 16); d[3] = (uint8_t)(v >> 24);
            s += 4; d += 4;
            break;
        }
        case NXCONV_ATI1: break;   /* handled above (blocks, not pixels) */
        case NXCONV_NONE: break;   /* excluded by the NXFMT_CONVERT test above */
        }
    }
    return need;
}
