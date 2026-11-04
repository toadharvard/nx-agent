#version 460
// land family, vertex stage. Block = NxLandDraw. LIT (LANDAD: SLS1025.vso): the light direction (model space) goes
// to the tangent frame, Lt = (T.L, B.L, N.L), for the normal map term in the fragment stage.
// nx-variants: -= lit=LIT
layout (location = 0) in vec3 inPos;
layout (location = 1) in vec2 inUv;
layout (location = 2) in vec4 inColor;
layout (location = 3) in vec3 inWeights;
#ifdef LIT
layout (location = 4) in vec3 inNormal;
layout (location = 5) in vec3 inTangent;
layout (location = 6) in vec3 inBinormal;
#endif
layout (std140, binding = 1) uniform LandDraw { mat4 wvp; vec4 uvScale; vec4 lightDir; vec4 lightColor; vec4 ambient; } d;
layout (location = 0) out vec2 outUv;
layout (location = 1) out vec4 outColor;
layout (location = 2) out float outFog;
layout (location = 3) out vec3 outWeights;
layout (location = 4) out vec3 outLight;
void main() {
    gl_Position = d.wvp * vec4(inPos, 1.0);
    outUv = inUv * d.uvScale.xy;
    outColor = inColor;
    outFog = 1.0;
    outWeights = inWeights;
#ifdef LIT
    outLight = vec3(dot(inTangent, d.lightDir.xyz), dot(inBinormal, d.lightDir.xyz), dot(inNormal, d.lightDir.xyz));
#else
    outLight = vec3(0.0);
#endif
}
