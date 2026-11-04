#version 460
// nolight family, fragment stage. TEX: diffuse texture x interpolated colour; otherwise the colour alone.
// nx-variants: tex=TEX col=
#include "d3d9_epilogue.glsl"
layout (location = 0) in vec2 inUv;
layout (location = 1) in vec4 inColor;
layout (location = 2) in float inFog;
layout (binding = 0) uniform sampler2D tex;
layout (location = 0) out vec4 outColor;
void main() {
#ifdef TEX
    vec4 c = texture(tex, inUv) * inColor;
#else
    vec4 c = inColor;
#endif
    outColor = nx_d3d9_epilogue(c, inFog);
}
