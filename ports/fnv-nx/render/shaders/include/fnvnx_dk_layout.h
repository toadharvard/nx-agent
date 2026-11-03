/*
 * fnvnx_dk_layout.h - the binding contract between translated shaders (any
 * GLSL backend) and the D3D9 front end. Header-only, C99, no dependencies.
 *
 * Per stage (deko3d bindings are per stage):
 *   UBO 0  FNVNX_DK_UBO_FLOAT   std140 { vec4 c[256]; }            c# by register
 *   UBO 1  FNVNX_DK_UBO_INTBOOL std140 { ivec4 i[16]; bool b[16]; } i# at 16*n, b# at 256+16*n (one uint each)
 *   UBO 2  FNVNX_DK_UBO_DRIVER  std140 driver constants, see below
 *   sampler binding = D3D sampler register (ps s0..s15, vs s0..s3)
 *
 * VS driver UBO: vec4 posfix  (xy = half-pixel offset in NDC (-1/w, +1/h), applied * gl_Position.w;
 *                              z  = y scale, -1 for DkDeviceFlags_OriginUpperLeft)
 * PS driver UBO: vec4 fogcolor; vec4 fog (x mode 0 off/1 vertex/2 exp/3 exp2/4 linear, y start, z end,
 *                w density); ivec4 alpha (x = D3DCMPFUNC, 0 = alpha test off); vec4 alpharef (x, 0..1);
 *                vec4 texbem[16] (2 per stage: bump matrix 00,01,10,11; then lscale, loffset)
 *
 * Vertex attribute locations: by D3DDECLUSAGE + usage index (fnvnx_dk_attrib_location).
 * VS->PS varying locations: by usage + index (fnvnx_dk_varying_location); PS < 3.0 reads the
 * fog factor at location 18 (x).
 *
 * Mirrored in mojoshader.patch (dk_attrib_location / dk_varying_location); keep in sync.
 */
#ifndef FNVNX_DK_LAYOUT_H
#define FNVNX_DK_LAYOUT_H

#define FNVNX_DK_UBO_FLOAT   0
#define FNVNX_DK_UBO_INTBOOL 1
#define FNVNX_DK_UBO_DRIVER  2
#define FNVNX_DK_NUM_FLOAT4  256
#define FNVNX_DK_NUM_INT4    16
#define FNVNX_DK_NUM_BOOL    16
#define FNVNX_DK_BOOL_OFFSET 256  /* bytes, inside UBO 1 */
#define FNVNX_DK_FOG_VARYING 18

/* D3DDECLUSAGE values (same numbering as MOJOSHADER_usage) */
enum {
    FNVNX_USAGE_POSITION = 0, FNVNX_USAGE_BLENDWEIGHT, FNVNX_USAGE_BLENDINDICES, FNVNX_USAGE_NORMAL,
    FNVNX_USAGE_PSIZE, FNVNX_USAGE_TEXCOORD, FNVNX_USAGE_TANGENT, FNVNX_USAGE_BINORMAL,
    FNVNX_USAGE_TESSFACTOR, FNVNX_USAGE_POSITIONT, FNVNX_USAGE_COLOR, FNVNX_USAGE_FOG,
    FNVNX_USAGE_DEPTH, FNVNX_USAGE_SAMPLE
};

/* -1 = not representable */
static inline int fnvnx_dk_attrib_location(int usage, int index)
{
    /* uam/Maxwell GLSL: 16 vertex attributes */
    switch (usage) {
    case FNVNX_USAGE_POSITION:     return index == 0 ? 0 : -1;
    case FNVNX_USAGE_BLENDWEIGHT:  return index == 0 ? 1 : -1;
    case FNVNX_USAGE_BLENDINDICES: return index == 0 ? 2 : -1;
    case FNVNX_USAGE_NORMAL:       return index == 0 ? 3 : -1;
    case FNVNX_USAGE_COLOR:        return index < 2 ? 4 + index : -1;
    case FNVNX_USAGE_TEXCOORD:     return index < 8 ? 6 + index : -1;
    case FNVNX_USAGE_TANGENT:      return index == 0 ? 14 : -1;
    case FNVNX_USAGE_BINORMAL:     return index == 0 ? 15 : -1;
    }
    return -1;
}

static inline int fnvnx_dk_varying_location(int usage, int index)
{
    switch (usage) {
    case FNVNX_USAGE_COLOR:        return index < 2 ? index : -1;
    case FNVNX_USAGE_TEXCOORD:     return index < 16 ? 2 + index : -1;
    case FNVNX_USAGE_FOG:          return index == 0 ? 18 : -1;
    case FNVNX_USAGE_NORMAL:       return index < 2 ? 19 + index : -1;
    case FNVNX_USAGE_TANGENT:      return index == 0 ? 21 : -1;
    case FNVNX_USAGE_BINORMAL:     return index == 0 ? 22 : -1;
    case FNVNX_USAGE_BLENDWEIGHT:  return index == 0 ? 23 : -1;
    case FNVNX_USAGE_BLENDINDICES: return index == 0 ? 24 : -1;
    case FNVNX_USAGE_POSITION:     return (index >= 1 && index < 4) ? 24 + index : -1;
    case FNVNX_USAGE_DEPTH:        return index == 0 ? 28 : -1;
    case FNVNX_USAGE_PSIZE:        return (index >= 1 && index < 3) ? 28 + index : -1;
    }
    return -1;
}

#endif
