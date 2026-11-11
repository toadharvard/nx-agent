#version 460
// fnv-nx L6 deko3d: multisample resolve, depth of sample 0 (NXDK_MSRES_S0_DEPTH): gl_FragDepth = the depth plane of sample 0 of the same texel, written into the
// depth plane of the single-sample destination of the same format (depth test Always, depth write on). The source is sampled through a DkDsSource_Depth view.
// Exact: ZF32 is a float copy; Zn (n = 16, 24) stores k / (2^n - 1), the sampler returns f = fl(k / N) with |f - k / N| <= 2^-(n+1), so f * N is within
// N * 2^-(n+1) < 0.5 of k and the round-to-nearest unorm conversion of the depth write gives k back.
layout(std140, binding = 15) uniform U { uvec4 u; vec4 f; };
layout(binding = 31) uniform sampler2DMS s;
void main() {
    gl_FragDepth = texelFetch(s, ivec2(gl_FragCoord.xy), 0).r;
}
