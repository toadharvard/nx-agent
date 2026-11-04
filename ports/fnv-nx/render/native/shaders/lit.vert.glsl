#version 460
// lit family (SM3 path), vertex stage. Block = NxLitDraw (nx/families.h).
// nx-variants: -= vc=VC
layout (location = 0) in vec3 inPos;
layout (location = 1) in vec2 inUv;
layout (location = 2) in vec4 inColor;
layout (location = 3) in vec3 inNormal;
struct NxLight { vec4 posRange; vec4 color; };
layout (std140, binding = 1) uniform LitDraw {
    mat4 wvp; mat4 world; vec4 ambient; vec4 diffuse; vec4 specular; vec4 eye; uvec4 count; NxLight lights[8];
} d;
layout (location = 0) out vec2 outUv;
layout (location = 1) out vec4 outColor;
layout (location = 2) out float outFog;
layout (location = 3) out vec3 outWorld;
layout (location = 4) out vec3 outNormal;
void main() {
    gl_Position = d.wvp * vec4(inPos, 1.0);
    outWorld = (d.world * vec4(inPos, 1.0)).xyz;
    outNormal = mat3(d.world) * inNormal;
    outUv = inUv;
#ifdef VC
    outColor = inColor;
#else
    outColor = vec4(1.0);
#endif
    outFog = 1.0;
}
