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
 *                              z  = y scale, -1 for DkDeviceFlags_OriginUpperLeft;
 *                              w  = 0: the opaque zero, below)
 *               vec4 pad     (byte offset FNVNX_DK_VS_PAD_OFFSET = 16; x = the default of the diffuse (COLOR0) components a VS does not write:
 *                              1.0 when the pair's pixel stage is not ps_3_0 (ps_1_x, ps_2_x, fixed function), 0.0 for ps_3_0;
 *                              yzw reserved, write 0)
 *               vec4 reserved[2]  (bytes 32..63, write 0)
 *               vec4 clip[8]      (byte FNVNX_DK_VS_CLIP_OFFSET = 64; plane i as SetClipPlane stored it, raw clip space; zero unless CLIPPING and the plane is enabled, 6 and 7 always zero)
 *               vec4 point        (byte FNVNX_DK_VS_POINT_OFFSET = 192; x = POINTSIZE, y = size min, z = size max (raw; the shader takes min(max(size, y), z), so min > max gives max); w 0; block size 208, nothing before 64 moved)
 * PS driver UBO: vec4 fogcolor; vec4 fog (x mode 0 off/1 vertex/2 exp/3 exp2/4 linear, y start, z end,
 *                w density); ivec4 alpha (x = D3DCMPFUNC, 0 = alpha test off); vec4 alpharef (x, 0..1);
 *                vec4 texbem[16] (2 per stage: bump matrix 00,01,10,11; then lscale, loffset);
 *                uvec4 stype[2] (byte offset FNVNX_DK_PS_STYPE_OFFSET = 320, appended: nothing before it moved; block size 352)
 *                alpha.y = 0: the opaque zero.
 * ps_1_x sampler dimensionality is DATA: ps_1_x has no dcl, the type is that of the texture bound at draw time (and the CTAB is D3DX
 * metadata, not part of the D3D9 contract). Every ps_1_x stage n that samples declares three samplers, 2D at binding n, CUBE at
 * FNVNX_DK_SAMP_CUBE_BASE + n (8 + n), VOLUME at FNVNX_DK_SAMP_VOLUME_BASE + n (16 + n) (the numbering ffp.frag.glsl uses), and selects
 * by the stage's type word stype[n / 4][n % 4] = FNVNX_DK_STYPE_2D / _CUBE / _VOLUME. The host binds the bound texture at the slot of
 * its dimensionality and writes the word; unused dimensionalities of a stage may point at any texture (never read). A zeroed block
 * means 2D everywhere (the former behaviour). ps_2_0 and later declare their types (dcl_2d/cube/volume) and ignore the words.
 * Opaque zero: a shader with relative constant reads XORs its def'd values with it at the start of main(),
 * so the GLSL compiler treats them as run-time values (as WineD3D's uniforms) and cannot fold x * 0 -> 0.
 * Hosts must write 0 there (every host clears the blocks).
 *
 * Vertex attribute locations and VS->PS varying locations: ONE total rule, fnvnx_dk_assign() below, a pure function of the
 * set of (usage, index) pairs a program declares (see "Location rule"). The fixed tables fnvnx_dk_attrib_location /
 * fnvnx_dk_varying_location are its CANONICAL part (pairs that keep their slot whatever else is declared); PS < 3.0
 * reads the fog factor at location 18 (x), the canonical slot of FOG0.
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
#define FNVNX_DK_VS_PAD_OFFSET     16   /* bytes, inside the VS driver block: vec4 pad, x = the COLOR0 default (1.0 unless the pixel stage is ps_3_0) */
#define FNVNX_DK_VS_CLIP_OFFSET    64   /* bytes: vec4 clip[8] */
#define FNVNX_DK_VS_POINT_OFFSET   192  /* bytes: vec4 point = (POINTSIZE, min, max, 0) */
#define FNVNX_DK_VS_DRIVER_SIZE    208
#define FNVNX_DK_PS_STYPE_OFFSET   320  /* bytes, inside the PS driver block: uvec4 stype[2], stage n at word n */
#define FNVNX_DK_STYPE_2D          0
#define FNVNX_DK_STYPE_CUBE        1
#define FNVNX_DK_STYPE_VOLUME      2
#define FNVNX_DK_SAMP_CUBE_BASE    8    /* ps_1_x: stage n's CUBE sampler binding is 8 + n, its VOLUME one 16 + n */
#define FNVNX_DK_SAMP_VOLUME_BASE  16
#define FNVNX_DK_PS_DRIVER_SIZE    352
#define FNVNX_DK_MAX_FLOAT_VS      256  /* relative constant reads c[a0 + n] / c[aL + n]: c[0..N) of the stage, 0 outside */
#define FNVNX_DK_MAX_FLOAT_PS      224

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

/* ---- Location rule (total over usage 0..13, index 0..15) ----------------------------------------------------------
 * Hardware limits (uam source: nv_attributes.h NvAttrib_Generic(0..31), compiler_iface.cpp CLIPVERTEX = Generic(31);
 * Maxwell vertex fetch has 16 attributes): 16 vertex attribute locations (0..15); 31 usable generic varying locations
 * (0..30; 31 is the clip-vertex slot). D3D9 allows at most 16 VS inputs (v0..v15) and 12 VS outputs / PS inputs (SM3),
 * and 224 (usage, index) pairs exist, so no fixed map is injective: the slot is a function of the program's DECLARATION SET.
 *
 * Rule: let S be the set of declared pairs (|S| <= slots, no duplicates). (1) a pair with a canonical slot (the tables above,
 * unchanged since the first release) gets it. (2) the remaining pairs, in ascending (usage, index) order, get the free slots
 * (those not taken by (1)) in ascending order. Proof: (1) is injective (the canonical map is), (2) is injective and disjoint
 * from (1) by construction, and slots - |canonical in S| >= |S| - |canonical in S| slots are free, so every pair gets one.
 * Stage agreement: both stages of a draw apply the rule to the SAME set L = (VS outputs without POSITION0/PSIZE0) U
 * (PS inputs); a stage compiled alone uses its own set, which gives the same locations whenever every pair of L is
 * canonical (fixed-function and nearly all real programs), so only draws with non-canonical varyings need a per-L variant
 * (pure data, no run-time decision): fnvnx_shader_opts.link_pairs = L. VS POSITION0 / PSIZE0 live in gl_Position /
 * gl_PointSize; a PS that declares them reads a varying copy, which the VS writes only when they are in link_pairs.
 * VS inputs use the VS's own declared inputs: the device binds a stream element to the location the reflection JSON
 * lists for the dcl with the same (usage, index). */
#define FNVNX_DK_KIND_ATTRIB   0
#define FNVNX_DK_KIND_VARYING  1
#define FNVNX_DK_ATTRIB_SLOTS  16
#define FNVNX_DK_VARYING_SLOTS 31
#define FNVNX_DK_PAIR(usage, index) ((unsigned char)((usage) * 16 + (index)))   /* usage 0..13, index 0..15 */

/* loc[k] = location of pairs[k] (a FNVNX_DK_PAIR). Returns 0, or -1 if the set is not a legal declaration set
 * (duplicate pair, usage > 13, more pairs than slots): the D3D9 validator rejects such a program. */
static inline int fnvnx_dk_assign(int kind, const unsigned char *pairs, int n, signed char *loc)
{
    const int slots = kind == FNVNX_DK_KIND_ATTRIB ? FNVNX_DK_ATTRIB_SLOTS : FNVNX_DK_VARYING_SLOTS;
    unsigned char taken[32] = { 0 }; int i, j, p, slot = 0;
    if (n > slots) return -1;
    for (i = 0; i < n; i++) {
        if (pairs[i] / 16 > 13) return -1;
        for (j = 0; j < i; j++) if (pairs[j] == pairs[i]) return -1;
        loc[i] = (signed char)(kind == FNVNX_DK_KIND_ATTRIB ? fnvnx_dk_attrib_location(pairs[i] / 16, pairs[i] % 16)
                                                            : fnvnx_dk_varying_location(pairs[i] / 16, pairs[i] % 16));
        if (loc[i] >= 0) taken[(int)loc[i]] = 1;
    }
    for (p = 0; p < 224; p++)                                         /* ascending (usage, index) */
        for (i = 0; i < n; i++)
            if (pairs[i] == p && loc[i] < 0) {
                while (slot < slots && taken[slot]) slot++;
                if (slot >= slots) return -1;                         /* unreachable: see the proof */
                loc[i] = (signed char)slot++;
            }
    return 0;
}

/* ---- Varying completion (the "unwritten pixel input" rule) -----------------------------------------------------------------
 * D3D9 leaves a pixel shader input the vertex shader does not write undefined (MS register pages: "Defaults: None"); GL cannot link it. The
 * project's reference profile, WineD3D 9.0, defines it per component: diffuse (COLOR0) 1.0 for a pixel stage below ps_3_0 / fixed function, 0.0
 * for ps_3_0; every other pair 0.0 (an unwritten oFog is 0.0). Every translated VS therefore writes, with no run-time decision:
 *   base     : the components of each declared output it leaves out, the pairs of the link set L it does not declare, and FOG0
 *              (a VS that does not write oFog / a FOG output writes 0.0: ps < 3.0 reads the fog factor at FNVNX_DK_FOG_VARYING);
 *   complete : (FNVNX_SHADER_VS_COMPLETE) additionally every slot 0..30 still unwritten, with its canonical pair's default.
 * COLOR0's default is the VS driver word pad.x (data). The variant of the VS is a pure function of the two stages' reflection
 * ("varyings_written" of the VS, "varyings_read" of the PS, FNVNX_DK_PAIR codes): complete iff the PS reads a canonical pair the VS does not
 * write (FOG0 is always written). A non-canonical pair the PS reads is in L (the location rule above), which the VS writes. Proof: a pair q the PS
 * reads is (a) written by the VS (declared: its missing components are base-completed), or (b) in L (written by base), or (c) canonical and
 * not written: then the complete variant writes its slot, and the slots 0..30 are exactly the canonical slots (the table above is a bijection onto them),
 * so no slot a pixel stage can read is ever left unwritten. */
static inline int fnvnx_dk_vs_needs_complete(const unsigned char *vs_written, int nvs, const unsigned char *ps_read, int nps)
{
    int i, j;
    for (i = 0; i < nps; i++) {
        int found = 0;
        if (ps_read[i] / 16 > 13 || fnvnx_dk_varying_location(ps_read[i] / 16, ps_read[i] % 16) < 0) continue;   /* non-canonical: in L */
        for (j = 0; j < nvs; j++) if (vs_written[j] == ps_read[i]) { found = 1; break; }
        if (!found) return 1;
    }
    return 0;
}

#endif
