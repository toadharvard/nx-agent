#version 460
// lit family (SM3 path), vertex stage. Block = NxLitDraw (nx/families.h).
// nx-variants: -= vc=VC skin=SKIN
layout (location = 0) in vec3 inPos;
layout (location = 1) in vec2 inUv;
layout (location = 2) in vec4 inColor;
layout (location = 3) in vec3 inNormal;
#ifdef SKIN
layout (location = 4) in vec4 inWeights;   // unorm8 x 4
layout (location = 5) in vec4 inIndices;   // unorm8 x 4: palette indices / 255
#endif
struct NxLight { vec4 posRange; vec4 color; };
layout (std140, binding = 1) uniform LitDraw {
    mat4 wvp; mat4 world; vec4 ambient; vec4 diffuse; vec4 specular; vec4 eye; uvec4 count; NxLight lights[8];
#ifdef SKIN
    vec4 bones[96];   // NX_SKIN_MAX_BONES x 3 rows (nx/skin.h); world = identity, wvp = view-projection
#endif
} d;
layout (location = 0) out vec2 outUv;
layout (location = 1) out vec4 outColor;
layout (location = 2) out float outFog;
layout (location = 3) out vec3 outWorld;
layout (location = 4) out vec3 outNormal;
void main() {
    vec3 pos = inPos, nrm = inNormal;
#ifdef SKIN
    ivec4 bi = ivec4(inIndices * 255.0 + 0.5);
    vec3 sp = vec3(0.0), sn = vec3(0.0);
    for (int k = 0; k < 4; k++) {
        vec4 r0 = d.bones[3 * bi[k]], r1 = d.bones[3 * bi[k] + 1], r2 = d.bones[3 * bi[k] + 2];
        sp += inWeights[k] * vec3(dot(r0, vec4(pos, 1.0)), dot(r1, vec4(pos, 1.0)), dot(r2, vec4(pos, 1.0)));
        sn += inWeights[k] * vec3(dot(r0.xyz, nrm), dot(r1.xyz, nrm), dot(r2.xyz, nrm));
    }
    pos = sp; nrm = sn;
#endif
    gl_Position = d.wvp * vec4(pos, 1.0);
    outWorld = (d.world * vec4(pos, 1.0)).xyz;
    outNormal = mat3(d.world) * nrm;
    outUv = inUv;
#ifdef VC
    outColor = inColor;
#else
    outColor = vec4(1.0);
#endif
    outFog = 1.0;
}
