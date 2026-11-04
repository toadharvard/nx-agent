// fnv-nx native renderer, N1: the engine-facing override stubs (render/native/overrides.tsv). Each one has the
// signature of the decomp function it replaces (thiscall on the engine object: `this` is the engine's NiDX9Renderer,
// never dereferenced) and forwards to the process's nx::Renderer / nx::StateTracker. Built into libnxrender.a.
// Only for the native build (NATIVE1): the D3D9 (Nine) build must not link these overrides.
// When nx::Renderer is not initialised yet the stubs do nothing and report failure, as the engine's own functions do
// before the device exists.
#include "engine_stubs.h"

namespace nx {
namespace engine {

static Renderer* R() { return Renderer::Get(); }

bool NiDX9Renderer::Do_BeginFrame() { return R() && R()->Do_BeginFrame(); }
bool NiDX9Renderer::Do_EndFrame() { return R() && R()->Do_EndFrame(); }
bool NiDX9Renderer::Do_DisplayFrame() { return R() && R()->Do_DisplayFrame(); }
void NiDX9Renderer::Do_ClearBuffer(NiRectF* rect, uint32_t mode) { if (R()) R()->Do_ClearBuffer(rect, mode); }
bool NiDX9Renderer::Do_BeginUsingRenderTargetGroup(const void* g, uint32_t clear) {
    return R() && R()->Do_BeginUsingRenderTargetGroup(g, clear);
}
bool NiDX9Renderer::Do_EndUsingRenderTargetGroup() { return R() && R()->Do_EndUsingRenderTargetGroup(); }
void NiDX9Renderer::Do_SetCameraData(const NiPoint3f* loc, const NiPoint3f* dir, const NiPoint3f* up,
                                     const NiPoint3f* right, const NiFrustumF* f, const NiRectF* port) {
    if (R() && loc && dir && up && right && f && port) R()->Do_SetCameraData(*loc, *dir, *up, *right, *f, *port);
}
void NiDX9Renderer::Do_SetScreenSpaceCameraData(const NiRectF* port) { if (R()) R()->Do_SetScreenSpaceCameraData(port); }

void NiDX9RenderState::SetRenderState(uint32_t s, uint32_t v, bool save) { if (R()) R()->state().SetRenderState(s, v, save); }
uint32_t NiDX9RenderState::GetRenderState(uint32_t s) { return R() ? R()->state().GetRenderState(s) : 0; }
void NiDX9RenderState::RestoreRenderState(uint32_t s) { if (R()) R()->state().RestoreRenderState(s); }
void NiDX9RenderState::SetSamplerState(uint32_t st, uint32_t t, uint32_t v, bool save) {
    if (R()) R()->state().SetSamplerState(st, t, v, save);
}
uint32_t NiDX9RenderState::GetSamplerState(uint32_t st, uint32_t t) { return R() ? R()->state().GetSamplerState(st, t) : 0; }
void NiDX9RenderState::RestoreSamplerState(uint32_t st, uint32_t t) { if (R()) R()->state().RestoreSamplerState(st, t); }

} // namespace engine
} // namespace nx
