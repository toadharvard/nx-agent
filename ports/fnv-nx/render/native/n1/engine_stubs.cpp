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
void BindPassGlobals(const uint32_t* passId, const uint32_t* renderPass) { g_passId = passId; g_renderPass = renderPass; }
void BindImagePassGlobals() {
    BindPassGlobals((const uint32_t*)(g_guestBase + 0x011f91e4u), (const uint32_t*)(g_guestBase + 0x011f91e0u));
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

// ---- the family dispatch table: one row per family (passes.h routes) ----------------------------------------------
// native = the family's own draw serves its passes; otherwise they take the translated route (the game's programs,
// translated). Family streams switch their row to native when their family is finished and checked on real frames
// (CLAIMS.md: render-lit owns lit, render-land owns land / LOD / sky / water / depth / the world image-space chain).
// A row switched to native whose family has no engine-path draw here yet still goes to the translated route.
static FamilyRow g_rows[Route_Count] = {
    {Route_Legacy, "tile (menu path, no pass)", true},
    {Route_Tile, "tile", true},
    {Route_NoLight, "nolight", false},      // the native engine-path draw for NoLighting passes is not written
    {Route_Lit, "lit", false},
    {Route_Depth, "depth", false},
    {Route_Land, "land", false},
    {Route_Sky, "sky", false},
    {Route_ImageSpace, "imagespace (menuBG)", true},
    {Route_Translated, "translated", false},
};
const FamilyRow* FamilyRows() { return g_rows; }
void SetFamilyNative(PassRoute r, bool native) { if (r < Route_Count && r != Route_Translated) g_rows[r].native = native; }
static bool HasNativeDraw(PassRoute r) { return r == Route_Tile || r == Route_Legacy; }

void NiDX9Renderer::Do_RenderGeometry(const void* geometry) {
    Renderer* r = R();
    if (!r) { g_skipped++; return; }
    DrawStats& st = r->stats();
    st.engineDraws++;
    uint16_t passId = 0;
    CurrentRenderPass(geometry, &passId);
    PassInfo pi = RoutePass(passId);
    if (pi.route >= Route_Count || !g_rows[pi.route].native || !HasNativeDraw(pi.route)) pi = {Route_Translated, 0};
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
    default: {
        if (!r->translatedStore()) { st.skipped[Skip_NoFamily]++; g_skipped++; return; }
        int vs = -1, ps = -1;
        const uint8_t* pass = nullptr;
        const DrawSkip why = TranslatedPrograms(geometry, &vs, &ps, &pass);
        if (why) { st.skipped[why]++; g_skipped++; return; }
        if (!r->TranslatedLoadable(vs) || !r->TranslatedLoadable(ps)) { st.skipped[Skip_NoDksh]++; g_skipped++; return; }
        static TranslatedConstants c;
        const uint32_t unmapped = TranslatedConstantsFor(geometry, pass, g, &c);
        if (!r->RenderTranslated(g, vs, ps, c, passId)) { st.skipped[Skip_Submit]++; g_skipped++; return; }
        st.translated++;
        if (unmapped) st.translatedPartial++;
        return;
    }
    }
}

// ---- translated programs and constants ------------------------------------------------------------------------------
static int ProgramOf(const TranslatedStore* s, const uint8_t* obj) {
    if (!obj) return -1;
    for (uint32_t o = 0x08; o < 0x60; o += 4) {   // the D3D shader the hooked device created, held by the program
        uint32_t v; memcpy(&v, obj + o, 4);
        uint64_t h;
        if (v && ShaderObjectHash((const void*)(g_guestBase + v), &h)) return s->Find(h);
    }
    uint32_t name; memcpy(&name, obj + 0x0C, 4);   // NiD3DShaderProgram name
    return name ? s->FindName((const char*)(g_guestBase + name)) : -1;
}

DrawSkip TranslatedPrograms(const void* geometry, int* vs, int* ps, const uint8_t** passOut) {
    const TranslatedStore* s = R() ? R()->translatedStore() : nullptr;
    const uint8_t* geo = (const uint8_t*)geometry;
    uint32_t a; memcpy(&a, geo + 0xC0, 4);
    const uint8_t* shader = a ? (const uint8_t*)(g_guestBase + a) : nullptr;
    if (!s || !shader) return Skip_NoEngineShader;
    memcpy(&a, shader + 0x5C, 4);
    const uint8_t* pass = a ? (const uint8_t*)(g_guestBase + a) : nullptr;
    if (!pass) return Skip_NoEngineShader;
    uint32_t v, p; memcpy(&v, pass + 0x5C, 4); memcpy(&p, pass + 0x44, 4);
    if (!v || !p) return Skip_NoEngineShader;
    *vs = ProgramOf(s, (const uint8_t*)(g_guestBase + v));
    *ps = ProgramOf(s, (const uint8_t*)(g_guestBase + p));
    *passOut = pass;
    return *vs < 0 || *ps < 0 ? Skip_NoProgram : Skip_NotSkipped;
}

static void MulCM(const float* a, const float* b, float* o) {   // column-major o = a * b
    for (int c = 0; c < 4; c++)
        for (int r = 0; r < 4; r++) { float s = 0; for (int k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k]; o[c * 4 + r] = s; }
}
// a matrix into registers: "...Transpose" keys hold the rows of the column-vector matrix (dp4 with c0..c3 gives x..w),
// the others its columns (Gamebryo's D3D row-vector matrix rows)
static void PutMatrix(float (*c)[4], uint32_t reg, uint32_t count, const float* m, bool transpose) {
    for (uint32_t i = 0; i < count && i < 4 && reg + i < 256; i++)
        for (int k = 0; k < 4; k++) c[reg + i][k] = transpose ? m[k * 4 + i] : m[i * 4 + k];
}
static bool Ends(const char* s, const char* suf) {
    const size_t n = strlen(s), m = strlen(suf);
    return n >= m && !strcmp(s + n - m, suf);
}

static uint32_t EvalMap(const uint8_t* map, float (*c)[4], const float* world, const float* view, const float* proj,
                        const float* eye) {
    if (!map) return 0;
    uint32_t base; memcpy(&base, map + 0x10, 4);
    uint16_t maxSize; memcpy(&maxSize, map + 0x14, 2);
    uint32_t unmapped = 0;
    for (uint32_t i = 0; base && i < maxSize; i++) {
        uint32_t ea; memcpy(&ea, (const uint8_t*)(g_guestBase + base) + 4 * i, 4);
        if (!ea) continue;
        const uint8_t* e = (const uint8_t*)(g_guestBase + ea);
        uint32_t keyA, reg, count, size, data;
        memcpy(&keyA, e + 0x0C, 4); memcpy(&reg, e + 0x1C, 4); memcpy(&count, e + 0x20, 4);
        memcpy(&size, e + 0x28, 4); memcpy(&data, e + 0x30, 4);
        if (reg >= 256) continue;
        if (!count) count = 1;
        if (data) {   // attribute / constant / global entry: its data source
            uint32_t bytes = count * 16;
            if (size && size < bytes) bytes = size;
            if (bytes > (256 - reg) * 16) bytes = (256 - reg) * 16;
            memcpy(c[reg], (const void*)(g_guestBase + data), bytes);
            continue;
        }
        const char* key = keyA ? (const char*)(g_guestBase + keyA) : "";
        const bool tr = Ends(key, "Transpose");
        char k[64];
        size_t kl = strlen(key) - (tr ? 9 : 0);
        if (kl >= sizeof k) kl = sizeof k - 1;
        memcpy(k, key, kl); k[kl] = 0;
        float m[16], t[16];
        if (!strcmp(k, "WorldViewProj") || !strcmp(k, "ModelViewProj")) { MulCM(proj, view, t); MulCM(t, world, m); }
        else if (!strcmp(k, "World")) memcpy(m, world, 64);
        else if (!strcmp(k, "View")) memcpy(m, view, 64);
        else if (!strcmp(k, "Proj") || !strcmp(k, "Projection")) memcpy(m, proj, 64);
        else if (!strcmp(k, "ViewProj")) MulCM(proj, view, m);
        else if (!strcmp(k, "WorldView")) MulCM(view, world, m);
        else if (!strcmp(k, "EyePos") || !strcmp(k, "EyePosition")) {
            c[reg][0] = eye[0]; c[reg][1] = eye[1]; c[reg][2] = eye[2]; c[reg][3] = 1.0f;
            continue;
        } else { unmapped++; continue; }
        PutMatrix(c, reg, count, m, tr);
    }
    return unmapped;
}

uint32_t TranslatedConstantsFor(const void*, const uint8_t* pass, const Renderer::GeomView& g, TranslatedConstants* c) {
    *c = DeviceConstants();
    float world[16];
    Renderer::WorldMatrix(g, world);
    Renderer* r = R();
    uint32_t vm, pm; memcpy(&vm, pass + 0x48, 4); memcpy(&pm, pass + 0x34, 4);
    return EvalMap(vm ? (const uint8_t*)(g_guestBase + vm) : nullptr, c->vsc, world, r->view(), r->proj(), r->eye()) +
           EvalMap(pm ? (const uint8_t*)(g_guestBase + pm) : nullptr, c->psc, world, r->view(), r->proj(), r->eye());
}

bool HookRendererDevice(const void* renderer) {
    uint32_t d; memcpy(&d, (const uint8_t*)renderer + 0x28C, 4);
    return d && HookD3DDevice((void*)(g_guestBase + d));
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
        t[26] = 1; t[27] = 1;      // COLORARG0, ALPHAARG0: CURRENT
        t[28] = 1;                 // RESULTARG: CURRENT (V1 O3: 26 is COLORARG0, not RESULTARG)
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
// The engine's defaults pass (slot 47, not overridden) writes each sampler default into its own saved field and
// sets it through slot 51; slot 53 later re-sets that saved value. The first set of a (stage, type) is that
// defaults pass, so it is recorded here as the value slot 53 restores (V1 O2: restoring D3D9's default instead was
// wrong for 10 of 10 states the engine sets differently).
enum { kSampStages = 16, kSampTypes = 14 };
static uint32_t g_sampSaved[kSampStages][kSampTypes];
static bool g_sampSeen[kSampStages][kSampTypes];
void NiDX9RenderState::SetSamplerState(uint32_t st, uint32_t t, uint32_t v, uint32_t) {
    if (st < kSampStages && t < kSampTypes && !g_sampSeen[st][t]) { g_sampSeen[st][t] = true; g_sampSaved[st][t] = v; }
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
void NiDX9RenderState::RestoreSamplerState(uint32_t st, uint32_t t) {
    if (!R()) return;
    if (st < kSampStages && t < kSampTypes && g_sampSeen[st][t]) R()->state().SetSamplerState(st, t, g_sampSaved[st][t]);
    else R()->state().RestoreSamplerState(st, t);
}

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
