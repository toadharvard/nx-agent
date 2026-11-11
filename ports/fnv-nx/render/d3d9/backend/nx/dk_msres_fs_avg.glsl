#version 460
// fnv-nx L6 deko3d: multisample resolve, colour average (NXDK_MSRES_AVG). Every texel of the destination is the arithmetic mean of the n samples of the
// same texel of the source (gl_FragCoord is the image coordinate: OriginUpperLeft, viewport = the whole target): the sum is taken in float in sample order
// 0 .. n-1 and scaled by u.f.x = 1 / n, which the CPU makes exactly (n is 2, 4 or 8, a power of two, so 1 / n and the product are exact). Every renderable format
// of the backend is unorm, snorm or float (dk_fmt.c has no integer format), so the float sum is the average the reference (gl4_blit.c, a nearest blit of the
// multisampled image) leaves to the driver. Raw texels: the default view is the non-sRGB one, identity swizzle.
layout(std140, binding = 15) uniform U { uvec4 u; vec4 f; };   // u = (samples, stencil bit mask, 0, 0), f = (1 / samples, 0, 0, 0)
layout(binding = 31) uniform sampler2DMS s;
layout(location = 0) out vec4 o;
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    vec4 a = vec4(0.0);
    for (int i = 0; i < int(u.x); i++) a += texelFetch(s, p, i);
    o = a * f.x;
}
