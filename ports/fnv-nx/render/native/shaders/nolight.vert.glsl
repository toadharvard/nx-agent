#version 460
// nolight family, vertex stage (BSShaderNoLighting: NoLighting.v.hlsl x {TEX, VC, FALLOFF}; SKIN comes in N2).
// Block = NxNoLightDraw (nx/families.h).
// nx-variants: tex=TEX vc=VC texvc=TEX,VC texfall=TEX,FALLOFF texvcfall=TEX,VC,FALLOFF
layout (location = 0) in vec3 inPos;
layout (location = 1) in vec2 inUv;
layout (location = 2) in vec4 inColor;
layout (location = 3) in vec3 inNormal;
layout (std140, binding = 1) uniform NoLightDraw { mat4 wvp; vec4 eyeModel; vec4 falloff; vec4 color; vec4 uvOffsetScale; };
layout (location = 0) out vec2 outUv;
layout (location = 1) out vec4 outColor;
layout (location = 2) out float outFog;
void main() {
    gl_Position = wvp * vec4(inPos, 1.0);
    outUv = inUv * uvOffsetScale.zw + uvOffsetScale.xy;
#ifdef VC
    vec4 c = inColor * color;
#else
    vec4 c = color;
#endif
#ifdef FALLOFF
    // opacity by view angle: |N.V| from start (facing) to stop (grazing) angle
    float d = abs(dot(normalize(inNormal), normalize(eyeModel.xyz - inPos)));
    float t = clamp((falloff.x - d) / max(falloff.x - falloff.y, 1e-5), 0.0, 1.0);
    t = t * t * (3.0 - 2.0 * t);   // NOLIGHT004: smoothstep of the angle term before the opacity mix
    c.a *= mix(falloff.z, falloff.w, t);
#endif
    outColor = c;
    outFog = 1.0;
}
