// fnv-nx native renderer, N1: the engine texture path (render/native/overrides.tsv, RENDER_NATIVE.md §2.4).
// NiDX9SourceTextureData::LoadFromFilename reads a texture file into memory and asks D3DX for its info and for a
// texture made from it. Overriding those two D3DX entry points gives the engine a NativeTexture: an object with the
// IDirect3DTexture9 method table layout (the methods the engine may call on it answer sensibly) that owns a native
// image created from the DDS bytes (front ParseDDS, nx_d3d9 formats, NXFMT_CONVERT expansion). The engine keeps the
// pointer in its texture renderer data, binds it with NiDX9RenderState::SetTexture (overridden too), and
// Renderer::RenderGeometry finds it again from a tile's NiTexture.
#include "engine_stubs.h"
#include <stdlib.h>
#include <string.h>

namespace nx {
namespace engine {

namespace {

enum : int32_t { kOk = 0, kInvalidCall = (int32_t)0x8876086Cu, kNoInterface = (int32_t)0x80004002u };
enum { kPool = 4096 };

NativeTexture g_pool[kPool];
uint32_t g_live = 0, g_created = 0;
uint8_t* g_conv = nullptr;
uint32_t g_convCap = 0;

typedef uintptr_t W;   // one method-table slot
// IUnknown
int32_t QueryInterface(NativeTexture*, const void*, void** out) { if (out) *out = nullptr; return kNoInterface; }
uint32_t AddRef(NativeTexture* t) { return ++t->refs; }
uint32_t Release(NativeTexture* t) {
    if (t->refs > 1) return --t->refs;
    t->refs = 0; t->magic = 0; g_live--;      // the native image stays (images are not freed in N1)
    return 0;
}
// IDirect3DResource9
int32_t GetDevice(NativeTexture*, void** out) { if (out) *out = nullptr; return kInvalidCall; }
int32_t SetPrivateData(NativeTexture*, const void*, const void*, uint32_t, uint32_t) { return kOk; }
int32_t GetPrivateData(NativeTexture*, const void*, void*, uint32_t*) { return kInvalidCall; }
int32_t FreePrivateData(NativeTexture*, const void*) { return kOk; }
uint32_t SetPriority(NativeTexture*, uint32_t) { return 0; }
uint32_t GetPriority(NativeTexture*) { return 0; }
void PreLoad(NativeTexture*) {}
uint32_t GetType(NativeTexture*) { return 3; }                       // D3DRTYPE_TEXTURE
// IDirect3DBaseTexture9
uint32_t SetLOD(NativeTexture* t, uint32_t lod) { uint32_t o = t->lod; t->lod = lod < t->levels ? lod : t->levels - 1; return o; }
uint32_t GetLOD(NativeTexture* t) { return t->lod; }
uint32_t GetLevelCount(NativeTexture* t) { return t->levels; }
int32_t SetAutoGenFilterType(NativeTexture*, uint32_t) { return kOk; }
uint32_t GetAutoGenFilterType(NativeTexture*) { return 2; }          // D3DTEXF_LINEAR
void GenerateMipSubLevels(NativeTexture*) {}
// IDirect3DTexture9
int32_t GetLevelDesc(NativeTexture* t, uint32_t level, uint32_t* desc) {   // D3DSURFACE_DESC, 8 dwords
    if (level >= t->levels || !desc) return kInvalidCall;
    uint32_t w = t->w >> level, h = t->h >> level;
    desc[0] = t->d3dfmt; desc[1] = 1; desc[2] = 0; desc[3] = 1;         // format, D3DRTYPE_SURFACE, usage, MANAGED
    desc[4] = 0; desc[5] = 0; desc[6] = w ? w : 1; desc[7] = h ? h : 1;
    return kOk;
}
int32_t GetSurfaceLevel(NativeTexture* t, uint32_t level, void** out) {
    if (out) *out = nullptr;
    if (!t->staging || level >= t->levels || !out) return kInvalidCall;
    t->refs++;
    *out = &t->surfaces[level];
    return kOk;
}
int32_t LockRect(NativeTexture* t, uint32_t level, uint32_t* locked, const void*, uint32_t) {   // D3DLOCKED_RECT
    if (!t->staging || level >= t->levels || !locked) return kInvalidCall;
    NativeSurface* s = &t->surfaces[level];
    locked[0] = s->pitch; locked[1] = GuestAddress(t->staging + s->offset);
    return kOk;
}
int32_t UnlockRect(NativeTexture* t, uint32_t level) {
    if (!t->staging || level >= t->levels) return kInvalidCall;
    t->dirty = 1;
    return kOk;
}
int32_t AddDirtyRect(NativeTexture*, const void*) { return kOk; }

// IDirect3DSurface9 over a level: IUnknown, IDirect3DResource9 (8), GetContainer, GetDesc, LockRect, UnlockRect,
// GetDC, ReleaseDC
int32_t SQueryInterface(NativeSurface*, const void*, void** out) { if (out) *out = nullptr; return kNoInterface; }
uint32_t SAddRef(NativeSurface* s) { return ++s->tex->refs; }
uint32_t SRelease(NativeSurface* s) { return Release(s->tex); }
int32_t SGetDevice(NativeSurface*, void** out) { if (out) *out = nullptr; return kInvalidCall; }
int32_t SStub4(NativeSurface*, const void*, const void*, uint32_t, uint32_t) { return kOk; }
int32_t SStub3(NativeSurface*, const void*, void*, uint32_t*) { return kInvalidCall; }
int32_t SStub1(NativeSurface*, const void*) { return kOk; }
uint32_t SSetPriority(NativeSurface*, uint32_t) { return 0; }
uint32_t SGetPriority(NativeSurface*) { return 0; }
void SPreLoad(NativeSurface*) {}
uint32_t SGetType(NativeSurface*) { return 1; }                      // D3DRTYPE_SURFACE
int32_t SGetContainer(NativeSurface* s, const void*, void** out) { if (out) { s->tex->refs++; *out = s->tex; } return kOk; }
int32_t SGetDesc(NativeSurface* s, uint32_t* desc) { return GetLevelDesc(s->tex, s->level, desc); }
int32_t SLockRect(NativeSurface* s, uint32_t* locked, const void* r, uint32_t f) { return LockRect(s->tex, s->level, locked, r, f); }
int32_t SUnlockRect(NativeSurface* s) { return UnlockRect(s->tex, s->level); }
int32_t SGetDC(NativeSurface*, void** out) { if (out) *out = nullptr; return kInvalidCall; }
int32_t SReleaseDC(NativeSurface*, void*) { return kInvalidCall; }
const W kSurfVtbl[17] = {
    (W)SQueryInterface, (W)SAddRef, (W)SRelease,
    (W)SGetDevice, (W)SStub4, (W)SStub3, (W)SStub1, (W)SSetPriority, (W)SGetPriority, (W)SPreLoad, (W)SGetType,
    (W)SGetContainer, (W)SGetDesc, (W)SLockRect, (W)SUnlockRect, (W)SGetDC, (W)SReleaseDC,
};

const W kVtbl[22] = {
    (W)QueryInterface, (W)AddRef, (W)Release,
    (W)GetDevice, (W)SetPrivateData, (W)GetPrivateData, (W)FreePrivateData, (W)SetPriority, (W)GetPriority, (W)PreLoad,
    (W)GetType,
    (W)SetLOD, (W)GetLOD, (W)GetLevelCount, (W)SetAutoGenFilterType, (W)GetAutoGenFilterType, (W)GenerateMipSubLevels,
    (W)GetLevelDesc, (W)GetSurfaceLevel, (W)LockRect, (W)UnlockRect, (W)AddDirtyRect,
};

inline uint32_t Rd32(const uint8_t* p) { uint32_t v; memcpy(&v, p, 4); return v; }

}  // namespace

NativeTexture* NativeTextureFromPointer(const void* p) {
    const NativeTexture* t = (const NativeTexture*)p;
    if (t < g_pool || t >= g_pool + kPool || ((uintptr_t)t - (uintptr_t)g_pool) % sizeof(NativeTexture)) return nullptr;
    return t->magic == NativeTexture::kMagic ? (NativeTexture*)t : nullptr;
}

NativeTexture* FindNativeTexture(const void* rendererData, uint32_t bytes) {
    if (!rendererData) return nullptr;
    const uint8_t* p = (const uint8_t*)rendererData;
    for (uint32_t o = 0; o + 4 <= bytes; o += 4) {
        uint32_t v = Rd32(p + o);
        if (!v) continue;   // a null pointer field (GuestBase() itself may be the pool's first entry)
        NativeTexture* t = NativeTextureFromPointer((const void*)(GuestBase() + v));
        if (t) { if (t->dirty) MaterializeNativeTexture(t); return t; }
    }
    return nullptr;
}

uint32_t NativeTexturesLive() { return g_live; }
const void* NativeTexturePool() { return g_pool; }
uint32_t NativeTexturesCreated() { return g_created; }

NativeTexture* CreateNativeTexture(const void* dds, uint32_t size) {
    Renderer* r = Renderer::Get();
    NxImageDesc d;
    uint32_t d3d = 0;
    if (!r || !ParseDDS(dds, size, &d, &d3d)) return nullptr;
    NxFormatInfo fi = nx_d3d9_format(d3d);
    if (fi.flags & NXFMT_CONVERT) {   // expanded on upload (no channel-moving view swizzles); source sizes are D3D's
        uint32_t src = 0;
        for (uint32_t m = 0, w = d.width, h = d.height; m < (d.mips ? d.mips : 1u); m++, w = w > 1 ? w / 2 : 1, h = h > 1 ? h / 2 : 1)
            src += (fi.flags & NXFMT_COMPRESSED) ? ((w + 3) / 4) * ((h + 3) / 4) * fi.srcBytes : w * h * fi.srcBytes;
        if (128 + src > size) return nullptr;
        uint32_t need = nx_d3d9_converted_size(d3d, d.width, d.height, d.mips ? d.mips : 1, 1);
        if (need > g_convCap) { free(g_conv); g_conv = (uint8_t*)malloc(need); g_convCap = g_conv ? need : 0; }
        if (!g_conv) return nullptr;
        uint32_t n = nx_d3d9_convert(d3d, d.width, d.height, d.mips ? d.mips : 1, 1, (const uint8_t*)dds + 128, src, g_conv, need);
        if (!n) return nullptr;
        d.data = FNVNX_ADDR_(g_conv); d.data_size = n;
        d.swizzle = NXIMG_SWIZZLE(fi.swizzle[0], fi.swizzle[1], fi.swizzle[2], fi.swizzle[3]);
    }
    uint32_t image = r->backend()->ImageCreate(d);
    if (!image) return nullptr;
    for (uint32_t i = 0; i < kPool; i++) {
        NativeTexture* t = &g_pool[i];
        if (t->magic == NativeTexture::kMagic) continue;
        free(t->staging); free(t->surfaces);
        memset(t, 0, sizeof *t);
        t->vtbl = kVtbl; t->magic = NativeTexture::kMagic; t->refs = 1; t->image = image; t->d3dfmt = d3d;
        t->w = d.width; t->h = d.height; t->levels = d.mips ? d.mips : 1;
        g_live++; g_created++;
        return t;
    }
    return nullptr;
}

// level layout of a D3D format: bytes of level l (tightly packed rows, BCn by 4x4 blocks), pitch, rows
static void LevelSize(uint32_t d3d, uint32_t w, uint32_t h, uint32_t* bytes, uint32_t* pitch, uint32_t* rows) {
    NxFormatInfo fi = nx_d3d9_format(d3d);
    uint32_t bpp = fi.srcBytes ? fi.srcBytes : fi.bytes;
    if (fi.flags & NXFMT_COMPRESSED) { *pitch = ((w + 3) / 4) * bpp; *rows = (h + 3) / 4; }
    else { *pitch = w * bpp; *rows = h; }
    *bytes = *pitch * *rows;
}

NativeTexture* CreateLockableTexture(uint32_t d3dfmt, uint32_t w, uint32_t h, uint32_t levels) {
    NxFormatInfo fi = nx_d3d9_format(d3dfmt);
    if (fi.format == NxFmt_None || !w || !h || w > 16384 || h > 16384) return nullptr;
    if (!levels) levels = 1;
    uint32_t total = 0;
    for (uint32_t l = 0, lw = w, lh = h; l < levels; l++, lw = lw > 1 ? lw / 2 : 1, lh = lh > 1 ? lh / 2 : 1) {
        uint32_t b, p, r; LevelSize(d3dfmt, lw, lh, &b, &p, &r); total += b;
    }
    for (uint32_t i = 0; i < kPool; i++) {
        NativeTexture* t = &g_pool[i];
        if (t->magic == NativeTexture::kMagic) continue;
        uint8_t* staging = (uint8_t*)calloc(1, total);
        NativeSurface* surf = (NativeSurface*)calloc(levels, sizeof(NativeSurface));
        if (!staging || !surf) { free(staging); free(surf); return nullptr; }
        memset(t, 0, sizeof *t);
        t->vtbl = kVtbl; t->magic = NativeTexture::kMagic; t->refs = 1; t->d3dfmt = d3dfmt;
        t->w = (uint16_t)w; t->h = (uint16_t)h; t->levels = (uint16_t)levels; t->staging = staging; t->surfaces = surf;
        uint32_t off = 0;
        for (uint32_t l = 0, lw = w, lh = h; l < levels; l++, lw = lw > 1 ? lw / 2 : 1, lh = lh > 1 ? lh / 2 : 1) {
            uint32_t b, p, r; LevelSize(d3dfmt, lw, lh, &b, &p, &r);
            surf[l].vtbl = kSurfVtbl; surf[l].tex = t; surf[l].level = l; surf[l].offset = off; surf[l].pitch = p; surf[l].rows = r;
            off += b;
        }
        t->dirty = 1;
        g_live++; g_created++;
        return t;
    }
    return nullptr;
}

void MaterializeNativeTexture(NativeTexture* t) {
    Renderer* r = Renderer::Get();
    if (!t || !t->staging || !r) return;
    // a DDS-shaped header in front of the staging copy reuses the DDS path (formats, NXFMT_CONVERT)
    uint32_t total = 0;
    for (uint32_t l = 0; l < t->levels; l++) { uint32_t b, p, rr; LevelSize(t->d3dfmt, t->w >> l ? t->w >> l : 1, t->h >> l ? t->h >> l : 1, &b, &p, &rr); total += b; }
    NxFormatInfo fi = nx_d3d9_format(t->d3dfmt);
    const uint8_t* data = t->staging;
    uint32_t size = total;
    if (fi.flags & NXFMT_CONVERT) {
        uint32_t need = nx_d3d9_converted_size(t->d3dfmt, t->w, t->h, t->levels, 1);
        if (need > g_convCap) { free(g_conv); g_conv = (uint8_t*)malloc(need); g_convCap = g_conv ? need : 0; }
        if (!g_conv) return;
        size = nx_d3d9_convert(t->d3dfmt, t->w, t->h, t->levels, 1, t->staging, total, g_conv, need);
        if (!size) return;
        data = g_conv;
    }
    NxImageDesc d;
    memset(&d, 0, sizeof d);
    d.width = t->w; d.height = t->h; d.format = fi.format; d.mips = (uint8_t)t->levels;
    d.swizzle = NXIMG_SWIZZLE(fi.swizzle[0], fi.swizzle[1], fi.swizzle[2], fi.swizzle[3]);
    d.data = FNVNX_ADDR_(data); d.data_size = size;
    uint32_t image = r->backend()->ImageCreate(d);   // images are immutable: new contents, new image
    if (image) { t->image = image; t->dirty = 0; }
}

// Interop with NiDX9SourceTextureData (the CreateSurf body): this +0x0C/+0x10 image size, +0x14 the renderer pixel
// format (its m_uiRendererHint, the D3DFORMAT, at +0x20), +0x64 the D3D texture, +0x68 levels, +0x78 skipped levels;
// NiPixelData: +0x54 / +0x58 width / height arrays, +0x60 mip levels.
bool NiDX9SourceTextureData::CreateSurf(const void* pixelData) {
    uint8_t* self = (uint8_t*)this;
    const uint8_t* pd = (const uint8_t*)pixelData;
    if (!pd) return false;
    const uint8_t* wp = (const uint8_t*)(GuestBase() + Rd32(pd + 0x54));
    const uint8_t* hp = (const uint8_t*)(GuestBase() + Rd32(pd + 0x58));
    if (!Rd32(pd + 0x54) || !Rd32(pd + 0x58)) return false;
    uint32_t w = Rd32(wp), h = Rd32(hp), levels = Rd32(pd + 0x60), fmt = Rd32(self + 0x20);
    if (!nx_d3d9_format(fmt).format) fmt = 21;   // no usable hint: A8R8G8B8
    NativeTexture* t = CreateLockableTexture(fmt, w, h, levels);
    uint32_t zero = 0, addr = GuestAddress(t);
    memcpy(self + 0x0C, &w, 4); memcpy(self + 0x10, &h, 4);
    memcpy(self + 0x64, t ? &addr : &zero, 4);
    memcpy(self + 0x68, &levels, 4); memcpy(self + 0x78, &zero, 4);
    return t != nullptr;
}

// 0x00ee6e22 D3DXGetImageInfoFromFileInMemory(data, size, D3DXIMAGE_INFO*): DDS only (the engine's textures)
int32_t D3DXGetImageInfoFromFileInMemory(const void* data, uint32_t size, uint32_t* info) {
    NxImageDesc d;
    uint32_t d3d = 0;
    if (!ParseDDS(data, size, &d, &d3d)) return kInvalidCall;
    if (info) {   // Width, Height, Depth, MipLevels, Format, ResourceType (TEXTURE), ImageFileFormat (DDS)
        info[0] = d.width; info[1] = d.height; info[2] = 1; info[3] = d.mips ? d.mips : 1; info[4] = d3d; info[5] = 3; info[6] = 4;
    }
    return kOk;
}

// 0x00ee6e1c D3DXCreateTextureFromFileInMemory(device, data, size, IDirect3DTexture9** out)
int32_t D3DXCreateTextureFromFileInMemory(void*, const void* data, uint32_t size, void** out) {
    NativeTexture* t = CreateNativeTexture(data, size);
    if (out) *out = t;
    return t ? kOk : kInvalidCall;
}

} // namespace engine
} // namespace nx
