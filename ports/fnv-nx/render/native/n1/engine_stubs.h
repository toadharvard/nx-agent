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
    void SetSamplerState(uint32_t stage, uint32_t type, uint32_t value, uint32_t unused); // 0x00e88930, slot 48
    uint32_t GetSamplerState(uint32_t stage, uint32_t type);                              // 0x00e88980, slot 49
    void RestoreRenderState(uint32_t state);                                              // (no row)
    void RestoreSamplerState(uint32_t stage, uint32_t type);                              // (no row)
};

// Shader construction the native path does not need (it never compiles the HLSL nor builds constant maps):
struct TileShader {
    void CreateVertexShaders();     // 0x00bca0d0, slot 80
    void CreatePixelShaders();      // 0x00bca240, slot 81
    void CreateConstantMaps();      // 0x00bcaff0, slot 71
};
struct BSShaderNoLighting {
    void InitializeShaderData();    // 0x00bc5c40
};

// Interop: reads an engine NiTriShape / NiTriStrips into a Renderer::GeomView. Offsets are those the engine code
// uses (NiAVObject world transform +0x68; NiGeometry model data +0xB8, skin instance +0xBC; NiGeometryData
// vertex count +0x08, vertices +0x20, colours +0x28, uv sets +0x2C; NiTriBasedGeomData triangles +0x40;
// NiTriShapeData list length +0x44, list +0x48; NiTriStripsData strips +0x44, lengths +0x48, lists +0x4C).
// Embedded pointers are 32-bit guest addresses; guestBase (0 in the guest) lets a 64-bit host test map them.
// Returns false (nothing drawable: no data, skinned, empty) without touching out.
bool ReadGeometry(const void* geometry, Renderer::GeomView* out);
void SetGuestBase(uintptr_t base);
uint32_t SkippedGeometry();         // skinned / unreadable geometry the stub did not draw

} // namespace engine
} // namespace nx
