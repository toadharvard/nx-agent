#version 460
// fnv-nx L6 first-frame self test: vertex stage. One source for deko3d (uam -> DKSH) and GL (nxgl4_shim_glsl rewrites
// the #version line; no other rule is needed).
// Interface (nxgpu4_selftest.c is the only user):
//   inputs : location 0 = vec2 position in clip space (NxAttr4_Float2, stream 0 offset 0)
//            location 1 = vec2 uv (NxAttr4_Float2, stream 0 offset 8); stride 16
//   outputs: location 0 = vec2 v_uv (to the fragment stage's location 0)
//   UBO    : none (no driver UBO is needed: no clip planes, point size, alpha test or fog)
//   clip convention: +y = top of the image, depth 0..1, z = 0
layout(location = 0) in vec2 pos;
layout(location = 1) in vec2 uv;
layout(location = 0) out vec2 v_uv;
void main() {
    gl_Position = vec4(pos, 0.0, 1.0);
    v_uv = uv;
}
