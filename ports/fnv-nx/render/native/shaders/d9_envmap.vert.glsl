#version 460
// d9_envmap: ShadowLightShader's environment-map pass (cube reflection of the normal-mapped surface, masked by the
// normal map's alpha or a custom mask), vertex stage. A d9_* family: it reads the engine's D3D9 constant registers
// (fnvnx_dk_layout.h) and replaces the translated program one for one (capture/d3d9_families.h, RENDER_NATIVE.md §4.5).
// Registers: c0-c3 ModelViewProj (rows), c8-c10 ObjToCubeSpace (rows, model -> cube-map space), c14 FogParam
// (x near, y range, z power). Inputs: position 0, normal 3, colour 4, uv 6, tangent 14, binormal 15.
// The tangent frame is passed as the three rows of cube-space x [0.1 T, 0.1 B, N]: the original flattens the normal
// map's tilt to a tenth for the reflection.
layout(location = 0) in vec4 inPos;
layout(location = 3) in vec4 inNormal;
layout(location = 4) in vec4 inColor;
layout(location = 6) in vec4 inUv;
layout(location = 14) in vec4 inTangent;
layout(location = 15) in vec4 inBinormal;

layout (std140, binding = 0) uniform d9_vs_f { vec4 vc[256]; };
layout (std140, binding = 2) uniform d9_vs_driver { vec4 posfix; vec4 reserved[3]; };

layout(location = 0) out vec4 vColor;      // rgb vertex colour (saturated)
layout(location = 1) out vec4 vUvFog;      // xy uv, z = 1 - fog factor (the reflection fades into the fog)
layout(location = 2) out vec3 vRow0;       // tangent -> cube space, rows
layout(location = 3) out vec3 vRow1;
layout(location = 4) out vec3 vRow2;
layout(location = 5) out vec3 vEye;        // cube-space direction from the surface to the eye (unit)

vec3 unit(vec3 v) { float d = dot(v, v); return d > 0.0 ? v * inversesqrt(d) : vec3(0.0); }

void main() {
    vec4 p = inPos;
    gl_Position = vec4(dot(vc[0], p), dot(vc[1], p), dot(vc[2], p), dot(vc[3], p));
    vec3 t = 0.1 * inTangent.xyz, b = 0.1 * inBinormal.xyz, n = inNormal.xyz;
    vRow0 = vec3(dot(t, vc[8].xyz), dot(b, vc[8].xyz), dot(n, vc[8].xyz));
    vRow1 = vec3(dot(t, vc[9].xyz), dot(b, vc[9].xyz), dot(n, vc[9].xyz));
    vRow2 = vec3(dot(t, vc[10].xyz), dot(b, vc[10].xyz), dot(n, vc[10].xyz));
    vEye = unit(-vec3(dot(vc[8], p), dot(vc[9], p), dot(vc[10], p)));

    float f = clamp((vc[14].x - length(gl_Position.xyz)) / vc[14].y, 0.0, 1.0);
    vUvFog = vec4(inUv.xy, 1.0 - pow(abs(1.0 - f), vc[14].z), 0.0);
    vColor = vec4(clamp(inColor.rgb, 0.0, 1.0), 1.0);

    gl_Position.xy += posfix.xy * gl_Position.w;
    gl_Position.y *= posfix.z;
}
