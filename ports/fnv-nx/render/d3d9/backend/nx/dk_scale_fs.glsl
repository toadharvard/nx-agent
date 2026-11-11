#version 460
// fnv-nx L6 deko3d: point scale, fragment stage. Built three times by build_scale_dksh.sh with a "#define KIND n" line after #version:
// KIND 0 = sampler2D (a 2D source), 1 = sampler2DArray (a cube source, all six faces), 2 = sampler3D (a 3D source), as gl4_scale.c's P_C2D/P_CARR/P_C3D.
// Rule: destination texel d takes source texel floor((d + 0.5) * sw / dw) = ((2d + 1) * sw) / (2 dw) per axis, in integers (the reference line is
//   ivec2 t = src.xy + ((2 * (ivec2(gl_FragCoord.xy) - dst.xy) + 1) * src.zw) / (2 * dst.zw);).
// The hardware has no integer divide (uam emulates it in float with a loss of precision), so the exact quotient is made here:
//   n = (2 (f - dst) + 1) * sw <= (2 * 16384 + 1) * 16384 < 2^30 and m = 2 dw <= 2^15, f = floor(gl_FragCoord) is exact below 2^23;
//   q0 = int(float(n) / float(m)): float(n) has relative error <= 2^-24, float(m) is exact, the division at most 2^-21 more, and the true quotient
//   is <= 2^14, so |q0 - n/m| < 2^-6 + 1 truncation and q0 is within 1 of floor(n/m);  r = n - q0 * m is exact in int32 (|q0 * m| < 2^31);
//   one correction each way (r < 0 -> q0 - 1, r >= m -> q0 + 1) lands on floor(n/m) = n div m. tests/l6/dkscale checks this model against the
//   integer rule for every size pair of the stated range, with the estimate pushed to both ends of the error bound.
// Sources are the raw texels (identity swizzle view, nearest fetch with texelFetch: no filtering, no sRGB conversion).
// The gamma ramp (lzm.x == 1): per colour channel i = floor(clamp(c, 0, 1) * 255 + 0.5), out = float(ramp[ch][i] >> 8) / 255, alpha unchanged. ramp[] holds the
// 768 high bytes (ramp[ch][i] >> 8, made by the CPU) packed four to a word, channel-major: byte k of the 768 is word k / 4, bits (k & 3) * 8.
layout(std140, binding = 15) uniform U { ivec4 src; ivec4 dst; ivec4 lzm; uvec4 ramp[48]; };   // lzm = (mode, layer, 0, 0)
#if KIND == 0
layout(binding = 31) uniform sampler2D s;
#elif KIND == 1
layout(binding = 31) uniform sampler2DArray s;
#else
layout(binding = 31) uniform sampler3D s;
#endif
layout(location = 0) out vec4 o;

int divx(int n, int m) {
    int q = int(float(n) / float(m));
    int r = n - q * m;
    q = r < 0 ? q - 1 : q;
    r = n - q * m;
    return r >= m ? q + 1 : q;
}
float gamma_of(float c, int ch) {
    uint i = uint(floor(clamp(c, 0.0, 1.0) * 255.0 + 0.5));
    uint k = uint(ch) * 256u + i;
    return float((ramp[k >> 4][(k >> 2) & 3u] >> ((k & 3u) * 8u)) & 255u) / 255.0;
}
void main() {
    ivec2 f = ivec2(gl_FragCoord.xy) - dst.xy;
    ivec2 t = src.xy + ivec2(divx((2 * f.x + 1) * src.z, 2 * dst.z), divx((2 * f.y + 1) * src.w, 2 * dst.w));
#if KIND == 0
    vec4 c = texelFetch(s, t, 0);
#else
    vec4 c = texelFetch(s, ivec3(t, lzm.y), 0);
#endif
    if (lzm.x == 1) c.rgb = vec3(gamma_of(c.r, 0), gamma_of(c.g, 1), gamma_of(c.b, 2));
    o = c;
}
