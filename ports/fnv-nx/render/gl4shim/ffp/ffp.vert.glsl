#version 460
// D3D9 fixed-function VERTEX pipeline as ONE total program (rule RD5): behaviour is selected by the data block
// NxFfpVsState (ffp_vs_state.h, std140, binding 4) and the world-matrix palette (binding 5); the driver UBO 2 supplies
// posfix (fnvnx_dk_layout.h). Twin of ffp_vs_ref.c (same step order and statements); open points: ffp_vs_README.md.
// Matrices: the D3D row-major bytes read as a GLSL mat4 are the TRANSPOSE, so D3D's  v * M  is written  M * v.
// Attribute locations follow fnvnx_dk_attrib_location (POSITION 0, BLENDWEIGHT 1, BLENDINDICES 2, NORMAL 3, COLOR 4-5,
// TEXCOORD 6-13); the host binds absent inputs to anything (presence comes from the block). Tweening (VERTEXBLEND ==
// 255) has no weights/indices, so POSITION1 uses location 1 and NORMAL1 location 2 then; PSIZE is location 14.
// Varyings: COLOR0 0, COLOR1 1, TEXCOORDn 2+n, fog 18 (.x). D3DRS_SHADEMODE FLAT: build with NX_FFP_FLAT (as ffp.frag.glsl).
// nx-variants: -= flat=NX_FFP_FLAT
#ifdef NX_FFP_FLAT
#define FFP_FLAT flat
#else
#define FFP_FLAT
#endif
struct FfpLight { vec4 diffuse; vec4 specular; vec4 ambient; vec4 position; vec4 direction; vec4 att; vec4 cone; uvec4 kind; };
layout (std140, binding = 4) uniform NxFfpVs {
    uvec4 inputs; uvec4 rsBlend; uvec4 rsLight; uvec4 rsFog; uvec4 rsSrc; uvec4 rsMisc; uvec4 tci[2]; uvec4 ttff[2];
    vec4 fogp; vec4 point; vec4 point2; vec4 viewport; vec4 gAmbient;
    vec4 mDiffuse; vec4 mAmbient; vec4 mSpecular; vec4 mEmissive; vec4 mPower;
    mat4 view; mat4 proj; mat4 normalMat; mat4 texm[8]; vec4 clipPlane[6]; FfpLight light[8];
};
layout (std140, binding = 5) uniform NxFfpPalette { mat4 world[256]; };
layout (std140, binding = 2) uniform NxVsDriver { vec4 posfix; };

layout (location = 0) in vec4 aPos;   layout (location = 1) in vec4 aBW;   layout (location = 2) in vec4 aBI;
layout (location = 3) in vec4 aNrm;   layout (location = 4) in vec4 aC0;   layout (location = 5) in vec4 aC1;
layout (location = 6) in vec4 aTex0;  layout (location = 7) in vec4 aTex1; layout (location = 8) in vec4 aTex2;
layout (location = 9) in vec4 aTex3;  layout (location = 10) in vec4 aTex4; layout (location = 11) in vec4 aTex5;
layout (location = 12) in vec4 aTex6; layout (location = 13) in vec4 aTex7; layout (location = 14) in vec4 aPsz;

layout (location = 0) FFP_FLAT out vec4 oColor0;
layout (location = 1) FFP_FLAT out vec4 oColor1;
layout (location = 2) out vec4 oTex0;  layout (location = 3) out vec4 oTex1;  layout (location = 4) out vec4 oTex2;
layout (location = 5) out vec4 oTex3;  layout (location = 6) out vec4 oTex4;  layout (location = 7) out vec4 oTex5;
layout (location = 8) out vec4 oTex6;  layout (location = 9) out vec4 oTex7;
layout (location = 18) out vec4 oFog;
// Every other canonical varying slot (fnvnx_dk_layout.h fnvnx_dk_varying_location: 10-17 TEXCOORD8-15, 19-30 NORMAL0/1, TANGENT0, BINORMAL0, BLENDWEIGHT0,
// BLENDINDICES0, POSITION1-3, DEPTH0, PSIZE1-2) is written 0.0, so this stage is "complete" (draw_ffp.h): it pairs with any pixel stage ("Varying completion").
layout (location = 10) out vec4 oPad10;  layout (location = 11) out vec4 oPad11;  layout (location = 12) out vec4 oPad12;  layout (location = 13) out vec4 oPad13;
layout (location = 14) out vec4 oPad14;  layout (location = 15) out vec4 oPad15;  layout (location = 16) out vec4 oPad16;  layout (location = 17) out vec4 oPad17;
layout (location = 19) out vec4 oPad19;  layout (location = 20) out vec4 oPad20;  layout (location = 21) out vec4 oPad21;  layout (location = 22) out vec4 oPad22;
layout (location = 23) out vec4 oPad23;  layout (location = 24) out vec4 oPad24;  layout (location = 25) out vec4 oPad25;  layout (location = 26) out vec4 oPad26;
layout (location = 27) out vec4 oPad27;  layout (location = 28) out vec4 oPad28;  layout (location = 29) out vec4 oPad29;  layout (location = 30) out vec4 oPad30;
out gl_PerVertex { vec4 gl_Position; float gl_PointSize; float gl_ClipDistance[6]; };

float sat(float x) { return clamp(x, 0.0, 1.0); }
// ffp_normalize (wined3d): the zero vector stays zero
vec3 nz3(vec3 v) { float l = dot(v, v); return l == 0.0 ? v : v * inversesqrt(l); }

// D3DMATERIALCOLORSOURCE: COLOR1 = vertex diffuse, COLOR2 = vertex specular; a source not provided, any other value and
// COLORVERTEX == 0 select the material colour.
vec4 mcs(uint src, vec4 c0, vec4 c1, bool h0, bool h1, vec4 mat) {
    bool cv = rsLight.x != 0u;
    if (cv && src == 1u && h0) return c0;
    if (cv && src == 2u && h1) return c1;
    return mat;
}

vec4 texIn(uint set) {
    return set == 0u ? aTex0 : set == 1u ? aTex1 : set == 2u ? aTex2 : set == 3u ? aTex3 :
           set == 4u ? aTex4 : set == 5u ? aTex5 : set == 6u ? aTex6 : aTex7;
}

// One texture stage: TEXCOORDINDEX (set + texgen), TEXTURETRANSFORMFLAGS and the texture matrix (compute_texture_matrix:
// component after the last one is 1, components beyond COUNT are zeroed, PROJECTED copies component COUNT-1 to w).
vec4 texStage(uint s, bool posT, vec3 normal, vec4 ec) {
    uint tc = tci[s >> 2u][s & 3u], tf = ttff[s >> 2u][s & 3u], hi = tc >> 16u, set = tc & 0xFFFFu;
    uint gen = (hi >= 1u && hi <= 4u) ? hi : 0u;
    vec4 src; uint n;
    if (gen != 0u) {
        n = 3u;
        vec3 e = nz3(ec.xyz);
        vec3 r = e - 2.0 * dot(normal, e) * normal;
        float m = 2.0 * sqrt(r.x * r.x + r.y * r.y + (r.z - 1.0) * (r.z - 1.0));
        src = gen == 1u ? vec4(normal, 1.0) : gen == 2u ? ec : gen == 3u ? vec4(r, 1.0)
            : vec4(m != 0.0 ? r.x / m + 0.5 : 0.5, m != 0.0 ? r.y / m + 0.5 : 0.5, 0.0, 1.0);
    } else {
        n = set < 8u ? min((inputs.y >> (4u * set)) & 15u, 4u) : 0u;
        vec4 raw = texIn(set & 7u);
        src = vec4(0.0, 0.0, 0.0, 1.0);
        if (set < 8u) {
            if (n > 0u) src.x = raw.x;
            if (n > 1u) src.y = raw.y;
            if (n > 2u) src.z = raw.z;
            if (n > 3u) src.w = raw.w;
        }
    }
    uint cnt = tf & 255u, proj = (tf >> 8u) & 1u;
    vec4 o;
    if (!posT && cnt >= 1u && cnt <= 4u) {
        vec4 pad = vec4(0.0, 0.0, 0.0, 1.0);
        if (n != 0u) {
            pad.x = src.x; pad.y = n > 1u ? src.y : (n == 1u ? 1.0 : 0.0);
            pad.z = n > 2u ? src.z : (n == 2u ? 1.0 : 0.0); pad.w = n > 3u ? src.w : (n == 3u ? 1.0 : 0.0);
        }
        o = texm[s] * pad;
        if (cnt < 4u) o.w = 0.0;
        if (cnt < 3u) o.z = 0.0;
        if (cnt < 2u) o.y = 0.0;
        if (proj != 0u && cnt < 4u) o.w = cnt == 1u ? o.x : cnt == 2u ? o.y : o.z;
    } else {
        o = src;
        if (!posT && proj != 0u && n >= 1u && n <= 3u) o.w = n == 1u ? src.x : n == 2u ? src.y : src.z;
    }
    return o;
}

void main() {
    uint fl = inputs.x;
    bool posT = (fl & 1u) != 0u, hasN = (fl & 2u) != 0u, hasC0 = (fl & 8u) != 0u, hasC1 = (fl & 16u) != 0u;
    uint nbw = min((fl >> 12u) & 7u, 4u);
    vec4 c0 = hasC0 ? aC0 : vec4(1.0), c1 = hasC1 ? aC1 : vec4(0.0);
    vec3 nin = hasN ? aNrm.xyz : vec3(0.0), normal = vec3(0.0);
    uint vb = rsBlend.x;
    uint nb = (vb >= 1u && vb <= 3u) ? vb : 0u, K = nb + 1u;
    bool tween = vb == 255u, idxOn = rsBlend.y != 0u && ((vb >= 1u && vb <= 3u) || vb == 256u);
    vec4 ec, ecRaw, clip;

    if (posT) {                                            // POSITIONT: viewport -> clip, no transform, no lighting
        float X = aPos.x, Y = aPos.y, Z = aPos.z, rhw = aPos.w;
        float nx = viewport.z != 0.0 ? 2.0 * (X - viewport.x) / viewport.z - 1.0 : 0.0;
        float ny = viewport.w != 0.0 ? 1.0 - 2.0 * (Y - viewport.y) / viewport.w : 0.0;
        float w = rhw != 0.0 ? 1.0 / rhw : 1.0;
        clip = vec4(nx * w, ny * w, Z * w, w);
        ec = vec4(X, Y, Z, 1.0); ecRaw = ec;
    } else {
        vec3 p = aPos.xyz, nn = nin;
        if (tween) {
            float t = fogp.w;
            vec3 p1 = (fl & 32u) != 0u ? aBW.xyz : vec3(0.0), n1 = (fl & 64u) != 0u ? aBI.xyz : vec3(0.0);
            p = (1.0 - t) * p + t * p1; nn = (1.0 - t) * nn + t * n1;
        }
        float bw[4];
        bw[0] = nbw > 0u ? aBW.x : 0.0; bw[1] = nbw > 1u ? aBW.y : 0.0; bw[2] = nbw > 2u ? aBW.z : 0.0; bw[3] = nbw > 3u ? aBW.w : 0.0;
        float wt[4]; float sum = 0.0;
        for (uint i = 0u; i < 4u; i++) { wt[i] = 0.0; }
        for (uint i = 0u; i < 4u; i++) { if (i < nb) { wt[i] = bw[i]; sum += bw[i]; } }
        wt[nb] = 1.0 - sum;
        float sc = (fl & 256u) != 0u ? 255.0 : 1.0;
        vec4 bi = (fl & 128u) != 0u ? aBI : vec4(0.0);
        float bif[4] = float[4](bi.x, bi.y, bi.z, bi.w);
        ec = vec4(0.0); vec3 nsum = vec3(0.0);
        for (uint i = 0u; i < 4u; i++) {
            if (i >= K) break;
            uint mi = i;
            if (idxOn) mi = uint(clamp(floor(bif[i] * sc + 0.5), 0.0, 255.0));
            mat4 wv = view * world[mi];                    // = (W_mi * V) transposed
            ec += wt[i] * (wv * vec4(p, 1.0));
            nsum += wt[i] * (mat3(wv) * nn);
        }
        clip = proj * ec; ecRaw = ec;
        if (ec.w != 0.0) ec *= 1.0 / ec.w;
        vec3 nr = (K == 1u && !idxOn) ? mat3(normalMat) * nn : nsum;
        normal = rsBlend.z != 0u ? nz3(nr) : nr;
    }

    // ---- lighting (D3D9 "Mathematics of Lighting", eye space)
    vec4 o0, o1;
    if (!posT && rsBlend.w != 0u) {
        bool specOn = rsLight.y != 0u, localViewer = rsLight.z != 0u;
        vec3 accA = gAmbient.rgb, accD = vec3(0.0); vec4 accS = vec4(0.0);
        for (uint i = 0u; i < 8u; i++) {
            if (i >= rsMisc.z) break;
            uint kind = light[i].kind.x;
            vec3 ldir; float att = 1.0;
            if (kind == 3u) {
                ldir = light[i].position.xyz;
                accA += light[i].ambient.rgb;
            } else if (kind == 1u || kind == 2u) {
                ldir = light[i].position.xyz - ec.xyz;
                float dist = sqrt(dot(ldir, ldir));
                if (dist > light[i].direction.w) continue;     // beyond Range: no contribution at all
                ldir = nz3(ldir);
                float spot = 1.0;
                if (kind == 2u) {
                    float rho = -dot(ldir, light[i].direction.xyz);
                    float ch = light[i].cone.x, cp = light[i].cone.y;
                    spot = rho > ch ? 1.0 : rho <= cp ? 0.0 : pow((rho - cp) / (ch - cp), light[i].att.w);
                }
                float den = light[i].att.x + light[i].att.y * dist + light[i].att.z * dist * dist;
                att = den == 0.0 ? 0.0 : spot / den;
                accA += light[i].ambient.rgb * att;
            } else continue;
            float ndl = dot(ldir, normal);
            accD += sat(ndl) * light[i].diffuse.rgb * att;
            if (specOn) {
                vec3 hv = localViewer ? ldir - nz3(ec.xyz) : vec3(ldir.xy, ldir.z - 1.0);
                hv = nz3(hv);
                float t = dot(normal, hv);
                if (ndl > 0.0 && t > 0.0) accS += (pow(t, mPower.x) * att) * light[i].specular;
            }
        }
        vec4 A = mcs(rsSrc.z, c0, c1, hasC0, hasC1, mAmbient), D = mcs(rsSrc.x, c0, c1, hasC0, hasC1, mDiffuse);
        vec4 S = mcs(rsSrc.y, c0, c1, hasC0, hasC1, mSpecular), E = mcs(rsSrc.w, c0, c1, hasC0, hasC1, mEmissive);
        o0 = vec4(A.rgb * accA + D.rgb * accD + E.rgb, D.a);
        o1 = specOn ? S * accS : c1;
    } else { o0 = c0; o1 = c1; }
    oColor0 = clamp(o0, 0.0, 1.0); oColor1 = clamp(o1, 0.0, 1.0);

    // ---- texture coordinates
    oTex0 = texStage(0u, posT, normal, ec); oTex1 = texStage(1u, posT, normal, ec);
    oTex2 = texStage(2u, posT, normal, ec); oTex3 = texStage(3u, posT, normal, ec);
    oTex4 = texStage(4u, posT, normal, ec); oTex5 = texStage(5u, posT, normal, ec);
    oTex6 = texStage(6u, posT, normal, ec); oTex7 = texStage(7u, posT, normal, ec);

    // ---- fog: factor 1 = unfogged; table fog: eye depth for the pixel epilogue (d9_epilogue.glsl, location 18 .x)
    uint tm = (rsFog.y >= 1u && rsFog.y <= 3u) ? rsFog.y : 0u, vmode = (rsFog.x >= 1u && rsFog.x <= 3u) ? rsFog.x : 0u;
    float f;
    if (rsLight.w == 0u) f = 1.0;
    else if (posT) f = tm != 0u ? aPos.z : sat(c1.a);
    else if (tm != 0u) f = abs(ec.z);
    else if (vmode == 0u) f = sat(c1.a);
    else {
        float d = rsFog.z != 0u ? sqrt(dot(ec.xyz, ec.xyz)) : abs(ec.z);
        float s = fogp.x, e = fogp.y, dn = fogp.z;
        if (vmode == 3u) f = e != s ? (e - d) / (e - s) : (d <= e ? 1.0 : 0.0);
        else if (vmode == 1u) f = exp(-d * dn);
        else f = exp(-(d * dn) * (d * dn));
        f = sat(f);
    }
    oFog = vec4(f, 0.0, 0.0, 0.0);

    // ---- point size (Ss = Vh * Si / sqrt(A + B*De + C*De^2) with POINTSCALEENABLE), clamped to [MIN, MAX]
    float si = (fl & 4u) != 0u ? aPsz.x : point.x, s = si;
    if (rsFog.w != 0u) {
        float de = sqrt(dot(ec.xyz, ec.xyz)), den = point.w + point2.x * de + point2.y * de * de;
        s = den > 0.0 ? point2.z * si / sqrt(den) : uintBitsToFloat(0x7F800000u);
    }
    s = s < point.y ? point.y : s;
    gl_PointSize = s > point.z ? point.z : s;

    // ---- user clip planes (eye space; none on pre-transformed vertices or with CLIPPING off)
    bool clipOn = !posT && rsMisc.y != 0u;
    for (uint i = 0u; i < 6u; i++)
        gl_ClipDistance[i] = (clipOn && ((rsMisc.x >> i) & 1u) != 0u) ? dot(ecRaw, clipPlane[i]) : 1.0;

    // ---- position convention of the translated shaders (render/shaders/README.md "D3D9 conventions")
    oPad10 = vec4(0.0); oPad11 = vec4(0.0); oPad12 = vec4(0.0); oPad13 = vec4(0.0); oPad14 = vec4(0.0); oPad15 = vec4(0.0); oPad16 = vec4(0.0);
    oPad17 = vec4(0.0); oPad19 = vec4(0.0); oPad20 = vec4(0.0); oPad21 = vec4(0.0); oPad22 = vec4(0.0); oPad23 = vec4(0.0); oPad24 = vec4(0.0);
    oPad25 = vec4(0.0); oPad26 = vec4(0.0); oPad27 = vec4(0.0); oPad28 = vec4(0.0); oPad29 = vec4(0.0); oPad30 = vec4(0.0);
    gl_Position = vec4(clip.x + posfix.x * clip.w, (clip.y + posfix.y * clip.w) * posfix.z, clip.z, clip.w);
}
