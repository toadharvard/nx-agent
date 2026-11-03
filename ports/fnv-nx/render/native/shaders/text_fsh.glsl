#version 460
// glyph atlas is A8 (swizzle 000R): coverage in alpha
layout (location = 0) in vec2 inUv;
layout (location = 1) in vec4 inColor;
layout (binding = 0) uniform sampler2D tex;
layout (location = 0) out vec4 outColor;
void main() { outColor = vec4(inColor.rgb, inColor.a * texture(tex, inUv).a); }
