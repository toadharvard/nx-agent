#version 460
// tile family, fragment stage (TileShader: tile.p.hlsl x {-, ALPHAMAP, VC}). Provisional semantics, to be checked
// against S6b's translation of the original TILE1000-1002 pixel shaders (parity harness, RENDER_NATIVE.md §4):
//   tile:          texture * tint
//   tile_alphamap: (tint.rgb, tint.a * texture.a)   (the texture only masks)
//   tile_vc:       texture * vertex colour * tint
// nx-variants: -= alphamap=ALPHAMAP vc=VC
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec2 inUv;
layout (location = 1) in vec4 inColor;
layout (location = 2) in float inFog;
layout (std140, binding = 1) uniform TileDraw { mat4 wvp; vec4 texScroll; vec4 tint; } d;
layout (binding = 0) uniform sampler2D tex;
layout (location = 0) out vec4 outColor;
void main() {
    vec4 t = texture(tex, inUv);
#if defined(ALPHAMAP)
    vec4 c = vec4(d.tint.rgb, d.tint.a * t.a);
#elif defined(VC)
    vec4 c = t * inColor * d.tint;
#else
    vec4 c = t * d.tint;
#endif
    outColor = nx_d3d9_epilogue(c, inFog);
}
