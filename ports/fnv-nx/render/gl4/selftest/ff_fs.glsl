#version 460
// fnv-nx L6 first-frame self test: fragment stage (see ff_vs.glsl).
// Interface: input location 0 = vec2 v_uv; sampler2D `tex` at binding 0 (fragment slot 0, NxOp_BindTextures stage 1
// first slot 0; the GL backend maps it to unit 16); output location 0 = vec4 colour (render target 0). UBO: none.
layout(location = 0) in vec2 v_uv;
layout(binding = 0) uniform sampler2D tex;
layout(location = 0) out vec4 o;
void main() {
    o = texture(tex, v_uv);
}
