#version 460
// nx-variants: 3=NX_TAPS=3 5=NX_TAPS=5 7=NX_TAPS=7 9=NX_TAPS=9 11=NX_TAPS=11 13=NX_TAPS=13 15=NX_TAPS=15 bp3=NX_TAPS=3,NX_BP bp5=NX_TAPS=5,NX_BP bp7=NX_TAPS=7,NX_BP bp9=NX_TAPS=9,NX_BP bp11=NX_TAPS=11,NX_BP bp13=NX_TAPS=13,NX_BP bp15=NX_TAPS=15,NX_BP
// fnv-nx native renderer, render-land, post family: separable blur (ISBLUR<n>.pso) and bright-pass blur
// (ISBPBLUR<n>.pso, NX_BP), n taps. Registers: c0 BlurScale (xy), c1.. BlurOffsets (xy offset, z weight).
//   blur:        rgb = sum w_i * tex(uv_i).rgb, alpha = the last tap's alpha
//   bright-pass: each tap max(rgb - HDRParam.x, 0) * HDRParam.y (HDRParam = c17); alpha = r + g + b of AvgLum
//                (sampler 1) at the pixel
#define NX_IS_FRAG 1
#include "is_dk.glsl"
layout (binding = 0) uniform sampler2D src0;
#ifdef NX_BP
layout (binding = 1) uniform sampler2D avgLum;
#endif
layout(location = 2) in vec4 uv0;
void main() {
#ifdef NX_BP
    vec2 k = c[0].xy;
    vec3 sum = vec3(0.0);
    for (int i = 0; i < NX_TAPS; i++) {
        vec3 t = texture(src0, c[1 + i].xy * k + uv0.xy).rgb;
        sum += max(t - c[17].x, 0.0) * c[17].y * c[1 + i].z;
    }
    vec3 l = texture(avgLum, uv0.xy).rgb;
    is_write(vec4(sum, l.r + l.g + l.b));
#else
    is_write(is_taps(src0, uv0.xy, 0, 1, NX_TAPS));
#endif
}
