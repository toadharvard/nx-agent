#version 460
// menubg family (ImageSpaceEffectMenuBackground, the ISMENUBG image-space effect): the scene behind a menu, its
// luminance (dot with d.luma.rgb) looked up in a tone texture (sepia.dds in the game), mixed with the scene by
// d.params.x; output alpha d.params.y. Block = NxMenuBgDraw (nx/families.h).
layout (std140, binding = 1) uniform MenuBgDraw { vec4 luma; vec4 params; vec4 uvXform; } d;
layout (binding = 0) uniform sampler2D scene;
layout (binding = 1) uniform sampler2D tone;
layout (location = 0) in vec2 inUv;
layout (location = 0) out vec4 outColor;
void main() {
    vec4 c = texture(scene, inUv);
    float l = dot(c.rgb, d.luma.rgb);
    vec3 t = texture(tone, vec2(l, l)).rgb;
    outColor = vec4(mix(c.rgb, t, d.params.x), d.params.y);
}
