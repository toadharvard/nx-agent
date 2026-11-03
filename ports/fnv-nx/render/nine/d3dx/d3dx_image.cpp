// fnv-nx D3DX9 (stream S6c-nine): pixel formats, A8R8G8B8 conversion, DDS parsing. See d3dx_internal.h.
#include <stdio.h>
#include "d3dx_internal.h"

namespace fnvnx_d3dx {

static const Format kFormats[] = {
    {D3DFMT_DXT1, 8, true, false, true},
    {D3DFMT_DXT2, 16, true, false, true},
    {D3DFMT_DXT3, 16, true, false, true},
    {D3DFMT_DXT4, 16, true, false, true},
    {D3DFMT_DXT5, 16, true, false, true},
    {D3DFMT_A8R8G8B8, 4, false, true, true},
    {D3DFMT_X8R8G8B8, 4, false, true, false},
    {D3DFMT_A8B8G8R8, 4, false, true, true},
    {D3DFMT_X8B8G8R8, 4, false, true, false},
    {D3DFMT_R8G8B8, 3, false, true, false},
    {D3DFMT_R5G6B5, 2, false, true, false},
    {D3DFMT_X1R5G5B5, 2, false, true, false},
    {D3DFMT_A1R5G5B5, 2, false, true, true},
    {D3DFMT_A4R4G4B4, 2, false, true, true},
    {D3DFMT_X4R4G4B4, 2, false, true, false},
    {D3DFMT_L8, 1, false, true, false},
    {D3DFMT_A8L8, 2, false, true, true},
    {D3DFMT_A8, 1, false, true, true},
    {D3DFMT_A4L4, 1, false, true, true},
    {D3DFMT_A2R10G10B10, 4, false, false, true},
    {D3DFMT_A2B10G10R10, 4, false, false, true},
    {D3DFMT_G16R16, 4, false, false, false},
    {D3DFMT_A8R3G3B2, 2, false, false, true},
    {D3DFMT_R3G3B2, 1, false, false, false},
    {D3DFMT_L16, 2, false, false, false},
    {D3DFMT_V8U8, 2, false, false, false},
    {D3DFMT_L6V5U5, 2, false, false, false},
    {D3DFMT_X8L8V8U8, 4, false, false, false},
    {D3DFMT_Q8W8V8U8, 4, false, false, true},
    {D3DFMT_V16U16, 4, false, false, false},
    {D3DFMT_A16B16G16R16, 8, false, false, true},
    {D3DFMT_Q16W16V16U16, 8, false, false, true},
    {D3DFMT_R16F, 2, false, false, false},
    {D3DFMT_G16R16F, 4, false, false, false},
    {D3DFMT_A16B16G16R16F, 8, false, false, true},
    {D3DFMT_R32F, 4, false, false, false},
    {D3DFMT_G32R32F, 8, false, false, false},
    {D3DFMT_A32B32G32R32F, 16, false, false, true},
};

const Format* format_info(D3DFORMAT f) {
    for (const Format& e : kFormats)
        if (e.fmt == f) return &e;
    return nullptr;
}

void copy_rows(void* dst, int dst_pitch, const void* src, int src_pitch, unsigned row_bytes, unsigned rows) {
    uint8_t* d = (uint8_t*)dst;
    const uint8_t* s = (const uint8_t*)src;
    if (dst_pitch == src_pitch && (unsigned)dst_pitch == row_bytes) {
        memcpy(d, s, (size_t)row_bytes * rows);
        return;
    }
    for (unsigned y = 0; y < rows; y++, d += dst_pitch, s += src_pitch) memcpy(d, s, row_bytes);
}

bool Image::alloc(unsigned w_, unsigned h_) {
    w = w_; h = h_;
    px = (uint32_t*)malloc((size_t)w * h * 4);
    return px != nullptr;
}

static inline uint32_t x5(uint32_t v) { return (v << 3) | (v >> 2); }
static inline uint32_t x6(uint32_t v) { return (v << 2) | (v >> 4); }
static inline uint32_t argb(uint32_t a, uint32_t r, uint32_t g, uint32_t b) { return a << 24 | r << 16 | g << 8 | b; }

static uint32_t decode_px(D3DFORMAT f, const uint8_t* p) {
    uint32_t v;
    switch (f) {
    case D3DFMT_A8R8G8B8: memcpy(&v, p, 4); return v;
    case D3DFMT_X8R8G8B8: memcpy(&v, p, 4); return v | 0xFF000000u;
    case D3DFMT_A8B8G8R8: memcpy(&v, p, 4); return (v & 0xFF00FF00u) | (v & 0xFF) << 16 | (v >> 16 & 0xFF);
    case D3DFMT_X8B8G8R8: memcpy(&v, p, 4); return 0xFF000000u | (v & 0xFF00u) | (v & 0xFF) << 16 | (v >> 16 & 0xFF);
    case D3DFMT_R8G8B8: return argb(255, p[2], p[1], p[0]);
    case D3DFMT_R5G6B5: v = p[0] | p[1] << 8; return argb(255, x5(v >> 11), x6(v >> 5 & 63), x5(v & 31));
    case D3DFMT_X1R5G5B5: v = p[0] | p[1] << 8; return argb(255, x5(v >> 10 & 31), x5(v >> 5 & 31), x5(v & 31));
    case D3DFMT_A1R5G5B5:
        v = p[0] | p[1] << 8; return argb(v & 0x8000 ? 255 : 0, x5(v >> 10 & 31), x5(v >> 5 & 31), x5(v & 31));
    case D3DFMT_A4R4G4B4: v = p[0] | p[1] << 8; return argb((v >> 12) * 17, (v >> 8 & 15) * 17, (v >> 4 & 15) * 17, (v & 15) * 17);
    case D3DFMT_X4R4G4B4: v = p[0] | p[1] << 8; return argb(255, (v >> 8 & 15) * 17, (v >> 4 & 15) * 17, (v & 15) * 17);
    case D3DFMT_L8: return argb(255, p[0], p[0], p[0]);
    case D3DFMT_A8L8: return argb(p[1], p[0], p[0], p[0]);
    case D3DFMT_A8: return argb(p[0], 0, 0, 0);
    case D3DFMT_A4L4: return argb((p[0] >> 4) * 17, (p[0] & 15) * 17, (p[0] & 15) * 17, (p[0] & 15) * 17);
    default: return 0;
    }
}

static void encode_px(D3DFORMAT f, uint32_t c, uint8_t* p) {
    uint32_t a = c >> 24, r = c >> 16 & 255, g = c >> 8 & 255, b = c & 255, v;
    switch (f) {
    case D3DFMT_A8R8G8B8: case D3DFMT_X8R8G8B8: memcpy(p, &c, 4); return;
    case D3DFMT_A8B8G8R8: case D3DFMT_X8B8G8R8: v = a << 24 | b << 16 | g << 8 | r; memcpy(p, &v, 4); return;
    case D3DFMT_R8G8B8: p[0] = b; p[1] = g; p[2] = r; return;
    case D3DFMT_R5G6B5: v = (r >> 3) << 11 | (g >> 2) << 5 | b >> 3; break;
    case D3DFMT_X1R5G5B5: v = 0x8000 | (r >> 3) << 10 | (g >> 3) << 5 | b >> 3; break;
    case D3DFMT_A1R5G5B5: v = (a >= 128 ? 0x8000 : 0) | (r >> 3) << 10 | (g >> 3) << 5 | b >> 3; break;
    case D3DFMT_A4R4G4B4: v = (a >> 4) << 12 | (r >> 4) << 8 | (g >> 4) << 4 | b >> 4; break;
    case D3DFMT_X4R4G4B4: v = 0xF000 | (r >> 4) << 8 | (g >> 4) << 4 | b >> 4; break;
    case D3DFMT_L8: p[0] = (uint8_t)((r * 54 + g * 183 + b * 19) >> 8); return;   // Rec. 709 luma
    case D3DFMT_A8L8: p[0] = (uint8_t)((r * 54 + g * 183 + b * 19) >> 8); p[1] = (uint8_t)a; return;
    case D3DFMT_A8: p[0] = (uint8_t)a; return;
    case D3DFMT_A4L4: p[0] = (uint8_t)((a >> 4) << 4 | ((r * 54 + g * 183 + b * 19) >> 12)); return;
    default: return;
    }
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8);
}

void decode_rows(const Format* f, const uint8_t* src, int pitch, unsigned x0, unsigned y0, Image* out) {
    for (unsigned y = 0; y < out->h; y++) {
        const uint8_t* s = src + (size_t)(y0 + y) * pitch + (size_t)x0 * f->bytes;
        uint32_t* d = out->px + (size_t)y * out->w;
        if (f->fmt == D3DFMT_A8R8G8B8) { memcpy(d, s, (size_t)out->w * 4); continue; }
        for (unsigned x = 0; x < out->w; x++, s += f->bytes) d[x] = decode_px(f->fmt, s);
    }
}

void encode_rows(const Format* f, const Image& in, uint8_t* dst, int pitch) {
    for (unsigned y = 0; y < in.h; y++) {
        uint8_t* d = dst + (size_t)y * pitch;
        const uint32_t* s = in.px + (size_t)y * in.w;
        if (f->fmt == D3DFMT_A8R8G8B8 || f->fmt == D3DFMT_X8R8G8B8) { memcpy(d, s, (size_t)in.w * 4); continue; }
        for (unsigned x = 0; x < in.w; x++, d += f->bytes) encode_px(f->fmt, s[x], d);
    }
}

// D3DX: a pixel whose colour (alpha forced opaque for formats without alpha, already done by decode) equals the key
// becomes transparent black.
void apply_color_key(Image* img, D3DCOLOR key) {
    if (!key) return;
    size_t n = (size_t)img->w * img->h;
    for (size_t i = 0; i < n; i++)
        if (img->px[i] == key) img->px[i] = 0;
}

bool half_image(const Image& s, Image* d) {
    unsigned w = s.w > 1 ? s.w / 2 : 1, h = s.h > 1 ? s.h / 2 : 1;
    if (!d->alloc(w, h)) return false;
    for (unsigned y = 0; y < h; y++)
        for (unsigned x = 0; x < w; x++) {
            unsigned x0 = s.w > 1 ? 2 * x : 0, y0 = s.h > 1 ? 2 * y : 0;
            unsigned x1 = s.w > 1 ? x0 + 1 : x0, y1 = s.h > 1 ? y0 + 1 : y0;
            uint32_t p[4] = {s.px[y0 * s.w + x0], s.px[y0 * s.w + x1], s.px[y1 * s.w + x0], s.px[y1 * s.w + x1]};
            uint32_t r = 0;
            for (int c = 0; c < 32; c += 8) {
                uint32_t sum = 2;
                for (uint32_t v : p) sum += v >> c & 255;
                r |= (sum / 4) << c;
            }
            d->px[y * w + x] = r;
        }
    return true;
}

bool scale_image(const Image& s, Image* d, unsigned w, unsigned h, DWORD filter) {
    DWORD kind = filter & 0xFF;
    if (kind != D3DX_FILTER_NONE && kind != D3DX_FILTER_POINT && w * 2 == s.w && h * 2 == s.h) return half_image(s, d);
    if (!d->alloc(w, h)) return false;
    for (unsigned y = 0; y < h; y++) {
        unsigned sy = (unsigned)(((uint64_t)y * 2 + 1) * s.h / (2 * (uint64_t)h));
        for (unsigned x = 0; x < w; x++) {
            unsigned sx = (unsigned)(((uint64_t)x * 2 + 1) * s.w / (2 * (uint64_t)w));
            d->px[y * w + x] = s.px[sy * s.w + sx];
        }
    }
    return true;
}

// ---- DDS (DirectDraw Surface): 'DDS ' + DDS_HEADER (124 bytes) [+ DDS_HEADER_DXT10 when FourCC 'DX10'] ----
namespace {
enum : uint32_t {
    DDSD_MIPMAPCOUNT = 0x20000, DDSD_DEPTH = 0x800000,
    DDPF_ALPHAPIXELS = 0x1, DDPF_ALPHA = 0x2, DDPF_FOURCC = 0x4, DDPF_RGB = 0x40, DDPF_LUMINANCE = 0x20000,
    DDPF_BUMPDUDV = 0x80000,
    DDSCAPS2_CUBEMAP = 0x200, DDSCAPS2_CUBEMAP_ALLFACES = 0xFC00, DDSCAPS2_VOLUME = 0x200000,
};
struct PixelFormat { uint32_t size, flags, fourcc, bits, r, g, b, a; };
struct Header {
    uint32_t size, flags, height, width, pitch, depth, mips, reserved1[11];
    PixelFormat pf;
    uint32_t caps, caps2, caps3, caps4, reserved2;
};
static_assert(sizeof(Header) == 124, "DDS_HEADER");

D3DFORMAT dds_format(const PixelFormat& pf) {
    if (pf.flags & DDPF_FOURCC)   // DXT1..5, or a D3DFORMAT number (D3DX writes the float/16-bit formats so)
        return (D3DFORMAT)pf.fourcc;
    bool a = pf.flags & DDPF_ALPHAPIXELS;
    if (pf.flags & DDPF_RGB) {
        switch (pf.bits) {
        case 32:
            if (pf.r == 0xFF0000 && pf.g == 0xFF00 && pf.b == 0xFF) return a && pf.a ? D3DFMT_A8R8G8B8 : D3DFMT_X8R8G8B8;
            if (pf.r == 0xFF && pf.g == 0xFF00 && pf.b == 0xFF0000) return a && pf.a ? D3DFMT_A8B8G8R8 : D3DFMT_X8B8G8R8;
            if (pf.r == 0x3FF00000 && pf.b == 0x3FF) return D3DFMT_A2R10G10B10;
            if (pf.r == 0x3FF && pf.b == 0x3FF00000) return D3DFMT_A2B10G10R10;
            if (pf.r == 0xFFFF && pf.g == 0xFFFF0000) return D3DFMT_G16R16;
            break;
        case 24:
            if (pf.r == 0xFF0000 && pf.g == 0xFF00 && pf.b == 0xFF) return D3DFMT_R8G8B8;
            break;
        case 16:
            if (pf.r == 0xF800 && pf.g == 0x7E0 && pf.b == 0x1F) return D3DFMT_R5G6B5;
            if (pf.r == 0x7C00 && pf.g == 0x3E0 && pf.b == 0x1F) return a && pf.a ? D3DFMT_A1R5G5B5 : D3DFMT_X1R5G5B5;
            if (pf.r == 0xF00 && pf.g == 0xF0 && pf.b == 0xF) return a && pf.a ? D3DFMT_A4R4G4B4 : D3DFMT_X4R4G4B4;
            if (pf.r == 0xE0 && pf.g == 0x1C && pf.b == 0x3) return D3DFMT_A8R3G3B2;
            break;
        case 8:
            if (pf.r == 0xE0 && pf.g == 0x1C && pf.b == 0x3) return D3DFMT_R3G3B2;
            break;
        }
        return D3DFMT_UNKNOWN;
    }
    if (pf.flags & DDPF_LUMINANCE) {
        if (pf.bits == 8) return a && pf.a == 0xF0 ? D3DFMT_A4L4 : D3DFMT_L8;
        if (pf.bits == 16) return a && pf.a ? D3DFMT_A8L8 : D3DFMT_L16;
        return D3DFMT_UNKNOWN;
    }
    if (pf.flags & DDPF_BUMPDUDV) {
        if (pf.bits == 16 && pf.r == 0xFF) return D3DFMT_V8U8;
        if (pf.bits == 32 && pf.r == 0xFF && pf.a) return D3DFMT_Q8W8V8U8;
        if (pf.bits == 32 && pf.r == 0xFFFF) return D3DFMT_V16U16;
        return D3DFMT_UNKNOWN;
    }
    if ((pf.flags & DDPF_ALPHA) && pf.bits == 8) return D3DFMT_A8;
    return D3DFMT_UNKNOWN;
}
} // namespace

HRESULT dds_parse(const void* src, UINT size, Dds* out) {
    const uint8_t* p = (const uint8_t*)src;
    if (!p || size < 4 + sizeof(Header) || memcmp(p, "DDS ", 4)) return D3DXERR_INVALIDDATA;
    Header h;
    memcpy(&h, p + 4, sizeof h);
    if (h.size != 124 || !h.width || !h.height) return D3DXERR_INVALIDDATA;
    size_t off = 4 + sizeof(Header);
    if ((h.pf.flags & DDPF_FOURCC) && h.pf.fourcc == MAKEFOURCC('D', 'X', '1', '0')) {
        D3DX_LOG_LIMITED(4, "d3dx: DDS with a DX10 header is not supported\n");
        return D3DXERR_INVALIDDATA;
    }
    out->d3dfmt = dds_format(h.pf);
    out->fmt = format_info(out->d3dfmt);
    if (!out->fmt) {
        D3DX_LOG_LIMITED(8, "d3dx: DDS pixel format not supported (flags %#x fourcc %#x bits %u)\n",
                         (unsigned)h.pf.flags, (unsigned)h.pf.fourcc, (unsigned)h.pf.bits);
        return D3DXERR_INVALIDDATA;
    }
    out->w = h.width;
    out->h = h.height;
    out->d = (h.caps2 & DDSCAPS2_VOLUME) && (h.flags & DDSD_DEPTH) && h.depth ? h.depth : 1;
    unsigned max_mips = 1, m = out->w > out->h ? out->w : out->h;
    if (out->d > m) m = out->d;
    while (m > 1) { m >>= 1; max_mips++; }
    out->mips = (h.flags & DDSD_MIPMAPCOUNT) && h.mips ? h.mips : 1;
    if (out->mips > max_mips) out->mips = max_mips;
    if (h.caps2 & DDSCAPS2_CUBEMAP) {
        out->type = D3DRTYPE_CUBETEXTURE;
        out->faces = (h.caps2 & DDSCAPS2_CUBEMAP_ALLFACES) >> 10;
        if (!out->faces || out->w != out->h) return D3DXERR_INVALIDDATA;
    } else {
        out->type = out->d > 1 ? D3DRTYPE_VOLUMETEXTURE : D3DRTYPE_TEXTURE;
        out->faces = 1;
    }
    out->data = p + off;
    out->size = size - off;
    unsigned nfaces = __builtin_popcount(out->faces);
    size_t need = 0;
    for (unsigned l = 0; l < out->mips; l++) need += dds_level_size(*out, l);
    if (need * nfaces > out->size) {
        D3DX_LOG_LIMITED(8, "d3dx: DDS %ux%ux%u, %u levels: %zu bytes of data, %zu needed\n", out->w, out->h, out->d,
                         out->mips, out->size, need * nfaces);
        return D3DXERR_INVALIDDATA;
    }
    return D3D_OK;
}

size_t dds_level_size(const Dds& d, unsigned l) {
    return slice_size(d.fmt, level_dim(d.w, l), level_dim(d.h, l)) * (d.type == D3DRTYPE_VOLUMETEXTURE ? level_dim(d.d, l) : 1);
}

// face = index among D3DCUBEMAP_FACES (0..5); data holds the present faces in that order, each with all its levels
const uint8_t* dds_level(const Dds& d, unsigned face, unsigned level) {
    if (!(d.faces & (1u << face))) return nullptr;
    size_t chain = 0;
    for (unsigned l = 0; l < d.mips; l++) chain += dds_level_size(d, l);
    size_t off = (size_t)__builtin_popcount(d.faces & ((1u << face) - 1)) * chain;
    for (unsigned l = 0; l < level; l++) off += dds_level_size(d, l);
    return d.data + off;
}

} // namespace fnvnx_d3dx
