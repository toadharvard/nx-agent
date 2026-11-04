#version 460
// d9_fogpass, fragment stage: the interpolated colour (see d9_fogpass.vert.glsl), then the D3D9 epilogue.
layout(location = 0) in vec4 vColor;
layout(location = 0) out vec4 outColor;
#include "d9_epilogue.glsl"
void main() { outColor = d9_epilogue(vColor); }
