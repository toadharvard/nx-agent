// fnv-nx: D3DXSaveTextureToFileA / D3DXSaveSurfaceToFileA (S4, on S6c's d3dx): the game's screenshot.
//
// The exe calls it once, from 0x007a0797 (the screenshot command), with D3DXIFF_PNG and the renderer's texture.
// Level 0 of a texture (or a surface) is read through LockRect; a texture the CPU cannot lock (a DEFAULT-pool render
// target) is copied first with GetRenderTargetData into a SYSTEMMEM surface, as D3DX does. Pixels go through
// decode_rows (any loadable format) to A8R8G8B8 and are written as PNG (8-bit RGB, deflate "stored" blocks: the
// guest has no zlib, and a stored stream is a valid PNG), BMP (24-bit), TGA (32-bit) or DDS (A8R8G8B8). JPG and the
// other formats fail with D3DERR_INVALIDCALL. The file is written through CreateFileA, so Windows paths map as for
// every other file of the game.
#include <stdio.h>
#include "d3dx_internal.h"

using namespace fnvnx_d3dx;

namespace {

struct Out {   // a growing byte buffer
    uint8_t *p = nullptr;
    size_t n = 0, cap = 0;
    bool ok = true;
    void put(const void *src, size_t k)
    {
        if (!ok) return;
        if (n + k > cap) {
            size_t c = cap ? cap * 2 : 4096;
            while (c < n + k) c *= 2;
            uint8_t *q = (uint8_t *)realloc(p, c);
            if (!q) { ok = false; return; }
            p = q;
            cap = c;
        }
        memcpy(p + n, src, k);
        n += k;
    }
    void u8(unsigned v) { uint8_t b = (uint8_t)v; put(&b, 1); }
    void le16(unsigned v) { u8(v); u8(v >> 8); }
    void le32(uint32_t v) { le16(v & 0xffff); le16(v >> 16); }
    void be32(uint32_t v) { u8(v >> 24); u8(v >> 16); u8(v >> 8); u8(v); }
    ~Out() { free(p); }
};

uint32_t crc32_of(const uint8_t *p, size_t n, uint32_t c = 0)
{
    static uint32_t t[256];
    if (!t[1])
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t k = i;
            for (int j = 0; j < 8; j++) k = k & 1 ? 0xEDB88320u ^ (k >> 1) : k >> 1;
            t[i] = k;
        }
    c = ~c;
    for (size_t i = 0; i < n; i++) c = t[(c ^ p[i]) & 0xff] ^ (c >> 8);
    return ~c;
}

void png_chunk(Out &o, const char *type, const uint8_t *data, size_t n)
{
    o.be32((uint32_t)n);
    size_t at = o.n;
    o.put(type, 4);
    o.put(data, n);
    if (o.ok) o.be32(crc32_of(o.p + at, n + 4));
}

void write_png(Out &o, const Image &img)
{
    static const uint8_t sig[8] = {0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'};
    o.put(sig, 8);
    uint8_t ihdr[13] = {0};
    for (int i = 0; i < 4; i++) {
        ihdr[i] = (uint8_t)(img.w >> (24 - 8 * i));
        ihdr[4 + i] = (uint8_t)(img.h >> (24 - 8 * i));
    }
    ihdr[8] = 8;   // bit depth
    ihdr[9] = 2;   // RGB (a screenshot: the back buffer's alpha means nothing)
    png_chunk(o, "IHDR", ihdr, sizeof ihdr);
    // raw scanlines (filter 0) in a zlib stream of stored blocks (at most 65535 bytes each)
    Out raw;
    for (unsigned y = 0; y < img.h; y++) {
        raw.u8(0);
        for (unsigned x = 0; x < img.w; x++) {
            uint32_t c = img.px[(size_t)y * img.w + x];
            raw.u8(c >> 16); raw.u8(c >> 8); raw.u8(c);
        }
    }
    Out z;
    z.u8(0x78); z.u8(0x01);
    size_t off = 0;
    do {
        size_t k = raw.n - off < 65535 ? raw.n - off : 65535;
        z.u8(off + k == raw.n ? 1 : 0);   // BFINAL, BTYPE 00
        z.le16((unsigned)k); z.le16((unsigned)~k & 0xffff);
        z.put(raw.p + off, k);
        off += k;
    } while (off < raw.n);
    uint32_t a = 1, b = 0;   // adler32
    for (size_t i = 0; i < raw.n; i++) { a = (a + raw.p[i]) % 65521; b = (b + a) % 65521; }
    z.be32((b << 16) | a);
    o.ok = o.ok && raw.ok && z.ok;
    if (o.ok) png_chunk(o, "IDAT", z.p, z.n);
    png_chunk(o, "IEND", nullptr, 0);
}

void write_bmp(Out &o, const Image &img)
{
    uint32_t row = (img.w * 3 + 3) & ~3u, size = 54 + row * img.h;
    o.u8('B'); o.u8('M'); o.le32(size); o.le32(0); o.le32(54);
    o.le32(40); o.le32(img.w); o.le32(img.h); o.le16(1); o.le16(24); o.le32(0); o.le32(row * img.h);
    o.le32(2835); o.le32(2835); o.le32(0); o.le32(0);
    for (unsigned y = img.h; y-- > 0;) {   // bottom-up
        for (unsigned x = 0; x < img.w; x++) {
            uint32_t c = img.px[(size_t)y * img.w + x];
            o.u8(c); o.u8(c >> 8); o.u8(c >> 16);
        }
        for (unsigned p = img.w * 3; p < row; p++) o.u8(0);
    }
}

void write_tga(Out &o, const Image &img)
{
    uint8_t h[18] = {0};
    h[2] = 2;   // uncompressed true colour
    h[12] = (uint8_t)img.w; h[13] = (uint8_t)(img.w >> 8); h[14] = (uint8_t)img.h; h[15] = (uint8_t)(img.h >> 8);
    h[16] = 32; h[17] = 0x28;   // 8 alpha bits, top-left origin
    o.put(h, sizeof h);
    for (size_t i = 0; i < (size_t)img.w * img.h; i++) o.le32(img.px[i]);   // B G R A
}

void write_dds(Out &o, const Image &img)
{
    o.le32(0x20534444);   // "DDS "
    o.le32(124); o.le32(0x1 | 0x2 | 0x4 | 0x8 | 0x1000); o.le32(img.h); o.le32(img.w); o.le32(img.w * 4);
    o.le32(0); o.le32(1);
    for (int i = 0; i < 11; i++) o.le32(0);
    o.le32(32); o.le32(0x41); o.le32(0); o.le32(32);   // pixel format: RGB | ALPHAPIXELS, 32 bits
    o.le32(0x00ff0000); o.le32(0x0000ff00); o.le32(0x000000ff); o.le32(0xff000000);
    o.le32(0x1000); o.le32(0); o.le32(0); o.le32(0); o.le32(0);
    o.put(img.px, (size_t)img.w * img.h * 4);
}

// level 0 of a surface as A8R8G8B8; a surface the CPU cannot lock is read back first
HRESULT read_surface(IDirect3DSurface9 *s, Image *img)
{
    D3DSURFACE_DESC d;
    HRESULT hr = s->GetDesc(&d);
    if (FAILED(hr)) return hr;
    const Format *f = format_info(d.Format);
    if (!f || !f->argb) {
        D3DX_LOG_LIMITED(4, "fnvnx d3dx: D3DXSave*ToFile: surface format %u not convertible\n", (unsigned)d.Format);
        return D3DERR_INVALIDCALL;
    }
    IDirect3DSurface9 *src = s, *tmp = nullptr;
    D3DLOCKED_RECT lr;
    hr = s->LockRect(&lr, nullptr, D3DLOCK_READONLY);
    if (FAILED(hr)) {   // a render target: GetRenderTargetData into system memory
        IDirect3DDevice9 *dev = nullptr;
        if (FAILED(s->GetDevice(&dev)) || !dev) return hr;
        hr = dev->CreateOffscreenPlainSurface(d.Width, d.Height, d.Format, D3DPOOL_SYSTEMMEM, &tmp, nullptr);
        if (SUCCEEDED(hr)) hr = dev->GetRenderTargetData(s, tmp);
        dev->Release();
        if (SUCCEEDED(hr)) hr = tmp->LockRect(&lr, nullptr, D3DLOCK_READONLY);
        if (FAILED(hr)) {
            if (tmp) tmp->Release();
            return hr;
        }
        src = tmp;
    }
    img->w = d.Width;
    img->h = d.Height;
    if (!img->alloc(d.Width, d.Height)) {
        src->UnlockRect();
        if (tmp) tmp->Release();
        return E_OUTOFMEMORY;
    }
    decode_rows(f, (const uint8_t *)lr.pBits, lr.Pitch, 0, 0, img);
    src->UnlockRect();
    if (tmp) tmp->Release();
    return S_OK;
}

HRESULT save(const char *file, D3DXIMAGE_FILEFORMAT fmt, const Image &img)
{
    Out o;
    switch (fmt) {
    case D3DXIFF_PNG: write_png(o, img); break;
    case D3DXIFF_BMP: case D3DXIFF_DIB: write_bmp(o, img); break;
    case D3DXIFF_TGA: write_tga(o, img); break;
    case D3DXIFF_DDS: write_dds(o, img); break;
    default:
        D3DX_LOG_LIMITED(4, "fnvnx d3dx: D3DXSave*ToFile: image format %d not written\n", (int)fmt);
        return D3DERR_INVALIDCALL;
    }
    if (!o.ok) return E_OUTOFMEMORY;
    HANDLE h = CreateFileA(file, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return D3DERR_INVALIDCALL;
    DWORD put = 0;
    BOOL w = WriteFile(h, o.p, (DWORD)o.n, &put, nullptr);
    CloseHandle(h);
    return w && put == (DWORD)o.n ? D3D_OK : D3DERR_INVALIDCALL;
}

} // namespace

extern "C" HRESULT WINAPI D3DXSaveSurfaceToFileA(LPCSTR file, D3DXIMAGE_FILEFORMAT fmt, LPDIRECT3DSURFACE9 surf,
                                                 const PALETTEENTRY *, const RECT *rect)
{
    if (!file || !surf || rect) return D3DERR_INVALIDCALL;   // a source rectangle: not needed by the game
    Image img;
    HRESULT hr = read_surface(surf, &img);
    if (SUCCEEDED(hr)) hr = save(file, fmt, img);
    img.release();
    return hr;
}

extern "C" HRESULT WINAPI D3DXSaveTextureToFileA(LPCSTR file, D3DXIMAGE_FILEFORMAT fmt, LPDIRECT3DBASETEXTURE9 tex,
                                                 const PALETTEENTRY *pal)
{
    if (!file || !tex) return D3DERR_INVALIDCALL;
    IDirect3DSurface9 *s = nullptr;
    HRESULT hr;
    switch (tex->GetType()) {
    case D3DRTYPE_TEXTURE: hr = ((IDirect3DTexture9 *)tex)->GetSurfaceLevel(0, &s); break;
    case D3DRTYPE_CUBETEXTURE:   // the +X face, as an image file has one
        hr = ((IDirect3DCubeTexture9 *)tex)->GetCubeMapSurface(D3DCUBEMAP_FACE_POSITIVE_X, 0, &s);
        break;
    default: return D3DERR_INVALIDCALL;
    }
    if (FAILED(hr) || !s) return FAILED(hr) ? hr : D3DERR_INVALIDCALL;
    hr = D3DXSaveSurfaceToFileA(file, fmt, s, pal, nullptr);
    s->Release();
    return hr;
}
