/* rcpgen.c: derives the RCPSS / RSQRTSS lookup tables of the reference CPU from the real instructions and prints the
 * generated region of rt/rcptab.c (tables only; the logic is in rt/rcptab.c). Run via tools/rcpgen.py, or by hand:
 *   gcc -O2 -msse2 tools/rcpgen.c -o rcpgen && ./rcpgen            (native x86-64 host = the reference CPU)
 * Structure of the hardware functions (all of it is proven equal on all 2^32 inputs by test/rcptest.py):
 *   rcp:   normal input (exponent e in 1..254): result exponent 253-e, 23-bit mantissa = table[m >> 12] (2048 entries)
 *   rsqrt: normal input: result exponent (380-e)>>1, mantissa = table[(e&1)<<10 | m>>13] (2048 entries)
 * Table entries are mantissa bits 22..11 (12 bits); the low 11 mantissa bits of every hardware result are 0 or 0x400/0x800 patterns
 * that the generator verifies to be zero below bit 11. */
#include <immintrin.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
static uint32_t hw_rcp(uint32_t b) { float f; memcpy(&f, &b, 4); float r = _mm_cvtss_f32(_mm_rcp_ss(_mm_set_ss(f))); uint32_t o; memcpy(&o, &r, 4); return o; }
static uint32_t hw_rsq(uint32_t b) { float f; memcpy(&f, &b, 4); float r = _mm_cvtss_f32(_mm_rsqrt_ss(_mm_set_ss(f))); uint32_t o; memcpy(&o, &r, 4); return o; }
static void emit(const char *name, const uint16_t *t) {
    printf("static const unsigned short %s[2048] = {\n", name);
    for (int i = 0; i < 2048; i++) printf("%s0x%03x,%s", i % 16 == 0 ? "" : " ", t[i], i % 16 == 15 ? "\n" : "");
    printf("};\n");
}
int main(void) {
    static uint16_t rc[2048], rs[2048];
    for (uint32_t i = 0; i < 2048; i++) {
        uint32_t r = hw_rcp((127u << 23) | (i << 12));
        if (r & 0x7ff) { fprintf(stderr, "rcp low bits nonzero\n"); return 1; }
        rc[i] = (r >> 11) & 0xfff;
        uint32_t e = (i >> 10) ? 127 : 128;     /* parity bit set -> odd exponent 127, clear -> 128 */
        r = hw_rsq((e << 23) | ((i & 1023) << 13));
        if (r & 0x7ff) { fprintf(stderr, "rsqrt low bits nonzero\n"); return 1; }
        rs[i] = (r >> 11) & 0xfff;
    }
    emit("rcp_tab", rc); emit("rsqrt_tab", rs);
    return 0;
}
