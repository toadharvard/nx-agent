#version 460
// d9_fogpass: ShadowLightShader's fog-colour pass (position only; colour = fog colour, alpha = fog factor), vertex
// stage. A d9_* family (capture/d3d9_families.h, RENDER_NATIVE.md §4.5). Registers: c0-c3 ModelViewProj (rows),
// c14 FogParam (x near, y range, z power), c15 FogColor. Pixel stage: d9_fogpass.frag.glsl (outputs the colour).
layout(location = 0) in vec4 inPos;
layout (std140, binding = 0) uniform d9_vs_f { vec4 vc[256]; };
layout (std140, binding = 2) uniform d9_vs_driver { vec4 posfix; vec4 reserved[3]; };
layout(location = 0) out vec4 vColor;

void main() {
    vec4 p = inPos;
    gl_Position = vec4(dot(vc[0], p), dot(vc[1], p), dot(vc[2], p), dot(vc[3], p));
    float g = 1.0 - clamp((vc[14].x - length(gl_Position.xyz)) / vc[14].y, 0.0, 1.0);
    float fog = g > 0.0 ? exp2(log2(g) * vc[14].z) : (vc[14].z > 0.0 ? 0.0 : 1.0);
    vColor = clamp(vec4(vc[15].rgb, fog), 0.0, 1.0);
    gl_Position.xy += posfix.xy * gl_Position.w;
    gl_Position.y *= posfix.z;
}
