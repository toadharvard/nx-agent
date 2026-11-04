#version 460
// N0 quad: unit quad from the vertex buffer, placed by the per-draw UBO (slot 1); the pass UBO (slot 0) holds the
// target size. Instance i adds inst[i].xy to the position and scales the colour by inst[i].z (non-instanced
// draws: inst[0] = (0, 0, 1, 0)).
layout (location = 0) in vec2 inPos;
layout (location = 1) in vec2 inUv;
layout (std140, binding = 0) uniform Pass { vec4 target; };          // 2/w, 2/h, 0, 0
layout (std140, binding = 1) uniform Draw {
    vec4 rect;      // x, y, w, h in pixels (top-left origin)
    vec4 color;
    vec4 uvRect;    // u0, v0, du, dv
    vec4 rot;       // cos, sin, depth (0..1), vertex fog factor
    vec4 inst[16];  // x, y offset in pixels, colour scale, 0
};
layout (location = 0) out vec2 outUv;
layout (location = 1) out vec4 outColor;
layout (location = 2) out float outFog;
void main() {
    vec2 p = (inPos - 0.5) * rect.zw;
    p = vec2(p.x * rot.x - p.y * rot.y, p.x * rot.y + p.y * rot.x) + rect.xy + 0.5 * rect.zw;
    vec4 iv = inst[gl_InstanceID & 15];
    p += iv.xy;
    // pixels (y down) -> clip space (y up)
    gl_Position = vec4(p.x * target.x - 1.0, 1.0 - p.y * target.y, rot.z, 1.0);
    outUv = uvRect.xy + inUv * uvRect.zw;
    outColor = vec4(color.rgb * iv.z, color.a);
    outFog = rot.w;
}
