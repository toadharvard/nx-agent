#version 460
// lit family (SM3 path), fragment stage: one pass, up to 8 lights.
//   colour = tex * vc * (ambient + sum_i lambert_i * atten_i * light_i) * diffuse (alpha = tex.a * vc.a * diffuse.a)
//   lit_spec adds sum_i pow(max(N.H, 0), power) * atten_i * light_i * specular.rgb * tex.a (alpha then = diffuse.a)
// nx-variants: -= spec=SPECULAR
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec2 inUv;
layout (location = 1) in vec4 inColor;
layout (location = 2) in float inFog;
layout (location = 3) in vec3 inWorld;
layout (location = 4) in vec3 inNormal;
struct NxLight { vec4 posRange; vec4 color; };
layout (std140, binding = 1) uniform LitDraw {
    mat4 wvp; mat4 world; vec4 ambient; vec4 diffuse; vec4 specular; vec4 eye; uvec4 count; NxLight lights[8];
} d;
layout (binding = 0) uniform sampler2D tex;
layout (location = 0) out vec4 outColor;
void main() {
    vec4 t = texture(tex, inUv);
    vec3 n = normalize(inNormal);
    vec3 v = normalize(d.eye.xyz - inWorld);
    vec3 light = d.ambient.rgb, spec = vec3(0.0);
    for (uint i = 0u; i < d.count.x && i < 8u; i++) {
        vec3 l; float att;
        if (d.lights[i].color.w > 0.5) { l = -normalize(d.lights[i].posRange.xyz); att = 1.0; }
        else {
            vec3 dv = d.lights[i].posRange.xyz - inWorld;
            float dist = length(dv);
            l = dv / max(dist, 1e-6);
            float q = dist / d.lights[i].posRange.w;
            att = clamp(1.0 - q * q, 0.0, 1.0);
        }
        light += max(dot(n, l), 0.0) * att * d.lights[i].color.rgb;
#ifdef SPECULAR
        vec3 h = normalize(l + v);
        spec += pow(max(dot(n, h), 0.0), d.specular.w) * att * d.lights[i].color.rgb;
#endif
    }
    vec4 c = vec4(t.rgb * inColor.rgb * light * d.diffuse.rgb, t.a * inColor.a * d.diffuse.a);
#ifdef SPECULAR
    c.rgb += spec * d.specular.rgb * t.a;
    c.a = inColor.a * d.diffuse.a;
#endif
    outColor = nx_d3d9_epilogue(c, inFog);
}
