// fnv-nx native renderer, render-land: common part of the image-space (post) family, written against the binding
// contract of the translated shaders (render/shaders/include/fnvnx_dk_layout.h), so a native image-space shader
// drops into the same slot as the translated original: UBO 0 = the D3D9 float register file c[256], UBO 2 = the
// driver block, sampler binding = D3D9 sampler register, varyings by usage (TEXCOORDn at 2 + n, fog at 18).
// Fragment side only (define NX_IS_FRAG before the include).
// block names differ per stage: GL links stages by block name, and the bindings are per stage
layout (std140, binding = 0) uniform IsRegsF { vec4 c[256]; };
#ifdef NX_IS_FRAG
layout (std140, binding = 2) uniform IsDriver {
    vec4 drvFogColor;
    vec4 drvFog;            // x mode (0 off, 1 vertex, 2 exp, 3 exp2, 4 linear), y start, z end, w density
    ivec4 drvAlpha;         // x D3DCMPFUNC (0 = alpha test off)
    vec4 drvAlphaRef;       // x in 0..1
    vec4 drvTexbem[16];
};
layout(location = 18) in vec4 isFogIn;      // "layout(location = N)" exactly: nx_render matches VS outputs to PS inputs by this text
layout(location = 0) out vec4 isOut;

// D3D9 rcp: 1/x with +-0 and overflow clamped to the largest half-range float, as the shader model does
const float IS_FLT_HALF = uintBitsToFloat(0x7effffffu);
float is_rcp(float x) {
    if (x == 0.0) return (floatBitsToUint(x) & 0x80000000u) != 0u ? -IS_FLT_HALF : IS_FLT_HALF;
    return clamp(1.0 / x, -IS_FLT_HALF, IS_FLT_HALF);
}

// D3D9 output stage: alpha test, then fog; both off for image space in practice, kept for state left on by the
// world passes. NX_IS_NO_EPILOGUE (the __e0 variant) drops it.
void is_write(vec4 o) {
#ifndef NX_IS_NO_EPILOGUE
    if (drvAlpha.x != 0) {
        float a = o.a, r = drvAlphaRef.x;
        int f = drvAlpha.x;
        bool pass = f == 8 || (f == 2 && a < r) || (f == 3 && a == r) || (f == 4 && a <= r) || (f == 5 && a > r) ||
                    (f == 6 && a != r) || (f == 7 && a >= r) || f > 8;
        if (!pass) discard;
    }
    if (drvFog.x != 0.0) {
        float z = gl_FragCoord.z, k;
        if (drvFog.x == 1.0) k = isFogIn.x;
        else if (drvFog.x == 2.0) k = exp(-z * drvFog.w);
        else if (drvFog.x == 3.0) k = exp(-(z * drvFog.w) * (z * drvFog.w));
        else k = (drvFog.z - z) / (drvFog.z - drvFog.y);
        o.rgb = mix(drvFogColor.rgb, o.rgb, clamp(k, 0.0, 1.0));
    }
#endif
    isOut = o;
}

// Weighted taps: tap i at uv + BlurOffsets[i].xy * BlurScale.xy, weight BlurOffsets[i].z (registers scale and
// base..base+n-1). Returns the weighted rgb sum; the last tap's alpha in .a (the effects that keep an alpha use it).
vec4 is_taps(sampler2D s, vec2 uv, int scale, int base, int n) {
    vec2 k = c[scale].xy;
    vec3 sum = vec3(0.0);
    float a = 0.0;
    for (int i = 0; i < n; i++) {
        vec4 t = texture(s, c[base + i].xy * k + uv);
        sum += t.rgb * c[base + i].z;
        a = t.a;
    }
    return vec4(sum, a);
}
#endif
