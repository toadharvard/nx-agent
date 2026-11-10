/* enum_probe.c - dynamic coverage probe for render/native/front/d3d9_state.c (L6 R11 census, measurement only).
 * Calls nx_d3d9_translate / nx_d3d9_sampler / nx_d3d9_format / nx_d3d9_convert / nx_d3d9_bias_scale for every
 * state index and a fixed value set; prints one TSV line per case:
 *   RSV  idx value marks changed eqsent markname   changed = output differs from the same context with the default
 *   SPV  idx value texvariant marks changed eqsent markname   value; eqsent = output equals the one for 0xDEADBEEF
 *   FMT  value marks format flags sw0 sw1 sw2 sw3 bytes srcbytes markname
 *   CNV  value ret marks markname      BIAS value scale
 * marks = number of NX_PLAT_UNSUPPORTED calls the case fired (the weak recorder is defined here). */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include "d3d9_state.h"
#include "fnvnx_dk_layout.h"   /* fnvnx_dk_attrib_location: d3d9_exec.cpp AttribLocation() is a copy of it */

volatile int fnvnx_plat_on = 1;
static int g_marks; static const char* g_name = "";
void fnvnx_plat_note(int cls, const char* name, unsigned caller, const char* detail) {
    (void)cls; (void)caller; (void)detail;
    if (!g_marks) g_name = name;
    g_marks++;
}

static const uint32_t VALS[] = {0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,32,64,100,128,255,256,
                                0x3F800000u,0x40000000u,0x80000000u,0xDEADBEEFu,0xFFFFFFFFu,0x00FF00FFu};
#define NV (sizeof VALS / sizeof VALS[0])
#define SENT 0xDEADBEEFu

static uint64_t fnv(const void* p, size_t n, uint64_t h) {
    const unsigned char* b = (const unsigned char*)p;
    while (n--) { h ^= *b++; h *= 1099511628211ull; }
    return h;
}

static uint64_t rs_hash(const uint32_t* rs) {
    NxD3D9Input in; NxRenderState st; NxDynamicState dyn; uint32_t sb = 0;
    memset(&in, 0, sizeof in); memset(&st, 0, sizeof st);
    in.rs = rs; in.ps_version = 0x0200; in.depth_format = 75;
    in.viewport.Width = 640; in.viewport.Height = 480; in.viewport.MaxZ = 1.0f;
    in.scissor.right = 640; in.scissor.bottom = 480;
    nx_d3d9_translate(&in, &st, &dyn, &sb);
    uint64_t h = 1469598103934665603ull;
    h = fnv(st.w, sizeof st.w, h); h = fnv(&sb, 4, h);
    h = fnv(&dyn.blendConstant, sizeof dyn.blendConstant, h);
    h = fnv(&dyn.depthBiasD3D, 4, h); h = fnv(&dyn.depthBiasUnits, 4, h); h = fnv(&dyn.depthBiasSlope, 4, h);
    h = fnv(&dyn.alphaRef, 4, h); h = fnv(dyn.fogColor, sizeof dyn.fogColor, h);
    h = fnv(&dyn.fogEnd, 4, h); h = fnv(&dyn.fogScale, 4, h); h = fnv(&dyn.fogDensity, 4, h);
    h = fnv(&dyn.pointSize, 4, h); h = fnv(&dyn.stencilRef, sizeof dyn.stencilRef, h);
    h = fnv(&dyn.stencilReadMask, sizeof dyn.stencilReadMask, h); h = fnv(&dyn.stencilWriteMask, sizeof dyn.stencilWriteMask, h);
    return h;
}

static void rs_gates(uint32_t* rs) {
    nx_d3d9_default_rs(rs);
    rs[7] = 1; rs[52] = 1; rs[185] = 1; rs[27] = 1; rs[206] = 1; rs[15] = 1; rs[28] = 1; rs[35] = 3; rs[48] = 0;
    rs[19] = 5; rs[20] = 6; rs[207] = 2; rs[208] = 1; rs[209] = 2; rs[37] = 0x42c80000u; rs[36] = 0x3F800000u;
    rs[154] = 0x40000000u; rs[155] = 0x3F000000u; rs[166] = 0x42800000u;   /* point size 2 in [0.5, 64] */
}

static void probe_rs(void) {
    for (unsigned idx = 0; idx < NXD3D_RS_COUNT; idx++) {
        for (unsigned vi = 0; vi < NV; vi++) {
            int changed = 0, eqsent = 1, marks = 0; const char* nm = "";
            for (int ctx = 0; ctx < 2; ctx++) {
                uint32_t base[NXD3D_RS_COUNT], a[NXD3D_RS_COUNT], s[NXD3D_RS_COUNT];
                if (ctx) rs_gates(base); else nx_d3d9_default_rs(base);
                memcpy(a, base, sizeof a); memcpy(s, base, sizeof s);
                a[idx] = VALS[vi]; s[idx] = SENT;
                g_marks = 0; g_name = "";
                uint64_t ha = rs_hash(a);
                if (g_marks) { marks += g_marks; nm = g_name; }
                if (ha != rs_hash(base)) changed = 1;
                if (ha != rs_hash(s)) eqsent = 0;
            }
            printf("RSV\t%u\t%u\t%d\t%d\t%d\t%s\n", idx, VALS[vi], marks, changed, eqsent, nm);
        }
    }
}

static uint64_t samp_hash(const uint32_t* s, const NxD3D9Texture* t) {
    NxSamplerState o;
    nx_d3d9_sampler(s, t, &o);
    return fnv(&o, sizeof o, 1469598103934665603ull);
}

static void probe_samp(void) {
    NxD3D9Texture tv[4];
    memset(tv, 0, sizeof tv);
    tv[1].format = 21; tv[2].format = 21; tv[2].cube = 1; tv[3].format = 21; tv[3].shadow = 1;
    for (unsigned idx = 0; idx < NXD3D_SAMP_COUNT; idx++) {
        for (unsigned vi = 0; vi < NV; vi++) {
            for (int t = 0; t < 4; t++) {
                uint32_t base[NXD3D_SAMP_COUNT], a[NXD3D_SAMP_COUNT], s[NXD3D_SAMP_COUNT];
                nx_d3d9_default_samp(base);
                base[7] = 2; base[6] = 3; base[10] = 4;
                memcpy(a, base, sizeof a); memcpy(s, base, sizeof s);
                a[idx] = VALS[vi]; s[idx] = SENT;
                g_marks = 0; g_name = "";
                const NxD3D9Texture* tp = t ? &tv[t] : 0;
                uint64_t ha = samp_hash(a, tp);
                int marks = g_marks; const char* nm = g_name;
                printf("SPV\t%u\t%u\t%d\t%d\t%d\t%d\t%s\n", idx, VALS[vi], t, marks, ha != samp_hash(base, tp),
                       ha == samp_hash(s, tp), nm);
            }
        }
    }
}

static void fcc(uint32_t* o, unsigned* n, const char* s) { o[(*n)++] = (uint32_t)(uint8_t)s[0] | (uint32_t)(uint8_t)s[1] << 8 | (uint32_t)(uint8_t)s[2] << 16 | (uint32_t)(uint8_t)s[3] << 24; }

static void probe_fmt(void) {
    static uint32_t L[400]; unsigned n = 0;
    for (uint32_t v = 0; v <= 300; v++) L[n++] = v;
    static const char* F4[] = {"UYVY","YUY2","DXT1","DXT2","DXT3","DXT4","DXT5","MET1","GRGB","RGBG","ATI1","ATI2","INTZ","DF16","DF24","NULL","RAWZ","RESZ","ATOC"};
    for (unsigned i = 0; i < sizeof F4 / sizeof F4[0]; i++) fcc(L, &n, F4[i]);
    L[n++] = 0x7FFFFFFFu; L[n++] = 0xFFFFFFFFu;
    static unsigned char src[8192], dst[8192];
    for (unsigned i = 0; i < n; i++) {
        g_marks = 0; g_name = "";
        NxFormatInfo f = nx_d3d9_format(L[i]);
        printf("FMT\t%u\t%d\t%d\t%u\t%d\t%d\t%d\t%d\t%u\t%u\t%s\n", L[i], g_marks, (int)f.format, (unsigned)f.flags,
               (int)f.swizzle[0], (int)f.swizzle[1], (int)f.swizzle[2], (int)f.swizzle[3], (unsigned)f.bytes, (unsigned)f.srcBytes, g_name);
        g_marks = 0; g_name = "";
        uint32_t r = nx_d3d9_convert(L[i], 4, 4, 1, 1, src, sizeof src, dst, sizeof dst);
        printf("CNV\t%u\t%u\t%d\t%s\n", L[i], r, g_marks, g_name);
        printf("BIAS\t%u\t%g\n", L[i], (double)nx_d3d9_bias_scale(L[i]));
    }
}

static void probe_usage(void) {   /* USG usage index location */
    for (int u = 0; u <= 13; u++) for (int i = 0; i < 16; i++) printf("USG\t%d\t%d\t%d\n", u, i, fnvnx_dk_attrib_location(u, i));
}

int main(void) { probe_rs(); probe_samp(); probe_fmt(); probe_usage(); return 0; }
