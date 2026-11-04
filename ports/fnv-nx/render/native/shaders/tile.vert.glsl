#version 460
// tile family, vertex stage (TileShader: tile.v.hlsl x {-, TEXSCROLL, VC}). Block = NxTileDraw (nx/families.h).
// nx-variants: -= scroll=TEXSCROLL vc=VC vc_scroll=VC,TEXSCROLL
layout (location = 0) in vec3 inPos;
layout (location = 1) in vec2 inUv;
#ifdef VC
layout (location = 2) in vec4 inColor;
#endif
// A block both stages declare needs an instance name (GL links anonymous-block members by name across stages).
layout (std140, binding = 1) uniform TileDraw { mat4 wvp; vec4 texScroll; vec4 tint; } d;
layout (location = 0) out vec2 outUv;
layout (location = 1) out vec4 outColor;
layout (location = 2) out float outFog;
void main() {
    gl_Position = d.wvp * vec4(inPos, 1.0);
    outUv = inUv;
#ifdef TEXSCROLL
    outUv += d.texScroll.xy;
#endif
#ifdef VC
    outColor = inColor;
#else
    outColor = vec4(1.0);
#endif
    outFog = 1.0;
}
