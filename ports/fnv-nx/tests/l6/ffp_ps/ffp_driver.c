/* ffp_driver: CPU reference driver for test_ffp_ps.py.
 *   ffp_driver sweep            full cross product (stdout: 11 words per case, see below)
 *   ffp_driver general <file>   file = N * Rec; stdout: 4 floats per case
 * sweep record: s0mode chan op arg0 arg1 arg2 result r g b a (u32 x7, f32 x4). Enumeration order = nested loops below;
 * the oracle re-enumerates and checks the keys. Stage 0 fixed (ADDSIGNED tex0 + diffuse / alpha tex0), stage 1 swept
 * (chan 0 = colour op swept, alpha op fixed MODULATE cur*tex; chan 1 = alpha op swept, colour op fixed MODULATE cur*tex),
 * stage 2 DISABLE; both stages bound 2D; texture = constant colour per stage (ignores coordinates). */
#include <stdio.h>
#include <stdlib.h>
#include "ffp_test_common.h"

static const float TEX[2][4] = {{0.90f, 0.20f, 0.50f, 0.60f}, {0.30f, 0.75f, 0.45f, 0.85f}};
static void sample_const(void *u, uint32_t st, uint32_t type, const float c[3], float o[4])
{ (void)u; (void)type; (void)c; for (int k = 0; k < 4; k++) o[k] = TEX[st & 1][k]; }
static void sample_fn(void *u, uint32_t st, uint32_t type, const float c[3], float o[4]) { (void)u; texel(st, type, c, o); }

static void put(const uint32_t *k, int nk, const float *f)
{ fwrite(k, 4, nk, stdout); fwrite(f, 4, 4, stdout); }

/* index 0..27 = source (7) x modifiers (4: none, COMPLEMENT, ALPHAREPLICATE, both) */
static uint32_t argv28(int i) { return (uint32_t)(i % 7) | ((i / 7) & 1 ? 0x10u : 0u) | ((i / 7) & 2 ? 0x20u : 0u); }

int main(int argc, char **argv)
{
    NxFfpPsState st; NxFfpPsInputs in;
    if (argc >= 3 && !strcmp(argv[1], "general")) {
        FILE *f = fopen(argv[2], "rb"); Rec r; if (!f) return 2;
        while (fread(&r, sizeof r, 1, f) == 1) {
            float o[4]; rec_to_state(&r, &st, &in); in.sample = sample_fn; in.user = 0;
            nx_ffp_ps_eval(&st, &in, o); fwrite(o, 4, 4, stdout);
        }
        return 0;
    }
    /* sweep */
    static const uint32_t used[27] = {0,0,1,2,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,7,7};
    for (uint32_t s0mode = 0; s0mode < 2; s0mode++)
    for (uint32_t chan = 0; chan < 2; chan++)
    for (uint32_t op = 1; op <= 26; op++) {
        int n0 = (used[op] & 4) ? 28 : 1, n1 = (used[op] & 1) ? 28 : 1, n2 = (used[op] & 2) ? 28 : 1;
        for (int i0 = 0; i0 < n0; i0++) for (int i1 = 0; i1 < n1; i1++) for (int i2 = 0; i2 < n2; i2++)
        for (uint32_t res = 0; res < 2; res++) {
            Rec r; memset(&r, 0, sizeof r);
            uint32_t *g = &r.w[144];
            for (int s = 0; s < 8; s++) {
                uint32_t *p = &r.w[s * 18];
                p[0] = NX_FFP_TOP_DISABLE; p[1] = NX_FFP_TOP_DISABLE; p[3] = NX_FFP_TEX_2D;
                p[4] = p[5] = p[6] = p[8] = p[9] = p[10] = 0x13;         /* unused slots: TFACTOR|COMPLEMENT, must not matter */
                p[11] = s == 0 ? 0x40CC7F19u : 0xB35A8D2Fu;
            }
            uint32_t *p0 = &r.w[0], *p1 = &r.w[18];
            p0[0] = NX_FFP_TOP_ADDSIGNED; p0[4] = 0; p0[5] = NX_FFP_TA_TEXTURE; p0[6] = NX_FFP_TA_DIFFUSE;
            p0[1] = NX_FFP_TOP_SELECTARG1; p0[9] = NX_FFP_TA_TEXTURE; p0[2] = s0mode ? NX_FFP_TA_TEMP : NX_FFP_TA_CURRENT;
            p1[2] = res ? NX_FFP_TA_TEMP : NX_FFP_TA_CURRENT;
            uint32_t a0 = argv28(i0), a1 = argv28(i1), a2 = argv28(i2);
            if (!(used[op] & 4)) a0 = 0x13;
            if (!(used[op] & 1)) a1 = 0x13;
            if (!(used[op] & 2)) a2 = 0x13;
            if (chan == 0) {
                p1[0] = op; p1[4] = a0; p1[5] = a1; p1[6] = a2;
                p1[1] = NX_FFP_TOP_MODULATE; p1[9] = NX_FFP_TA_CURRENT; p1[10] = NX_FFP_TA_TEXTURE;
            } else {
                p1[1] = op; p1[8] = a0; p1[9] = a1; p1[10] = a2;
                p1[0] = NX_FFP_TOP_MODULATE; p1[5] = NX_FFP_TA_CURRENT; p1[6] = NX_FFP_TA_TEXTURE;
            }
            g[0] = 0x66CC3399u; g[1] = 1;
            float d[4] = {0.80f, 0.30f, 0.60f, 0.70f}, sp[4] = {0.10f, 0.55f, 0.20f, 0.40f};
            memcpy(&g[2], d, 16); memcpy(&g[6], sp, 16);
            rec_to_state(&r, &st, &in); in.sample = sample_const; in.user = 0;
            float o[4]; nx_ffp_ps_eval(&st, &in, o);
            uint32_t key[7] = {s0mode, chan, op, a0, a1, a2, res};
            put(key, 7, o);
        }
    }
    return 0;
}
