#version 460
// glyph atlas is A8 (swizzle 000R): coverage in alpha
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec2 inUv;
layout (location = 1) in vec4 inColor;
layout (location = 2) in float inFog;
layout (binding = 0) uniform sampler2D tex;
layout (location = 0) out vec4 outColor;
void main() { outColor = nx_d3d9_epilogue(vec4(inColor.rgb, inColor.a * texture(tex, inUv).a), inFog); }
