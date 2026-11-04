#version 460
// tile family, fragment stage (TileShader: TILE1000-1002.pso), semantics of the originals:
//   tile (TILE1000):          texture0 * tint
//   tile_alphamap (TILE1001): rgb = texture0.rgb * tint.rgb, a = texture0.a * tint.a * texture1.r (texture 1 = the
//                             alpha map, sampled at the unscrolled uv)
//   tile_vc (TILE1002):       rgb = texture0.rgb * tint.rgb, a = texture0.a * vertex alpha (vertex rgb, tint.a unused)
// nx-variants: -= alphamap=ALPHAMAP vc=VC
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec2 inUv;
layout (location = 1) in vec4 inColor;
layout (location = 2) in float inFog;
layout (location = 3) in vec2 inUv1;
layout (std140, binding = 1) uniform TileDraw { mat4 wvp; vec4 texScroll; vec4 tint; } d;
layout (binding = 0) uniform sampler2D tex;
#ifdef ALPHAMAP
layout (binding = 1) uniform sampler2D alphaMap;
#endif
layout (location = 0) out vec4 outColor;
void main() {
    vec4 t = texture(tex, inUv);
#if defined(ALPHAMAP)
    vec4 c = t * d.tint;
    c.a *= texture(alphaMap, inUv1).r;
#elif defined(VC)
    vec4 c = vec4(t.rgb * d.tint.rgb, t.a * inColor.a);
#else
    vec4 c = t * d.tint;
#endif
    outColor = nx_d3d9_epilogue(c, inFog);
}
