/* fnv-nx native renderer, N2 skinning: bone palettes (RENDER_NATIVE.md §4). Header-only, C99/C++.
 * Gamebryo skinning (NiSkinInstance / NiSkinData / NiSkinPartition): a vertex in skin space goes to world space by
 *   world = BoneWorld[b] * SkinToBone[b] * v
 * for each of its up to 4 bones (weights sum to 1). A partition draws with a palette of the bones it uses; the
 * vertex's bone indices index that palette. The palette holds one 3x4 row-major matrix per bone (3 vec4 rows):
 * the `bones` array of the lit / depth SKIN shader variants (shaders/lit.vert.glsl, depth.vert.glsl). */
#ifndef FNVNX_NX_SKIN_H
#define FNVNX_NX_SKIN_H
#include <stdint.h>

#define NX_SKIN_MAX_BONES 32   /* per partition (palette entries) */

typedef struct NxBoneXform {   /* NiTransform: p' = rot * (scale * p) + trans, rot row-major */
    float rot[9], trans[3], scale;
} NxBoneXform;

/* a * b (apply b first) */
static inline NxBoneXform nx_bone_compose(const NxBoneXform* a, const NxBoneXform* b) {
    NxBoneXform o;
    for (int r = 0; r < 3; r++)
        for (int c = 0; c < 3; c++)
            o.rot[r * 3 + c] = a->rot[r * 3] * b->rot[c] + a->rot[r * 3 + 1] * b->rot[3 + c] + a->rot[r * 3 + 2] * b->rot[6 + c];
    for (int r = 0; r < 3; r++)
        o.trans[r] = a->scale * (a->rot[r * 3] * b->trans[0] + a->rot[r * 3 + 1] * b->trans[1] + a->rot[r * 3 + 2] * b->trans[2]) + a->trans[r];
    o.scale = a->scale * b->scale;
    return o;
}

/* rows[3 * i + r] = row r of BoneWorld[bones[i]] * SkinToBone[bones[i]]; returns the palette size (<= max). */
static inline uint32_t nx_skin_palette(const NxBoneXform* boneWorld, const NxBoneXform* skinToBone,
                                       const uint16_t* bones, uint32_t n, float rows[][4]) {
    if (n > NX_SKIN_MAX_BONES) n = NX_SKIN_MAX_BONES;
    for (uint32_t i = 0; i < n; i++) {
        NxBoneXform m = nx_bone_compose(&boneWorld[bones[i]], &skinToBone[bones[i]]);
        for (int r = 0; r < 3; r++) {
            rows[3 * i + r][0] = m.rot[r * 3] * m.scale; rows[3 * i + r][1] = m.rot[r * 3 + 1] * m.scale;
            rows[3 * i + r][2] = m.rot[r * 3 + 2] * m.scale; rows[3 * i + r][3] = m.trans[r];
        }
    }
    return n;
}
#endif
