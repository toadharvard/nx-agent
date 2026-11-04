#version 460
// land family, fragment stage: base texture blended towards layers 1-3 by the vertex weights, times the vertex
// colour: c = (base * (1 - w1 - w2 - w3) + l1 * w1 + l2 * w2 + l3 * w3) * vc.
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec2 inUv;
layout (location = 1) in vec4 inColor;
layout (location = 2) in float inFog;
layout (location = 3) in vec3 inWeights;
layout (binding = 0) uniform sampler2D base;
layout (binding = 1) uniform sampler2D layer1;
layout (binding = 2) uniform sampler2D layer2;
layout (binding = 3) uniform sampler2D layer3;
layout (location = 0) out vec4 outColor;
void main() {
    vec3 w = clamp(inWeights, 0.0, 1.0);
    vec4 c = texture(base, inUv) * (1.0 - w.x - w.y - w.z) + texture(layer1, inUv) * w.x +
             texture(layer2, inUv) * w.y + texture(layer3, inUv) * w.z;
    outColor = nx_d3d9_epilogue(vec4(c.rgb * inColor.rgb, inColor.a), inFog);
}
