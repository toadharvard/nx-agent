#version 460
// menubg family, vertex stage: one full-screen triangle (gl_VertexID, no vertex buffer). uv follows D3D9 pixel
// centres (uv = x / W at pixel x) plus d.uvXform.zw (half a texel: texel centres) after scaling by d.uvXform.xy.
layout (std140, binding = 1) uniform MenuBgDraw { vec4 luma; vec4 params; vec4 uvXform; } d;
layout (location = 0) out vec2 outUv;
void main() {
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));   // (0,0) (2,0) (0,2)
    outUv = p * d.uvXform.xy + d.uvXform.zw;
    gl_Position = vec4(p.x * 2.0 - 1.0, 1.0 - p.y * 2.0, 0.0, 1.0);
}
