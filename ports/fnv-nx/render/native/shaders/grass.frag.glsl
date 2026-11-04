#version 460
// grass family, fragment stage (TallGrassShader: tallgrass\1x\p\highDetail.p.hlsl, GRASS2000.pso / GRASS2000TMS.pso).
//   base (GRASS2000):  a = (texture.a > AlphaTestRef ? 1 : 0) x fade
//   tms  (TRANSPARENCYMS, GRASS2000TMS): a = sat(texture.a x 1.75) x fade
//   rgb = mix(texture.rgb x light, fog colour, fog amount); the D3D9 alpha test follows (epilogue permutations).
// Block = the head of NxGrassDraw (n1/veg_families.h), its own name: GL links blocks of one name across stages.
// nx-variants: -= tms=TMS
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec2 inUv;
layout (location = 1) centroid in vec3 inLight;
layout (location = 2) in vec2 inFadeFog;
layout (std140, binding = 1) uniform GrassDrawPs {
    mat4 mvp; vec4 lightDir; vec4 diffuse; vec4 ambient; vec4 scaleMask; vec4 wind; vec4 fadeFog; vec4 fogPow;
    vec4 fogColor;
    vec4 alpha;
} d;
layout (binding = 0) uniform sampler2D tex;
layout (location = 0) out vec4 outColor;
void main() {
    vec4 t = texture(tex, inUv);
#ifdef TMS
    float a = clamp(t.a * 1.75, 0.0, 1.0);
#else
    float a = d.alpha.x - t.a >= 0.0 ? 0.0 : 1.0;
#endif
    vec4 c = vec4(mix(t.rgb * inLight, d.fogColor.rgb, inFadeFog.y), a * inFadeFog.x);
    outColor = nx_d3d9_epilogue(c, 1.0);
}
