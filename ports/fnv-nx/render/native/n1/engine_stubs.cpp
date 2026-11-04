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

extern uintptr_t g_guestBase;
static uint32_t g_skipped = 0;
uint32_t SkippedGeometry() { return g_skipped; }

// ---- the engine's current render pass ------------------------------------------------------------------------------
// FUN_00b994f0 (the batch renderer's per-pass draw) stores the pass id (DAT_011f91e4) and the
// BSShaderProperty::RenderPass (DAT_011f91e0: +0 geometry, +4 u16 pass, +9 light count, +0xC ShadowSceneLight**) before
// the geometry reaches Do_RenderShape. A pass counts for a draw only when its RenderPass names that geometry (a menu
// drawn outside the accumulator would otherwise inherit the last world pass).
static const uint32_t* g_passId = nullptr;
static const uint32_t* g_renderPass = nullptr;
static const float* g_skyColors = nullptr;
void BindPassGlobals(const uint32_t* passId, const uint32_t* renderPass) { g_passId = passId; g_renderPass = renderPass; }
void BindSkyColors(const float* color0) { g_skyColors = color0; }
void BindImagePassGlobals() {
    BindPassGlobals((const uint32_t*)(g_guestBase + 0x011f91e4u), (const uint32_t*)(g_guestBase + 0x011f91e0u));
    // SkyShader's vertex constants Color0-2 (its constant map's data sources: 0x011ff910, +0x10, +0x20)
    BindSkyColors((const float*)(g_guestBase + 0x011ff910u));
}
const uint8_t* CurrentRenderPass(const void* geometry, uint16_t* passId) {
    *passId = 0;
    if (!g_passId || !g_renderPass || !*g_renderPass) return nullptr;
    const uint8_t* rp = (const uint8_t*)(g_guestBase + *g_renderPass);
    uint32_t geo; memcpy(&geo, rp, 4);
    if (geo != GuestAddress(geometry)) return nullptr;
    uint16_t id; memcpy(&id, rp + 4, 2);
    *passId = id ? id : (uint16_t)*g_passId;
    return rp;
}

// Native families on the engine path. Plan of 2026-11-04 (orchestrator): until a hand-written family is finished
// and checked against the game's program on real frames, its passes go through the translated original; on by default
// only tile, nolight and image space (menuBG). Lit, depth, land and sky are written (Renderer::RenderWorld) and tested
// by the self-test with SetNativeFamilies.
static uint32_t g_nativeRoutes = 1u << Route_Legacy | 1u << Route_Tile | 1u << Route_NoLight | 1u << Route_ImageSpace;
void SetNativeFamilies(uint32_t routeMask) { g_nativeRoutes = routeMask | 1u << Route_Legacy | 1u << Route_Translated; }
uint32_t NativeFamilies() { return g_nativeRoutes; }
bool NativeFamilyEnabled(PassRoute r) { return r == Route_Translated || ((g_nativeRoutes >> r) & 1); }

void NiDX9Renderer::Do_RenderGeometry(const void* geometry) {
    Renderer* r = R();
    if (!r) { g_skipped++; return; }
    DrawStats& st = r->stats();
    st.engineDraws++;
    uint16_t passId = 0;
    const uint8_t* rp = CurrentRenderPass(geometry, &passId);
    PassInfo pi = RoutePass(passId);
    if (!NativeFamilyEnabled(pi.route)) pi = {Route_Translated, 0};
    Renderer::GeomView g;
    if (!ReadGeometry(geometry, &g)) {
        const uint8_t* geo = (const uint8_t*)geometry;
        uint32_t skin = 0;
        if (geo) memcpy(&skin, geo + 0xBC, 4);
        st.skipped[skin ? Skip_Skinned : Skip_Unreadable]++;
        g_skipped++;
        return;
    }
    switch (pi.route) {
    case Route_Tile: case Route_Legacy:
        if (!r->RenderGeometry(g)) { st.skipped[Skip_Submit]++; g_skipped++; return; }
        st.native[pi.route]++;
        return;
    case Route_Lit: case Route_Depth: case Route_Land: case Route_Sky: case Route_NoLight: {
        Renderer::WorldInputs in;
        ReadWorldInputs(geometry, rp, pi, passId, &in);
        if (!r->RenderWorld(g, in)) { st.skipped[Skip_Submit]++; g_skipped++; return; }
        st.native[pi.route]++;
        return;
    }
    default:
        st.skipped[Skip_NoFamily]++;
        g_skipped++;
        return;
    }
}

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
// Texture stage states: only the fixed-function pipeline reads them, which the native families do not have; they
// are recorded so GetTextureStageState answers like the engine's own cache (D3D9 defaults before the first set).
enum { kTssStages = 16, kTssTypes = 33 };
static uint32_t g_tss[kTssStages][kTssTypes];
static bool g_tssInit;
static void TssDefaults() {
    for (uint32_t s = 0; s < kTssStages; s++) {
        uint32_t* t = g_tss[s];
        t[1] = s ? 1 : 4;          // COLOROP: MODULATE on stage 0, DISABLE after
        t[2] = 2; t[3] = 1;        // COLORARG1 TEXTURE, COLORARG2 CURRENT
        t[4] = s ? 1 : 2;          // ALPHAOP: SELECTARG1 on stage 0, DISABLE after
        t[5] = 2; t[6] = 1;        // ALPHAARG1 TEXTURE, ALPHAARG2 CURRENT
        t[11] = s;                 // TEXCOORDINDEX
        t[26] = 1;                 // RESULTARG CURRENT
    }
    g_tssInit = true;
}
void NiDX9RenderState::SetTextureStageState(uint32_t st, uint32_t t, uint32_t v, uint32_t) {
    if (st >= kTssStages || t >= kTssTypes) return;
    if (!g_tssInit) TssDefaults();
    g_tss[st][t] = v;
}
uint32_t NiDX9RenderState::GetTextureStageState(uint32_t st, uint32_t t) {
    if (st >= kTssStages || t >= kTssTypes) return 0;
    if (!g_tssInit) TssDefaults();
    return g_tss[st][t];
}
void NiDX9RenderState::SetSamplerState(uint32_t st, uint32_t t, uint32_t v, uint32_t) {
    if (R()) R()->state().SetSamplerState(st, t, v);
}
uint32_t NiDX9RenderState::GetSamplerState(uint32_t st, uint32_t t) { return R() ? R()->state().GetSamplerState(st, t) : 0; }
static void* g_tex[16];
void NiDX9RenderState::SetTexture(uint32_t stage, void* texture) {
    if (stage >= 16 || g_tex[stage] == texture) return;
    g_tex[stage] = texture;
    NativeTexture* t = NativeTextureFromPointer(texture);
    if (t && t->dirty) MaterializeNativeTexture(t);
    NxD3D9Texture kind = {t ? t->d3dfmt : 0, 0, 0, 0, 0};
    if (R()) R()->state().SetTexture(stage, t ? t->image : 0, kind);
}
void* NiDX9RenderState::GetTexture(uint32_t stage) { return stage < 16 ? g_tex[stage] : nullptr; }
void NiDX9RenderState::ClearTextureUsage(void* texture) {
    if (!texture) return;
    for (uint32_t s = 0; s < 16; s++) if (g_tex[s] == texture) SetTexture(s, nullptr);
}
void NiDX9RenderState::RestoreRenderState(uint32_t s) { if (R()) R()->state().RestoreRenderState(s); }
void NiDX9RenderState::RestoreSamplerState(uint32_t st, uint32_t t) { if (R()) R()->state().RestoreSamplerState(st, t); }

// ---- shader construction: nothing to build natively ---------------------------------------------------------------
void TileShader::CreateVertexShaders() {}
void TileShader::CreatePixelShaders() {}
void TileShader::CreateConstantMaps() {}
void BSShaderNoLighting::InitializeShaderData() {}

// ---- geometry interop ------------------------------------------------------------------------------------------
uintptr_t g_guestBase = 0;
void SetGuestBase(uintptr_t base) { g_guestBase = base; }
uintptr_t GuestBase() { return g_guestBase; }
uint32_t GuestAddress(const void* p) { return p ? (uint32_t)((uintptr_t)p - g_guestBase) : 0; }

static inline uint32_t U32(const uint8_t* p, uint32_t o) { uint32_t v; memcpy(&v, p + o, 4); return v; }
static inline uint16_t U16(const uint8_t* p, uint32_t o) { uint16_t v; memcpy(&v, p + o, 2); return v; }
static inline const uint8_t* GP(uint32_t a) { return a ? (const uint8_t*)(g_guestBase + a) : nullptr; }

bool ReadGeometry(const void* geometry, Renderer::GeomView* out) {
    const uint8_t* geo = (const uint8_t*)geometry;
    if (!geo) return false;
    const uint8_t* data = GP(U32(geo, 0xB8));
    if (!data || U32(geo, 0xBC)) return false;            // no model data, or skinned (not in N1)
    Renderer::GeomView g{};
    memcpy(g.rot, geo + 0x68, 36); memcpy(g.trans, geo + 0x68 + 36, 12); memcpy(&g.scale, geo + 0x68 + 48, 4);
    g.vertices = U16(data, 0x08);
    g.pos = (const float*)GP(U32(data, 0x20));
    g.color = (const float*)GP(U32(data, 0x28));
    g.uv = (const float*)GP(U32(data, 0x2C));
    // NiGeometryData normals (+0x24); with an NBT method (data flags +0x0C, bits 12-15) the binormals and the tangents
    // follow the normals (N, B, T: vertices x 3 floats each)
    g.normal = (const float*)GP(U32(data, 0x24));
    if (g.normal && (U16(data, 0x0C) & 0xF000)) { g.binormal = g.normal + 3 * g.vertices; g.tangent = g.normal + 6 * g.vertices; }
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
    // NiAlphaProperty: flags bit 0 blend, 1-4 src, 5-8 dst (NiAlphaProperty::AlphaFunction), 9 test, 10-12 test func
    if (const uint8_t* ap = GP(U32(geo, 0x9C))) {
        g.alphaFlags = U16(ap, 0x18); g.alphaRef = ap[0x1A]; g.hasAlpha = true;
    }
    if (const uint8_t* stp = GP(U32(geo, 0xAC))) {   // NiStencilProperty
        g.hasStencil = true; g.stencilFlags = U16(stp, 0x18); g.stencilRef = U32(stp, 0x1C); g.stencilMask = U32(stp, 0x20);
    }
    float matAlpha = 1.0f;
    if (const uint8_t* mp = GP(U32(geo, 0xA4))) memcpy(&matAlpha, mp + 0x3C, 4);
    if (const uint8_t* sp = GP(U32(geo, 0xA8))) {   // TileShaderProperty
        memcpy(g.tint, sp + 0x68, 12);
        float a; memcpy(&a, sp + 0x78, 4);
        g.tint[3] = a * matAlpha;
        if (sp[0x91]) { g.hasScroll = true; memcpy(g.scroll, sp + 0x7C, 16); }         // TexScroll: uv * zw + xy
        if (sp[0xAC]) { g.hasScissor = true; memcpy(&g.scissor, sp + 0x9C, 16); }     // NiRectI l, t, r, b
        if (const uint8_t* tex = GP(U32(sp, 0x60)))
            if (NativeTexture* t = FindNativeTexture(GP(U32(tex, 0x24)), 0x80)) { g.image = t->image; g.d3dfmt = t->d3dfmt; }
        if (const uint8_t* am = GP(U32(sp, 0x64)))          // second texture: the alpha map (TILE1001)
            if (NativeTexture* t = FindNativeTexture(GP(U32(am, 0x24)), 0x80)) { g.alphaMap = t->image; g.alphaMapFmt = t->d3dfmt; }
    } else {
        g.tint[3] = matAlpha;
    }
    *out = g;
    return true;
}

// ---- world-family inputs -------------------------------------------------------------------------------------------
static NativeTexture* TextureOf(uint32_t niTexture) {   // NiTexture -> renderer data (+0x24) -> its NativeTexture
    const uint8_t* tex = GP(niTexture);
    return tex ? FindNativeTexture(GP(U32(tex, 0x24)), 0x80) : nullptr;
}
static void SetTex(Renderer::WorldInputs* in, int stage, NativeTexture* t) {
    if (t) { if (t->dirty) MaterializeNativeTexture(t); in->tex[stage] = t->image; in->texFmt[stage] = t->d3dfmt; }
}

void ReadWorldInputs(const void* geometry, const uint8_t* rp, const PassInfo& pi, uint16_t passId, Renderer::WorldInputs* in) {
    memset(in, 0, sizeof *in);
    in->route = pi; in->pass = passId;
    in->color[0] = in->color[1] = in->color[2] = in->color[3] = 1.0f;
    in->specular[3] = 10.0f;
    const uint8_t* geo = (const uint8_t*)geometry;
    const uint8_t* sp = GP(U32(geo, 0xA8));      // the BSShaderProperty
    float alpha = 1.0f, matAlpha = 1.0f;
    if (sp) memcpy(&alpha, sp + 0x28, 4);         // BSShaderProperty alpha
    const uint8_t* mp = GP(U32(geo, 0xA4));      // NiMaterialProperty: alpha +0x3C, glossiness +0x38
    if (mp) { memcpy(&matAlpha, mp + 0x3C, 4); memcpy(&in->specular[3], mp + 0x38, 4); }
    in->color[3] = alpha * matAlpha;
    if (pi.route == Route_NoLight) {             // BSShaderNoLightingProperty: texture +0x68, emissive colour +0x78
        if (sp) { SetTex(in, 0, TextureOf(U32(sp, 0x68))); memcpy(in->color, sp + 0x78, 16); in->color[3] *= alpha * matAlpha; }
        return;
    }
    if (pi.route == Route_Sky) {
        for (int i = 0; i < 3; i++) {
            in->blend[i][0] = in->blend[i][1] = in->blend[i][2] = in->blend[i][3] = 1.0f;
            if (g_skyColors) memcpy(in->blend[i], g_skyColors + 4 * i, 16);
        }
        return;   // the cloud texture: the state tracker's stage 0 (set by the sky shader's texture setup)
    }
    // BSShaderPPLightingProperty: texture slot arrays +0xB4 (slot 0 diffuse, slot 1 normal map; element = layer),
    // specular colour +0x9C
    if (sp) {
        const uint8_t* s0 = GP(U32(sp, 0xB4));
        const uint8_t* s1 = GP(U32(sp, 0xB8));
        if (s0) SetTex(in, 0, TextureOf(U32(s0, 0)));
        if (s1 && pi.route == Route_Land) SetTex(in, 4, TextureOf(U32(s1, 0)));
        memcpy(in->specular, sp + 0x9C, 12);
    }
    // lights of the pass: RenderPass +9 count, +0xC ShadowSceneLight** (+0xF8 the NiLight: world translate +0x8C,
    // effect type +0x9D (1 point, 2 directional), dimmer +0xC4, ambient +0xC8, diffuse +0xD4; NiDirectionalLight world
    // direction +0xF0; NiPointLight radius +0xF0)
    if (!rp) return;
    const uint32_t n = rp[9] < 8 ? rp[9] : 8;
    const uint8_t* list = GP(U32(rp, 0xC));
    for (uint32_t i = 0; list && i < n; i++) {
        const uint8_t* ssl = GP(U32(list, 4 * i));
        const uint8_t* nl = ssl ? GP(U32(ssl, 0xF8)) : nullptr;
        if (!nl) continue;
        NxLight& L = in->lights[in->lightCount];
        float dimmer, amb[3], diff[3];
        memcpy(&dimmer, nl + 0xC4, 4); memcpy(amb, nl + 0xC8, 12); memcpy(diff, nl + 0xD4, 12);
        for (int k = 0; k < 3; k++) L.color[k] = diff[k] * dimmer;
        if (nl[0x9D] == 2) { memcpy(L.posRange, nl + 0xF0, 12); L.posRange[3] = 0; L.color[3] = 1.0f; }
        else { memcpy(L.posRange, nl + 0x8C, 12); memcpy(&L.posRange[3], nl + 0xF0, 4); L.color[3] = 0; }
        if (in->lightCount == 0) for (int k = 0; k < 3; k++) in->ambient[k] = amb[k] * dimmer;
        in->lightCount++;
    }
}

// ---- image space: the effect dispatch ----------------------------------------------------------------------------
static uint32_t g_isNative = 0, g_isSkipped = 0;
uint32_t ImageSpaceEffectsNative() { return g_isNative; }
uint32_t ImageSpaceEffectsSkipped() { return g_isSkipped; }

void ImageSpaceManager::RenderEffect(int32_t effect, void*, void* src, void* dst, void*, bool) {
    Renderer* r = R();
    const uint8_t* self = (const uint8_t*)this;
    const uint8_t* list = r && effect >= 0 ? GP(U32(self, 0x08)) : nullptr;
    const uint8_t* fx = list ? GP(U32(list, 4 * (uint32_t)effect)) : nullptr;
    if (r) r->stats().engineDraws++;
    if (!fx || effect != kEffectMenuBg) { g_isSkipped++; if (r) r->stats().skipped[Skip_ImageSpace]++; return; }
    uint32_t scene = 0, w = 0, h = 0;
    if (!r->GroupColor(src, &scene, &w, &h)) { g_isSkipped++; r->stats().skipped[Skip_ImageSpace]++; return; }
    NativeTexture* tone = nullptr;
    if (const uint8_t* ist = GP(U32(fx, 0x58)))
        if (const uint8_t* tex = GP(U32(ist, 4))) tone = FindNativeTexture(GP(U32(tex, 0x24)), 0x80);
    float blend; memcpy(&blend, fx + 0x5C, 4);
    if (!tone) { g_isSkipped++; r->stats().skipped[Skip_ImageSpace]++; return; }
    static const float kLuma[3] = {0.299f, 0.587f, 0.114f};   // Rec. 601 (the effect's vertex constant)
    const bool switchTarget = dst != nullptr;
    if (switchTarget && !r->Do_BeginUsingRenderTargetGroup(dst, 0)) { g_isSkipped++; r->stats().skipped[Skip_ImageSpace]++; return; }
    bool ok = r->RenderMenuBackground(scene, 21, w, h, tone->image, tone->d3dfmt, kLuma, blend, 1.0f);
    if (switchTarget) r->Do_EndUsingRenderTargetGroup();
    if (ok) { g_isNative++; r->stats().native[Route_ImageSpace]++; }
    else { g_isSkipped++; r->stats().skipped[Skip_ImageSpace]++; }
}

} // namespace engine
} // namespace nx
