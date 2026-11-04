#version 460
// beam family, vertex stage (BeamShader: BEAM.vso). Block = NxBeamDraw (n1/veg_families.h).
// clip = MVP (POSITION.x x fVars0.y, POSITION.y x fVars0.x, 0, POSITION.w); uv = (TEXCOORD0.y x uScale,
// TEXCOORD0.x) with uScale = fVars0.x / (fVars0.z x fVars0.y) (folded on the CPU); alpha = sat(fVars0.w).
layout (location = 0) in vec4 inPos;
layout (location = 6) in vec2 inUv;
layout (std140, binding = 1) uniform BeamDraw {
    mat4 mvp;           // ModelViewProj (c0-c3)
    vec4 vars0;         // fVars0 (c4), w saturated
    vec4 uScale;        // x = fVars0.x / (fVars0.z x fVars0.y)
};
layout (location = 0) out vec3 outUvAlpha;
void main() {
    gl_Position = mvp * vec4(inPos.x * vars0.y, inPos.y * vars0.x, 0.0, inPos.w);
    outUvAlpha = vec3(inUv.y * uScale.x, inUv.x, vars0.w);
}
