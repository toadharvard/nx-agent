// fnv-nx native renderer: D3D9 pixel epilogue for the d9_* families, which take the engine's D3D9 constant registers
// directly (layout contract render/shaders/include/fnvnx_dk_layout.h: UBO 0 = c[256], UBO 2 = driver constants).
// Same semantics as the glsldk profile's epilogue (render/shaders/mojoshader.patch), so a d9_* family can replace the
// translated program of a draw one for one: alpha test (D3DCMPFUNC from the driver UBO, 0 = off), then fixed-function
// fog for ps < 3.0 (mode 1 vertex factor at varying 18, 2 exp, 3 exp2, 4 linear on window z).
layout (std140, binding = 2) uniform d9_driver { vec4 d9FogColor; vec4 d9Fog; ivec4 d9Alpha; vec4 d9AlphaRef; vec4 d9Texbem[16]; };
layout(location = 18) in highp vec4 d9FogIn;

vec4 d9_epilogue(vec4 c) {
    int f = d9Alpha.x;
    if (f != 0) {
        float a = c.a, r = d9AlphaRef.x;
        // D3DCMPFUNC 1 NEVER .. 8 ALWAYS as a LESS/EQUAL/GREATER bit set (f - 1), unordered operands pass NOTEQUAL/ALWAYS
        uint m = (a < r ? 1u : 0u) | (a == r ? 2u : 0u) | (a > r ? 4u : 0u);
        uint g = uint(f - 1);
        if (!((g & m) != 0u || g >= 7u || (m == 0u && g == 5u))) discard;
    }
    if (d9Fog.x != 0.0) {
        float z = gl_FragCoord.z, k;
        if (d9Fog.x == 1.0) k = d9FogIn.x;
        else if (d9Fog.x == 2.0) k = exp(-z * d9Fog.w);
        else if (d9Fog.x == 3.0) k = exp(-(z * d9Fog.w) * (z * d9Fog.w));
        else k = (d9Fog.z - z) / (d9Fog.z - d9Fog.y);
        c.rgb = mix(d9FogColor.rgb, c.rgb, clamp(k, 0.0, 1.0));
    }
    return c;
}
