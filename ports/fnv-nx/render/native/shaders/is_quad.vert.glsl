#version 460
// nx-variants: -= uv2=NX_IS_UV1
// fnv-nx native renderer, render-land, post family: the image-space full-screen quad (ISCOPY.vso group; uv2 =
// the ISBLEND.vso group with a second texture coordinate). Registers: c0 geometryOffset (xy, in half-target units:
// the position moves by 2 * (-x, +y)), c1 texOffset0, c2 texOffset1. Vertex position is already in clip space.
layout (std140, binding = 0) uniform IsRegsV { vec4 vc[256]; };   // not "c": GL also links non-instanced block members by name
layout (std140, binding = 2) uniform IsVsDriver { vec4 posfix; vec4 reserved[3]; };
layout(location = 0) in vec4 inPos;
layout(location = 6) in vec4 inUv;
layout(location = 2) out vec4 outUv0;
#ifdef NX_IS_UV1
layout(location = 3) out vec4 outUv1;
#endif
void main() {
    gl_Position = vec4(inPos.xy + vec2(-2.0, 2.0) * vc[0].xy, inPos.zw);
    outUv0 = vec4(inUv.xy + vc[1].xy, 0.0, 0.0);
#ifdef NX_IS_UV1
    outUv1 = vec4(inUv.xy + vc[2].xy, 0.0, 0.0);
#endif
    gl_Position.xy += posfix.xy * gl_Position.w;     // D3D9 half pixel (driver block)
    gl_Position.y *= posfix.z;
}
