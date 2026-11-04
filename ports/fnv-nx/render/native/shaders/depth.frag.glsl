#version 460
// depth family, fragment stage: depth (ZONLY: depth only, colour writes off in the state) / depth_out (DEPTHMAP:
// post-projection depth z/w in all channels).
// nx-variants: -= out=DEPTH_OUT
layout (location = 0) in vec4 inClip;
layout (location = 0) out vec4 outColor;
void main() {
#ifdef DEPTH_OUT
    outColor = vec4(inClip.z / inClip.w);
#else
    outColor = vec4(0.0);
#endif
}
