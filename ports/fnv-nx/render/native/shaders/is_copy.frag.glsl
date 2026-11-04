#version 460
// nx-variants: -= e0=NX_IS_NO_EPILOGUE
// fnv-nx native renderer, render-land, post family: copy (ISCOPY.pso and the identical COPYUNPACK / STENCILCOPY
// programs): the source texel as is.
#define NX_IS_FRAG 1
#include "is_dk.glsl"
layout (binding = 0) uniform sampler2D src0;
layout(location = 2) in vec4 uv0;
void main() { is_write(texture(src0, uv0.xy)); }
