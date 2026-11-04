#version 460
// grass family, fragment stage, drop-in for the original (veg_dk.glsl): GRASS2000.pso, tms = GRASS2000TMS.pso.
// Same arithmetic as grass.frag.glsl on the original's varyings: L = T4 + T5 (centroid), rgb = mix(texture x L,
// D0.rgb, D0.a), a = (texture.a > AlphaTestRef (c3.x) ? 1 : 0) x T5.w (tms: sat(1.75 texture.a) x T5.w).
// nx-variants: -= tms=TMS
#define NX_VEG_FRAG 1
#include "veg_dk.glsl"
layout (binding = 0) uniform sampler2D diffuseMap;
layout (location = 0) in vec4 iD0;
layout (location = 2) in vec4 iT0;
layout (location = 6) centroid in vec4 iT4;
layout (location = 7) centroid in vec4 iT5;
void main() {
    vec4 t = texture(diffuseMap, iT0.xy);
#ifdef TMS
    float a = clamp(t.a * 1.75, 0.0, 1.0);
#else
    float a = c[3].x - t.a >= 0.0 ? 0.0 : 1.0;
#endif
    veg_write(vec4(mix(t.rgb * (iT5.rgb + iT4.rgb), iD0.rgb, iD0.a), a * iT5.w));
}
