#version 460
// blood family, fragment stage (BS.pso; FLARE = BSF.pso). Block = NxBloodDraw.
//   BS:  a = map0.a x z x Alpha.x; rgb = mix(a (map0.rgb - 1) + 1, map1.rgb (at T1), a); out alpha 1
//   BSF: out = map0.r x map1 (at T0) x Alpha.x
// nx-variants: -= flare=FLARE
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec4 inUv;
layout (location = 1) in vec2 inUv1;
layout (std140, binding = 1) uniform BloodDrawPs { mat4 mvp; vec4 lightLoc; vec4 ctrl; vec4 alpha; } d;
layout (binding = 0) uniform sampler2D map0;
layout (binding = 1) uniform sampler2D map1;
layout (location = 0) out vec4 outColor;
void main() {
    vec4 t0 = texture(map0, inUv.xy);
#ifdef FLARE
    vec4 c = t0.x * texture(map1, inUv.xy) * d.alpha.x;
#else
    vec4 t1 = texture(map1, inUv1);
    float a = t0.a * (inUv.z * d.alpha.x);
    vec4 c = vec4(mix(a * (t0.rgb - 1.0) + 1.0, t1.rgb, a), 1.0);
#endif
    outColor = nx_d3d9_epilogue(c, 1.0);
}
