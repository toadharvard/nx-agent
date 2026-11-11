#version 460
// fnv-nx L6 deko3d: multisample resolve, stencil of sample 0 (NXDK_MSRES_S0_STENCIL). Maxwell has no shader stencil output, so the stencil plane of the
// destination is cleared to 0 and written one bit per draw: draw b (u.y = 1 << b) discards the texel unless bit b of the stencil of sample 0 is set, and the
// stencil test (Always, op Replace, reference = write mask = 1 << b) then sets exactly that bit of the destination. Eight draws cover the eight bits, so the
// destination stencil equals the source stencil of sample 0 for every value. The source is sampled through a DkDsSource_Stencil view.
layout(std140, binding = 15) uniform U { uvec4 u; vec4 f; };
layout(binding = 31) uniform usampler2DMS s;
void main() {
    if ((texelFetch(s, ivec2(gl_FragCoord.xy), 0).r & u.y) == 0u) discard;
}
