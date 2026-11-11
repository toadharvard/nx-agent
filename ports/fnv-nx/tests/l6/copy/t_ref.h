// CPU reference of the colour formats used by parts b / c: decode a texel (D3D byte layout) to four doubles, encode four doubles to a texel (round to nearest, clamp).
#pragma once
#include <math.h>
#include <stdint.h>
#include <string.h>
struct RF { uint32_t fmt; const char* name; uint32_t bytes; double tol[4]; uint32_t mask; };   // mask: bits of the texel that carry data (X channels excluded)
static inline double h2d(uint16_t h) { int e = h >> 10 & 31, m = h & 1023; double v = e == 0 ? ldexp(m, -24) : e == 31 ? (m ? NAN : INFINITY) : ldexp(1024 + m, e - 25); return h >> 15 ? -v : v; }
static inline uint16_t d2h(double v) {   // round to nearest even
    if (v != v) return 0x7e00; uint16_t s = v < 0 || (v == 0 && signbit(v)) ? 0x8000 : 0; v = fabs(v); if (v >= 65520.0) return s | 0x7c00;
    if (v < ldexp(1, -14)) return s | (uint16_t)nearbyint(ldexp(v, 24));
    int e; double m = frexp(v, &e); uint32_t q = (uint32_t)nearbyint(ldexp(m, 11)); if (q == 2048) { q = 1024; e++; } return s | (uint16_t)(((e + 14) << 10) | (q & 1023));
}
static inline double un(uint32_t v, int bits) { return (double)v / (double)((1u << bits) - 1); }
static inline double sn(int32_t v, int bits) { double m = (double)((1 << (bits - 1)) - 1); double r = v / m; return r < -1 ? -1 : r; }
static inline uint32_t pun(double x, int bits) { x = x < 0 ? 0 : x > 1 ? 1 : x; return (uint32_t)nearbyint(x * (double)((1u << bits) - 1)); }
static inline int32_t psn(double x, int bits) { x = x < -1 ? -1 : x > 1 ? 1 : x; return (int32_t)nearbyint(x * (double)((1 << (bits - 1)) - 1)); }
static inline uint32_t rd(const uint8_t* p, int n) { uint32_t v = 0; for (int i = 0; i < n; i++) v |= (uint32_t)p[i] << (8 * i); return v; }
static inline void wr(uint8_t* p, uint32_t v, int n) { for (int i = 0; i < n; i++) p[i] = (uint8_t)(v >> (8 * i)); }
static const RF RFS[] = {
    {21, "A8R8G8B8", 4, {1, 1, 1, 1}, ~0u}, {22, "X8R8G8B8", 4, {1, 1, 1, 0}, 0x00FFFFFF}, {32, "A8B8G8R8", 4, {1, 1, 1, 1}, ~0u}, {33, "X8B8G8R8", 4, {1, 1, 1, 0}, 0x00FFFFFF},
    {23, "R5G6B5", 2, {1, 1, 1, 0}, ~0u}, {24, "X1R5G5B5", 2, {1, 1, 1, 0}, 0x7FFF}, {25, "A1R5G5B5", 2, {1, 1, 1, 1}, ~0u}, {26, "A4R4G4B4", 2, {1, 1, 1, 1}, ~0u},
    {31, "A2B10G10R10", 4, {1, 1, 1, 1}, ~0u}, {35, "A2R10G10B10", 4, {1, 1, 1, 1}, ~0u}, {34, "G16R16", 4, {1, 1, 0, 0}, ~0u}, {36, "A16B16G16R16", 8, {1, 1, 1, 1}, ~0u},
    {28, "A8", 1, {0, 0, 0, 1}, ~0u}, {50, "L8", 1, {1, 1, 1, 0}, ~0u}, {20, "R8G8B8", 3, {1, 1, 1, 0}, ~0u}, {60, "V8U8", 2, {1, 1, 0, 0}, ~0u}, {63, "Q8W8V8U8", 4, {1, 1, 1, 1}, ~0u},
    {64, "V16U16", 4, {1, 1, 0, 0}, ~0u}, {110, "Q16W16V16U16", 8, {1, 1, 1, 1}, ~0u}, {111, "R16F", 2, {1, 0, 0, 0}, ~0u}, {112, "G16R16F", 4, {1, 1, 0, 0}, ~0u},
    {113, "A16B16G16R16F", 8, {1, 1, 1, 1}, ~0u}, {114, "R32F", 4, {1, 0, 0, 0}, ~0u}, {115, "G32R32F", 8, {1, 1, 0, 0}, ~0u}, {116, "A32B32G32R32F", 16, {1, 1, 1, 1}, ~0u},
    {101, "INDEX16", 2, {1, 0, 0, 0}, ~0u}, {102, "INDEX32", 4, {1, 0, 0, 0}, ~0u},
};
static const int NRF = sizeof RFS / sizeof RFS[0];
static inline const RF* rf_of(uint32_t f) { for (int i = 0; i < NRF; i++) if (RFS[i].fmt == f) return &RFS[i]; return nullptr; }
static inline void decode(uint32_t f, const uint8_t* p, double o[4]) {
    o[0] = o[1] = o[2] = 0; o[3] = 1; const RF* rf = rf_of(f); uint32_t v = rd(p, rf->bytes > 4 ? 4 : (int)rf->bytes);
    switch (f) {
    case 21: o[0] = un(v >> 16 & 255, 8); o[1] = un(v >> 8 & 255, 8); o[2] = un(v & 255, 8); o[3] = un(v >> 24, 8); break;
    case 22: o[0] = un(v >> 16 & 255, 8); o[1] = un(v >> 8 & 255, 8); o[2] = un(v & 255, 8); break;
    case 32: o[0] = un(v & 255, 8); o[1] = un(v >> 8 & 255, 8); o[2] = un(v >> 16 & 255, 8); o[3] = un(v >> 24, 8); break;
    case 33: o[0] = un(v & 255, 8); o[1] = un(v >> 8 & 255, 8); o[2] = un(v >> 16 & 255, 8); break;
    case 23: o[0] = un(v >> 11, 5); o[1] = un(v >> 5 & 63, 6); o[2] = un(v & 31, 5); break;
    case 24: o[0] = un(v >> 10 & 31, 5); o[1] = un(v >> 5 & 31, 5); o[2] = un(v & 31, 5); break;
    case 25: o[0] = un(v >> 10 & 31, 5); o[1] = un(v >> 5 & 31, 5); o[2] = un(v & 31, 5); o[3] = v >> 15; break;
    case 26: o[0] = un(v >> 8 & 15, 4); o[1] = un(v >> 4 & 15, 4); o[2] = un(v & 15, 4); o[3] = un(v >> 12, 4); break;
    case 31: o[0] = un(v & 1023, 10); o[1] = un(v >> 10 & 1023, 10); o[2] = un(v >> 20 & 1023, 10); o[3] = un(v >> 30, 2); break;
    case 35: o[2] = un(v & 1023, 10); o[1] = un(v >> 10 & 1023, 10); o[0] = un(v >> 20 & 1023, 10); o[3] = un(v >> 30, 2); break;
    case 34: o[0] = un(v & 65535, 16); o[1] = un(v >> 16, 16); break;
    case 36: o[0] = un(rd(p, 2), 16); o[1] = un(rd(p + 2, 2), 16); o[2] = un(rd(p + 4, 2), 16); o[3] = un(rd(p + 6, 2), 16); break;
    case 28: o[3] = un(v, 8); break;
    case 50: o[0] = o[1] = o[2] = un(v, 8); break;
    case 20: o[0] = un(v >> 16 & 255, 8); o[1] = un(v >> 8 & 255, 8); o[2] = un(v & 255, 8); break;
    case 60: o[0] = sn((int8_t)(v & 255), 8); o[1] = sn((int8_t)(v >> 8), 8); break;
    case 63: o[0] = sn((int8_t)(v & 255), 8); o[1] = sn((int8_t)(v >> 8 & 255), 8); o[2] = sn((int8_t)(v >> 16 & 255), 8); o[3] = sn((int8_t)(v >> 24), 8); break;
    case 64: o[0] = sn((int16_t)(v & 65535), 16); o[1] = sn((int16_t)(v >> 16), 16); break;
    case 110: for (int i = 0; i < 4; i++) o[i] = sn((int16_t)rd(p + 2 * i, 2), 16); break;
    case 111: o[0] = h2d((uint16_t)v); break;
    case 112: o[0] = h2d((uint16_t)v); o[1] = h2d((uint16_t)(v >> 16)); break;
    case 113: for (int i = 0; i < 4; i++) o[i] = h2d((uint16_t)rd(p + 2 * i, 2)); break;
    case 114: { float x; memcpy(&x, p, 4); o[0] = x; break; }
    case 115: { float x[2]; memcpy(x, p, 8); o[0] = x[0]; o[1] = x[1]; break; }
    case 116: { float x[4]; memcpy(x, p, 16); for (int i = 0; i < 4; i++) o[i] = x[i]; break; }
    case 101: o[0] = un(v, 16); break;
    case 102: { float x; memcpy(&x, p, 4); o[0] = x; break; }
    }
}
static inline void encode(uint32_t f, const double c[4], uint8_t* p) {
    uint32_t r = pun(c[0], 8), g = pun(c[1], 8), b = pun(c[2], 8), a = pun(c[3], 8);
    switch (f) {
    case 21: wr(p, b | g << 8 | r << 16 | a << 24, 4); break;
    case 22: wr(p, b | g << 8 | r << 16 | 0xFF000000u, 4); break;
    case 32: wr(p, r | g << 8 | b << 16 | a << 24, 4); break;
    case 33: wr(p, r | g << 8 | b << 16 | 0xFF000000u, 4); break;
    case 23: wr(p, pun(c[2], 5) | pun(c[1], 6) << 5 | pun(c[0], 5) << 11, 2); break;
    case 24: wr(p, pun(c[2], 5) | pun(c[1], 5) << 5 | pun(c[0], 5) << 10 | 0x8000, 2); break;
    case 25: wr(p, pun(c[2], 5) | pun(c[1], 5) << 5 | pun(c[0], 5) << 10 | (c[3] >= 0.5 ? 0x8000u : 0), 2); break;
    case 26: wr(p, pun(c[2], 4) | pun(c[1], 4) << 4 | pun(c[0], 4) << 8 | pun(c[3], 4) << 12, 2); break;
    case 31: wr(p, pun(c[0], 10) | pun(c[1], 10) << 10 | pun(c[2], 10) << 20 | pun(c[3], 2) << 30, 4); break;
    case 35: wr(p, pun(c[2], 10) | pun(c[1], 10) << 10 | pun(c[0], 10) << 20 | pun(c[3], 2) << 30, 4); break;
    case 34: wr(p, pun(c[0], 16) | pun(c[1], 16) << 16, 4); break;
    case 36: for (int i = 0; i < 4; i++) wr(p + 2 * i, pun(c[i], 16), 2); break;
    case 28: p[0] = (uint8_t)a; break;
    case 50: p[0] = (uint8_t)r; break;
    case 20: wr(p, b | g << 8 | r << 16, 3); break;
    case 60: p[0] = (uint8_t)psn(c[0], 8); p[1] = (uint8_t)psn(c[1], 8); break;
    case 63: for (int i = 0; i < 4; i++) p[i] = (uint8_t)psn(c[i], 8); break;
    case 64: wr(p, (uint16_t)psn(c[0], 16) | (uint32_t)(uint16_t)psn(c[1], 16) << 16, 4); break;
    case 110: for (int i = 0; i < 4; i++) wr(p + 2 * i, (uint16_t)psn(c[i], 16), 2); break;
    case 111: wr(p, d2h(c[0]), 2); break;
    case 112: wr(p, d2h(c[0]) | (uint32_t)d2h(c[1]) << 16, 4); break;
    case 113: for (int i = 0; i < 4; i++) wr(p + 2 * i, d2h(c[i]), 2); break;
    case 114: case 102: { float x = (float)c[0]; memcpy(p, &x, 4); break; }
    case 115: { float x[2] = {(float)c[0], (float)c[1]}; memcpy(p, x, 8); break; }
    case 116: { float x[4] = {(float)c[0], (float)c[1], (float)c[2], (float)c[3]}; memcpy(p, x, 16); break; }
    case 101: wr(p, pun(c[0], 16), 2); break;
    }
}
// tolerance of one channel of a destination format (1 LSB of its width; floats: relative)
static inline double tol_of(uint32_t f, int ch) {
    switch (f) {
    case 23: return ch == 1 ? 1.01 / 63 : 1.01 / 31; case 24: case 25: return 1.01 / 31; case 26: return 1.01 / 15; case 31: case 35: return ch == 3 ? 1.01 / 3 : 1.01 / 1023;
    case 34: case 36: case 101: return 1.01 / 65535; case 64: case 110: return 1.01 / 32767; case 60: case 63: return 1.01 / 127;
    case 111: case 112: case 113: case 114: case 115: case 116: case 102: return 2e-3;
    default: return 1.01 / 255;
    }
}
