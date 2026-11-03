#version 460
// N0 test quad: unit quad from the vertex buffer, placed by the per-draw UBO (slot 1); the pass UBO (slot 0)
// holds the target size.
layout (location = 0) in vec2 inPos;
layout (location = 1) in vec2 inUv;
layout (std140, binding = 0) uniform Pass { vec4 target; };          // 2/w, 2/h, time, 0
layout (std140, binding = 1) uniform Draw { vec4 rect; vec4 color; vec4 uvRect; vec4 rot; };
layout (location = 0) out vec2 outUv;
layout (location = 1) out vec4 outColor;
void main() {
    vec2 p = (inPos - 0.5) * rect.zw;
    p = vec2(p.x * rot.x - p.y * rot.y, p.x * rot.y + p.y * rot.x) + rect.xy + 0.5 * rect.zw;
    gl_Position = vec4(p * target.xy - 1.0, 0.0, 1.0);
    outUv = uvRect.xy + inUv * uvRect.zw;
    outColor = color;
}
