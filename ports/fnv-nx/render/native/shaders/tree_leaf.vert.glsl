#version 460
// trees family, leaves, vertex stage (SpeedTreeLeafShader: STLEAF000-003.vso). Block = NxTreeLeafDraw
// (n1/veg_families.h); per-draw products folded on the CPU (veg_tree.cpp).
// Per vertex: POSITION, NORMAL, TEXCOORD0, BLENDINDICES (location 2) = (wind weight, wind matrix register offset,
// leaf corner index + rock/rustle phase + shade in the fraction, leaf scale).
// The leaf corner (LeafBase[int(bi.z)] x scale) is rocked about x, then the billboard axes are rustled about z;
// the result is blended towards the wind matrix by the wind weight.
// Variants: base (STLEAF000), fog = FOG (STLEAF001), pt = one point light (STLEAF002), ptfog (STLEAF003).
// nx-variants: -= fog=FOG pt=PT ptfog=PT,FOG
layout (location = 0) in vec4 inPos;
layout (location = 2) in vec4 inBi;
layout (location = 3) in vec3 inNormal;
layout (location = 6) in vec2 inUv;
layout (std140, binding = 1) uniform TreeLeafDraw {
    mat4 mvp;           // ModelViewProj (c0-c3)
    vec4 ambient;       // AmbientColor (c5)
    vec4 diffuse;       // DiffColor (c6) x SunDimmer.x (c10)
    vec4 diffusePt;     // DiffColorPt (c7)
    vec4 lightVec;      // LightVector (c11)
    vec4 lightPos;      // LightPos (c12): xyz, w = 1 / range
    vec4 bbRight;       // BillboardRight (c13)
    vec4 bbUp;          // BillboardUp (c14)
    vec4 rock;          // RockParams (c15): x scale, y phase, z amplitude
    vec4 rustle;        // RustleParams (c16)
    vec4 leafLight;     // LeafLighting (c17): y = corner-normal weight
    vec4 fog;           // FogParam (c8): x far, y = 1 / range, z power
    vec4 fogColor;      // FogColor (c9)
    vec4 wind[16];      // WindMatrices (c18-c33), rows
    vec4 leafBase[48];  // LeafBase (c34-c81)
};
layout (location = 0) out vec4 outUvFog;             // xy uv, z fog amount (FOG)
layout (location = 1) out vec4 outLight;             // T1 of the original
// sin of x reduced to [-pi, pi) as the original programs do (fract-based, exact for large phases)
float rsin(float t) { return sin(fract(t * 0.499999583 + 0.5) * 6.28318548 - 3.14159274); }
vec2 rcossin(float a) { float r = fract(a * 0.159154937 + 0.5) * 6.28318548 - 3.14159274; return vec2(cos(r), sin(r)); }
void main() {
    vec2 ru = rcossin(rsin(inBi.z * 0.020833334 + rustle.y) * rustle.z * rustle.x);   // about z
    vec2 ro = rcossin(rsin(inBi.z * 0.020833334 + rock.y) * rock.z * rock.x);         // about x
    vec4 c = inBi.w * leafBase[int(inBi.z)];
    float y = ro.x * c.y - ro.y * c.z;
    float z = ro.y * c.y + ro.x * c.z;
    vec4 R = vec4(ru.x * bbRight.x - ru.y * bbRight.y, ru.y * bbRight.x + ru.x * bbRight.y, bbRight.zw);
    vec4 U = vec4(ru.x * bbUp.x - ru.y * bbUp.y, ru.y * bbUp.x + ru.x * bbUp.y, bbUp.zw);
    vec4 p = y * R + z * U + inPos;
    int w = int(inBi.y);
    vec4 pw = vec4(dot(wind[w], p), dot(wind[w + 1], p), dot(wind[w + 2], p), dot(wind[w + 3], p));
    vec4 P = mix(p, pw, inBi.x);
    gl_Position = mvp * P;
    vec3 n = normalize(c.xyz * (inversesqrt(dot(c, c)) * leafLight.y) + inNormal);
    float shade = fract(inBi.z);
    vec4 L = (clamp(dot(n, lightVec.xyz), 0.0, 1.0) * diffuse + ambient) * vec4(shade, shade, shade, 1.0);
#ifdef PT
    vec3 d = lightPos.xyz - P.xyz;
    float dl = length(d);
    float a = clamp(dl * lightPos.w, 0.0, 1.0);
    L += clamp(dot(n, d / dl), 0.0, 1.0) * diffusePt * (1.0 - a * a);
#endif
    outLight = L;
    float f = 0.0;
#ifdef FOG
    float g = 1.0 - clamp((fog.x - length(gl_Position.xyz)) * fog.y, 0.0, 1.0);
    f = clamp(exp2(log2(g) * fog.z), 0.0, 1.0);
#endif
    outUvFog = vec4(inUv, f, 0.0);
}
