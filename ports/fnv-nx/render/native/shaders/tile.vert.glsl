#version 460
// tile family, vertex stage (TileShader: TILE1000-1002.vso). Block = NxTileDraw (nx/families.h).
//   TILE1000 (-):          uv as is
//   TILE1001 (TEXSCROLL):  uv * TexScroll.zw + TexScroll.xy for texture 0; texture 1 (ALPHAMAP) keeps the plain uv
//   TILE1002 (VC):         passes the vertex colour
// Checked against the S6b translation of the originals (V6n tests/v6n/claims/c69_families.py, local .sdp).
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
layout (location = 3) out vec2 outUv1;
void main() {
    gl_Position = d.wvp * vec4(inPos, 1.0);
#ifdef TEXSCROLL
    outUv = inUv * d.texScroll.zw + d.texScroll.xy;
#else
    outUv = inUv;
#endif
    outUv1 = inUv;
#ifdef VC
    outColor = inColor;
#else
    outColor = vec4(1.0);
#endif
    outFog = 1.0;
}
