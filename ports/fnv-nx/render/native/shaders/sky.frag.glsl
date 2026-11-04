#version 460
// sky family, fragment stage: gradient by elevation; sky_clouds blends texture 0 over it (alpha x cloud alpha).
// nx-variants: -= clouds=CLOUDS
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec2 inUv;
layout (location = 1) in vec4 inColor;
layout (location = 2) in float inFog;
layout (location = 3) in vec3 inDir;
layout (std140, binding = 1) uniform SkyDraw { mat4 wvp; vec4 zenith; vec4 horizon; vec4 params; } d;
layout (binding = 0) uniform sampler2D clouds;
layout (location = 0) out vec4 outColor;
void main() {
    float e = max(normalize(inDir).z, 0.0);
    float k = smoothstep(0.0, 1.0, pow(e, 1.0 / d.params.x));
    vec4 c = vec4(mix(d.horizon.rgb, d.zenith.rgb, k), 1.0);
#ifdef CLOUDS
    vec4 t = texture(clouds, inUv);
    c.rgb = mix(c.rgb, t.rgb, t.a * d.params.y);
#endif
    outColor = nx_d3d9_epilogue(c, inFog);
}
