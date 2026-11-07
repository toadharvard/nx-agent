#version 460
// trees family, leaves, fragment stage (SpeedTreeLeafShader: STLEAF2000/2001[TMS].pso):
//   base: rgb = texture.rgb x T1.rgb, a = texture.a;  fog (STLEAF2001): rgb = mix(that, FogColor, fog amount);
//   tms (TRANSPARENCYMS): a = 2 x texture.a (unclamped, as the original). D3D9 alpha test from the epilogue.
// Block = the head of NxTreeLeafDraw (n1/veg_families.h).
// nx-variants: -= fog=FOG tms=TMS fogtms=FOG,TMS
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec4 inUvFog;
layout (location = 1) in vec4 inLight;
layout (std140, binding = 1) uniform TreeLeafDrawPs {
    mat4 mvp; vec4 ambient; vec4 diffuse; vec4 diffusePt; vec4 lightVec; vec4 lightPos; vec4 bbRight; vec4 bbUp;
    vec4 rock; vec4 rustle; vec4 leafLight; vec4 fog;
    vec4 fogColor;
} d;
layout (binding = 0) uniform sampler2D tex;
layout (location = 0) out vec4 outColor;
void main() {
    vec4 t = texture(tex, inUvFog.xy);
    vec3 c = t.rgb * inLight.rgb;
#ifdef FOG
    c = mix(c, d.fogColor.rgb, inUvFog.z);
#endif
#ifdef TMS
    float a = t.a + t.a;
#else
    float a = t.a;
#endif
    outColor = nx_d3d9_epilogue(vec4(c, a), 1.0);
}
