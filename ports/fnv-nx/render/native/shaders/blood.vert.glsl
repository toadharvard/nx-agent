#version 460
// blood family, vertex stage (BSShaderBloodSplatter: BS.vso; FLARE = BSF.vso, the light flare). Block =
// NxBloodDraw (n1/veg_families.h). The clip position uses POSITION.xyw (POSITION.z is the splatter's alpha
// weight, passed to the PS); d = (clip.xy - LightLoc.xy) x LightLoc.w.
//   BS:  T0 = (uv, z), T1 = uv + d x (0.5, -0.5) (the light-shifted second map)
//   BSF: clip.xy += d x Ctrl.x, T0 = (uv, z)
// nx-variants: -= flare=FLARE
layout (location = 0) in vec4 inPos;
layout (location = 6) in vec2 inUv;
layout (std140, binding = 1) uniform BloodDraw {
    mat4 mvp;           // ModelViewProj (c0-c3)
    vec4 lightLoc;      // LightLoc (c4)
    vec4 ctrl;          // Ctrl (BSF c5)
    vec4 alpha;         // Alpha (PS c0)
};
layout (location = 0) out vec4 outUv;   // xy T0, z alpha weight
layout (location = 1) out vec2 outUv1;
void main() {
    vec4 q = vec4(inPos.xy, 0.0, inPos.w);
    vec4 c = mvp * q;
    vec2 d = (c.xy - lightLoc.xy) * lightLoc.w;
#ifdef FLARE
    c.xy += d * ctrl.x;
    outUv1 = inUv;
#else
    outUv1 = inUv + d * vec2(0.5, -0.5);
#endif
    gl_Position = c;
    outUv = vec4(inUv, inPos.z, 0.0);
}
