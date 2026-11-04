#version 460
// decals family, fragment stage (GDECAL.pso): texture 1 (DecalMap) at the clamped uv mapped into the decal's
// atlas rectangle; rgb = texel.rgb x f, a = texel.a x f^2 (f = the faded weight). Block = the head of NxDecalDraw.
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec3 inUvFade;
layout (std140, binding = 1) uniform DecalDrawPs { mat4 mvp; vec4 fade; vec4 offset; } d;
layout (binding = 1) uniform sampler2D decalMap;
layout (location = 0) out vec4 outColor;
void main() {
    vec2 uv = clamp(inUvFade.xy, 0.0, 1.0);
    vec4 t = texture(decalMap, vec2(d.offset.y * uv.x + d.offset.x, d.offset.w * uv.y + d.offset.z));
    float f = inUvFade.z;
    outColor = nx_d3d9_epilogue(vec4(t.rgb * f, t.a * (f * f)), 1.0);
}
