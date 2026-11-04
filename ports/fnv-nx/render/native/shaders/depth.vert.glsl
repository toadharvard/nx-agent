#version 460
// depth family, vertex stage. Block = NxDepthDraw.
layout (location = 0) in vec3 inPos;
layout (std140, binding = 1) uniform DepthDraw { mat4 wvp; } d;
layout (location = 0) out vec4 outClip;
void main() { gl_Position = d.wvp * vec4(inPos, 1.0); outClip = gl_Position; }
