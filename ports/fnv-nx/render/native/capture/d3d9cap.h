// fnv-nx native renderer: NXD9, the D3D9-level capture format nx_render executes (docs/RENDER_NATIVE.md §8.3).
// A converter (S16-compare) writes it from a D3D9 call stream; nx_render runs it through the native D3D9 translator
// (front/d3d9_state.h) and S6b's translated GLSL into the GL reference backend.
//
// File: magic "NXD9\0\0\0\1" (8 bytes), then records until the end of the file:
//   u32 tag (four ASCII characters, first character in the low byte), u32 payload bytes, payload, zero padding to a
//   multiple of 4. Little-endian. Unknown tags are skipped. Ids are converter-chosen, nonzero, unique per kind.
#ifndef FNVNX_NX_D3D9CAP_H
#define FNVNX_NX_D3D9CAP_H
#include <stdint.h>

#define NXD9_TAG(a, b, c, d) ((uint32_t)(a) | (uint32_t)(b) << 8 | (uint32_t)(c) << 16 | (uint32_t)(d) << 24)
enum : uint32_t {
    kD9Device   = NXD9_TAG('D', 'E', 'V', ' '),   // u32 width, height, backFormat (D3DFORMAT), autoDepthFormat (0 none)
    kD9Texture  = NXD9_TAG('T', 'E', 'X', ' '),   // u32 id, format, width, height, levels, flags (1 render target,
                                                  //   2 cube), then per level: u32 bytes, bytes (cube: the
                                                  //   six faces of the level in D3DCUBEMAP_FACES order;
                                                  //   cube render targets are unsupported)
    kD9TexLevel = NXD9_TAG('T', 'E', 'X', 'U'),   // u32 id, level, bytes, bytes: new contents of one level
    kD9TexCube  = NXD9_TAG('T', 'E', 'X', 'C'),   // u32 id, face (D3DCUBEMAP_FACES), level, bytes, bytes: one face
    kD9Surface  = NXD9_TAG('S', 'U', 'R', 'F'),   // u32 id, kind (1 colour, 2 depth-stencil), format, width,
                                                  //   height, texture id (level 0 of that render-target texture, or 0)
    kD9VBuffer  = NXD9_TAG('V', 'B', ' ', ' '),   // u32 id, size
    kD9IBuffer  = NXD9_TAG('I', 'B', ' ', ' '),   // u32 id, size, format (101 D3DFMT_INDEX16, 102 INDEX32)
    kD9VBData   = NXD9_TAG('V', 'B', 'U', ' '),   // u32 id, offset, bytes, bytes
    kD9IBData   = NXD9_TAG('I', 'B', 'U', ' '),   // u32 id, offset, bytes, bytes
    kD9Decl     = NXD9_TAG('D', 'E', 'C', 'L'),   // u32 id, count, count x D3DVERTEXELEMENT9 (8 bytes; no END)
    kD9VShader  = NXD9_TAG('V', 'S', 'H', ' '),   // u32 id, u32 hash lo, hash hi (fnvnx_shader_hash of the bytecode),
    kD9PShader  = NXD9_TAG('P', 'S', 'H', ' '),   //   u32 bytecodeBytes, bytecode, u32 glslBytes, glsl (S6b glsldk
                                                  //   output; 0 bytes: <shader dir>/<hash %016llx>.glsl)
    kD9RState   = NXD9_TAG('R', 'S', ' ', ' '),   // u32 D3DRENDERSTATETYPE, value
    kD9SState   = NXD9_TAG('S', 'S', ' ', ' '),   // u32 sampler (0-15), D3DSAMPLERSTATETYPE, value
    kD9TSState  = NXD9_TAG('T', 'S', 'S', ' '),   // u32 stage, type, value (fixed function: recorded, unused)
    kD9SetTex   = NXD9_TAG('S', 'T', 'E', 'X'),   // u32 sampler (0-15), texture id (0 none)
    kD9SetVS    = NXD9_TAG('S', 'V', 'S', ' '),   // u32 shader id (0: fixed function)
    kD9SetPS    = NXD9_TAG('S', 'P', 'S', ' '),   // u32 shader id (0: fixed function)
    kD9VSConstF = NXD9_TAG('V', 'S', 'C', 'F'),   // u32 start register, count, count x 4 floats
    kD9PSConstF = NXD9_TAG('P', 'S', 'C', 'F'),
    kD9VSConstI = NXD9_TAG('V', 'S', 'C', 'I'),   // u32 start, count, count x 4 int32
    kD9PSConstI = NXD9_TAG('P', 'S', 'C', 'I'),
    kD9VSConstB = NXD9_TAG('V', 'S', 'C', 'B'),   // u32 start, count, count x u32 (BOOL)
    kD9PSConstB = NXD9_TAG('P', 'S', 'C', 'B'),
    kD9SetDecl  = NXD9_TAG('S', 'D', 'C', 'L'),   // u32 declaration id (0 none; FVF draws are unsupported)
    kD9Stream   = NXD9_TAG('S', 'T', 'R', 'M'),   // u32 stream (0 only), vb id, offset, stride
    kD9Indices  = NXD9_TAG('S', 'I', 'D', 'X'),   // u32 ib id
    kD9SetRT    = NXD9_TAG('S', 'R', 'T', ' '),   // u32 index (0 only), surface id (0 = back buffer)
    kD9SetDS    = NXD9_TAG('S', 'D', 'S', ' '),   // u32 surface id (0 none, 0xFFFFFFFF = the device's auto depth)
    kD9Viewport = NXD9_TAG('V', 'I', 'E', 'W'),   // D3DVIEWPORT9: u32 x, y, w, h, f32 minZ, maxZ
    kD9Scissor  = NXD9_TAG('S', 'C', 'I', 'S'),   // RECT: i32 left, top, right, bottom
    kD9Clear    = NXD9_TAG('C', 'L', 'R', ' '),   // u32 flags (D3DCLEAR_*), color (D3DCOLOR), f32 z, u32 stencil,
                                                  //   u32 rect count, count x RECT
    kD9Draw     = NXD9_TAG('D', 'R', 'A', 'W'),   // u32 D3DPRIMITIVETYPE, startVertex, primitiveCount
    kD9DrawIdx  = NXD9_TAG('D', 'R', 'W', 'I'),   // u32 type, i32 baseVertex, u32 minIndex, numVertices, startIndex,
                                                  //   primitiveCount
    kD9Present  = NXD9_TAG('P', 'R', 'E', 'S'),   // (empty) end of a frame
};

#endif
