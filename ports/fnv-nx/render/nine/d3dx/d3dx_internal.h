// fnv-nx: D3DX9 for the game (stream S6c-nine, guest side, ILP32): shared pieces of render/nine/d3dx/.
//
// d3dx9_38.dll is not available on Switch; the game imports 21 of its functions (docs/RENDER.md §1) and these files
// implement them, plus the related functions the shim declares, on top of the D3D9 proxy (any IDirect3DDevice9:
// the calls go through the vtable). Written from the SDK documentation and the file formats (DDS, CTAB).
#ifndef FNVNX_D3DX_INTERNAL_H
#define FNVNX_D3DX_INTERNAL_H
#include <d3dx9.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

namespace fnvnx_d3dx {

// printf to the guest log, at most `limit` times per call site
#define D3DX_LOG_LIMITED(limit, ...) do { static unsigned n_; if (n_ < (limit)) { n_++; printf(__VA_ARGS__); } } while (0)

ID3DXBuffer* make_buffer(const void* src, DWORD size);   // d3dx_buffer.cpp; src NULL: zeroed

// ---- pixel formats ----
struct Format {
    D3DFORMAT fmt;
    unsigned bytes;     // per pixel, or per 4x4 block when `block`
    bool block;         // DXT1..5
    bool argb;          // decode/encode to A8R8G8B8 supported (conversion, scaling, colour key, mip generation)
    bool alpha;
};
const Format* format_info(D3DFORMAT f);   // NULL: unknown (not loadable)

inline unsigned level_dim(unsigned v, unsigned l) { v >>= l; return v ? v : 1; }
inline unsigned row_pitch(const Format* f, unsigned w) { return f->block ? ((w + 3) / 4) * f->bytes : w * f->bytes; }
inline unsigned row_count(const Format* f, unsigned h) { return f->block ? (h + 3) / 4 : h; }
inline size_t slice_size(const Format* f, unsigned w, unsigned h) { return (size_t)row_pitch(f, w) * row_count(f, h); }

void copy_rows(void* dst, int dst_pitch, const void* src, int src_pitch, unsigned row_bytes, unsigned rows);

// A8R8G8B8 image in guest memory (conversion path)
struct Image {
    uint32_t* px = nullptr;
    unsigned w = 0, h = 0;
    bool alloc(unsigned w_, unsigned h_);
    void release() { free(px); px = nullptr; }
};
void decode_rows(const Format* f, const uint8_t* src, int pitch, unsigned x0, unsigned y0, Image* out);   // out->w,h set
void encode_rows(const Format* f, const Image& in, uint8_t* dst, int pitch);
void apply_color_key(Image* img, D3DCOLOR key);
bool scale_image(const Image& src, Image* dst, unsigned w, unsigned h, DWORD filter);   // point or 2x box
bool half_image(const Image& src, Image* dst);                                            // box filter, next mip

// ---- DDS ----
struct Dds {
    const Format* fmt;
    D3DFORMAT d3dfmt;
    unsigned w, h, d, mips;
    D3DRESOURCETYPE type;      // TEXTURE, CUBETEXTURE, VOLUMETEXTURE
    unsigned faces;            // cube: bit i = face i present (D3DCUBEMAP_FACES order)
    const uint8_t* data;       // first byte after the header(s)
    size_t size;               // bytes after the header(s)
};
HRESULT dds_parse(const void* src, UINT size, Dds* out);
size_t dds_level_size(const Dds& d, unsigned level);      // one face / the whole volume level
const uint8_t* dds_level(const Dds& d, unsigned face, unsigned level);

} // namespace fnvnx_d3dx

#endif
