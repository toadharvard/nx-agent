// fnv-nx native renderer, N1: the engine-facing override stubs (n1/engine_stubs.cpp, render/native/overrides.tsv).
// Each class stands for an engine class; the stubs are thiscall members with the signatures of the decomp
// functions they replace. `this` is the engine object: the renderer / render-state / shader stubs never
// dereference it; the geometry stub reads the geometry it is given through the interop layout below.
#pragma once
#include "renderer.h"

namespace nx {
namespace engine {

struct NiDX9Renderer {
    bool Do_BeginFrame();                                                  // 0x00e74f40, slot 95
    bool Do_EndFrame();                                                    // 0x00e69660, slot 96
    bool Do_DisplayFrame();                                                // 0x00e7ccd0 (Present; slot 97 stays the engine's)
    void Do_ClearBuffer(NiRectF* rect, uint32_t mode);                     // 0x00e6f310, slot 98
    void Do_SetCameraData(const NiPoint3f* loc, const NiPoint3f* dir, const NiPoint3f* up, const NiPoint3f* right,
                          const NiFrustumF* frustum, const NiRectF* port);    // 0x00e6c780, slot 99
    void Do_SetScreenSpaceCameraData(const NiRectF* port);                // 0x00e6cf40, slot 100
    bool Do_BeginUsingRenderTargetGroup(const void* group, uint32_t clear);   // 0x00e6eea0, slot 101
    bool Do_EndUsingRenderTargetGroup();                                   // 0x00e74dc0, slot 102
    void Do_RenderGeometry(const void* geometry);                         // 0x00e744b0, slots 107 + 108
};

// NiDX9RenderState (the state cache): record-only, forwarded to nx::StateTracker. The engine's lock byte per
// render state (set by SetRenderState's last argument, blocks later sets) is kept here.
struct NiDX9RenderState {
    void SetRenderState(uint32_t state, uint32_t value, uint32_t unused, uint8_t lock);   // 0x00e88780, slot 26
    uint32_t GetRenderState(uint32_t state);                                              // 0x00e88860, slot 27
    void SetBlendFunc(uint32_t src, uint32_t dst, uint32_t unused1, uint32_t unused2);    // 0x00e887d0, slot 23
    void SetAlphaBlendEnable(uint32_t enable, uint32_t unused1, uint32_t unused2);        // 0x00e88830, slot 24
    // slots 48/49 are NiD3DRenderState's texture stage states (the device's SetTextureStageState, vtable +0x10C);
    // the sampler states are NiDX9RenderState's own slots 51-53 (the device's SetSamplerState, +0x114)
    void SetTextureStageState(uint32_t stage, uint32_t type, uint32_t value, uint32_t unused); // 0x00e88930, slot 48
    uint32_t GetTextureStageState(uint32_t stage, uint32_t type);                              // 0x00e88980, slot 49
    void SetSamplerState(uint32_t stage, uint32_t type, uint32_t value, uint32_t unused); // 0x00e910a0, slot 51
    uint32_t GetSamplerState(uint32_t stage, uint32_t type);                              // 0x00e910f0, slot 52
    void SetTexture(uint32_t stage, void* texture);                                       // 0x00e88a20, slot 55
    void* GetTexture(uint32_t stage);                                                     // 0x00e88a50, slot 56
    void ClearTextureUsage(void* texture);                                                // 0x00e88a60, slot 57
    void RestoreRenderState(uint32_t state);                                              // (no row)
    void RestoreSamplerState(uint32_t stage, uint32_t type);                              // 0x00e91120, slot 53
};

// ImageSpaceManager::RenderEffect (0x00b97550): the effect dispatch. Menu effects run natively; the others are
// counted and skipped until their families exist. Interop: manager +0x08 the effect array; EFFECT_MENUBG (15) is an
// ImageSpaceEffectMenuBackground: +0x58 ImageSpaceTexture* (its NiTexture at +4), +0x5C blend amount (float).
// src / dst are engine render targets registered with Renderer::RegisterTargetGroup (dst 0: the current target).
struct ImageSpaceManager {
    enum { kEffectMenuBg = 15 };
    void RenderEffect(int32_t effect, void* renderer, void* src, void* dst, void* param, bool flag);
};
uint32_t ImageSpaceEffectsNative();
uint32_t ImageSpaceEffectsSkipped();

// Shader construction the native path does not need (it never compiles the HLSL nor builds constant maps):
struct TileShader {
    void CreateVertexShaders();     // 0x00bca0d0, slot 80
    void CreatePixelShaders();      // 0x00bca240, slot 81
    void CreateConstantMaps();      // 0x00bcaff0, slot 71
};
struct BSShaderNoLighting {
    void InitializeShaderData();    // 0x00bc5c40
};

// ---- engine textures (n1/engine_textures.cpp) ---------------------------------------------------------------------
// A native texture handed to the engine where it expects an IDirect3DTexture9 (method-table layout of that
// interface; GetLevelDesc / GetLevelCount / AddRef / Release answer, CPU access is refused).
struct NativeSurface;
struct NativeTexture {
    const uintptr_t* vtbl;
    uint32_t magic, refs, image, d3dfmt, lod;
    uint16_t w, h, levels, dirty;
    uint8_t* staging;               // lockable textures (CreateSurf path): CPU copy of all levels, D3D layout
    NativeSurface* surfaces;        // one per level (GetSurfaceLevel)
    enum : uint32_t { kMagic = 0x5854584Eu };   // "NXTX"
};
// IDirect3DSurface9 layout over one level of a lockable NativeTexture (GetDesc, LockRect, UnlockRect).
struct NativeSurface {
    const uintptr_t* vtbl;
    NativeTexture* tex;
    uint32_t level, offset, pitch, rows;
};
// 0x00e68010 NiDX9SourceTextureData::CreateSurf(NiPixelData*): a lockable NativeTexture the engine then fills
// through GetSurfaceLevel / LockRect (fonts, NiPixelData textures). D3DFORMAT from the renderer pixel format's hint.
struct NiDX9SourceTextureData {
    bool CreateSurf(const void* pixelData);
};
NativeTexture* CreateLockableTexture(uint32_t d3dfmt, uint32_t w, uint32_t h, uint32_t levels);
void MaterializeNativeTexture(NativeTexture* t);   // a lockable texture's pending contents -> a native image
int32_t D3DXGetImageInfoFromFileInMemory(const void* data, uint32_t size, uint32_t* info);              // 0x00ee6e22
int32_t D3DXCreateTextureFromFileInMemory(void* device, const void* data, uint32_t size, void** out);  // 0x00ee6e1c
NativeTexture* CreateNativeTexture(const void* dds, uint32_t size);
NativeTexture* NativeTextureFromPointer(const void* p);           // 0 unless p is a live NativeTexture
NativeTexture* FindNativeTexture(const void* rendererData, uint32_t bytes);   // first NativeTexture pointer in it
const void* NativeTexturePool();
uint32_t NativeTexturesLive();
uint32_t NativeTexturesCreated();

// Interop: reads an engine NiTriShape / NiTriStrips into a Renderer::GeomView. Offsets are those the engine code
// uses (NiAVObject world transform +0x68; NiGeometry model data +0xB8, skin instance +0xBC; NiGeometryData
// vertex count +0x08, vertices +0x20, colours +0x28, uv sets +0x2C; NiTriBasedGeomData triangles +0x40;
// NiTriShapeData list length +0x44, list +0x48; NiTriStripsData strips +0x44, lengths +0x48, lists +0x4C).
// Embedded pointers are 32-bit guest addresses; guestBase (0 in the guest) lets a 64-bit host test map them.
// Returns false (nothing drawable: no data, skinned, empty) without touching out.
// Properties (NiPropertyState at +0x9C): NiAlphaProperty +0x9C (flags +0x18, ref +0x1A), NiMaterialProperty +0xA4
// (alpha +0x3C), the shader property +0xA8 read as a TileShaderProperty (texture +0x60, tint rgb +0x68, alpha
// +0x78); its NiTexture's renderer data (+0x24) holds the NativeTexture.
bool ReadGeometry(const void* geometry, Renderer::GeomView* out);
void SetGuestBase(uintptr_t base);
uintptr_t GuestBase();
uint32_t GuestAddress(const void* p);   // p - GuestBase() (the 32-bit value an engine pointer field holds)
uint32_t SkippedGeometry();         // skinned / unreadable geometry the stub did not draw
// The engine's current render pass (see engine_stubs.cpp): pointers to the 32-bit pass id and RenderPass* globals.
// The native build binds the data image's (BindImagePassGlobals: 0x011f91e4 / 0x011f91e0); tests bind their own.
// Unbound, every draw has pass 0 (routed as the N1 menu path did: tile family).
void BindPassGlobals(const uint32_t* passId, const uint32_t* renderPass);
void BindImagePassGlobals();
// The RenderPass of the draw of `geometry` (0 when none is current or it names another geometry) and its pass id.
const uint8_t* CurrentRenderPass(const void* geometry, uint16_t* passId);
// Which native families serve their passes (bit per PassRoute); the others go to the translated route. Default:
// tile, nolight, image space (lit / depth / land / sky through the translated originals until they are checked).
void SetNativeFamilies(uint32_t routeMask);
uint32_t NativeFamilies();
bool NativeFamilyEnabled(PassRoute r);
// SkyShader's Color0-2 (3 x float4; the native build: the data image's 0x011ff910, bound by BindImagePassGlobals).
void BindSkyColors(const float* color0);
// What the replaced shader pipeline would have put in constants / texture stages for a world-family pass: textures
// of the BSShader property, the RenderPass's lights, material / property colours (see engine_stubs.cpp).
void ReadWorldInputs(const void* geometry, const uint8_t* renderPass, const PassInfo& pi, uint16_t passId,
                     Renderer::WorldInputs* in);

} // namespace engine
} // namespace nx
