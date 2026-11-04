#version 460
// particles family, fragment stage (BSParticleShader: PARTICLE.pso): texture x vertex colour; D3D9 alpha test and
// fog from the epilogue.
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec2 inUv;
layout (location = 1) in vec4 inColor;
layout (binding = 0) uniform sampler2D tex;
layout (location = 0) out vec4 outColor;
void main() {
    outColor = nx_d3d9_epilogue(texture(tex, inUv) * inColor, 1.0);
}
