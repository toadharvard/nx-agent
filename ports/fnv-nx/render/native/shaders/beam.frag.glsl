#version 460
// beam family, fragment stage (BEAM.pso): the texture with its alpha x the beam alpha.
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec3 inUvAlpha;
layout (binding = 0) uniform sampler2D tex;
layout (location = 0) out vec4 outColor;
void main() {
    vec4 t = texture(tex, inUvAlpha.xy);
    outColor = nx_d3d9_epilogue(vec4(t.rgb, t.a * inUvAlpha.z), 1.0);
}
