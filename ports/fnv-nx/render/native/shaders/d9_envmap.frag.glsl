#version 460
// d9_envmap, fragment stage (see d9_envmap.vert.glsl). Registers: c1 AmbientColor (w = material alpha, scales the
// reflection), c27 EnvToggles (x vertex colour on, z mask scale, w = 0 mask from the normal map's alpha .. 1 from the
// custom mask); s0 NormalMap, s1 EnvironmentCubeMap, s3 CustomEnvMask.
// rgb = cube(reflect(eye, N)) x mask x alpha (x vertex colour) x (1 - fog), alpha 1; then the D3D9 epilogue.
layout (std140, binding = 0) uniform d9_ps_f { vec4 pc[256]; };
layout (binding = 0) uniform sampler2D NormalMap;
layout (binding = 1) uniform samplerCube EnvironmentCubeMap;
layout (binding = 3) uniform sampler2D CustomEnvMask;

layout(location = 0) in vec4 vColor;
layout(location = 1) in vec4 vUvFog;
layout(location = 2) in vec3 vRow0;
layout(location = 3) in vec3 vRow1;
layout(location = 4) in vec3 vRow2;
layout(location = 5) in vec3 vEye;
layout(location = 0) out vec4 outColor;

#include "d9_epilogue.glsl"

vec3 unit(vec3 v) { float d = dot(v, v); return d > 0.0 ? v * inversesqrt(d) : vec3(0.0); }

void main() {
    vec4 nm = texture(NormalMap, vUvFog.xy);
    vec3 n = unit(2.0 * (nm.rgb - 0.5));
    vec3 N = unit(vec3(dot(n, vRow0), dot(n, vRow1), dot(n, vRow2)));
    vec3 E = unit(vEye);
    vec3 R = 2.0 * dot(N, E) * N - dot(N, N) * E;      // the eye vector mirrored about N
    vec3 env = texture(EnvironmentCubeMap, R).rgb;
    vec4 tog = pc[27];
    float mask = mix(nm.a, texture(CustomEnvMask, vUvFog.xy).r, tog.w) * tog.z;
    vec3 rgb = env * mask * pc[1].w;
    if (tog.x > 0.0) rgb *= vColor.rgb;
    outColor = d9_epilogue(vec4(rgb * vUvFog.z, 1.0));
}
