#version 460
// fnv-nx L6 deko3d: point scale, vertex stage. One triangle that covers the whole target from gl_VertexID (no vertex buffers), as gl4_scale.c.
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
