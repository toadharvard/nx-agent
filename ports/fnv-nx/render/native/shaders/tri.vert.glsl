#version 460
// N0 raw triangles: clip-space position and an RGBA8 colour straight from the vertex buffer.
layout (location = 0) in vec4 inPos;
layout (location = 1) in vec4 inColor;
layout (location = 0) out vec2 outUv;
layout (location = 1) out vec4 outColor;
layout (location = 2) out float outFog;
void main() {
    gl_Position = inPos;
    outUv = vec2(0.0);
    outColor = inColor;
    outFog = 1.0;
}
