#version 460
// grass family, vertex stage (TallGrassShader, GRASS passes: tallgrass\1x\v\lowDetail.v.hlsl, GRASS2000-2003.vso).
// Block = NxGrassDraw (n1/veg_families.h). Per-draw constants are folded on the CPU (veg_grass.cpp); the per-instance
// data is the engine's InstanceData array (VS c20+, one vec4 per instance: xyz position, w = scale percent in the
// integer part and the shade in the fraction); the instance index is the integer part of TEXCOORD1.x.
// Variants: base (GRASS2000: normal from the instance's packed fraction), vl = VERTLIT (GRASS2001: vertex normal),
// slope = FIT_TO_SLOPE (GRASS2002: blade frame aligned to the packed normal), vlslope (GRASS2003).
// NX_DK (the _dk variants): the drop-in for the original program (veg_dk.glsl): constants from the register file,
// the original's varyings (D0 = (FogColor, fog), T0 = uv, T4 = shade x AmbientColor, T5 = (diffuse, fade)).
// nx-variants: -= vl=VERTLIT slope=FIT_TO_SLOPE vlslope=VERTLIT,FIT_TO_SLOPE dk=NX_DK vl_dk=VERTLIT,NX_DK slope_dk=FIT_TO_SLOPE,NX_DK vlslope_dk=VERTLIT,FIT_TO_SLOPE,NX_DK
#define NX_GRASS_MAX_INSTANCES 236
layout (location = 0) in vec3 inPos;
#ifdef VERTLIT
layout (location = 3) in vec3 inNormal;
#endif
layout (location = 4) in vec4 inColor;
layout (location = 6) in vec2 inUv;
layout (location = 7) in float inInstance;
#ifdef NX_DK
#include "veg_dk.glsl"
#define lightDir vc[0]
#define diffuse (vc[1] * vc[7].x)
#define ambient vc[6]
#define scaleMask (vc[2] * 0.01)
#define wind vec4(vc[4].xy * vc[4].z, vc[4].w, 0.0)
#define fadeFog vec4(vc[5].z, veg_rcp(vc[5].w), vc[15].x, veg_rcp(vc[15].y))
#define fogPow vc[15].zzzz
#define INST(i) vc[20 + (i)]
#define MVP(p) veg_rows(9, p)
layout (location = 0) out vec4 oD0;
layout (location = 2) out vec4 oT0;
layout (location = 6) centroid out vec4 oT4;
layout (location = 7) centroid out vec4 oT5;
#else
#define INST(i) inst[i]
#define MVP(p) (mvp * (p))
layout (std140, binding = 1) uniform GrassDraw {
    mat4 mvp;           // ModelViewProj (c9-c12)
    vec4 lightDir;      // DiffuseDir (c0), xyz
    vec4 diffuse;       // DiffuseColor (c1) x AddlParams.x (c7), rgb
    vec4 ambient;       // AmbientColor (c6)
    vec4 scaleMask;     // ScaleMask (c2) x 0.01, xyz
    vec4 wind;          // xy = WindData.xy x WindData.z, z = WindData.w (phase)
    vec4 fadeFog;       // x = AlphaParam.z, y = 1 / AlphaParam.w, z = FogParam.x, w = 1 / FogParam.y
    vec4 fogPow;        // x = FogParam.z
    vec4 fogColor;      // FogColor (c14), saturated; fragment stage
    vec4 alpha;         // x = AlphaTestRef (PS c3.x); fragment stage
    vec4 inst[NX_GRASS_MAX_INSTANCES];
};
layout (location = 0) out vec2 outUv;
layout (location = 1) centroid out vec3 outLight;    // T4.rgb + T5.rgb of the original (the PS uses only the sum)
layout (location = 2) out vec2 outFadeFog;           // x = distance fade (T5.w), y = fog amount (D0.a)
#endif
void main() {
    vec4 I = INST(int(inInstance));
    // distance fade of the instance: 1 - sat((|MVP (I.xyz, 1)| - z) / w), the clip-space 4-vector length
    float fade = 1.0 - clamp((length(MVP(vec4(I.xyz, 1.0))) - fadeFog.x) * fadeFog.y, 0.0, 1.0);
    // wind: sin(((x + y) / 128 + phase) reduced to [-pi, pi)) x vertex alpha^2
    float ph = fract(((I.x + I.y) * 0.0078125 + wind.z) * 0.159154937 + 0.5) * 6.28318548 - 3.14159274;
    vec2 sway = wind.xy * (sin(ph) * inColor.a * inColor.a);
    vec4 f = fract(I);
    vec3 s = inPos * (I.w * scaleMask.xyz + 1.0);
#if !defined(VERTLIT) || defined(FIT_TO_SLOPE)
    vec3 n = f.xyz * 2.0 - 1.0;                      // packed terrain normal (fraction - 0.5) x 2
#endif
#ifdef FIT_TO_SLOPE
    // blade frame: t = normalize(n x e) with e the axis of n's smallest component (x when |n.x| is the smallest),
    // b = t x n; p = b s.x + t s.y + n s.z
    vec3 t = normalize((abs(n.y) >= abs(n.x) && abs(n.z) >= abs(n.x)) ? vec3(0.0, -n.z, n.y) : vec3(-n.z, 0.0, n.x));
    vec3 b = cross(t, n);
    vec3 p = b * s.x + t * s.y + n * s.z;
#else
    vec3 p = s;
#endif
    vec4 P = vec4(p + vec3(sway, 0.0) + I.xyz, 1.0);
    vec4 clip = MVP(P);
#if defined(VERTLIT) && defined(FIT_TO_SLOPE)
    vec3 N = b * inNormal.x + t * inNormal.y + n * inNormal.z;   // the vertex normal in the blade frame
#elif defined(VERTLIT)
    vec3 N = inNormal;
#else
    vec3 N = n;
#endif
    float shade = f.w * 0.75 + 0.25;
    float fog = 1.0 - clamp((fadeFog.z - length(clip.xyz)) * fadeFog.w, 0.0, 1.0);
    float fogAmount = clamp(exp2(log2(fog) * fogPow.x), 0.0, 1.0);
    vec3 dif = inColor.rgb * diffuse.rgb * clamp(dot(lightDir.xyz, N), 0.0, 1.0);
#ifdef NX_DK
    veg_position(clip);
    oD0 = clamp(vec4(vc[14].rgb, fogAmount), 0.0, 1.0);
    oT0 = vec4(inUv, 0.0, 0.0);
    oT4 = shade * ambient;
    oT5 = vec4(shade * dif, fade);
#else
    gl_Position = clip;
    outLight = shade * (ambient.rgb + dif);
    outFadeFog = vec2(fade, fogAmount);
    outUv = inUv;
#endif
}
