#version 460
// d9_adts: ShadowLightShader's per-pixel "ADTS" lighting (ambient + one directional light + NPT point lights,
// diffuse texture, tangent-space normal map, optional specular), vertex stage. A d9_* family reads the engine's
// D3D9 constant registers (fnvnx_dk_layout.h) and replaces the translated program of a draw one for one
// (capture/d3d9_families.h classifies the original by its constant table and declarations; RENDER_NATIVE.md §4.5).
// Registers (the same in all 16 shader packages, checked per program by the classifier):
//   c0-c3 ModelViewProj (rows), c14 FogParam (x near, y range, z power), c15 FogColor, c16 EyePosition (SPECULAR),
//   c17 fvars0 (z = active light count), c25.. LightData[1 + NPT] (0: direction towards the light, w = specular
//   scale; 1..: point light position, w = radius), all in model space.
// Inputs (fnvnx_dk attribute locations): position 0, normal 3, colour 4, uv 6, tangent 14, binormal 15.
// nx-variants: p2s=NPT=2,SPECULAR p3=NPT=3 p5=NPT=5
layout(location = 0) in vec4 inPos;
layout(location = 3) in vec4 inNormal;
layout(location = 4) in vec4 inColor;
layout(location = 6) in vec4 inUv;
layout(location = 14) in vec4 inTangent;
layout(location = 15) in vec4 inBinormal;

layout (std140, binding = 0) uniform d9_vs_f { vec4 vc[256]; };
layout (std140, binding = 2) uniform d9_vs_driver { vec4 posfix; vec4 reserved[3]; };

layout(location = 0) out vec4 vColor;      // vertex colour (saturated, as a vs < 3.0 colour output)
layout(location = 1) out vec4 vFog;        // rgb fog colour, a fog factor (saturated)
layout(location = 2) out vec4 vUvScale;    // xy uv, z specular scale (LightData[0].w)
layout(location = 3) out vec3 vPos;        // model-space position (point light attenuation in the fragment)
layout(location = 4) out vec3 vL0;         // tangent-space direction to the directional light (unit)
layout(location = 5) out vec3 vL[NPT];     // 5..9: tangent-space directions to the point lights (0 when inactive)
#ifdef SPECULAR
layout(location = 10) out vec3 vH0;     // tangent-space half vectors (10, 11..15)
layout(location = 11) out vec3 vH[NPT];
#endif

vec3 unit(vec3 v) { float d = dot(v, v); return d > 0.0 ? v * inversesqrt(d) : vec3(0.0); }

void main() {
    vec4 p = inPos;
    gl_Position = vec4(dot(vc[0], p), dot(vc[1], p), dot(vc[2], p), dot(vc[3], p));
    mat3 toTangent = transpose(mat3(inTangent.xyz, inBinormal.xyz, inNormal.xyz));

    // active lights: the directional one plus fvars0.z - 1 points, truncated, at most 1 + NPT
    float n = trunc(min(float(NPT + 1), vc[17].z));
    vec3 L0 = vc[25].xyz;
    vL0 = unit(toTangent * L0);
#ifdef SPECULAR
    vec3 V = unit(vc[16].xyz - p.xyz);
    vH0 = unit(toTangent * unit(V + L0));
#endif
    for (int i = 0; i < NPT; i++) {
        float on = n > float(i + 1) ? 1.0 : 0.0;
        vec3 Li = unit(vc[26 + i].xyz - p.xyz);
        vL[i] = (toTangent * Li) * on;
#ifdef SPECULAR
        vH[i] = (toTangent * unit(Li + V)) * on;
#endif
    }

    // vertex fog: (1 - clamp((near - distance) / range))^power, from the camera-space distance
    float t = clamp((vc[14].x - length(gl_Position.xyz)) / vc[14].y, 0.0, 1.0);
    float g = 1.0 - t;
    float fog = g > 0.0 ? exp2(log2(g) * vc[14].z) : (vc[14].z > 0.0 ? 0.0 : 1.0);
    vFog = clamp(vec4(vc[15].xyz, fog), 0.0, 1.0);
    vColor = clamp(inColor, 0.0, 1.0);
    vUvScale = vec4(inUv.xy, vc[25].w, 0.0);
    vPos = p.xyz;

    gl_Position.xy += posfix.xy * gl_Position.w;
    gl_Position.y *= posfix.z;
}
