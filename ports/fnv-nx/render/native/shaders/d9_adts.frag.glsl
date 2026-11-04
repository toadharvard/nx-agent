#version 460
// d9_adts, fragment stage (see d9_adts.vert.glsl). Registers: c1 AmbientColor (w = material alpha), c2
// EmittanceColor (w = active light count), c3.. PSLightColor[1 + NPT] (0 the directional light), c19..
// PSLightPosition[NPT] (xyz model space, w radius), c27 Toggles (x vertex colour on, y fog on, z specular power,
// w alpha cut-off); s0 BaseMap (a = alpha), s1 NormalMap (a = gloss).
// Model: N from the normal map; diffuse = sum of clamp(N.L) x colour (x attenuation 1 - (d/r)^2 for points), plus
// ambient, floored at 0, times albedo (base, x vertex colour when toggled); specular = sum of clamp(N.H)^power x
// gloss x colour (x attenuation), faded by clamp(N.L + 0.5) where N.L <= 0.2, times LightData[0].w; then the
// shader's own fog and the D3D9 epilogue.
// nx-variants: p2s=NPT=2,SPECULAR p3=NPT=3 p5=NPT=5
layout (std140, binding = 0) uniform d9_ps_f { vec4 pc[256]; };
layout (binding = 0) uniform sampler2D BaseMap;
layout (binding = 1) uniform sampler2D NormalMap;

layout(location = 0) in vec4 vColor;
layout(location = 1) in vec4 vFog;
layout(location = 2) in vec4 vUvScale;
layout(location = 3) in vec3 vPos;
layout(location = 4) in vec3 vL0;
layout(location = 5) in vec3 vL[NPT];
#ifdef SPECULAR
layout(location = 10) in vec3 vH0;
layout(location = 11) in vec3 vH[NPT];
#endif
layout(location = 0) out vec4 outColor;

#include "d9_epilogue.glsl"

vec3 unit(vec3 v) { float d = dot(v, v); return d > 0.0 ? v * inversesqrt(d) : vec3(0.0); }
// x^p for x in [0, 1] as the hardware's log/exp pair does it (0^p = 0 for p > 0)
float powlog(float x, float p) { return x > 0.0 ? exp2(log2(x) * p) : (p > 0.0 ? 0.0 : p == 0.0 ? 1.0 : 3.4e38); }

void main() {
    vec4 base = texture(BaseMap, vUvScale.xy);
    vec4 nm = texture(NormalMap, vUvScale.xy);
    vec4 amb = pc[1], tog = pc[27];
    if (amb.w < 1.0 && base.a < tog.w) discard;   // the shader's own alpha cut-off for translucent materials

    vec3 N = unit(2.0 * (nm.rgb - 0.5));
    float lights = pc[2].w;
    vec3 diffuse = clamp(dot(N, vL0), 0.0, 1.0) * pc[3].rgb;
#ifdef SPECULAR
    float nl = dot(N, vL0);
    float s = powlog(clamp(dot(N, unit(vH0)), 0.0, 1.0), tog.z) * nm.a;
    if (nl <= 0.2) s *= clamp(nl + 0.5, 0.0, 1.0);
    vec3 spec = s * pc[3].rgb;
#endif
    for (int i = 0; i < NPT; i++) {
        if (!(lights > float(i + 1))) continue;
        vec3 d = (pc[19 + i].xyz - vPos) / pc[19 + i].w;
        float att = 1.0 - clamp(dot(d, d), 0.0, 1.0);
        diffuse += clamp(dot(N, unit(vL[i])), 0.0, 1.0) * pc[4 + i].rgb * att;
#ifdef SPECULAR
        float nli = dot(N, vL[i]);
        float si = powlog(clamp(dot(N, unit(vH[i])), 0.0, 1.0), tog.z) * nm.a * att;
        if (nli <= 0.2) si *= clamp(nli + 0.5, 0.0, 1.0);
        spec += si * pc[4 + i].rgb;
#endif
    }
    vec3 lit = max(diffuse + amb.rgb, vec3(0.0));
    vec3 albedo = tog.x > 0.0 ? base.rgb * vColor.rgb : base.rgb;
    vec3 rgb = lit * albedo;
#ifdef SPECULAR
    rgb += spec * vUvScale.z;
#endif
    if (tog.y > 0.0) rgb = mix(rgb, vFog.rgb, vFog.a);
    outColor = d9_epilogue(vec4(rgb, base.a * amb.w));
}
