/* host_textconv.h - the pure text conversions of the TEXT_ENTRY service (hostapi.h 36), plain C, no libnx, so a host test includes the
 * real file. UTF-16 code units are uint16_t; UTF-8 is bytes.
 *   te_cut16        the units the keyboard starts with: the first max_len units; a cut through a surrogate pair drops the pair.
 *   te_utf16_to_8   exact UTF-8 of a unit string: a pair is one 4-byte character, a lone surrogate is U+FFFD (EF BF BD).
 *   te_utf8_to_16   exact UTF-8 definition (Unicode 15, table 3-7): a lead byte that does not start a well-formed sequence is U+FFFD and
 *                   consumes that one byte (so one U+FFFD per byte outside a well-formed sequence); characters above U+FFFF are two units.
 * te_utf8_to_16 writes at most one unit per input byte, so a buffer of n units holds n bytes of input. */
#ifndef FNVNX_HOST_TEXTCONV_H
#define FNVNX_HOST_TEXTCONV_H
#include <stddef.h>
#include <stdint.h>

static size_t te_cut16(const uint16_t *u, size_t n, size_t max_len) {
    if (n <= max_len) return n;
    if (max_len && u[max_len - 1] >= 0xD800 && u[max_len - 1] <= 0xDBFF && u[max_len] >= 0xDC00 && u[max_len] <= 0xDFFF) return max_len - 1;
    return max_len;
}
/* out has room for 3 bytes per unit; returns the byte count (no terminator written) */
static size_t te_utf16_to_8(const uint16_t *u, size_t n, unsigned char *out) {
    size_t o = 0;
    for (size_t i = 0; i < n; i++) {
        uint32_t c = u[i];
        if (c >= 0xD800 && c <= 0xDBFF && i + 1 < n && u[i + 1] >= 0xDC00 && u[i + 1] <= 0xDFFF) {
            c = 0x10000 + ((c - 0xD800) << 10) + (u[i + 1] - 0xDC00);
            i++;
        } else if (c >= 0xD800 && c <= 0xDFFF) {
            c = 0xFFFD;
        }
        if (c < 0x80) out[o++] = (unsigned char)c;
        else if (c < 0x800) { out[o++] = 0xC0 | (c >> 6); out[o++] = 0x80 | (c & 63); }
        else if (c < 0x10000) { out[o++] = 0xE0 | (c >> 12); out[o++] = 0x80 | ((c >> 6) & 63); out[o++] = 0x80 | (c & 63); }
        else { out[o++] = 0xF0 | (c >> 18); out[o++] = 0x80 | ((c >> 12) & 63); out[o++] = 0x80 | ((c >> 6) & 63); out[o++] = 0x80 | (c & 63); }
    }
    return o;
}
/* out has room for n units; returns the unit count */
static size_t te_utf8_to_16(const unsigned char *s, size_t n, uint16_t *out) {
    size_t i = 0, o = 0;
    while (i < n) {
        unsigned char b = s[i];
        size_t len = 0;
        unsigned char lo = 0x80, hi = 0xBF;
        uint32_t c = 0;
        if (b < 0x80) { out[o++] = b; i++; continue; }
        if (b >= 0xC2 && b <= 0xDF) { len = 2; c = b & 0x1F; }
        else if (b >= 0xE0 && b <= 0xEF) { len = 3; c = b & 0x0F; if (b == 0xE0) lo = 0xA0; if (b == 0xED) hi = 0x9F; }
        else if (b >= 0xF0 && b <= 0xF4) { len = 4; c = b & 0x07; if (b == 0xF0) lo = 0x90; if (b == 0xF4) hi = 0x8F; }
        int ok = len != 0 && i + len <= n;
        for (size_t k = 1; ok && k < len; k++) {
            unsigned char t = s[i + k];
            if (t < (k == 1 ? lo : 0x80) || t > (k == 1 ? hi : 0xBF)) ok = 0;
            else c = (c << 6) | (t & 63);
        }
        if (!ok) { out[o++] = 0xFFFD; i++; continue; }
        if (c >= 0x10000) { c -= 0x10000; out[o++] = (uint16_t)(0xD800 + (c >> 10)); out[o++] = (uint16_t)(0xDC00 + (c & 0x3FF)); }
        else out[o++] = (uint16_t)c;
        i += len;
    }
    return o;
}
#endif
