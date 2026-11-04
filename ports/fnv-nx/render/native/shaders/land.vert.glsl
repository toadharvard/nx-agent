#version 460
// land family, vertex stage. Block = NxLandDraw.
layout (location = 0) in vec3 inPos;
layout (location = 1) in vec2 inUv;
layout (location = 2) in vec4 inColor;
layout (location = 3) in vec3 inWeights;
layout (std140, binding = 1) uniform LandDraw { mat4 wvp; vec4 uvScale; } d;
layout (location = 0) out vec2 outUv;
layout (location = 1) out vec4 outColor;
layout (location = 2) out float outFog;
layout (location = 3) out vec3 outWeights;
void main() {
    gl_Position = d.wvp * vec4(inPos, 1.0);
    outUv = inUv * d.uvScale.xy;
    outColor = inColor;
    outFog = 1.0;
    outWeights = inWeights;
}
