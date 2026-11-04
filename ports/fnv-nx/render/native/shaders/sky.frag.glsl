#version 460
// sky family, fragment stage (SKY.pso: the interpolated vertex colour); sky_clouds blends texture 0 over it
// (alpha x cloud alpha, params.y).
// nx-variants: -= clouds=CLOUDS
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec2 inUv;
layout (location = 1) in vec4 inColor;
layout (location = 2) in float inFog;
layout (std140, binding = 1) uniform SkyDraw { mat4 wvp; vec4 blend0; vec4 blend1; vec4 blend2; vec4 params; } d;
layout (binding = 0) uniform sampler2D clouds;
layout (location = 0) out vec4 outColor;
void main() {
    vec4 c = inColor;
#ifdef CLOUDS
    vec4 t = texture(clouds, inUv);
    c.rgb = mix(c.rgb, t.rgb, t.a * d.params.y);
#endif
    outColor = nx_d3d9_epilogue(c, inFog);
}
