// fnv-nx D3DX9 (stream S6c-nine, guest ILP32): DDS texture loading and surface copies on top of the D3D9 proxy.
//
// D3DX{Create,CreateCube,CreateVolume}TextureFromFileInMemory(Ex), D3DXGetImageInfoFromFileInMemory: DDS only (the
// game's data is DDS; other file formats fail with D3DXERR_INVALIDDATA and a log line). The common case (file
// format, file size, file mip chain, no colour key) creates the texture and copies every level straight into
// LockRect/LockBox, row by row: DXT stays compressed. A DEFAULT-pool target (not lockable in D3D9) is filled through
// a SYSTEMMEM staging texture and UpdateTexture. Otherwise the A8R8G8B8 path converts (format the device lacks,
// e.g. R8G8B8 -> X8R8G8B8; another requested format; colour key), point/box-scales a requested size and box-filters
// mip levels the file does not have (uncompressed formats only; for DXT the chain is clamped to the file's).
// D3DX_SKIP_DDS_MIP_LEVELS in Filter drops top levels. D3DX_DEFAULT sizes keep the file size (the device has no POW2
// restriction) and D3DX_DEFAULT levels keep the file's chain.
//
// D3DXLoadSurfaceFromMemory / FromSurface: same-format copies (DXT by blocks) or the A8R8G8B8 path; DEFAULT ->
// DEFAULT goes through StretchRect, a render-target source through GetRenderTargetData.
#include <stdio.h>
#include "d3dx_internal.h"

using namespace fnvnx_d3dx;

namespace {

inline bool is_default(UINT v) { return v == 0 || v == D3DX_DEFAULT || v == D3DX_DEFAULT_NONPOW2 || v == D3DX_FROM_FILE; }
inline unsigned full_chain(unsigned w, unsigned h, unsigned d) {
    unsigned m = w > h ? w : h, n = 1;
    if (d > m) m = d;
    while (m > 1) { m >>= 1; n++; }
    return n;
}

template <class T> void release(T*& p) { if (p) { p->Release(); p = nullptr; } }

// Device support of a texture format (one IDirect3D9::CheckDeviceFormat per format/type, cached).
bool device_supports(IDirect3DDevice9* dev, D3DFORMAT fmt, D3DRESOURCETYPE type) {
    static uint64_t cache[64];
    uint64_t key = (uint64_t)(uint32_t)fmt << 32 | (uint32_t)type << 8;
    for (uint64_t& e : cache) {
        uint64_t v = __atomic_load_n(&e, __ATOMIC_RELAXED);
        if (v && (v & ~0xFFull) == key) return v & 1;
    }
    IDirect3D9* d3d = nullptr;
    bool ok = true;   // cannot ask: assume it is
    if (SUCCEEDED(dev->GetDirect3D(&d3d)) && d3d) {
        ok = SUCCEEDED(d3d->CheckDeviceFormat(0, D3DDEVTYPE_HAL, D3DFMT_X8R8G8B8, 0, type, fmt));
        d3d->Release();
    }
    for (uint64_t& e : cache) {
        uint64_t zero = 0;
        if (__atomic_compare_exchange_n(&e, &zero, key | 0x80 | (ok ? 1 : 0), false, __ATOMIC_RELAXED, __ATOMIC_RELAXED))
            break;
    }
    return ok;
}

struct Plan {
    const Format* src;
    const Format* dst;
    D3DFORMAT dfmt;
    unsigned w, h, d;          // destination level 0
    unsigned levels;           // destination levels
    unsigned skip;             // file levels dropped at the top
    unsigned avail;            // file levels from `skip` on
    bool convert;              // A8R8G8B8 path
    bool scale;                // level 0 is not the file's level `skip`
    DWORD filter;
    D3DCOLOR key;
};

HRESULT make_plan(IDirect3DDevice9* dev, const Dds& dds, D3DRESOURCETYPE type, UINT W, UINT H, UINT D, UINT Mips,
                  D3DFORMAT Format, DWORD Filter, D3DCOLOR key, Plan* p) {
    p->src = dds.fmt;
    p->skip = 0;
    if (Filter != D3DX_DEFAULT) p->skip = (Filter >> D3DX_SKIP_DDS_MIP_LEVELS_SHIFT) & D3DX_SKIP_DDS_MIP_LEVELS_MASK;
    if (p->skip >= dds.mips) p->skip = dds.mips - 1;
    p->avail = dds.mips - p->skip;
    unsigned bw = level_dim(dds.w, p->skip), bh = level_dim(dds.h, p->skip);
    unsigned bd = type == D3DRTYPE_VOLUMETEXTURE ? level_dim(dds.d, p->skip) : 1;
    p->w = is_default(W) ? bw : W;
    p->h = is_default(H) ? bh : H;
    p->d = type == D3DRTYPE_VOLUMETEXTURE && !is_default(D) ? D : bd;
    p->dfmt = (Format == D3DFMT_UNKNOWN || Format == D3DFMT_FROM_FILE || Format == (D3DFORMAT)D3DX_DEFAULT) ? dds.d3dfmt
                                                                                                          : Format;
    if (p->dfmt == D3DFMT_R8G8B8) p->dfmt = D3DFMT_X8R8G8B8;   // no 24-bit textures on D3D9 hardware (nor Nine)
    else if (!device_supports(dev, p->dfmt, type) && dds.fmt->argb)
        p->dfmt = dds.fmt->alpha || key ? D3DFMT_A8R8G8B8 : D3DFMT_X8R8G8B8;
    p->dst = format_info(p->dfmt);
    if (!p->dst) return D3DERR_NOTAVAILABLE;
    p->scale = p->w != bw || p->h != bh || p->d != bd;
    p->convert = p->dfmt != dds.d3dfmt || p->scale || key;
    p->filter = Filter == D3DX_DEFAULT ? D3DX_FILTER_TRIANGLE : Filter;
    p->key = key;
    if (p->convert && !(p->src->argb && p->dst->argb)) {
        D3DX_LOG_LIMITED(8, "d3dx: cannot convert DDS format %#x to %#x (%ux%u -> %ux%u, key %#x)\n",
                         (unsigned)dds.d3dfmt, (unsigned)p->dfmt, bw, bh, p->w, p->h, (unsigned)key);
        return D3DERR_NOTAVAILABLE;
    }
    if (type == D3DRTYPE_VOLUMETEXTURE && p->scale) return D3DERR_NOTAVAILABLE;
    unsigned chain = full_chain(p->w, p->h, p->d);
    p->levels = is_default(Mips) ? (p->scale ? chain : p->avail) : Mips;
    if (p->levels > chain) p->levels = chain;
    // levels the file lacks are generated on the A8R8G8B8 path only (2D/cube); otherwise the chain is the file's
    if (p->levels > p->avail && !p->scale && (!p->dst->argb || !p->src->argb || type == D3DRTYPE_VOLUMETEXTURE))
        p->levels = p->avail;
    if (p->levels > p->avail) p->convert = true;   // generated levels
    return D3D_OK;
}

// Fills one 2D level (dst level l of face `face`) of plan p from the file; `prev` carries the A8R8G8B8 image of
// the level above on the conversion path (next mip generated from it when the file has none).
HRESULT fill_2d(const Plan& p, const Dds& dds, unsigned face, unsigned l, uint8_t* dst, int pitch, Image* prev) {
    unsigned w = level_dim(p.w, l), h = level_dim(p.h, l);
    const uint8_t* src = l < p.avail && !p.scale ? dds_level(dds, face, p.skip + l) : nullptr;
    if (!p.convert) {
        if (!src) return D3DERR_INVALIDCALL;
        copy_rows(dst, pitch, src, (int)row_pitch(p.src, w), row_pitch(p.dst, w), row_count(p.dst, h));
        return D3D_OK;
    }
    Image img;
    if (src) {                                          // file level, converted
        if (!img.alloc(w, h)) return E_OUTOFMEMORY;
        decode_rows(p.src, src, (int)row_pitch(p.src, w), 0, 0, &img);
        apply_color_key(&img, p.key);
    } else if (l == 0) {                                // requested size: scale the file's top level
        const uint8_t* top = dds_level(dds, face, p.skip);
        Image s;
        unsigned sw = level_dim(dds.w, p.skip), sh = level_dim(dds.h, p.skip);
        if (!top || !s.alloc(sw, sh)) return E_OUTOFMEMORY;
        decode_rows(p.src, top, (int)row_pitch(p.src, sw), 0, 0, &s);
        apply_color_key(&s, p.key);
        bool ok = scale_image(s, &img, w, h, p.filter);
        s.release();
        if (!ok) return E_OUTOFMEMORY;
    } else {                                            // generated mip
        if (!prev->px || !half_image(*prev, &img)) return E_OUTOFMEMORY;
    }
    encode_rows(p.dst, img, dst, pitch);
    prev->release();
    *prev = img;
    return D3D_OK;
}

void zero_2d(const Plan& p, unsigned l, uint8_t* dst, int pitch) {
    unsigned w = level_dim(p.w, l), h = level_dim(p.h, l);
    for (unsigned y = 0; y < row_count(p.dst, h); y++) memset(dst + (size_t)y * pitch, 0, row_pitch(p.dst, w));
}

HRESULT load_texture(IDirect3DDevice9* dev, const Dds& dds, const Plan& p, DWORD usage, D3DPOOL pool,
                     IDirect3DTexture9** out) {
    IDirect3DTexture9 *tex = nullptr, *stage = nullptr;
    HRESULT hr = dev->CreateTexture(p.w, p.h, (usage & D3DUSAGE_AUTOGENMIPMAP) ? 0 : p.levels, usage, p.dfmt, pool,
                                    &tex, nullptr);
    if (FAILED(hr)) return hr;
    IDirect3DTexture9* target = tex;
    if (pool == D3DPOOL_DEFAULT && !(usage & D3DUSAGE_DYNAMIC)) {   // not lockable: SYSTEMMEM staging + UpdateTexture
        hr = dev->CreateTexture(p.w, p.h, tex->GetLevelCount(), 0, p.dfmt, D3DPOOL_SYSTEMMEM, &stage, nullptr);
        if (FAILED(hr)) { release(tex); return hr; }
        target = stage;
    }
    unsigned n = target->GetLevelCount();
    if (n > p.levels) n = p.levels;
    Image prev;
    for (unsigned l = 0; l < n && SUCCEEDED(hr); l++) {
        D3DLOCKED_RECT lr;
        hr = target->LockRect(l, &lr, nullptr, 0);
        if (FAILED(hr)) break;
        hr = fill_2d(p, dds, 0, l, (uint8_t*)lr.pBits, lr.Pitch, &prev);
        target->UnlockRect(l);
    }
    prev.release();
    if (SUCCEEDED(hr) && stage) hr = dev->UpdateTexture(stage, tex);
    release(stage);
    if (FAILED(hr)) { release(tex); return hr; }
    *out = tex;
    return D3D_OK;
}

HRESULT load_cube(IDirect3DDevice9* dev, const Dds& dds, const Plan& p, DWORD usage, D3DPOOL pool,
                  IDirect3DCubeTexture9** out) {
    IDirect3DCubeTexture9 *tex = nullptr, *stage = nullptr;
    HRESULT hr = dev->CreateCubeTexture(p.w, (usage & D3DUSAGE_AUTOGENMIPMAP) ? 0 : p.levels, usage, p.dfmt, pool,
                                        &tex, nullptr);
    if (FAILED(hr)) return hr;
    IDirect3DCubeTexture9* target = tex;
    if (pool == D3DPOOL_DEFAULT && !(usage & D3DUSAGE_DYNAMIC)) {
        hr = dev->CreateCubeTexture(p.w, tex->GetLevelCount(), 0, p.dfmt, D3DPOOL_SYSTEMMEM, &stage, nullptr);
        if (FAILED(hr)) { release(tex); return hr; }
        target = stage;
    }
    unsigned n = target->GetLevelCount();
    if (n > p.levels) n = p.levels;
    for (unsigned f = 0; f < 6 && SUCCEEDED(hr); f++) {
        Image prev;
        for (unsigned l = 0; l < n && SUCCEEDED(hr); l++) {
            D3DLOCKED_RECT lr;
            hr = target->LockRect((D3DCUBEMAP_FACES)f, l, &lr, nullptr, 0);
            if (FAILED(hr)) break;
            if (dds.faces & (1u << f))
                hr = fill_2d(p, dds, f, l, (uint8_t*)lr.pBits, lr.Pitch, &prev);
            else
                zero_2d(p, l, (uint8_t*)lr.pBits, lr.Pitch);   // face missing from the file
            target->UnlockRect((D3DCUBEMAP_FACES)f, l);
        }
        prev.release();
    }
    if (SUCCEEDED(hr) && stage) hr = dev->UpdateTexture(stage, tex);
    release(stage);
    if (FAILED(hr)) { release(tex); return hr; }
    *out = tex;
    return D3D_OK;
}

HRESULT load_volume(IDirect3DDevice9* dev, const Dds& dds, const Plan& p, DWORD usage, D3DPOOL pool,
                    IDirect3DVolumeTexture9** out) {
    IDirect3DVolumeTexture9 *tex = nullptr, *stage = nullptr;
    HRESULT hr = dev->CreateVolumeTexture(p.w, p.h, p.d, p.levels, usage, p.dfmt, pool, &tex, nullptr);
    if (FAILED(hr)) return hr;
    IDirect3DVolumeTexture9* target = tex;
    if (pool == D3DPOOL_DEFAULT && !(usage & D3DUSAGE_DYNAMIC)) {
        hr = dev->CreateVolumeTexture(p.w, p.h, p.d, tex->GetLevelCount(), 0, p.dfmt, D3DPOOL_SYSTEMMEM, &stage, nullptr);
        if (FAILED(hr)) { release(tex); return hr; }
        target = stage;
    }
    unsigned n = target->GetLevelCount();
    if (n > p.levels) n = p.levels;
    for (unsigned l = 0; l < n && SUCCEEDED(hr); l++) {
        D3DLOCKED_BOX lb;
        hr = target->LockBox(l, &lb, nullptr, 0);
        if (FAILED(hr)) break;
        unsigned w = level_dim(p.w, l), h = level_dim(p.h, l), d = level_dim(p.d, l);
        size_t slice = slice_size(p.src, w, h);
        const uint8_t* src = dds_level(dds, 0, p.skip + l);
        for (unsigned z = 0; z < d && SUCCEEDED(hr); z++) {
            uint8_t* dst = (uint8_t*)lb.pBits + (size_t)z * lb.SlicePitch;
            if (!p.convert) {
                copy_rows(dst, lb.RowPitch, src + z * slice, (int)row_pitch(p.src, w), row_pitch(p.dst, w), row_count(p.dst, h));
                continue;
            }
            Image img;
            if (!img.alloc(w, h)) { hr = E_OUTOFMEMORY; break; }
            decode_rows(p.src, src + z * slice, (int)row_pitch(p.src, w), 0, 0, &img);
            apply_color_key(&img, p.key);
            encode_rows(p.dst, img, dst, lb.RowPitch);
            img.release();
        }
        target->UnlockBox(l);
    }
    if (SUCCEEDED(hr) && stage) hr = dev->UpdateTexture(stage, tex);
    release(stage);
    if (FAILED(hr)) { release(tex); return hr; }
    *out = tex;
    return D3D_OK;
}

HRESULT parse_file(LPCVOID src, UINT size, Dds* dds) {
    HRESULT hr = dds_parse(src, size, dds);
    if (FAILED(hr) && src && size >= 4 && memcmp(src, "DDS ", 4)) {
        const uint8_t* b = (const uint8_t*)src;
        D3DX_LOG_LIMITED(8, "d3dx: image file not supported (only DDS): %u bytes, starts %02x %02x %02x %02x\n", size,
                         b[0], b[1], b[2], b[3]);
    }
    return hr;
}

void fill_info(const Dds& dds, D3DXIMAGE_INFO* info) {
    if (!info) return;
    info->Width = dds.w;
    info->Height = dds.h;
    info->Depth = dds.d;
    info->MipLevels = dds.mips;
    info->Format = dds.d3dfmt;
    info->ResourceType = dds.type;
    info->ImageFileFormat = D3DXIFF_DDS;
}

// ---- surfaces ----
HRESULT write_rect(const Format* df, uint8_t* dst, int dpitch, unsigned dw, unsigned dh, const Format* sf,
                   const uint8_t* src, int spitch, const RECT& sr, DWORD filter, D3DCOLOR key) {
    unsigned sw = (unsigned)(sr.right - sr.left), sh = (unsigned)(sr.bottom - sr.top);
    if (sf == df && sw == dw && sh == dh && !key) {
        if (sf->block && ((sr.left | sr.top) & 3)) return D3DERR_INVALIDCALL;
        // (block-aligned for DXT: row_count/row_pitch of the offset are then whole blocks)
        const uint8_t* s = src + (size_t)row_count(sf, (unsigned)sr.top) * spitch + row_pitch(sf, (unsigned)sr.left);
        copy_rows(dst, dpitch, s, spitch, row_pitch(df, dw), row_count(df, dh));
        return D3D_OK;
    }
    if (!sf->argb || !df->argb) {
        D3DX_LOG_LIMITED(8, "d3dx: LoadSurface %#x %ux%u -> %#x %ux%u not supported\n", (unsigned)sf->fmt, sw, sh,
                         (unsigned)df->fmt, dw, dh);
        return D3DERR_NOTAVAILABLE;
    }
    Image img, scaled;
    if (!img.alloc(sw, sh)) return E_OUTOFMEMORY;
    decode_rows(sf, src, spitch, (unsigned)sr.left, (unsigned)sr.top, &img);
    apply_color_key(&img, key);
    if (sw != dw || sh != dh) {
        bool ok = scale_image(img, &scaled, dw, dh, filter == D3DX_DEFAULT ? D3DX_FILTER_TRIANGLE : filter);
        img.release();
        if (!ok) return E_OUTOFMEMORY;
        img = scaled;
    }
    encode_rows(df, img, dst, dpitch);
    img.release();
    return D3D_OK;
}

bool valid_rect(const RECT& r, unsigned w, unsigned h) {
    return r.left >= 0 && r.top >= 0 && r.right > r.left && r.bottom > r.top && (unsigned)r.right <= w &&
           (unsigned)r.bottom <= h;
}

} // namespace

extern "C" {

HRESULT WINAPI D3DXGetImageInfoFromFileInMemory(LPCVOID pSrcData, UINT SrcDataSize, D3DXIMAGE_INFO* pSrcInfo) {
    if (!pSrcData || !SrcDataSize) return D3DERR_INVALIDCALL;
    Dds dds;
    HRESULT hr = parse_file(pSrcData, SrcDataSize, &dds);
    if (FAILED(hr)) return hr;
    fill_info(dds, pSrcInfo);
    return D3D_OK;
}

HRESULT WINAPI D3DXCreateTextureFromFileInMemoryEx(LPDIRECT3DDEVICE9 pDevice, LPCVOID pSrcData, UINT SrcDataSize,
                                                   UINT Width, UINT Height, UINT MipLevels, DWORD Usage,
                                                   D3DFORMAT Format, D3DPOOL Pool, DWORD Filter, DWORD MipFilter,
                                                   D3DCOLOR ColorKey, D3DXIMAGE_INFO* pSrcInfo, PALETTEENTRY* pPalette,
                                                   LPDIRECT3DTEXTURE9* ppTexture) {
    (void)MipFilter; (void)pPalette;
    if (!pDevice || !pSrcData || !SrcDataSize || !ppTexture) return D3DERR_INVALIDCALL;
    *ppTexture = nullptr;
    Dds dds;
    HRESULT hr = parse_file(pSrcData, SrcDataSize, &dds);
    if (FAILED(hr)) return hr;
    if (dds.type == D3DRTYPE_VOLUMETEXTURE) return D3DXERR_INVALIDDATA;
    if (dds.type == D3DRTYPE_CUBETEXTURE) {             // a cube file into a 2D texture: its first face
        unsigned first = __builtin_ctz(dds.faces);
        dds.data = dds_level(dds, first, 0);
        dds.faces = 1;
        dds.type = D3DRTYPE_TEXTURE;
    }
    Plan p;
    hr = make_plan(pDevice, dds, D3DRTYPE_TEXTURE, Width, Height, 1, MipLevels, Format, Filter, ColorKey, &p);
    if (FAILED(hr)) return hr;
    hr = load_texture(pDevice, dds, p, Usage, Pool, ppTexture);
    if (SUCCEEDED(hr)) fill_info(dds, pSrcInfo);
    return hr;
}

HRESULT WINAPI D3DXCreateTextureFromFileInMemory(LPDIRECT3DDEVICE9 pDevice, LPCVOID pSrcData, UINT SrcDataSize,
                                                 LPDIRECT3DTEXTURE9* ppTexture) {
    return D3DXCreateTextureFromFileInMemoryEx(pDevice, pSrcData, SrcDataSize, D3DX_DEFAULT, D3DX_DEFAULT, D3DX_DEFAULT,
                                               0, D3DFMT_UNKNOWN, D3DPOOL_MANAGED, D3DX_DEFAULT, D3DX_DEFAULT, 0,
                                               nullptr, nullptr, ppTexture);
}

HRESULT WINAPI D3DXCreateCubeTextureFromFileInMemoryEx(LPDIRECT3DDEVICE9 pDevice, LPCVOID pSrcData, UINT SrcDataSize,
                                                       UINT Size, UINT MipLevels, DWORD Usage, D3DFORMAT Format,
                                                       D3DPOOL Pool, DWORD Filter, DWORD MipFilter, D3DCOLOR ColorKey,
                                                       D3DXIMAGE_INFO* pSrcInfo, PALETTEENTRY* pPalette,
                                                       LPDIRECT3DCUBETEXTURE9* ppCubeTexture) {
    (void)MipFilter; (void)pPalette;
    if (!pDevice || !pSrcData || !SrcDataSize || !ppCubeTexture) return D3DERR_INVALIDCALL;
    *ppCubeTexture = nullptr;
    Dds dds;
    HRESULT hr = parse_file(pSrcData, SrcDataSize, &dds);
    if (FAILED(hr)) return hr;
    if (dds.type != D3DRTYPE_CUBETEXTURE) return D3DXERR_INVALIDDATA;
    Plan p;
    hr = make_plan(pDevice, dds, D3DRTYPE_CUBETEXTURE, Size, Size, 1, MipLevels, Format, Filter, ColorKey, &p);
    if (FAILED(hr)) return hr;
    hr = load_cube(pDevice, dds, p, Usage, Pool, ppCubeTexture);
    if (SUCCEEDED(hr)) fill_info(dds, pSrcInfo);
    return hr;
}

HRESULT WINAPI D3DXCreateCubeTextureFromFileInMemory(LPDIRECT3DDEVICE9 pDevice, LPCVOID pSrcData, UINT SrcDataSize,
                                                     LPDIRECT3DCUBETEXTURE9* ppCubeTexture) {
    return D3DXCreateCubeTextureFromFileInMemoryEx(pDevice, pSrcData, SrcDataSize, D3DX_DEFAULT, D3DX_DEFAULT, 0,
                                                   D3DFMT_UNKNOWN, D3DPOOL_MANAGED, D3DX_DEFAULT, D3DX_DEFAULT, 0,
                                                   nullptr, nullptr, ppCubeTexture);
}

HRESULT WINAPI D3DXCreateVolumeTextureFromFileInMemoryEx(LPDIRECT3DDEVICE9 pDevice, LPCVOID pSrcData, UINT SrcDataSize,
                                                         UINT Width, UINT Height, UINT Depth, UINT MipLevels,
                                                         DWORD Usage, D3DFORMAT Format, D3DPOOL Pool, DWORD Filter,
                                                         DWORD MipFilter, D3DCOLOR ColorKey, D3DXIMAGE_INFO* pSrcInfo,
                                                         PALETTEENTRY* pPalette, LPDIRECT3DVOLUMETEXTURE9* ppVolumeTexture) {
    (void)MipFilter; (void)pPalette;
    if (!pDevice || !pSrcData || !SrcDataSize || !ppVolumeTexture) return D3DERR_INVALIDCALL;
    *ppVolumeTexture = nullptr;
    Dds dds;
    HRESULT hr = parse_file(pSrcData, SrcDataSize, &dds);
    if (FAILED(hr)) return hr;
    if (dds.type == D3DRTYPE_CUBETEXTURE) return D3DXERR_INVALIDDATA;
    dds.type = D3DRTYPE_VOLUMETEXTURE;                  // a 2D file is a volume of depth 1
    Plan p;
    hr = make_plan(pDevice, dds, D3DRTYPE_VOLUMETEXTURE, Width, Height, Depth, MipLevels, Format, Filter, ColorKey, &p);
    if (FAILED(hr)) return hr;
    hr = load_volume(pDevice, dds, p, Usage, Pool, ppVolumeTexture);
    if (SUCCEEDED(hr)) fill_info(dds, pSrcInfo);
    return hr;
}

HRESULT WINAPI D3DXCreateVolumeTextureFromFileInMemory(LPDIRECT3DDEVICE9 pDevice, LPCVOID pSrcData, UINT SrcDataSize,
                                                       LPDIRECT3DVOLUMETEXTURE9* ppVolumeTexture) {
    return D3DXCreateVolumeTextureFromFileInMemoryEx(pDevice, pSrcData, SrcDataSize, D3DX_DEFAULT, D3DX_DEFAULT,
                                                     D3DX_DEFAULT, D3DX_DEFAULT, 0, D3DFMT_UNKNOWN, D3DPOOL_MANAGED,
                                                     D3DX_DEFAULT, D3DX_DEFAULT, 0, nullptr, nullptr, ppVolumeTexture);
}

HRESULT WINAPI D3DXLoadSurfaceFromMemory(LPDIRECT3DSURFACE9 pDestSurface, const PALETTEENTRY* pDestPalette,
                                         const RECT* pDestRect, LPCVOID pSrcMemory, D3DFORMAT SrcFormat, UINT SrcPitch,
                                         const PALETTEENTRY* pSrcPalette, const RECT* pSrcRect, DWORD Filter,
                                         D3DCOLOR ColorKey) {
    (void)pDestPalette; (void)pSrcPalette;
    if (!pDestSurface || !pSrcMemory || !pSrcRect) return D3DERR_INVALIDCALL;
    const Format* sf = format_info(SrcFormat);
    D3DSURFACE_DESC dd;
    HRESULT hr = pDestSurface->GetDesc(&dd);
    if (FAILED(hr)) return hr;
    const Format* df = format_info(dd.Format);
    if (!sf || !df) return E_FAIL;
    RECT dr = pDestRect ? *pDestRect : RECT{0, 0, (LONG)dd.Width, (LONG)dd.Height};
    if (!valid_rect(dr, dd.Width, dd.Height) || pSrcRect->left < 0 || pSrcRect->top < 0 ||
        pSrcRect->right <= pSrcRect->left || pSrcRect->bottom <= pSrcRect->top)
        return D3DERR_INVALIDCALL;
    unsigned dw = (unsigned)(dr.right - dr.left), dh = (unsigned)(dr.bottom - dr.top);
    D3DLOCKED_RECT lr;
    if (SUCCEEDED(pDestSurface->LockRect(&lr, &dr, 0))) {
        hr = write_rect(df, (uint8_t*)lr.pBits, lr.Pitch, dw, dh, sf, (const uint8_t*)pSrcMemory, (int)SrcPitch,
                        *pSrcRect, Filter, ColorKey);
        pDestSurface->UnlockRect();
        return hr;
    }
    // not lockable (DEFAULT pool): SYSTEMMEM staging surface + UpdateSurface
    IDirect3DDevice9* dev = nullptr;
    IDirect3DSurface9* tmp = nullptr;
    hr = pDestSurface->GetDevice(&dev);
    if (FAILED(hr)) return hr;
    hr = dev->CreateOffscreenPlainSurface(dw, dh, dd.Format, D3DPOOL_SYSTEMMEM, &tmp, nullptr);
    if (SUCCEEDED(hr)) hr = tmp->LockRect(&lr, nullptr, 0);
    if (SUCCEEDED(hr)) {
        hr = write_rect(df, (uint8_t*)lr.pBits, lr.Pitch, dw, dh, sf, (const uint8_t*)pSrcMemory, (int)SrcPitch,
                        *pSrcRect, Filter, ColorKey);
        tmp->UnlockRect();
        POINT pt = {dr.left, dr.top};
        if (SUCCEEDED(hr)) hr = dev->UpdateSurface(tmp, nullptr, pDestSurface, &pt);
    }
    release(tmp);
    dev->Release();
    return hr;
}

HRESULT WINAPI D3DXLoadSurfaceFromSurface(LPDIRECT3DSURFACE9 pDestSurface, const PALETTEENTRY* pDestPalette,
                                          const RECT* pDestRect, LPDIRECT3DSURFACE9 pSrcSurface,
                                          const PALETTEENTRY* pSrcPalette, const RECT* pSrcRect, DWORD Filter,
                                          D3DCOLOR ColorKey) {
    if (!pDestSurface || !pSrcSurface) return D3DERR_INVALIDCALL;
    D3DSURFACE_DESC sd, dd;
    HRESULT hr = pSrcSurface->GetDesc(&sd);
    if (SUCCEEDED(hr)) hr = pDestSurface->GetDesc(&dd);
    if (FAILED(hr)) return hr;
    RECT sr = pSrcRect ? *pSrcRect : RECT{0, 0, (LONG)sd.Width, (LONG)sd.Height};
    if (!valid_rect(sr, sd.Width, sd.Height)) return D3DERR_INVALIDCALL;
    IDirect3DDevice9* dev = nullptr;
    hr = pSrcSurface->GetDevice(&dev);
    if (FAILED(hr)) return hr;
    // GPU copy when both live in the DEFAULT pool
    if (sd.Pool == D3DPOOL_DEFAULT && dd.Pool == D3DPOOL_DEFAULT && !ColorKey) {
        DWORD k = Filter & 0xFF;
        D3DTEXTUREFILTERTYPE f = (k == D3DX_FILTER_NONE || k == D3DX_FILTER_POINT) ? D3DTEXF_POINT : D3DTEXF_LINEAR;
        hr = dev->StretchRect(pSrcSurface, &sr, pDestSurface, pDestRect, f);
        if (SUCCEEDED(hr)) { dev->Release(); return hr; }
    }
    // CPU copy: a render target is read back through a SYSTEMMEM surface
    IDirect3DSurface9 *readable = pSrcSurface, *tmp = nullptr;
    if (sd.Pool == D3DPOOL_DEFAULT && (sd.Usage & D3DUSAGE_RENDERTARGET)) {
        hr = dev->CreateOffscreenPlainSurface(sd.Width, sd.Height, sd.Format, D3DPOOL_SYSTEMMEM, &tmp, nullptr);
        if (SUCCEEDED(hr)) hr = dev->GetRenderTargetData(pSrcSurface, tmp);
        if (FAILED(hr)) { release(tmp); dev->Release(); return hr; }
        readable = tmp;
    }
    D3DLOCKED_RECT lr;
    hr = readable->LockRect(&lr, nullptr, D3DLOCK_READONLY);
    if (SUCCEEDED(hr)) {
        hr = D3DXLoadSurfaceFromMemory(pDestSurface, pDestPalette, pDestRect, lr.pBits, sd.Format, (UINT)lr.Pitch,
                                       pSrcPalette, &sr, Filter, ColorKey);
        readable->UnlockRect();
    }
    release(tmp);
    dev->Release();
    return hr;
}

} // extern "C"

// One unit of the game declares this function itself with plain pointer types and C++ linkage (the decomp's
// src/unmapped/d3dx9shader.cpp): the same function under that mangled name.
HRESULT D3DXCreateCubeTextureFromFileInMemory(void* pDevice, const void* pSrcData, unsigned SrcDataSize, void** ppCube) {
    return D3DXCreateCubeTextureFromFileInMemory((LPDIRECT3DDEVICE9)pDevice, pSrcData, SrcDataSize,
                                                 (LPDIRECT3DCUBETEXTURE9*)ppCube);
}
