#version 460
// lit family (SM3 path), fragment stage: one pass, up to 8 lights.
//   colour = tex * vc * (ambient + sum_i lambert_i * atten_i * light_i) * diffuse (alpha = tex.a * vc.a * diffuse.a)
//   lit_spec adds sum_i pow(max(N.H, 0), power) * atten_i * light_i * specular.rgb * tex.a (alpha then = diffuse.a)
// Static light permutations (RENDER_NATIVE.md §4.4): lit[_spec]_d<D>p<P> with NX_LIT_DIR = D (light 0 directional)
// and NX_LIT_POINTS = P point lights after it, unrolled with no per-light branch; nx::Renderer orders the lights and
// pads with zero-colour lights. Without them, the dynamic loop over count.x lights of either type.
// Precision (§4.4): positions and the specular power stay highp; unit vectors, attenuation and the colour sums are
// marked mediump (fp16-safe within 1/255), which uam's TGSI path does not use yet.
// nx-variants: -= spec=SPECULAR d0p1=NX_LIT_DIR=0,NX_LIT_POINTS=1 d0p2=NX_LIT_DIR=0,NX_LIT_POINTS=2 d0p4=NX_LIT_DIR=0,NX_LIT_POINTS=4 d0p8=NX_LIT_DIR=0,NX_LIT_POINTS=8 d1p0=NX_LIT_DIR=1,NX_LIT_POINTS=0 d1p1=NX_LIT_DIR=1,NX_LIT_POINTS=1 d1p2=NX_LIT_DIR=1,NX_LIT_POINTS=2 d1p4=NX_LIT_DIR=1,NX_LIT_POINTS=4 d1p8=NX_LIT_DIR=1,NX_LIT_POINTS=7 spec_d0p1=SPECULAR,NX_LIT_DIR=0,NX_LIT_POINTS=1 spec_d0p2=SPECULAR,NX_LIT_DIR=0,NX_LIT_POINTS=2 spec_d0p4=SPECULAR,NX_LIT_DIR=0,NX_LIT_POINTS=4 spec_d0p8=SPECULAR,NX_LIT_DIR=0,NX_LIT_POINTS=8 spec_d1p0=SPECULAR,NX_LIT_DIR=1,NX_LIT_POINTS=0 spec_d1p1=SPECULAR,NX_LIT_DIR=1,NX_LIT_POINTS=1 spec_d1p2=SPECULAR,NX_LIT_DIR=1,NX_LIT_POINTS=2 spec_d1p4=SPECULAR,NX_LIT_DIR=1,NX_LIT_POINTS=4 spec_d1p8=SPECULAR,NX_LIT_DIR=1,NX_LIT_POINTS=7
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

mediump vec3 nxLight, nxSpec;
// one light: l = unit direction to the light, att = attenuation
void nx_light(mediump vec3 n, mediump vec3 v, mediump vec3 l, mediump float att, vec3 color) {
    nxLight += max(dot(n, l), 0.0) * att * color;
#ifdef SPECULAR
    mediump vec3 h = normalize(l + v);
    nxSpec += pow(max(dot(n, h), 0.0), d.specular.w) * att * color;   // pow stays highp in effect (power up to ~100)
#endif
}
void nx_point(mediump vec3 n, mediump vec3 v, int i) {
    vec3 dv = d.lights[i].posRange.xyz - inWorld;                       // highp: world positions
    float d2 = dot(dv, dv);
    float r = d.lights[i].posRange.w;
    nx_light(n, v, dv * inversesqrt(max(d2, 1e-12)), clamp(1.0 - d2 / (r * r), 0.0, 1.0), d.lights[i].color.rgb);
}
void main() {
    vec4 t = texture(tex, inUv);
    mediump vec3 n = normalize(inNormal);
    mediump vec3 v = normalize(d.eye.xyz - inWorld);
    nxLight = d.ambient.rgb; nxSpec = vec3(0.0);
#ifdef NX_LIT_POINTS
#if NX_LIT_DIR
    nx_light(n, v, -normalize(d.lights[0].posRange.xyz), 1.0, d.lights[0].color.rgb);
#endif
    for (int i = 0; i < NX_LIT_POINTS; i++) nx_point(n, v, NX_LIT_DIR + i);
#else
    for (uint i = 0u; i < d.count.x && i < 8u; i++) {
        if (d.lights[i].color.w > 0.5) nx_light(n, v, -normalize(d.lights[i].posRange.xyz), 1.0, d.lights[i].color.rgb);
        else nx_point(n, v, int(i));
    }
#endif
    vec4 c = vec4(t.rgb * inColor.rgb * nxLight * d.diffuse.rgb, t.a * inColor.a * d.diffuse.a);
#ifdef SPECULAR
    c.rgb += nxSpec * d.specular.rgb * t.a;
    c.a = inColor.a * d.diffuse.a;
#endif
    outColor = nx_d3d9_epilogue(c, inFog);
}
