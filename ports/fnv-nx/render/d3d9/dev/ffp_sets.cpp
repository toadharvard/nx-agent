// ffp_sets.cpp: the sets of a fixed-function draw (ffp_dev.h): vertex inputs, pixel stages, pixel varyings. Pure functions of the keys; every set is what the PROGRAM reads (ffp.vert.glsl / ffp.frag.glsl), proven below.
//  V1 vertex attribute locations of the program (fixed by the GLSL source, which the program's reflection would report): POSITION / POSITIONT 0, BLENDWEIGHT 1 or POSITION1 (tweening), BLENDINDICES 2 or NORMAL1
//     (tweening), NORMAL 3, COLOR0 4, COLOR1 5, TEXCOORDn 6 + n, PSIZE 14. 1 and 2 are shared by exactly the pairs whose reading modes exclude each other (VERTEXBLEND == 255 reads POSITION1 / NORMAL1 and never the
//     weights / indices; 1-3 weights, or indexed blending, read weights / indices and never POSITION1 / NORMAL1), so a pair set never holds two pairs of one location.
//  V2 a pair is in the set iff the declaration provides it AND the program reads it for these states: POSITION(T) always (a declaration without one binds the zero attribute: layout rule L3); NORMAL, COLOR0, COLOR1, PSIZE:
//     provided; BLENDWEIGHT: 1-3 weights mode and >= 1 weight component; BLENDINDICES: indexed blending enabled in a 1-3 weights / 0 weights mode and provided; POSITION1 / NORMAL1: tweening and provided;
//     TEXCOORD k: some stage with texgen off (TEXCOORDINDEX high word not 1..4) selects set k (low word), k < 8, with >= 1 component provided.  (Absent inputs: the program ignores the bound value: presence bits.)
//  S1 pixel stages: the program loops the stages in order and stops at the first COLOROP == D3DTOP_DISABLE (the normal form of the op keeps 1 = DISABLE); it samples stage s iff its texture type is not NONE.
//  S2 pixel varyings: COLOR0, COLOR1 (read unconditionally), the TEXCOORD of every sampled stage, FOG0 (read by the epilogue's vertex-fog mode); a user vertex shader paired with this program is completed from this list.
#include "ffp_dev.h"

namespace {
enum VMode { V_NONE, V_WEIGHTS, V_ZERO, V_TWEEN };
VMode vmode(uint32_t vb) {                                  // ffp_vs_README.md: 1,2,3 = n weights, 255 = tweening, 256 = 0 weights, everything else = DISABLE
    switch (vb) {
    case D3DVBF_1WEIGHTS: case D3DVBF_2WEIGHTS: case D3DVBF_3WEIGHTS: return V_WEIGHTS;
    case D3DVBF_TWEENING: return V_TWEEN;
    case D3DVBF_0WEIGHTS: return V_ZERO;
    case D3DVBF_DISABLE: return V_NONE;
    }
    return V_NONE;
}
}

uint32_t nx_ffp_vs_inputs(const NxFfpVsKey& k, NxFfpVsBind* o) {
    const NxFfpVsDecl& dc = k.decl; VMode vm = vmode(k.rs[NX_VS_RS_VERTEXBLEND]);
    bool indexed = k.rs[NX_VS_RS_INDEXEDVERTEXBLENDENABLE] != 0 && (vm == V_WEIGHTS || vm == V_ZERO);
    uint8_t pair[16], loc[16]; uint32_t n = 0;
    auto add = [&](uint32_t usage, uint32_t idx, uint32_t l) { pair[n] = FNVNX_DK_PAIR(usage, idx); loc[n] = (uint8_t)l; n++; };
    add(dc.posT ? FNVNX_USAGE_POSITIONT : FNVNX_USAGE_POSITION, 0, 0);
    if (vm == V_WEIGHTS && dc.nBlendW > 0) add(FNVNX_USAGE_BLENDWEIGHT, 0, 1);
    if (vm == V_TWEEN && dc.hasPos1) add(FNVNX_USAGE_POSITION, 1, 1);
    if (indexed && dc.blendIdx) add(FNVNX_USAGE_BLENDINDICES, 0, 2);
    if (vm == V_TWEEN && dc.hasNormal1) add(FNVNX_USAGE_NORMAL, 1, 2);
    if (dc.hasNormal) add(FNVNX_USAGE_NORMAL, 0, 3);
    if (dc.hasColor0) add(FNVNX_USAGE_COLOR, 0, 4);
    if (dc.hasColor1) add(FNVNX_USAGE_COLOR, 1, 5);
    bool used[8] = {};
    for (uint32_t s = 0; s < 8; s++) {
        uint32_t hi = k.tci[s] >> 16, set = k.tci[s] & 0xFFFFu; bool gen = hi >= 1 && hi <= 4;
        if (!gen && set < 8 && dc.nTex[set] > 0) used[set] = true;
    }
    for (uint32_t t = 0; t < 8; t++) if (used[t]) add(FNVNX_USAGE_TEXCOORD, t, 6 + t);
    if (dc.hasPsize) add(FNVNX_USAGE_PSIZE, 0, 14);
    o->n_in = n; memcpy(o->in_pair, pair, n); memcpy(o->in_loc, loc, n);
    return n;
}

uint32_t nx_ffp_stage_mask(const uint32_t tss[8][33], const uint8_t tex[8]) {
    uint32_t m = 0;
    for (uint32_t s = 0; s < 8; s++) {
        if (tss[s][D3DTSS_COLOROP] == D3DTOP_DISABLE) break;
        if (tex[s] != NX_FFP_TEX_NONE) m |= 1u << s;
    }
    return m;
}

uint32_t nx_ffp_ps_pairs(uint32_t mask, uint8_t out[31]) {
    uint32_t n = 0;
    out[n++] = FNVNX_DK_PAIR(FNVNX_USAGE_COLOR, 0); out[n++] = FNVNX_DK_PAIR(FNVNX_USAGE_COLOR, 1);
    for (uint32_t s = 0; s < 8; s++) if (mask >> s & 1) out[n++] = FNVNX_DK_PAIR(FNVNX_USAGE_TEXCOORD, s);
    out[n++] = FNVNX_DK_PAIR(FNVNX_USAGE_FOG, 0);
    return n;
}
