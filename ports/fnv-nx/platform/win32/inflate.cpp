/* inflate.cpp - zlib/DEFLATE decompressor (RFC 1950/1951).
 *
 * Rule. The stream is accepted iff it is a well-formed zlib stream: CMF/FLG header (CM 8, window <= 32 KiB, FCHECK, no
 * FDICT), blocks of type stored (LEN == ~NLEN), fixed and dynamic Huffman, ending at BFINAL, then the big-endian Adler-32
 * of the output and nothing else. Every Huffman code set is validated by its Kraft sum: over-subscribed sets are rejected,
 * incomplete ones too, except the single code of length 1 (RFC 1951 3.2.7: one distance code) and an empty distance set;
 * the code-length code must be complete and the literal/length code must contain end-of-block. A match must reference
 * output already produced and inside the header's window; symbols 286/287 and distance codes 30/31 are errors. The input
 * is only read through a bounds-checked bit reader; the output buffer doubles up to `max_out` (256 MiB for the public
 * entry), a larger output is FNVNX_INF_TOO_BIG. Proof of totality: every loop consumes input bits or produces output. */
#include "inflate.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

namespace {
struct Huff { uint16_t count[16]; uint16_t sym[288]; };
struct St {
    const uint8_t *s; size_t n, p; uint32_t bits; int nb;
    uint8_t *o; size_t on, cap, max; int err;
};

bool need(St &z, int k)   /* k <= 16 bits into z.bits; false at end of input */
{
    while (z.nb < k) {
        if (z.p >= z.n) return false;
        z.bits |= (uint32_t)z.s[z.p++] << z.nb; z.nb += 8;
    }
    return true;
}
bool getbits(St &z, int k, unsigned *v)
{
    if (!k) { *v = 0; return true; }
    if (!need(z, k)) { z.err = FNVNX_INF_TRUNCATED; return false; }
    *v = z.bits & ((1u << k) - 1); z.bits >>= k; z.nb -= k;
    return true;
}
bool build(Huff *h, const uint8_t *len, int n)
{
    int total = 0, left = 1;
    memset(h, 0, sizeof *h);
    for (int i = 0; i < n; i++) h->count[len[i]]++, total += len[i] != 0;
    for (int l = 1; l < 16; l++) { left = 2 * left - h->count[l]; if (left < 0) return false; }   /* over-subscribed */
    if (left > 0 && total != 0 && !(total == 1 && h->count[1] == 1)) return false;                /* incomplete */
    uint16_t off[16]; off[1] = 0;
    for (int l = 1; l < 15; l++) off[l + 1] = off[l] + h->count[l];
    for (int i = 0; i < n; i++) if (len[i]) h->sym[off[len[i]]++] = (uint16_t)i;
    return true;
}
int decode(St &z, const Huff *h)   /* symbol, or -1 with z.err */
{
    int code = 0, first = 0, idx = 0;
    for (int l = 1; l < 16; l++) {
        unsigned b;
        if (!getbits(z, 1, &b)) return -1;
        code |= (int)b;
        int c = h->count[l];
        if (code - c < first) return h->sym[idx + (code - first)];
        idx += c; first = (first + c) << 1; code <<= 1;
    }
    z.err = FNVNX_INF_BAD_CODE;
    return -1;
}
bool put(St &z, const uint8_t *d, size_t k)   /* append k bytes (d == NULL: copy from distance k handled by caller) */
{
    if (k > z.max - z.on) { z.err = FNVNX_INF_TOO_BIG; return false; }
    if (z.on + k > z.cap) {
        size_t nc = z.cap ? z.cap : 4096;
        while (nc < z.on + k) nc *= 2;
        if (nc > z.max) nc = z.max;
        uint8_t *t = (uint8_t *)realloc(z.o, nc);
        if (!t) { z.err = FNVNX_INF_TOO_BIG; return false; }
        z.o = t; z.cap = nc;
    }
    if (d) memcpy(z.o + z.on, d, k);
    return true;
}
const uint16_t LB[29] = { 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258 };
const uint8_t LE[29] = { 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0 };
const uint16_t DB[30] = { 1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577 };
const uint8_t DE[30] = { 0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13 };

bool codes(St &z, const Huff *lt, const Huff *dt, size_t window)
{
    for (;;) {
        int s = decode(z, lt);
        if (s < 0) return false;
        if (s < 256) { if (!put(z, NULL, 1)) return false; z.o[z.on++] = (uint8_t)s; continue; }
        if (s == 256) return true;
        if (s > 285) { z.err = FNVNX_INF_BAD_CODE; return false; }
        unsigned x, len, dist;
        if (!getbits(z, LE[s - 257], &x)) return false;
        len = LB[s - 257] + x;
        int d = decode(z, dt);
        if (d < 0) return false;
        if (d > 29) { z.err = FNVNX_INF_BAD_CODE; return false; }
        if (!getbits(z, DE[d], &x)) return false;
        dist = DB[d] + x;
        if (dist > z.on || dist > window) { z.err = FNVNX_INF_DISTANCE; return false; }
        if (!put(z, NULL, len)) return false;
        for (unsigned i = 0; i < len; i++, z.on++) z.o[z.on] = z.o[z.on - dist];
    }
}
bool dynamic(St &z, size_t window)
{
    static const uint8_t ord[19] = { 16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15 };
    unsigned hlit, hdist, hclen, v;
    uint8_t len[320], cl[19] = { 0 };
    Huff lt, dt, ct;
    if (!getbits(z, 5, &hlit) || !getbits(z, 5, &hdist) || !getbits(z, 4, &hclen)) return false;
    hlit += 257; hdist += 1; hclen += 4;
    if (hlit > 286 || hdist > 30) { z.err = FNVNX_INF_BAD_CODE; return false; }
    for (unsigned i = 0; i < hclen; i++) { if (!getbits(z, 3, &v)) return false; cl[ord[i]] = (uint8_t)v; }
    if (!build(&ct, cl, 19) || ct.count[0] == 19) { z.err = FNVNX_INF_BAD_CODE; return false; }
    {   /* the code-length code must be complete */
        int left = 1; for (int l = 1; l < 16; l++) left = 2 * left - ct.count[l];
        if (left) { z.err = FNVNX_INF_BAD_CODE; return false; }
    }
    for (unsigned i = 0; i < hlit + hdist;) {
        int s = decode(z, &ct);
        if (s < 0) return false;
        if (s < 16) { len[i++] = (uint8_t)s; continue; }
        unsigned rep, val = 0;
        if (s == 16) { if (!i) { z.err = FNVNX_INF_BAD_CODE; return false; } val = len[i - 1]; if (!getbits(z, 2, &rep)) return false; rep += 3; }
        else if (s == 17) { if (!getbits(z, 3, &rep)) return false; rep += 3; }
        else { if (!getbits(z, 7, &rep)) return false; rep += 11; }
        if (i + rep > hlit + hdist) { z.err = FNVNX_INF_BAD_CODE; return false; }
        while (rep--) len[i++] = (uint8_t)val;
    }
    if (!len[256] || !build(&lt, len, (int)hlit) || !build(&dt, len + hlit, (int)hdist)) { z.err = FNVNX_INF_BAD_CODE; return false; }
    return codes(z, &lt, &dt, window);
}
uint32_t adler32(const uint8_t *p, size_t n)
{
    uint32_t a = 1, b = 0;
    while (n) {
        size_t k = n < 5552 ? n : 5552; n -= k;
        while (k--) { a += *p++; b += a; }
        a %= 65521; b %= 65521;
    }
    return b << 16 | a;
}
}   // namespace

extern "C" int fnvnx_inflate_zlib_max(const unsigned char *src, size_t n, unsigned char **out, size_t *out_n, size_t max_out)
{
    St z = { src, n, 2, 0, 0, NULL, 0, 0, max_out, 0 };
    *out = NULL; *out_n = 0;
    if (n < 2 || (src[0] & 15) != 8 || (src[0] >> 4) > 7 || (src[0] * 256 + src[1]) % 31 || (src[1] & 0x20)) return FNVNX_INF_HEADER;
    size_t window = (size_t)1 << ((src[0] >> 4) + 8);
    unsigned fin = 0, type;
    Huff fl, fd;
    uint8_t fx[288 + 32];
    for (int i = 0; i < 288; i++) fx[i] = i < 144 ? 8 : i < 256 ? 9 : i < 280 ? 7 : 8;
    memset(fx + 288, 5, 32);
    build(&fl, fx, 288); build(&fd, fx + 288, 32);   /* both complete; symbols 286/287 and distance codes 30/31 fail in codes() */
    while (!fin) {
        bool good = getbits(z, 1, &fin) && getbits(z, 2, &type);
        if (good && type == 0) {
            z.bits = 0; z.nb = 0;   /* discard to the byte boundary (need() never buffers a whole byte it did not consume) */
            if (z.p + 4 > z.n) { z.err = FNVNX_INF_TRUNCATED; good = false; }
            else {
                unsigned len = z.s[z.p] | z.s[z.p + 1] << 8, nl = z.s[z.p + 2] | z.s[z.p + 3] << 8;
                z.p += 4;
                if ((len ^ 0xFFFF) != nl) { z.err = FNVNX_INF_BAD_CODE; good = false; }
                else if (len > z.n - z.p) { z.err = FNVNX_INF_TRUNCATED; good = false; }
                else if ((good = put(z, z.s + z.p, len))) { z.on += len; z.p += len; }
            }
        } else if (good && type == 1) good = codes(z, &fl, &fd, window);
        else if (good && type == 2) good = dynamic(z, window);
        else if (good) { z.err = FNVNX_INF_BAD_CODE; good = false; }
        if (!good) { free(z.o); return z.err; }
    }
    z.bits = 0; z.nb = 0;
    if (z.n - z.p < 4) { free(z.o); return FNVNX_INF_TRUNCATED; }
    uint32_t want = (uint32_t)z.s[z.p] << 24 | z.s[z.p + 1] << 16 | z.s[z.p + 2] << 8 | z.s[z.p + 3];
    if (z.n - z.p > 4) { free(z.o); return FNVNX_INF_TRAILING; }
    if (want != adler32(z.o, z.on)) { free(z.o); return FNVNX_INF_ADLER; }
    if (!z.o) z.o = (uint8_t *)malloc(1);
    *out = z.o; *out_n = z.on;
    return 0;
}
extern "C" int fnvnx_inflate_zlib(const unsigned char *src, size_t n, unsigned char **out, size_t *out_n)
{
    return fnvnx_inflate_zlib_max(src, n, out, out_n, (size_t)256 << 20);
}
