#version 460
// depth family, vertex stage. Block = NxDepthDraw (SKIN: NxDepthSkinDraw, palette as in lit.vert.glsl).
// nx-variants: -= skin=SKIN at=ALPHATEST
layout (location = 0) in vec3 inPos;
layout (location = 1) in vec2 inUv;
#ifdef SKIN
layout (location = 4) in vec4 inWeights;
layout (location = 5) in vec4 inIndices;
layout (std140, binding = 1) uniform DepthDraw { mat4 wvp; vec4 bones[96]; } d;
#else
layout (std140, binding = 1) uniform DepthDraw { mat4 wvp; } d;
#endif
layout (location = 0) out vec4 outClip;
layout (location = 1) out vec2 outUv;
void main() {
    vec3 pos = inPos;
#ifdef SKIN
    ivec4 bi = ivec4(inIndices * 255.0 + 0.5);
    vec3 sp = vec3(0.0);
    for (int k = 0; k < 4; k++)
        sp += inWeights[k] * vec3(dot(d.bones[3 * bi[k]], vec4(pos, 1.0)), dot(d.bones[3 * bi[k] + 1], vec4(pos, 1.0)),
                                  dot(d.bones[3 * bi[k] + 2], vec4(pos, 1.0)));
    pos = sp;
#endif
    gl_Position = d.wvp * vec4(pos, 1.0);
    outClip = gl_Position;
    outUv = inUv;
}
