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

// ---- render-state cache: the engine's functions on the engine object, the device call -> StateTracker ---------------
typedef NiDX9RenderStateLayout L;
static inline uint32_t* Cache(void* self, uint32_t off) { return (uint32_t*)((uint8_t*)self + off); }

int NiDX9RenderStateLayout::TssSlot(uint32_t t) {
    if (t >= 1 && t <= 7) return (int)t - 1;   // COLOROP, COLORARG1, COLORARG2, ALPHAOP, ALPHAARG1, ALPHAARG2, BUMPENVMAT00
    return t == 24 ? 7 : -1;                   // TEXTURETRANSFORMFLAGS
}
int NiDX9RenderStateLayout::SampSlot(uint32_t t) {
    switch (t) {
    case 1: return 0; case 2: return 1;        // ADDRESSU, ADDRESSV
    case 5: return 2; case 6: return 3; case 7: return 4;   // MAGFILTER, MINFILTER, MIPFILTER
    default: return -1;
    }
}
void NiDX9RenderStateLayout::Construct(void* o, void* device) {
    memset(o, 0, kSize);
    for (uint32_t i = 0; i < kRsCount; i++) Cache(o, kRs)[2 * i] = kUnknown;
    for (uint32_t i = 0; i < kStages * kTssPerStage; i++) Cache(o, kTss)[2 * i] = kUnknown;
    for (uint32_t i = 0; i < kStages * kSampPerStage; i++) Cache(o, kSamp)[2 * i] = kUnknown;
    *Cache(o, kDevice) = GuestAddress(device);
}
void NiDX9RenderStateLayout::DefaultsPass(void* o, void* device, const uint32_t* rs, const uint32_t* tss, uint32_t tssStages,
                                          const uint32_t* samp, uint32_t sampStages) {
    const uintptr_t* vt = device ? *(const uintptr_t* const*)device : nullptr;
    typedef int32_t (*Set2)(void*, uint32_t, uint32_t);
    typedef int32_t (*Set3)(void*, uint32_t, uint32_t, uint32_t);
    for (const uint32_t* p = rs; p && p[0] != 0xFFFFFFFFu; p += 2) {
        if (p[0] >= kRsCount) continue;
        Cache(o, kRs)[2 * p[0] + 1] = p[1]; Cache(o, kRs)[2 * p[0]] = p[1];
        if (vt) ((Set2)vt[57])(device, p[0], p[1]);                       // IDirect3DDevice9::SetRenderState
    }
    for (uint32_t st = 0; st < tssStages && st < kStages; st++)
        for (const uint32_t* p = tss; p && p[0] != 0xFFFFFFFFu; p += 2) {
            uint32_t v = p[1];
            if (p[0] == 11) v = st;
            else if ((p[0] == 1 || p[0] == 4) && st) v = 1;
            const int k = TssSlot(p[0]);
            if (k >= 0) Cache(o, kTss)[2 * (st * kTssPerStage + (uint32_t)k) + 1] = v;
            if (vt) ((Set3)vt[67])(device, st, p[0], v);                  // SetTextureStageState
        }
    for (uint32_t st = 0; st < sampStages && st < kStages; st++)
        for (const uint32_t* p = samp; p && p[0] != 0xFFFFFFFFu; p += 2) {
            const int k = SampSlot(p[0]);
            if (k >= 0) Cache(o, kSamp)[2 * (st * kSampPerStage + (uint32_t)k) + 1] = p[1];
            ((NiDX9RenderState*)o)->SetSamplerState(st, p[0], p[1], 0);   // slot 51
            if (vt) ((Set3)vt[69])(device, st, p[0], p[1]);               // SetSamplerState
        }
}

void NiDX9RenderState::SetRenderState(uint32_t s, uint32_t v, uint32_t, uint8_t lock) {
    uint8_t* locks = (uint8_t*)this + L::kRsLock;
    if (s >= L::kRsCount || locks[s]) return;
    locks[s] = lock;
    uint32_t* cur = Cache(this, L::kRs) + 2 * s;
    if (*cur == v) return;
    if (R()) R()->state().SetRenderState(s, v);
    *cur = v;
}
uint32_t NiDX9RenderState::GetRenderState(uint32_t s) { return s < L::kRsCount ? Cache(this, L::kRs)[2 * s] : 0; }
void NiDX9RenderState::SetBlendFunc(uint32_t src, uint32_t dst, uint32_t, uint32_t) {   // SRCBLEND, DESTBLEND; no lock
    uint32_t* rs = Cache(this, L::kRs);
    if (rs[2 * 19] != src && R()) R()->state().SetRenderState(19, src);
    rs[2 * 19] = src;
    if (rs[2 * 20] != dst && R()) R()->state().SetRenderState(20, dst);
    rs[2 * 20] = dst;
}
void NiDX9RenderState::SetAlphaBlendEnable(uint32_t e, uint32_t, uint32_t) {             // ALPHABLENDENABLE; no lock
    uint32_t* rs = Cache(this, L::kRs);
    if (rs[2 * 27] != e && R()) R()->state().SetRenderState(27, e);
    rs[2 * 27] = e;
}
void NiDX9RenderState::RestoreRenderState(uint32_t s) {
    if (s < L::kRsCount) SetRenderState(s, Cache(this, L::kRs)[2 * s + 1], 0, 0);
}
// Texture stage states: only the fixed-function pipeline reads them (the native families have none); they reach the
// tracker's texture stage states as they would the device's.
void NiDX9RenderState::SetTextureStageState(uint32_t st, uint32_t t, uint32_t v, uint32_t) {
    const int k = L::TssSlot(t);
    if (k < 0 || st >= L::kStages) return;
    uint32_t* cur = Cache(this, L::kTss) + 2 * (st * L::kTssPerStage + (uint32_t)k);
    if (*cur != v && R()) R()->state().SetTextureStageState(st, t, v);
    *cur = v;
}
uint32_t NiDX9RenderState::GetTextureStageState(uint32_t st, uint32_t t) {
    const int k = L::TssSlot(t);
    return k < 0 || st >= L::kStages ? 0 : Cache(this, L::kTss)[2 * (st * L::kTssPerStage + (uint32_t)k)];
}
void NiDX9RenderState::SetSamplerState(uint32_t st, uint32_t t, uint32_t v, uint32_t) {
    const int k = L::SampSlot(t);
    if (k < 0 || st >= L::kStages) return;
    uint32_t* cur = Cache(this, L::kSamp) + 2 * (st * L::kSampPerStage + (uint32_t)k);
    if (*cur == v) return;
    *cur = v;
    if (R()) R()->state().SetSamplerState(st, t, v);
}
uint32_t NiDX9RenderState::GetSamplerState(uint32_t st, uint32_t t) {
    const int k = L::SampSlot(t);
    return k < 0 || st >= L::kStages ? 0 : Cache(this, L::kSamp)[2 * (st * L::kSampPerStage + (uint32_t)k)];
}
// The defaults pass (slot 47, the engine's) writes each sampler default into the saved field and sets it through
// slot 51; slot 53 re-sets the saved value through slot 51 (V1 O2).
void NiDX9RenderState::RestoreSamplerState(uint32_t st, uint32_t t) {
    const int k = L::SampSlot(t);
    if (k < 0 || st >= L::kStages) return;
    SetSamplerState(st, t, Cache(this, L::kSamp)[2 * (st * L::kSampPerStage + (uint32_t)k) + 1], 0);
}
// A texture reaches the tracker as the device would see it: a NativeTexture -> its image (0 for anything else).
static void DeviceSetTexture(uint32_t stage, void* texture) {
    NativeTexture* t = NativeTextureFromPointer(texture);
    if (t && t->dirty) MaterializeNativeTexture(t);
    NxD3D9Texture kind = {t ? t->d3dfmt : 0, 0, 0, 0, 0};
    if (R()) R()->state().SetTexture(stage, t ? t->image : 0, kind);
}
// The cache holds 32-bit guest addresses; g_texPtr keeps the pointer behind each (the same value in the guest; on a
// 64-bit host test the full pointer).
static void* g_texPtr[NiDX9RenderStateLayout::kStages];
void NiDX9RenderState::SetTexture(uint32_t stage, void* texture) {
    if (stage >= L::kStages) return;
    uint32_t* cur = Cache(this, L::kTex) + stage;
    if (*cur == GuestAddress(texture)) return;
    *cur = GuestAddress(texture);
    g_texPtr[stage] = texture;
    DeviceSetTexture(stage, texture);
}
void* NiDX9RenderState::GetTexture(uint32_t stage) {
    const uint32_t a = stage < L::kStages ? Cache(this, L::kTex)[stage] : 0;
    if (!a) return nullptr;
    return g_texPtr[stage] && GuestAddress(g_texPtr[stage]) == a ? g_texPtr[stage] : (void*)(GuestBase() + a);
}
void NiDX9RenderState::ClearTextureUsage(void* texture) {
    if (!texture) return;
    uint32_t* tex = Cache(this, L::kTex);
    for (uint32_t s = 0; s < L::kStages; s++)
        if (tex[s] == GuestAddress(texture)) { tex[s] = 0; g_texPtr[s] = nullptr; DeviceSetTexture(s, nullptr); }
}

// ---- NativeDevice: IDirect3DDevice9's state methods on the StateTracker ------------------------------------------------
namespace {
enum : int32_t { kOk = 0, kInvalidCall = (int32_t)0x8876086Cu, kNotFound = (int32_t)0x88760866u,
                 kNoInterface = (int32_t)0x80004002u };
typedef uintptr_t W;
typedef NativeDevice D;
StateTracker* ST() { return R() ? &R()->state() : nullptr; }
uint32_t g_unhandled[NativeDevice::kMethods];

int32_t QueryInterface(D*, const void*, void** out) { if (out) *out = nullptr; return kNoInterface; }
uint32_t AddRef(D* d) { return ++d->refs; }
uint32_t Release(D* d) { return d->refs > 1 ? --d->refs : (d->refs = 1); }   // the process's device stays
int32_t SetRenderTarget(D*, uint32_t i, void* surf) {
    uint32_t desc[8] = {};   // D3DSURFACE_DESC: the surface's own GetDesc (IDirect3DSurface9 slot 12)
    if (surf) {
        const uintptr_t* vt = *(const uintptr_t* const*)surf;
        ((int32_t(*)(void*, uint32_t*))vt[12])(surf, desc);
    }
    return ST() && ST()->SetRenderTarget(i, surf, desc[6], desc[7]) ? kOk : kInvalidCall;
}
int32_t GetRenderTarget(D*, uint32_t i, void** out) {
    if (!out || !ST() || i >= StateTracker::kRTs) return kInvalidCall;
    *out = (void*)ST()->RenderTarget(i);
    return *out ? kOk : kNotFound;
}
int32_t SetDepthStencilSurface(D*, void* ds) { if (!ST()) return kInvalidCall; ST()->SetDepthStencilSurface(ds); return kOk; }
int32_t GetDepthStencilSurface(D*, void** out) {
    if (!out || !ST()) return kInvalidCall;
    *out = (void*)ST()->DepthStencilSurface();
    return *out ? kOk : kNotFound;
}
int32_t SetViewport(D*, const NxD3D9Viewport* vp) { if (!vp || !ST()) return kInvalidCall; ST()->SetViewport(*vp); return kOk; }
int32_t GetViewport(D*, NxD3D9Viewport* vp) { if (!vp || !ST()) return kInvalidCall; *vp = ST()->Viewport(); return kOk; }
int32_t SetClipPlane(D*, uint32_t i, const float* p) { return ST() && ST()->SetClipPlane(i, p) ? kOk : kInvalidCall; }
int32_t GetClipPlane(D*, uint32_t i, float* p) { return ST() && ST()->GetClipPlane(i, p) ? kOk : kInvalidCall; }
int32_t SetRenderState(D*, uint32_t s, uint32_t v) {
    if (!ST() || s >= NXD3D_RS_COUNT) return kInvalidCall;
    ST()->SetRenderState(s, v);
    return kOk;
}
int32_t GetRenderState(D*, uint32_t s, uint32_t* v) {
    if (!ST() || !v || s >= NXD3D_RS_COUNT) return kInvalidCall;
    *v = ST()->GetRenderState(s);
    return kOk;
}
int32_t GetTexture(D*, uint32_t s, void** out);
int32_t SetTexture(D*, uint32_t s, void* tex);
int32_t GetTextureStageState(D*, uint32_t st, uint32_t t, uint32_t* v) {
    if (!ST() || !v || st >= StateTracker::kTssStages || t >= NXD3D_TSS_COUNT) return kInvalidCall;
    *v = ST()->GetTextureStageState(st, t);
    return kOk;
}
int32_t SetTextureStageState(D*, uint32_t st, uint32_t t, uint32_t v) {
    if (!ST() || st >= StateTracker::kTssStages || t >= NXD3D_TSS_COUNT) return kInvalidCall;
    ST()->SetTextureStageState(st, t, v);
    return kOk;
}
int32_t GetSamplerState(D*, uint32_t s, uint32_t t, uint32_t* v) {
    if (!ST() || !v || nx_d3d9_sampler_index(s) < 0 || t >= NXD3D_SAMP_COUNT) return kInvalidCall;
    *v = ST()->GetSamplerState(s, t);
    return kOk;
}
int32_t SetSamplerState(D*, uint32_t s, uint32_t t, uint32_t v) {
    if (!ST() || nx_d3d9_sampler_index(s) < 0 || t >= NXD3D_SAMP_COUNT) return kInvalidCall;
    ST()->SetSamplerState(s, t, v);
    return kOk;
}
int32_t SetScissorRect(D*, const NxD3D9Rect* r) { if (!r || !ST()) return kInvalidCall; ST()->SetScissorRect(*r); return kOk; }
int32_t GetScissorRect(D*, NxD3D9Rect* r) { if (!r || !ST()) return kInvalidCall; *r = ST()->ScissorRect(); return kOk; }
int32_t SetVertexDeclaration(D*, void* decl) { if (!ST()) return kInvalidCall; ST()->SetVertexDeclaration(decl); return kOk; }
int32_t GetVertexDeclaration(D*, void** out) {
    if (!out || !ST()) return kInvalidCall;
    *out = (void*)ST()->VertexDeclaration();
    return kOk;
}
int32_t SetFVF(D*, uint32_t fvf) { if (!ST()) return kInvalidCall; ST()->SetFVF(fvf); return kOk; }
int32_t GetFVF(D*, uint32_t* fvf) { if (!fvf || !ST()) return kInvalidCall; *fvf = ST()->FVF(); return kOk; }
int32_t SetShader(bool ps, void* sh) { if (!ST()) return kInvalidCall; ST()->SetShader(ps, sh); return kOk; }
int32_t GetShader(bool ps, void** out) { if (!out || !ST()) return kInvalidCall; *out = (void*)ST()->Shader(ps); return kOk; }
int32_t SetVertexShader(D*, void* sh) { return SetShader(false, sh); }
int32_t GetVertexShader(D*, void** out) { return GetShader(false, out); }
int32_t SetPixelShader(D*, void* sh) { return SetShader(true, sh); }
int32_t GetPixelShader(D*, void** out) { return GetShader(true, out); }
#define NX_CONST(Name, ps, T, Fn)                                                                                         \
    int32_t Set##Name(D*, uint32_t start, const T* data, uint32_t n) { return ST() && ST()->Set##Fn(ps, start, data, n) ? kOk : kInvalidCall; } \
    int32_t Get##Name(D*, uint32_t start, T* data, uint32_t n) { return ST() && ST()->Get##Fn(ps, start, data, n) ? kOk : kInvalidCall; }
NX_CONST(VertexShaderConstantF, false, float, ShaderConstantF)
NX_CONST(VertexShaderConstantI, false, int32_t, ShaderConstantI)
NX_CONST(VertexShaderConstantB, false, uint32_t, ShaderConstantB)
NX_CONST(PixelShaderConstantF, true, float, ShaderConstantF)
NX_CONST(PixelShaderConstantI, true, int32_t, ShaderConstantI)
NX_CONST(PixelShaderConstantB, true, uint32_t, ShaderConstantB)
#undef NX_CONST
int32_t SetStreamSource(D*, uint32_t s, void* vb, uint32_t off, uint32_t stride) {
    return ST() && ST()->SetStreamSource(s, vb, off, stride) ? kOk : kInvalidCall;
}
int32_t GetStreamSource(D*, uint32_t s, void** vb, uint32_t* off, uint32_t* stride) {
    if (!ST() || !vb || !off || !stride || s >= StateTracker::kStreams) return kInvalidCall;
    const StateTracker::Stream st = ST()->GetStream(s);
    *vb = (void*)st.vb; *off = st.offset; *stride = st.stride;
    return kOk;
}
int32_t SetStreamSourceFreq(D*, uint32_t s, uint32_t setting) { return ST() && ST()->SetStreamSourceFreq(s, setting) ? kOk : kInvalidCall; }
int32_t GetStreamSourceFreq(D*, uint32_t s, uint32_t* setting) {
    if (!ST() || !setting || s >= StateTracker::kStreams) return kInvalidCall;
    *setting = ST()->GetStream(s).freq;
    return kOk;
}
int32_t SetIndices(D*, void* ib) { if (!ST()) return kInvalidCall; ST()->SetIndices(ib); return kOk; }
int32_t GetIndices(D*, void** out) { if (!out || !ST()) return kInvalidCall; *out = (void*)ST()->Indices(); return kOk; }

// the device's textures (all 21 samplers): the bound object as given, its NativeTexture image to the tracker
void* g_devTex[NXD3D_SAMPLERS];
int32_t GetTexture(D*, uint32_t s, void** out) {
    const int i = nx_d3d9_sampler_index(s);
    if (i < 0 || !out) return kInvalidCall;
    *out = g_devTex[i];
    return kOk;
}
int32_t SetTexture(D*, uint32_t s, void* tex) {
    const int i = nx_d3d9_sampler_index(s);
    if (i < 0) return kInvalidCall;
    g_devTex[i] = tex;
    NativeTexture* t = NativeTextureFromPointer(tex);
    if (t && t->dirty) MaterializeNativeTexture(t);
    NxD3D9Texture kind = {t ? t->d3dfmt : 0, 0, 0, 0, 0};
    if (ST()) ST()->SetTexture(s, t ? t->image : 0, kind);
    return kOk;
}

// unhandled methods: counted, D3DERR_INVALIDCALL (extra arguments are ignored by the AAPCS64 / SysV callers)
template <int N> int32_t Unhandled(D*) { g_unhandled[N]++; return kInvalidCall; }
template <int... I> struct Seq {};
template <int N, int... I> struct MakeSeq : MakeSeq<N - 1, N - 1, I...> {};
template <int... I> struct MakeSeq<0, I...> { typedef Seq<I...> type; };
typedef int32_t (*UFn)(D*);
template <int... I> struct UnhandledFns { static constexpr UFn fns[sizeof...(I)] = {&Unhandled<I>...}; };
template <int... I> const UFn* Fns(Seq<I...>) { return UnhandledFns<I...>::fns; }
W g_table[NativeDevice::kMethods];
NativeDevice g_device;

void FillTable() {
    W* v = g_table;
    const UFn* u = Fns(MakeSeq<NativeDevice::kMethods>::type{});
    for (uint32_t i = 0; i < NativeDevice::kMethods; i++) v[i] = (W)u[i];
    v[0] = (W)QueryInterface; v[1] = (W)AddRef; v[2] = (W)Release;
    v[37] = (W)SetRenderTarget; v[38] = (W)GetRenderTarget; v[39] = (W)SetDepthStencilSurface;
    v[40] = (W)GetDepthStencilSurface; v[47] = (W)SetViewport; v[48] = (W)GetViewport;
    v[55] = (W)SetClipPlane; v[56] = (W)GetClipPlane; v[57] = (W)SetRenderState; v[58] = (W)GetRenderState;
    v[64] = (W)GetTexture; v[65] = (W)SetTexture; v[66] = (W)GetTextureStageState; v[67] = (W)SetTextureStageState;
    v[68] = (W)GetSamplerState; v[69] = (W)SetSamplerState; v[75] = (W)SetScissorRect; v[76] = (W)GetScissorRect;
    v[87] = (W)SetVertexDeclaration; v[88] = (W)GetVertexDeclaration; v[89] = (W)SetFVF; v[90] = (W)GetFVF;
    v[92] = (W)SetVertexShader; v[93] = (W)GetVertexShader;
    v[94] = (W)SetVertexShaderConstantF; v[95] = (W)GetVertexShaderConstantF;
    v[96] = (W)SetVertexShaderConstantI; v[97] = (W)GetVertexShaderConstantI;
    v[98] = (W)SetVertexShaderConstantB; v[99] = (W)GetVertexShaderConstantB;
    v[100] = (W)SetStreamSource; v[101] = (W)GetStreamSource; v[102] = (W)SetStreamSourceFreq;
    v[103] = (W)GetStreamSourceFreq; v[104] = (W)SetIndices; v[105] = (W)GetIndices;
    v[107] = (W)SetPixelShader; v[108] = (W)GetPixelShader;
    v[109] = (W)SetPixelShaderConstantF; v[110] = (W)GetPixelShaderConstantF;
    v[111] = (W)SetPixelShaderConstantI; v[112] = (W)GetPixelShaderConstantI;
    v[113] = (W)SetPixelShaderConstantB; v[114] = (W)GetPixelShaderConstantB;
}
}  // namespace

NativeDevice* CreateNativeDevice() {
    if (g_device.magic != NativeDevice::kMagic) {
        FillTable();
        g_device.vtbl = g_table; g_device.magic = NativeDevice::kMagic; g_device.refs = 1;
        memset(g_devTex, 0, sizeof g_devTex);
    }
    return &g_device;
}
uint32_t NativeDeviceUnhandled(uint32_t m) { return m < NativeDevice::kMethods ? g_unhandled[m] : 0; }

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
    if (!fx || effect != kEffectMenuBg) { g_isSkipped++; return; }
    uint32_t scene = 0, w = 0, h = 0;
    if (!r->GroupColor(src, &scene, &w, &h)) { g_isSkipped++; return; }
    NativeTexture* tone = nullptr;
    if (const uint8_t* ist = GP(U32(fx, 0x58)))
        if (const uint8_t* tex = GP(U32(ist, 4))) tone = FindNativeTexture(GP(U32(tex, 0x24)), 0x80);
    float blend; memcpy(&blend, fx + 0x5C, 4);
    if (!tone) { g_isSkipped++; return; }
    static const float kLuma[3] = {0.299f, 0.587f, 0.114f};   // Rec. 601 (the effect's vertex constant)
    const bool switchTarget = dst != nullptr;
    if (switchTarget && !r->Do_BeginUsingRenderTargetGroup(dst, 0)) { g_isSkipped++; return; }
    bool ok = r->RenderMenuBackground(scene, 21, w, h, tone->image, tone->d3dfmt, kLuma, blend, 1.0f);
    if (switchTarget) r->Do_EndUsingRenderTargetGroup();
    if (ok) g_isNative++; else g_isSkipped++;
}

} // namespace engine
} // namespace nx
