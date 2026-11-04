#version 460
// nx-variants: cin=NX_CIN
// fnv-nx native renderer, render-land, post family: HDR blend-in with the cinematic grade (ISHDRBLENDINSHADERCIN.pso).
// Samplers: 0 the HDR scene (Src0, texcoord 0), 1 the bloom (DestBlend, texcoord 1). Registers: c1 HDRParam,
// c2 BlurScale (z = output alpha), c19 Cinematic (x saturation, y grey point, z contrast, w brightness), c20 Tint
// (rgb colour, w amount), c22 Fade (rgb colour, w amount).
//   e = max(scene.a, HDRParam.x); rgb = max(scene.rgb * 0.5 / e, 0) + bloom.rgb * HDRParam.x / e
//   l = luma(rgb) (Rec. 601); rgb = mix(l, rgb, saturation); rgb = mix(rgb, l * tint, tint amount)
//   rgb = (rgb * brightness - grey) * contrast + grey; out = mix(rgb, fade, fade amount)
#define NX_IS_FRAG 1
#include "is_dk.glsl"
layout (binding = 0) uniform sampler2D src0;
layout (binding = 1) uniform sampler2D bloom;
layout(location = 2) in vec4 uv0;
layout(location = 3) in vec4 uv1;
void main() {
    vec3 b = texture(bloom, uv1.xy).rgb;
    vec4 s = texture(src0, uv0.xy);
    float inv = is_rcp(max(s.a, c[1].x));
    vec3 rgb = b * (inv * c[1].x) + max(s.rgb * (inv * 0.5), 0.0);
#ifdef NX_CIN
    float l = dot(rgb, vec3(0.299, 0.587, 0.114));
    vec3 sat = mix(vec3(l), rgb, c[19].x);
    vec3 tinted = c[20].w * (l * c[20].rgb - sat) + sat;
    vec3 graded = c[19].z * (c[19].w * tinted - c[19].y) + c[19].y;
    rgb = mix(graded, c[22].rgb, c[22].w);
#endif
    is_write(vec4(rgb, c[2].z));
}
