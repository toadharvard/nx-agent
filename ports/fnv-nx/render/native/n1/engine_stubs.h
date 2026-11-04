// fnv-nx native renderer, N1: the engine-facing override stubs (n1/engine_stubs.cpp, render/native/overrides.tsv).
#pragma once
#include "renderer.h"

namespace nx {
namespace engine {

// Stands for the engine's NiDX9Renderer object; empty: the stubs only use `this` as the receiver of a thiscall.
struct NiDX9Renderer {
    bool Do_BeginFrame();                                                  // 0x00e74f40
    bool Do_EndFrame();                                                    // 0x00e69660
    bool Do_DisplayFrame();                                                // 0x00e7ccd0
    void Do_ClearBuffer(NiRectF* rect, uint32_t mode);                     // 0x00e6f310
    bool Do_BeginUsingRenderTargetGroup(const void* group, uint32_t clear);   // 0x00e6eea0
    bool Do_EndUsingRenderTargetGroup();                                   // vslot (pending)
    void Do_SetCameraData(const NiPoint3f* loc, const NiPoint3f* dir, const NiPoint3f* up, const NiPoint3f* right,
                          const NiFrustumF* frustum, const NiRectF* port);    // vslot 99 (pending)
    void Do_SetScreenSpaceCameraData(const NiRectF* port);                // vslot 100 (pending)
};

// Stands for NiDX9RenderState (the state cache): record-only.
struct NiDX9RenderState {
    void SetRenderState(uint32_t state, uint32_t value, bool save);
    uint32_t GetRenderState(uint32_t state);
    void RestoreRenderState(uint32_t state);
    void SetSamplerState(uint32_t stage, uint32_t type, uint32_t value, bool save);
    uint32_t GetSamplerState(uint32_t stage, uint32_t type);
    void RestoreSamplerState(uint32_t stage, uint32_t type);
};

} // namespace engine
} // namespace nx
