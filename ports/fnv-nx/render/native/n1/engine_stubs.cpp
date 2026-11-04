// fnv-nx native renderer, N1: the engine-facing override stubs (render/native/overrides.tsv). They forward to the
// process's nx::Renderer / nx::StateTracker. Built into libnxrender.a, for the native build (NATIVE1) only.
// When nx::Renderer is not initialised yet the stubs do nothing and report failure.
#include "engine_stubs.h"
#include <string.h>

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

static uint32_t g_skipped = 0;
void NiDX9Renderer::Do_RenderGeometry(const void* geometry) {
    Renderer::GeomView g;
    if (!R() || !ReadGeometry(geometry, &g)) { g_skipped++; return; }
    if (!R()->RenderGeometry(g)) g_skipped++;
}
uint32_t SkippedGeometry() { return g_skipped; }

// ---- render-state cache --------------------------------------------------------------------------------------
static uint8_t g_rsLock[NXD3D_RS_COUNT];

void NiDX9RenderState::SetRenderState(uint32_t s, uint32_t v, uint32_t, uint8_t lock) {
    if (s >= NXD3D_RS_COUNT || g_rsLock[s]) return;
    g_rsLock[s] = lock;
    if (R()) R()->state().SetRenderState(s, v);
}
uint32_t NiDX9RenderState::GetRenderState(uint32_t s) { return R() ? R()->state().GetRenderState(s) : 0; }
void NiDX9RenderState::SetBlendFunc(uint32_t src, uint32_t dst, uint32_t, uint32_t) {
    if (R()) { R()->state().SetRenderState(19, src); R()->state().SetRenderState(20, dst); }   // SRCBLEND, DESTBLEND
}
void NiDX9RenderState::SetAlphaBlendEnable(uint32_t e, uint32_t, uint32_t) {
    if (R()) R()->state().SetRenderState(27, e);                                                // ALPHABLENDENABLE
}
void NiDX9RenderState::SetSamplerState(uint32_t st, uint32_t t, uint32_t v, uint32_t) {
    if (R()) R()->state().SetSamplerState(st, t, v);
}
uint32_t NiDX9RenderState::GetSamplerState(uint32_t st, uint32_t t) { return R() ? R()->state().GetSamplerState(st, t) : 0; }
void NiDX9RenderState::RestoreRenderState(uint32_t s) { if (R()) R()->state().RestoreRenderState(s); }
void NiDX9RenderState::RestoreSamplerState(uint32_t st, uint32_t t) { if (R()) R()->state().RestoreSamplerState(st, t); }

// ---- shader construction: nothing to build natively ---------------------------------------------------------------
void TileShader::CreateVertexShaders() {}
void TileShader::CreatePixelShaders() {}
void TileShader::CreateConstantMaps() {}
void BSShaderNoLighting::InitializeShaderData() {}

// ---- geometry interop ------------------------------------------------------------------------------------------
static uintptr_t g_guestBase = 0;
void SetGuestBase(uintptr_t base) { g_guestBase = base; }

static inline uint32_t U32(const uint8_t* p, uint32_t o) { uint32_t v; memcpy(&v, p + o, 4); return v; }
static inline uint16_t U16(const uint8_t* p, uint32_t o) { uint16_t v; memcpy(&v, p + o, 2); return v; }
static inline const uint8_t* GP(uint32_t a) { return a ? (const uint8_t*)(g_guestBase + a) : nullptr; }

bool ReadGeometry(const void* geometry, Renderer::GeomView* out) {
    const uint8_t* geo = (const uint8_t*)geometry;
    if (!geo) return false;
    const uint8_t* data = GP(U32(geo, 0xB8));
    if (!data || U32(geo, 0xBC)) return false;            // no model data, or skinned (not in N1)
    Renderer::GeomView g;
    memset(&g, 0, sizeof g);
    memcpy(g.rot, geo + 0x68, 36); memcpy(g.trans, geo + 0x68 + 36, 12); memcpy(&g.scale, geo + 0x68 + 48, 4);
    g.vertices = U16(data, 0x08);
    g.pos = (const float*)GP(U32(data, 0x20));
    g.color = (const float*)GP(U32(data, 0x28));
    g.uv = (const float*)GP(U32(data, 0x2C));
    const uint32_t tris = U16(data, 0x40);
    if (!g.vertices || !g.pos || !tris) return false;
    // NiTriShapeData keeps 3 x triangles at +0x44; NiTriStripsData keeps its strip count (< 3 x triangles) there.
    if (U32(data, 0x44) == 3 * tris) {
        g.indices = (const uint16_t*)GP(U32(data, 0x48));
        g.indexCount = 3 * tris;
        if (!g.indices) return false;
    } else {
        g.stripCount = U16(data, 0x44);
        g.stripLengths = (const uint16_t*)GP(U32(data, 0x48));
        g.strips = (const uint16_t*)GP(U32(data, 0x4C));
        if (!g.stripCount || !g.stripLengths || !g.strips) return false;
    }
    g.tint[0] = g.tint[1] = g.tint[2] = g.tint[3] = 1.0f;
    *out = g;
    return true;
}

} // namespace engine
} // namespace nx
