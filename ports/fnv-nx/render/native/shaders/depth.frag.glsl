#version 460
// depth family, fragment stage: depth (ZONLY: depth only, colour writes off in the state) / depth_out (DEPTHMAP:
// post-projection depth z/w in all channels) / depth_at (ZONLY_At, SLS1000.pso: alpha = the diffuse texture's alpha
// through the D3D9 alpha test, so the texture's holes are cut out of the depth pass; uv from depth_at.vert).
// nx-variants: -= out=DEPTH_OUT at=ALPHATEST
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec4 inClip;
layout (location = 1) in vec2 inUv;
#ifdef ALPHATEST
layout (binding = 0) uniform sampler2D diffuse;
#endif
layout (location = 0) out vec4 outColor;
void main() {
#if defined(DEPTH_OUT)
    outColor = vec4(inClip.z / inClip.w);
#elif defined(ALPHATEST)
    outColor = nx_d3d9_epilogue(vec4(0.0, 0.0, 0.0, texture(diffuse, inUv).a), 1.0);
#else
    outColor = vec4(0.0);
#endif
}
