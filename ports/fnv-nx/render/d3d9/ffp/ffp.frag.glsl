#version 460
// D3D9 fixed-function PIXEL pipeline as ONE total program (rule RD5): behaviour is selected by the data block
// NxFfpPsState (ffp_state.h, std140, UBO binding 3). Twin of ffp_ps_ref.c (same function names, same statements);
// semantic source for open points: WineD3D ffp fragment generator, deviations in README.md.
//
// Interface (render/shaders/translator/include/fnvnx_dk_layout.h): varyings by fnvnx_dk_varying_location: COLOR0 diffuse = 0,
// COLOR1 specular = 1, TEXCOORDn = 2 + n (n = 0..7, all vec4). Fog factor at 18 is read by d9_epilogue.glsl.
// D3DRS_SHADEMODE = FLAT: the vertex stage and this program must both qualify locations 0 and 1 `flat`; build the
// program with NX_FFP_FLAT defined (nx-variants line below). Texcoord varyings are never flat.
// Samplers (deko3d sampler binding = image slot): stage n 2D = n, CUBE = 8 + n, VOLUME = 16 + n. The host binds every
// slot (a stage's unused dimensionalities may point at any texture/sampler: they are never read) .
// nx-variants: -= flat=NX_FFP_FLAT
#ifdef NX_FFP_FLAT
#define FFP_FLAT flat
#else
#define FFP_FLAT
#endif
struct FfpStage { uvec4 op; uvec4 carg; uvec4 aarg; vec4 cst; vec4 bump; vec4 lum; };
layout (std140, binding = 3) uniform NxFfpPs { FfpStage stage[8]; vec4 tfactor; uvec4 spec; };

layout (binding = 0) uniform sampler2D t2d0;   layout (binding = 1) uniform sampler2D t2d1;
layout (binding = 2) uniform sampler2D t2d2;   layout (binding = 3) uniform sampler2D t2d3;
layout (binding = 4) uniform sampler2D t2d4;   layout (binding = 5) uniform sampler2D t2d5;
layout (binding = 6) uniform sampler2D t2d6;   layout (binding = 7) uniform sampler2D t2d7;
layout (binding = 8) uniform samplerCube tcb0;  layout (binding = 9) uniform samplerCube tcb1;
layout (binding = 10) uniform samplerCube tcb2; layout (binding = 11) uniform samplerCube tcb3;
layout (binding = 12) uniform samplerCube tcb4; layout (binding = 13) uniform samplerCube tcb5;
layout (binding = 14) uniform samplerCube tcb6; layout (binding = 15) uniform samplerCube tcb7;
layout (binding = 16) uniform sampler3D tvl0;  layout (binding = 17) uniform sampler3D tvl1;
layout (binding = 18) uniform sampler3D tvl2;  layout (binding = 19) uniform sampler3D tvl3;
layout (binding = 20) uniform sampler3D tvl4;  layout (binding = 21) uniform sampler3D tvl5;
layout (binding = 22) uniform sampler3D tvl6;  layout (binding = 23) uniform sampler3D tvl7;

layout(location = 0) FFP_FLAT in vec4 vDiffuse;
layout(location = 1) FFP_FLAT in vec4 vSpecular;
layout(location = 2) in vec4 vTc0;  layout(location = 3) in vec4 vTc1;  layout(location = 4) in vec4 vTc2;
layout(location = 5) in vec4 vTc3;  layout(location = 6) in vec4 vTc4;  layout(location = 7) in vec4 vTc5;
layout(location = 8) in vec4 vTc6;  layout(location = 9) in vec4 vTc7;
layout(location = 0) out vec4 outColor;

#include "d9_epilogue.glsl"

// ---- domain functions (D3DTEXTUREOP 1..26 and D3DTA_*: see ffp_state.h) -----------------------------------------
const uint NOP = 0u, DISABLE = 1u, SEL1 = 2u, SEL2 = 3u, MOD = 4u, MOD2X = 5u, MOD4X = 6u, ADD = 7u, ADDS = 8u,
    ADDS2X = 9u, SUB = 10u, ADDSMOOTH = 11u, BDA = 12u, BTA = 13u, BFA = 14u, BTAPM = 15u, BCA = 16u, PREMOD = 17u,
    MAAC = 18u, MCAA = 19u, MIAAC = 20u, MICAA = 21u, BUMP = 22u, BUMPL = 23u, DOT3 = 24u, MADD = 25u, LERP = 26u;
uint ffp_norm_op(uint v) { return (v >= 1u && v <= 26u) ? v : 0u; }
uint ffp_norm_arg_sel(uint a) { uint s = a & 15u; return s > 6u ? 7u : s; }
uint ffp_norm_textype(uint t) { return (t >= 1u && t <= 3u) ? t : 0u; }

uint ffp_used(uint op) {              // bit0 arg1, bit1 arg2, bit2 arg0
    if (op == SEL1 || op == PREMOD) return 1u;   // PREMODULATE: documented S = Arg1
    if (op == SEL2) return 2u;
    if (op == MADD || op == LERP) return 7u;
    if (op >= MOD) return 3u;
    return 0u;
}
bool ffp_invalid(bool hasTex, uint op, uint a0, uint a1, uint a2) {
    uint u = ffp_used(op);
    if (hasTex) return false;
    return ((u & 1u) != 0u && ffp_norm_arg_sel(a1) == 2u) || ((u & 2u) != 0u && ffp_norm_arg_sel(a2) == 2u) ||
           ((u & 4u) != 0u && ffp_norm_arg_sel(a0) == 2u);
}

struct Regs { vec4 diffuse, spec, cur, tmp, tex, tf, cst; };

vec4 ffp_source(uint sel, Regs r) {
    if (sel == 0u) return r.diffuse;
    if (sel == 1u) return r.cur;
    if (sel == 2u) return r.tex;
    if (sel == 3u) return r.tf;
    if (sel == 4u) return r.spec;
    if (sel == 5u) return r.tmp;
    if (sel == 6u) return r.cst;
    return vec4(0.0);
}
vec4 ffp_arg(uint a, Regs r) {
    vec4 v = ffp_source(ffp_norm_arg_sel(a), r);
    if ((a & 16u) != 0u) v = vec4(1.0) - v;
    if ((a & 32u) != 0u) v = vec4(v.w);
    return v;
}
vec4 ffp_op(uint op, vec4 a0, vec4 a1, vec4 a2, Regs r) {
    vec4 one = vec4(1.0), half4 = vec4(0.5), w1 = vec4(a1.w);
    if (op == SEL1) return a1;
    if (op == SEL2) return a2;
    if (op == MOD) return a1 * a2;
    if (op == MOD2X) return a1 * a2 * 2.0;
    if (op == MOD4X) return a1 * a2 * 4.0;
    if (op == ADD) return a1 + a2;
    if (op == ADDS) return a1 + a2 - half4;
    if (op == ADDS2X) return (a1 + a2 - half4) * 2.0;
    if (op == SUB) return a1 - a2;
    if (op == ADDSMOOTH) return a1 + a2 * (one - a1);
    if (op == BDA || op == BTA || op == BFA || op == BCA) {
        vec4 al = vec4(op == BDA ? r.diffuse.w : op == BTA ? r.tex.w : op == BFA ? r.tf.w : r.cur.w);
        return a1 * al + a2 * (one - al);
    }
    if (op == BTAPM) return a1 + a2 * vec4(1.0 - r.tex.w);
    if (op == PREMOD) return a1;
    if (op == MAAC) return a1 + a2 * w1;
    if (op == MCAA) return a1 * a2 + w1;
    if (op == MIAAC) return a1 + a2 * (one - w1);
    if (op == MICAA) return (one - a1) * a2 + w1;
    if (op == DOT3) {
        vec3 u = a1.xyz - vec3(0.5), v = a2.xyz - vec3(0.5);
        return vec4(4.0 * (u.x * v.x + u.y * v.y + u.z * v.z));
    }
    if (op == MADD) return a1 * a2 + a0;
    if (op == LERP) return a1 * a0 + a2 * (one - a0);
    return vec4(0.0);                      // NOP, DISABLE, BUMPENVMAP*: never written
}

vec4 ffp_texcoord(uint s) {
    if (s == 0u) return vTc0;  if (s == 1u) return vTc1;  if (s == 2u) return vTc2;  if (s == 3u) return vTc3;
    if (s == 4u) return vTc4;  if (s == 5u) return vTc5;  if (s == 6u) return vTc6;
    return vTc7;
}
vec4 ffp_fetch(uint s, uint type, vec3 c) {    // type is normalised 1..3; s is uniform, so every texture() is in uniform control flow
    if (type == 1u) {
        if (s == 0u) return texture(t2d0, c.xy);  if (s == 1u) return texture(t2d1, c.xy);
        if (s == 2u) return texture(t2d2, c.xy);  if (s == 3u) return texture(t2d3, c.xy);
        if (s == 4u) return texture(t2d4, c.xy);  if (s == 5u) return texture(t2d5, c.xy);
        if (s == 6u) return texture(t2d6, c.xy);  return texture(t2d7, c.xy);
    }
    if (type == 2u) {
        if (s == 0u) return texture(tcb0, c);  if (s == 1u) return texture(tcb1, c);
        if (s == 2u) return texture(tcb2, c);  if (s == 3u) return texture(tcb3, c);
        if (s == 4u) return texture(tcb4, c);  if (s == 5u) return texture(tcb5, c);
        if (s == 6u) return texture(tcb6, c);  return texture(tcb7, c);
    }
    if (s == 0u) return texture(tvl0, c);  if (s == 1u) return texture(tvl1, c);
    if (s == 2u) return texture(tvl2, c);  if (s == 3u) return texture(tvl3, c);
    if (s == 4u) return texture(tvl4, c);  if (s == 5u) return texture(tvl5, c);
    if (s == 6u) return texture(tvl6, c);  return texture(tvl7, c);
}

void main() {
    Regs r;
    r.diffuse = vDiffuse;  r.spec = vSpecular;  r.tf = tfactor;
    r.cur = r.diffuse;                          // CURRENT at stage 0 = DIFFUSE
    r.tmp = vec4(0.0);                          // TEMP starts as 0
    r.tex = vec4(0.0);  r.cst = vec4(0.0);
    uint prevBump = 0u;
    bool pmC = false, pmA = false;
    vec4 prevTex = vec4(0.0);

    for (uint s = 0u; s < 8u; s++) {
        uint cop = ffp_norm_op(stage[s].op.x);
        if (cop == DISABLE) break;
        uint type = ffp_norm_textype(stage[s].op.w);
        bool hasTex = type != 0u;
        uint c0 = stage[s].carg.x, c1 = stage[s].carg.y, c2 = stage[s].carg.z;
        if (ffp_invalid(hasTex, cop, c0, c1, c2)) { cop = SEL1; c1 = 1u; }
        uint aop, a0, a1, a2;
        if (cop == DOT3) { aop = cop; a0 = c0; a1 = c1; a2 = c2; }
        else {
            aop = ffp_norm_op(stage[s].op.y);  a0 = stage[s].aarg.x;  a1 = stage[s].aarg.y;  a2 = stage[s].aarg.z;
            if (ffp_invalid(hasTex, aop, a0, a1, a2)) { aop = SEL1; a1 = 1u; }
        }

        vec4 tex = vec4(0.0);
        if (hasTex) {
            uint ttff = stage[s].carg.w;
            uint count = ttff & 255u;
            bool proj = (ttff & 256u) != 0u && type != 2u;
            uint di = (count >= 1u && count <= 4u) ? count - 1u : 3u;
            vec4 tc = ffp_texcoord(s);
            vec3 c = tc.xyz;
            float d = tc[di];
            if (proj && d != 0.0) c = c / d;
            if (prevBump != 0u) {
                vec4 pb = stage[s - 1u].bump;
                c.x += pb.x * prevTex.x + pb.z * prevTex.y;
                c.y += pb.y * prevTex.x + pb.w * prevTex.y;
            }
            tex = ffp_fetch(s, type, c);
        }
        if (prevBump == 2u) {
            vec4 pl = stage[s - 1u].lum;
            tex = tex * clamp(prevTex.z * pl.x + pl.y, 0.0, 1.0);
        }
        if (pmC) r.cur.xyz *= tex.xyz;
        if (pmA) r.cur.w *= tex.w;
        r.tex = tex;
        r.cst = stage[s].cst;

        vec4 rc = ffp_op(cop, ffp_arg(c0, r), ffp_arg(c1, r), ffp_arg(c2, r), r);
        vec4 ra = ffp_op(aop, ffp_arg(a0, r), ffp_arg(a1, r), ffp_arg(a2, r), r);
        bool bump = cop == BUMP || cop == BUMPL;
        bool wc = cop != NOP && !bump;
        bool wa = !bump && aop != NOP && aop != DISABLE && aop != BUMP && aop != BUMPL;
        vec4 res = stage[s].op.z == 5u ? r.tmp : r.cur;       // RESULTARG: exactly TEMP, else CURRENT
        if (wc) res.xyz = clamp(rc.xyz, 0.0, 1.0);
        if (wa) res.w = clamp(ra.w, 0.0, 1.0);
        if (stage[s].op.z == 5u) r.tmp = res; else r.cur = res;

        pmC = cop == PREMOD;
        pmA = wa && aop == PREMOD;
        prevBump = cop == BUMP ? 1u : (cop == BUMPL ? 2u : 0u);
        prevTex = tex;
    }
    float se = spec.x != 0u ? 1.0 : 0.0;                         // D3DRS_SPECULARENABLE: RGB only
    vec4 o = clamp(r.cur + r.spec * vec4(se, se, se, 0.0), 0.0, 1.0);
    outColor = d9_epilogue(o);
}
