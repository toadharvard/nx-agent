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
int32_t GetSurfaceLevel(NativeTexture*, uint32_t, void** out) { if (out) *out = nullptr; return kInvalidCall; }
int32_t LockRect(NativeTexture*, uint32_t, void*, const void*, uint32_t) { return kInvalidCall; }   // GPU-only
int32_t UnlockRect(NativeTexture*, uint32_t) { return kInvalidCall; }
int32_t AddDirtyRect(NativeTexture*, const void*) { return kOk; }

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
        if (t) return t;
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
        memset(t, 0, sizeof *t);
        t->vtbl = kVtbl; t->magic = NativeTexture::kMagic; t->refs = 1; t->image = image; t->d3dfmt = d3d;
        t->w = d.width; t->h = d.height; t->levels = d.mips ? d.mips : 1;
        g_live++; g_created++;
        return t;
    }
    return nullptr;
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
