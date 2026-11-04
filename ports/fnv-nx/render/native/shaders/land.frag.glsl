#version 460
// land family, fragment stage: base texture blended towards layers 1-3 by the vertex weights, times the vertex
// colour: c = (base * (1 - w1 - w2 - w3) + l1 * w1 + l2 * w2 + l3 * w3) * vc. LIT (LANDAD: SLS1040.pso) multiplies by
// clamp(clamp(N.Lt) * lightColor + ambient), N = 2 * normalMap.rgb - 1 (texture 4), Lt from the vertex stage.
// nx-variants: -= lit=LIT
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec2 inUv;
layout (location = 1) in vec4 inColor;
layout (location = 2) in float inFog;
layout (location = 3) in vec3 inWeights;
layout (location = 4) in vec3 inLight;
layout (std140, binding = 1) uniform LandDraw { mat4 wvp; vec4 uvScale; vec4 lightDir; vec4 lightColor; vec4 ambient; } d;
layout (binding = 0) uniform sampler2D base;
layout (binding = 1) uniform sampler2D layer1;
layout (binding = 2) uniform sampler2D layer2;
layout (binding = 3) uniform sampler2D layer3;
#ifdef LIT
layout (binding = 4) uniform sampler2D normalMap;
#endif
layout (location = 0) out vec4 outColor;
void main() {
    vec3 w = clamp(inWeights, 0.0, 1.0);
    vec4 c = texture(base, inUv) * (1.0 - w.x - w.y - w.z) + texture(layer1, inUv) * w.x +
             texture(layer2, inUv) * w.y + texture(layer3, inUv) * w.z;
    vec3 rgb = c.rgb * inColor.rgb;
#ifdef LIT
    vec3 n = texture(normalMap, inUv).rgb * 2.0 - 1.0;
    float ndl = clamp(dot(n, inLight), 0.0, 1.0);
    rgb *= clamp(ndl * d.lightColor.rgb + d.ambient.rgb, vec3(0.0), vec3(1.0));
#endif
    outColor = nx_d3d9_epilogue(vec4(rgb, inColor.a), inFog);
}
