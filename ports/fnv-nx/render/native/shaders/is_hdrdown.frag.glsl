#version 460
// nx-variants: 4=NX_TAPS=4 adapt4=NX_TAPS=4,NX_ADAPT
// fnv-nx native renderer, render-land, post family: HDR downsample (ISHDRDOWN4.pso) and downsample + eye
// adaptation (ISHDRDS4ADAPT.pso, NX_ADAPT). Registers: c0 TimingData, c1 HDRParam, c2 BlurScale (xy scale, z the
// output alpha), c3.. BlurOffsets.
//   down:  rgb = weighted taps, alpha = BlurScale.z
//   adapt: k = 1 - |HDRParam.z| ^ TimingData.z; c = mix(previous (sampler 1), taps, k); the length of c is clamped
//          to [0.01, HDRParam.w] (rgb rescaled), alpha = BlurScale.z
#define NX_IS_FRAG 1
#include "is_dk.glsl"
layout (binding = 0) uniform sampler2D src0;
#ifdef NX_ADAPT
layout (binding = 1) uniform sampler2D prevLum;
#endif
layout(location = 2) in vec4 uv0;
void main() {
    vec3 rgb = is_taps(src0, uv0.xy, 2, 3, NX_TAPS).rgb;
#ifdef NX_ADAPT
    float k = 1.0 - pow(abs(c[1].z), c[0].z);
    vec3 m = mix(texture(prevLum, uv0.xy).rgb, rgb, k);
    float len = max(0.01, length(m));
    rgb = m * (min(len, c[1].w) * is_rcp(len));
#endif
    is_write(vec4(rgb, c[2].z));
}
