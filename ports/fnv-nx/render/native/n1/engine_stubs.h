// fnv-nx native renderer, N1: the engine-facing override stubs (n1/engine_stubs.cpp, render/native/overrides.tsv).
// Each class stands for an engine class; the stubs are thiscall members with the signatures of the decomp
// functions they replace. `this` is the engine object: the renderer / shader stubs never dereference it; the
// render-state stubs keep the engine's cache in it and the geometry stub reads the geometry it is given, both through
// the interop layouts below.
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

// NiDX9RenderState (the state cache). The stubs are the engine's own functions with the device call replaced by
// nx::StateTracker (docs/RENDER_NATIVE.md §2.5): they read and write the cache in the engine object, so the engine
// code that stays (the defaults passes, ClearTextures, inline restores) and the stubs see one cache. Layout (the
// object the engine constructs; NiDX9RenderStateLayout below):
//   +0x120  render states [256] {current, saved}: SetRenderState / SetBlendFunc / SetAlphaBlendEnable write current
//           and reach the device only when it changes; the saved value is the defaults pass's (the engine restores a
//           render state by setting the saved value through SetRenderState).
//   +0x920  lock bytes [256]: SetRenderState of a locked state does nothing; an unlocked one takes the call's lock
//           argument (so a lock is never cleared by SetRenderState). SetBlendFunc / SetAlphaBlendEnable ignore locks.
//   +0xA20  texture stage states [16][8] {current, saved}, +0xE20 sampler states [16][5] {current, saved}: the cache
//           holds only some types (kTssTypes / kSampTypes); a set of another type is dropped (it never reaches the
//           device), a get of one returns 0. Restore re-sets the saved sampler value (the defaults pass writes it).
//   +0x10A0 textures [16]: SetTexture reaches the device when the stage's texture changes; ClearTextureUsage unbinds
//           a texture from every stage that has it.
// The constructor fills every current value with kUnknown, so the first set of each state reaches the device; the
// 4th (render state: 3rd) argument of every setter is unused, as in the engine. Stage numbers above 15 are dropped.
struct NiDX9RenderStateLayout {
    enum : uint32_t {
        kRs = 0x120, kRsLock = 0x920, kTss = 0xA20, kSamp = 0xE20, kTex = 0x10A0, kDevice = 0x10F8,
        kRsCount = 256, kTssPerStage = 8, kSampPerStage = 5, kStages = 16, kSize = 0x1140,
        kUnknown = 0x7FFFFFFFu,
    };
    // cache slot of a type, -1: not cached. Texture stage: COLOROP..ALPHAARG2, BUMPENVMAT00, TEXTURETRANSFORMFLAGS;
    // sampler: ADDRESSU, ADDRESSV, MAGFILTER, MINFILTER, MIPFILTER.
    static int TssSlot(uint32_t type);
    static int SampSlot(uint32_t type);
    // Test support: a zeroed kSize-byte object as the engine's constructor leaves the caches (every current value
    // kUnknown, no locks, no textures); device: the device pointer it holds (0 for none).
    static void Construct(void* object, void* device);
    // Test support: what the engine's defaults passes do, for tests without the engine. Slot 22: every (state, value)
    // of rs (ends at state 0xFFFFFFFF) into current and saved, then the device. Slot 47: per stage < tssStages, every
    // (type, value) of tss into the saved field only and the device (COLOROP / ALPHAOP 1 = DISABLE above stage 0,
    // TEXCOORDINDEX = stage); per stage < sampStages, every (type, value) of samp into the saved field, then slot 51,
    // then the device. The device is `device` (a NativeDevice); tables may be 0. (The engine's pass also writes the
    // adjusted COLOROP / ALPHAOP / TEXCOORDINDEX back into its table, so a second pass sets DISABLE on stage 0 too;
    // the const tables here do not reproduce that, the engine's own slot 47 does.)
    static void DefaultsPass(void* object, void* device, const uint32_t* rs, const uint32_t* tss, uint32_t tssStages,
                             const uint32_t* samp, uint32_t sampStages);
};
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
    void RestoreRenderState(uint32_t state);   // (no row: the engine does it inline) SetRenderState(state, saved)
    void RestoreSamplerState(uint32_t stage, uint32_t type);                              // 0x00e91120, slot 53
};

// ---- the D3D9 device as the engine sees it (state only) -------------------------------------------------------------
// An object with the IDirect3DDevice9 method-table layout (119 methods) whose state methods record into the
// process's nx::StateTracker with the device's semantics: render / sampler / texture stage states, textures (a
// NativeTexture -> its image), shaders, constants F/I/B, vertex declaration and FVF, streams and their frequency,
// indices, render targets (SetRenderTarget(0): viewport and scissor rectangle reset to the target's size, read with
// the surface's GetDesc), depth-stencil surface, viewport, scissor rectangle, clip planes, and the Get* of each.
// Engine code that talks to the device directly instead of through the cache (NiDX9RenderState's defaults passes:
// slot 22 render states, slot 47 texture stage and sampler states) reaches the tracker through it. Every other
// method (resource creation, draws, Present, fixed-function transforms / lights / material, state blocks) answers
// D3DERR_INVALIDCALL and is counted per method (NativeDeviceUnhandled).
struct NativeDevice {
    const uintptr_t* vtbl;
    uint32_t magic, refs;
    enum : uint32_t { kMagic = 0x5645444Eu, kMethods = 119 };   // "NDEV"
};
NativeDevice* CreateNativeDevice();                     // one per process (the engine has one device)
uint32_t NativeDeviceUnhandled(uint32_t method);        // calls of an unhandled method so far
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
// The family dispatch table (engine_stubs.cpp): one row per PassRoute; native rows draw with their family, the
// others with the translated originals. Defaults: tile and image space (menuBG) native, every world family translated.
struct FamilyRow { PassRoute route; const char* family; bool native; };
const FamilyRow* FamilyRows();                      // Route_Count rows, indexed by PassRoute
void SetFamilyNative(PassRoute r, bool native);
// ---- the translated path (engine_device.cpp, engine_stubs.cpp) ----
// The engine's D3D9 device as the sink of program creation and direct constant writes (method-table hook; calls go
// on to the device behind). The native boot calls HookRendererDevice(NiDX9Renderer*) (device at +0x28C) before the
// renderer creates its shaders, or HookD3DDevice(device) right after CreateDevice.
bool HookD3DDevice(void* device);
bool HookRendererDevice(const void* niDX9Renderer);
void* HookedD3DDevice();
TranslatedConstants& DeviceConstants();                          // the register files the engine wrote so far
void RegisterShaderObject(const void* d3dShader, uint64_t hash);  // what the hooked CreateVertex/PixelShader record
bool ShaderObjectHash(const void* d3dShader, uint64_t* hash);
// The translated programs of the geometry's current engine pass: its shader (NiGeometry +0xC0, a BSShader) ->
// current NiD3DPass (+0x5C) -> VS object (+0x5C) / PS object (+0x44) -> the D3D shader the hooked device created
// (found by scanning the program object), else the program's name (+0x0C). Returns the skip reason, 0 when found.
DrawSkip TranslatedPrograms(const void* geometry, int* vs, int* ps, const uint8_t** pass);
// The constant registers of a translated draw: the device's register files, then the pass's constant maps (VS map
// NiD3DPass +0x48, PS map +0x34; entries in NiTArray +0x0C; entry key +0x0C, register +0x1C, count +0x20, data size
// +0x28, data +0x30): data-backed entries copied, predefined matrices / eye position computed. Returns the number of
// entries that could not be evaluated (0: complete).
uint32_t TranslatedConstantsFor(const void* geometry, const uint8_t* pass, const Renderer::GeomView& g, TranslatedConstants* c);

} // namespace engine
} // namespace nx
