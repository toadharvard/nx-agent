#version 460
// precipitation family, fragment stage (PRECIP000/001.pso): rgb = sat(texture.rgb + AmbientColor.rgb),
// a = texture.a x fade. Block = NxPrecipDraw.
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec3 inUvFade;
layout (std140, binding = 1) uniform PrecipDrawPs { mat4 wvp; vec4 eye; vec4 velocity; vec4 minPos; vec4 maxPos;
                                                    vec4 params; vec4 cameraUp; vec4 ambient; } d;
layout (binding = 0) uniform sampler2D tex;
layout (location = 0) out vec4 outColor;
void main() {
    vec4 t = texture(tex, inUvFade.xy);
    outColor = nx_d3d9_epilogue(vec4(clamp(t.rgb + d.ambient.rgb, 0.0, 1.0), t.a * inUvFade.z), 1.0);
}
