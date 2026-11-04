// fnv-nx native renderer: a dependency-free PNG writer for tools (RGBA8, stored deflate blocks). Host only.
#ifndef FNVNX_NX_PNG_H
#define FNVNX_NX_PNG_H
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace nxpng {

inline uint32_t Crc(const uint8_t* p, size_t n, uint32_t c = 0) {
    static uint32_t t[256];
    if (!t[1]) for (uint32_t i = 0; i < 256; i++) { uint32_t v = i; for (int k = 0; k < 8; k++) v = v & 1 ? 0xEDB88320u ^ (v >> 1) : v >> 1; t[i] = v; }
    c = ~c;
    for (size_t i = 0; i < n; i++) c = t[(c ^ p[i]) & 255] ^ (c >> 8);
    return ~c;
}
inline void Be32(uint8_t* p, uint32_t v) { p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
inline void Chunk(FILE* f, const char* type, const uint8_t* d, size_t n) {
    uint8_t h[8]; Be32(h, (uint32_t)n); memcpy(h + 4, type, 4);
    fwrite(h, 1, 8, f); if (n) fwrite(d, 1, n, f);
    uint32_t c = Crc((const uint8_t*)type, 4); c = Crc(d, n, c);
    uint8_t cb[4]; Be32(cb, c); fwrite(cb, 1, 4, f);
}
// rgba: w*h*4, row 0 = top. Returns false on an I/O error.
inline bool Write(const char* path, const uint8_t* rgba, uint32_t w, uint32_t h) {
    FILE* f = fopen(path, "wb");
    if (!f) return false;
    static const uint8_t sig[8] = {0x89, 'P', 'N', 'G', 13, 10, 26, 10};
    fwrite(sig, 1, 8, f);
    uint8_t ihdr[13]; Be32(ihdr, w); Be32(ihdr + 4, h); ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = ihdr[11] = ihdr[12] = 0;
    Chunk(f, "IHDR", ihdr, 13);
    size_t raw = (size_t)(w * 4 + 1) * h, blocks = raw / 65535 + 1;
    uint8_t* z = (uint8_t*)malloc(2 + raw + blocks * 5 + 4);
    uint8_t* r = (uint8_t*)malloc(raw);
    for (uint32_t y = 0; y < h; y++) { r[y * (w * 4 + 1)] = 0; memcpy(r + y * (w * 4 + 1) + 1, rgba + (size_t)y * w * 4, w * 4); }
    size_t n = 0; z[n++] = 0x78; z[n++] = 0x01;
    uint32_t a = 1, b = 0;
    for (size_t i = 0; i < raw; i++) { a = (a + r[i]) % 65521; b = (b + a) % 65521; }
    for (size_t off = 0; off < raw || off == 0;) {
        size_t len = raw - off > 65535 ? 65535 : raw - off;
        z[n++] = off + len >= raw ? 1 : 0;
        z[n++] = len & 255; z[n++] = len >> 8; z[n++] = ~len & 255; z[n++] = (~len >> 8) & 255;
        memcpy(z + n, r + off, len); n += len; off += len;
        if (!raw) break;
    }
    Be32(z + n, (b << 16) | a); n += 4;
    Chunk(f, "IDAT", z, n);
    Chunk(f, "IEND", nullptr, 0);
    free(z); free(r);
    return fclose(f) == 0;
}

} // namespace nxpng
#endif
