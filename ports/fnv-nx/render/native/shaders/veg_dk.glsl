// fnv-nx native renderer, render-veg: the drop-in ("dk") side of the vegetation/effect families, written against
// the binding contract of the translated shaders (render/shaders/include/fnvnx_dk_layout.h), so a native shader
// takes the slot of the translated original on the D3D9 path (nx_render --native, shaders/native.tsv) and pairs with
// either a native or a translated other stage: UBO 0 = the D3D9 float register file, UBO 2 = the driver block,
// sampler binding = sampler register, attribute and varying locations by usage (COLORn at n, TEXCOORDn at 2 + n,
// fog at 18), the original's varyings unchanged. Define NX_VEG_FRAG before the include in fragment shaders.
// Register files are named vc (vertex) / c (fragment): GL links non-instanced block members by name.
const float VEG_FLT_HALF = uintBitsToFloat(0x7effffffu);
float veg_rcp(float x) {      // D3D9 rcp: +-0 and overflow clamped to the largest half-range float
    if (x == 0.0) return (floatBitsToUint(x) & 0x80000000u) != 0u ? -VEG_FLT_HALF : VEG_FLT_HALF;
    return clamp(1.0 / x, -VEG_FLT_HALF, VEG_FLT_HALF);
}
#ifdef NX_VEG_FRAG
layout(std140, binding = 0) uniform VegRegsF { vec4 c[256]; };
layout(std140, binding = 2) uniform VegDriverF {
    vec4 drvFogColor;
    vec4 drvFog;            // x mode (0 off, 1 vertex, 2 exp, 3 exp2, 4 linear), y start, z end, w density
    ivec4 drvAlpha;         // x D3DCMPFUNC (0 = alpha test off)
    vec4 drvAlphaRef;       // x in 0..1
    vec4 drvTexbem[16];
};
layout(location = 18) in vec4 vegFogIn;
layout(location = 0) out vec4 vegOut;
// the D3D9 output stage of the translated programs: alpha test, then fixed-function fog (ps < 3.0)
void veg_write(vec4 o) {
    if (drvAlpha.x != 0) {
        float a = o.a, r = drvAlphaRef.x;
        int f = drvAlpha.x;
        bool pass = f == 8 || (f == 2 && a < r) || (f == 3 && a == r) || (f == 4 && a <= r) || (f == 5 && a > r) ||
                    (f == 6 && a != r) || (f == 7 && a >= r) || f > 8;
        if (!pass) discard;
    }
    if (drvFog.x != 0.0) {
        float z = gl_FragCoord.z, k;
        if (drvFog.x == 1.0) k = vegFogIn.x;
        else if (drvFog.x == 2.0) k = exp(-z * drvFog.w);
        else if (drvFog.x == 3.0) k = exp(-(z * drvFog.w) * (z * drvFog.w));
        else k = (drvFog.z - z) / (drvFog.z - drvFog.y);
        o.rgb = mix(drvFogColor.rgb, o.rgb, clamp(k, 0.0, 1.0));
    }
    vegOut = o;
}
#else
layout(std140, binding = 0) uniform VegRegsV { vec4 vc[256]; };
layout(std140, binding = 2) uniform VegDriverV { vec4 posfix; vec4 reserved[3]; };
layout(location = 18) out vec4 vegFogOut;     // vs < 3.0 without oFog: factor 1 (D3D fog is off for these passes)
vec4 veg_rows(int r, vec4 p) { return vec4(dot(vc[r], p), dot(vc[r + 1], p), dot(vc[r + 2], p), dot(vc[r + 3], p)); }
// gl_Position plus the D3D9 half pixel and y flip of the driver block
void veg_position(vec4 p) {
    gl_Position = p;
    gl_Position.xy += posfix.xy * p.w;
    gl_Position.y *= posfix.z;
    vegFogOut = vec4(1.0);
}
#endif
