#version 460
// decals family, vertex stage (geometry decals: GDECAL.vso, skinned GDECALS.vso). Block = NxDecalDraw
// (n1/veg_families.h). NORMAL (location 3) carries the decal uv (xy) and the fade weight (z);
// T0 = (uv, fade weight x DecalFade.x x DecalFade.y).
// SKIN: four bones of 3 rows each (Bones, c44+): BLENDWEIGHT xyz (the fourth = 1 - their sum), BLENDINDICES a
// D3DCOLOR (UBYTE4 via colour order: weight x -> index .z, y -> .y, z -> .x, w -> .w; index = value x 765.01 = 3 x
// the byte, the first row of that bone).
// nx-variants: -= skin=SKIN
layout (location = 0) in vec4 inPos;
layout (location = 3) in vec3 inUvFade;
#ifdef SKIN
layout (location = 1) in vec3 inWeights;
layout (location = 2) in vec4 inBones;
#endif
layout (std140, binding = 1) uniform DecalDraw {
    mat4 mvp;           // ModelViewProj (c0-c3) / SkinModelViewProj (c1-c4)
    vec4 fade;          // x = DecalFade.x x DecalFade.y
    vec4 offset;        // PSDecalOffset (PS c15): u' = u y + x, v' = v w + z
#ifdef SKIN
    vec4 bones[54];     // Bones (c44-c97): 18 bones x 3 rows
#endif
};
layout (location = 0) out vec3 outUvFade;
void main() {
#ifdef SKIN
    vec4 p = vec4(inPos.xyz, 1.0);
    ivec4 b = ivec4(inBones.zyxw * 765.01);
    vec4 w = vec4(inWeights, 1.0 - dot(inWeights, vec3(1.0)));
    vec3 s = vec3(0.0);
    for (int k = 0; k < 4; k++)
        s += w[k] * vec3(dot(bones[b[k]], p), dot(bones[b[k] + 1], p), dot(bones[b[k] + 2], p));
    gl_Position = mvp * vec4(s, 1.0);
#else
    gl_Position = mvp * inPos;
#endif
    outUvFade = vec3(inUvFade.xy, fade.x * inUvFade.z);
}
