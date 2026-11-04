#version 460
// sky family, vertex stage: positions are directions on the sky dome (world space, z up). Block = NxSkyDraw.
layout (location = 0) in vec3 inPos;
layout (location = 1) in vec2 inUv;
layout (std140, binding = 1) uniform SkyDraw { mat4 wvp; vec4 zenith; vec4 horizon; vec4 params; } d;
layout (location = 0) out vec2 outUv;
layout (location = 1) out vec4 outColor;
layout (location = 2) out float outFog;
layout (location = 3) out vec3 outDir;
void main() {
    gl_Position = d.wvp * vec4(inPos, 1.0);
    outDir = inPos;
    outUv = inUv + d.params.zw;
    outColor = vec4(1.0);
    outFog = 1.0;
}
