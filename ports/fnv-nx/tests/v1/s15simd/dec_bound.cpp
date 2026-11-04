// dec_bound.cpp - V1 (D14): the decomp's NiBound::Update 0x00a7fdd0. nibound.inl and its nibound_decls.h are copied
// from the decomp into the run's temporary directory by test_s15simd_decomp.py (never in git).
#include <stdint.h>
#include "nibound.inl"
extern "C" void *memcpy(void *, const void *, unsigned long);
static NiBound g_self, g_b;
static NiTransform g_t;
// in: [0..3] bound, [4..16] transform, [17] self == bound; out: self after
extern "C" void dec_bound(const uint32_t *in, uint32_t *out)
{
    memcpy(&g_b, in, 16);
    memcpy(&g_t, in + 4, 52);
    if (in[17]) {
        g_b.Update(g_b, g_t);
        memcpy(out, &g_b, 16);
    } else {
        g_self.Update(g_b, g_t);
        memcpy(out, &g_self, 16);
    }
}
