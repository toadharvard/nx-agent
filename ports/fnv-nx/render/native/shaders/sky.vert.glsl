#version 460
// sky family, vertex stage (SKY.vso): the sky mesh's vertex colour weights three blend colours,
//   rgb = B0.rgb * vc.r + B1.rgb * vc.g + B2.rgb * vc.b,  a = vc.a * B0.a,
// and the vertex sits on the far plane (z = params.x * w, the engine's 0.9999). Block = NxSkyDraw.
layout (location = 0) in vec3 inPos;
layout (location = 1) in vec2 inUv;
layout (location = 2) in vec4 inColor;
layout (std140, binding = 1) uniform SkyDraw { mat4 wvp; vec4 blend0; vec4 blend1; vec4 blend2; vec4 params; } d;
layout (location = 0) out vec2 outUv;
layout (location = 1) out vec4 outColor;
layout (location = 2) out float outFog;
void main() {
    vec4 p = d.wvp * vec4(inPos, 1.0);
    p.z = p.w * d.params.x;
    gl_Position = p;
    outUv = inUv + d.params.zw;
    outColor = vec4(d.blend0.rgb * inColor.r + d.blend1.rgb * inColor.g + d.blend2.rgb * inColor.b, inColor.a * d.blend0.a);
    outFog = 1.0;
}
