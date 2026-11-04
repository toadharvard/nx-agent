// fnv-nx native renderer, N1: nx::Renderer and nx::StateTracker. See renderer.h.
#include "renderer.h"
#include <string.h>
#include <new>

namespace nx {

// ---- StateTracker ------------------------------------------------------------------------------------------------
void StateTracker::Reset() {
    nx_d3d9_default_rs(rs_);
    memcpy(rsSaved_, rs_, sizeof rs_);
    for (int s = 0; s < kStages; s++) { nx_d3d9_default_samp(samp_[s]); image_[s] = 0; memset(&kind_[s], 0, sizeof kind_[s]); }
    memcpy(sampSaved_, samp_, sizeof samp_);
    psVersion_ = 0x0200;
    dirty_ = true;
}

void StateTracker::SetRenderState(uint32_t state, uint32_t value, bool save) {
    if (state >= NXD3D_RS_COUNT) return;
    if (save) rsSaved_[state] = rs_[state];
    if (rs_[state] != value) { rs_[state] = value; dirty_ = true; }
}
void StateTracker::RestoreRenderState(uint32_t state) {
    if (state < NXD3D_RS_COUNT) SetRenderState(state, rsSaved_[state]);
}
void StateTracker::SetSamplerState(uint32_t stage, uint32_t type, uint32_t value, bool save) {
    if (stage >= kStages || type >= NXD3D_SAMP_COUNT) return;
    if (save) sampSaved_[stage][type] = samp_[stage][type];
    samp_[stage][type] = value;
}
uint32_t StateTracker::GetSamplerState(uint32_t stage, uint32_t type) const {
    return stage < kStages && type < NXD3D_SAMP_COUNT ? samp_[stage][type] : 0;
}
void StateTracker::RestoreSamplerState(uint32_t stage, uint32_t type) {
    if (stage < kStages && type < NXD3D_SAMP_COUNT) samp_[stage][type] = sampSaved_[stage][type];
}
void StateTracker::SetTexture(uint32_t stage, uint32_t image, const NxD3D9Texture& kind) {
    if (stage >= kStages) return;
    image_[stage] = image; kind_[stage] = kind;
}

const StateTracker::Resolved& StateTracker::Resolve(const NxD3D9Viewport& vp, const NxD3D9Rect& sc, uint32_t depthFormat) {
    if (!dirty_ && !memcmp(&vp, &lastVp_, sizeof vp) && !memcmp(&sc, &lastSc_, sizeof sc) && depthFormat == lastDepth_)
        return out_;
    NxD3D9Input in;
    memset(&in, 0, sizeof in);
    in.rs = rs_; in.ps_version = psVersion_; in.depth_format = depthFormat; in.viewport = vp; in.scissor = sc;
    nx_d3d9_translate(&in, &out_.rs, &out_.dyn, &out_.shaderBits);
    lastVp_ = vp; lastSc_ = sc; lastDepth_ = depthFormat; dirty_ = false;
    resolves_++;
    return out_;
}

uint32_t StateTracker::Handles(Backend* be, uint32_t* out, uint32_t n) {
    if (n > kStages) n = kStages;
    for (uint32_t s = 0; s < n; s++) {
        NxSamplerState ss;
        nx_d3d9_sampler(samp_[s], &kind_[s], &ss);
        uint32_t id = 0;
        for (uint32_t i = 0; i < nSmp_; i++) if (!memcmp(&smpKey_[i], &ss, sizeof ss)) { id = smpId_[i]; break; }
        if (!id) {
            id = be->SamplerCreate(ss);
            if (nSmp_ < kSamplerCache) { smpKey_[nSmp_] = ss; smpId_[nSmp_++] = id; }
        }
        bool srgb = (ss.filter & NXSMP_SRGB) && nx_format_has_srgb(nx_d3d9_format(kind_[s].format).format);
        out[s] = image_[s] ? NX_TEX_HANDLE(image_[s] + (srgb ? 1 : 0), id) : NX_TEX_HANDLE(0, id);
    }
    return n;
}

// ---- matrices (column-major: m[col * 4 + row]) ---------------------------------------------------------------------
static void ident(float m[16]) { memset(m, 0, 64); m[0] = m[5] = m[10] = m[15] = 1; }

void CameraView(const NiPoint3f& loc, const NiPoint3f& dir, const NiPoint3f& up, const NiPoint3f& right, float m[16]) {
    // camera space: x = right, y = up, z = dir (D3D: looks down +z)
    const NiPoint3f* ax[3] = {&right, &up, &dir};
    ident(m);
    for (int r = 0; r < 3; r++) {
        m[0 * 4 + r] = ax[r]->x; m[1 * 4 + r] = ax[r]->y; m[2 * 4 + r] = ax[r]->z;
        m[3 * 4 + r] = -(ax[r]->x * loc.x + ax[r]->y * loc.y + ax[r]->z * loc.z);
    }
}

void CameraProjection(const NiFrustumF& f, float m[16]) {
    memset(m, 0, 64);
    float rl = 1.0f / (f.right - f.left), tb = 1.0f / (f.top - f.bottom), fn = 1.0f / (f.farZ - f.nearZ);
    if (f.ortho) {
        m[0] = 2 * rl; m[12] = -(f.right + f.left) * rl;
        m[5] = 2 * tb; m[13] = -(f.top + f.bottom) * tb;
        m[10] = fn; m[14] = -f.nearZ * fn;
        m[15] = 1;
    } else {   // frustum extents are tangents (the view plane at distance 1)
        m[0] = 2 * rl; m[8] = -(f.right + f.left) * rl;
        m[5] = 2 * tb; m[9] = -(f.top + f.bottom) * tb;
        m[10] = f.farZ * fn; m[14] = -f.nearZ * f.farZ * fn;
        m[11] = 1;
    }
}

void ScreenSpaceProjection(float m[16], uint32_t w, uint32_t h) {
    // The engine's screen-space camera (0x00e6cf40): view (x - 0.5, 0.5 - y, z + 1) times proj (2x - 1/W, 2y + 1/H,
    // (z - 1)/9999), i.e. clip = (2x - 1 - 1/W, 1 - 2y + 1/H, z/9999): Gamebryo's D3D9 half-pixel correction.
    // N1 keeps view = identity in screen space and folds both into this matrix.
    ident(m);
    m[0] = 2; m[12] = -1.0f - 1.0f / (float)w;     // x [0,1] -> [-1,1], half a pixel left
    m[5] = -2; m[13] = 1.0f + 1.0f / (float)h;     // y [0,1] (down) -> [1,-1], half a pixel up
    m[10] = 1.0f / 9999.0f; m[14] = 0;            // screen z in the 1..9999 camera range
}

// The engine's viewport from a port rectangle (0x00e6c780, 0x00e6cf40): truncation, not rounding.
static NxD3D9Viewport PortViewport(const NiRectF& p, uint32_t w, uint32_t h) {
    NxD3D9Viewport v;
    v.X = (uint32_t)(p.left * (float)w); v.Y = (uint32_t)((1.0f - p.top) * (float)h);
    v.Width = (uint32_t)((p.right - p.left) * (float)w); v.Height = (uint32_t)((p.top - p.bottom) * (float)h);
    v.MinZ = 0.0f; v.MaxZ = 1.0f;
    return v;
}

// ---- Renderer --------------------------------------------------------------------------------------------------
static Renderer* g_renderer;

Renderer* Renderer::Get() { return g_renderer; }

bool Renderer::Init(Backend* be, uint32_t w, uint32_t h, uint32_t backbuffer, uint32_t depth) {
    g_renderer = this;
    be_ = be;
    if (!frame_.Init(be, 8u << 20, 512)) return false;
    state_.Reset();
    nGroups_ = 0;
    groups_[nGroups_++] = {nullptr, backbuffer, depth, w, h};
    ident(view_); ScreenSpaceProjection(proj_, w, h);
    vp_ = {0, 0, w, h, 0.0f, 1.0f};
    return true;
}

const Renderer::Group* Renderer::FindGroup(const void* g) const {
    for (uint32_t i = 0; i < nGroups_; i++) if (groups_[i].key == g) return &groups_[i];
    return nullptr;
}

bool Renderer::RegisterTargetGroup(const void* g, uint32_t color, uint32_t depth, uint32_t w, uint32_t h) {
    for (uint32_t i = 0; i < nGroups_; i++)
        if (groups_[i].key == g) { groups_[i] = {g, color, depth, w, h}; return true; }
    if (nGroups_ >= kMaxGroups) return false;
    groups_[nGroups_++] = {g, color, depth, w, h};
    return true;
}

void Renderer::SetBackgroundColor(float r, float g, float b, float a) { bg_[0] = r; bg_[1] = g; bg_[2] = b; bg_[3] = a; }

static uint8_t u8_(float v) { v = v < 0 ? 0 : v > 1 ? 1 : v; return (uint8_t)(v * 255.0f + 0.5f); }

void Renderer::OpenPass(uint32_t clearMode) {
    const Group* g = cur_ ? cur_ : &groups_[0];
    PassDesc pd;
    memset(&pd, 0, sizeof pd);
    pd.color = g->color; pd.depth = g->depth; pd.width = (uint16_t)g->w; pd.height = (uint16_t)g->h;
    pd.clearFlags = (clearMode & CLEAR_BACKBUFFER ? 1u : 0u) | (clearMode & CLEAR_ZBUFFER && g->depth ? 2u : 0u) |
                    (clearMode & CLEAR_STENCIL && g->depth ? 4u : 0u);
    pd.clearColor = (uint32_t)u8_(bg_[0]) | (uint32_t)u8_(bg_[1]) << 8 | (uint32_t)u8_(bg_[2]) << 16 | (uint32_t)u8_(bg_[3]) << 24;
    pd.clearDepth = clearDepth_; pd.clearStencil = clearStencil_;
    pd.sampledAfter = g != &groups_[0];   // offscreen targets are read by later passes (image space)
    uint8_t p = frame_.AddPass(pd);
    pass_ = p == 0xFF ? -1 : p;
    float* t = pass_ >= 0 ? (float*)frame_.PassConstants((uint8_t)pass_, 16) : nullptr;
    if (t) { t[0] = 2.0f / g->w; t[1] = 2.0f / g->h; t[2] = (float)g->w; t[3] = (float)g->h; }
    passOpen_ = pass_ >= 0;
}

bool Renderer::Do_BeginFrame() {
    if (inFrame_) return false;
    frame_.Begin(frameNo_);
    inFrame_ = true;
    cur_ = nullptr; passOpen_ = false; pass_ = -1;
    return true;
}

bool Renderer::Do_EndFrame() {
    if (!inFrame_) return false;
    frame_.Finish(sc_, nullptr, true);
    lastBinds_ = frame_.stateBindsEncoded();
    frameNo_++;
    inFrame_ = false; passOpen_ = false;
    return true;
}

bool Renderer::Do_DisplayFrame() { return !inFrame_; }   // EndFrame submitted with present

void Renderer::Do_ClearBuffer(const NiRectF* rect, uint32_t mode) {
    if (!inFrame_) return;
    const Group* g = cur_ ? cur_ : &groups_[0];
    // rect: normalized like a camera port (left, right, top, bottom; top > bottom); 0 or the whole target = full clear
    int32_t x0 = 0, y0 = 0, x1 = (int32_t)g->w, y1 = (int32_t)g->h;
    if (rect) {
        x0 = (int32_t)(rect->left * g->w + 0.5f); x1 = (int32_t)(rect->right * g->w + 0.5f);
        y0 = (int32_t)((1.0f - rect->top) * g->h + 0.5f); y1 = (int32_t)((1.0f - rect->bottom) * g->h + 0.5f);
        if (x0 < 0) x0 = 0;
        if (y0 < 0) y0 = 0;
        if (x1 > (int32_t)g->w) x1 = (int32_t)g->w;
        if (y1 > (int32_t)g->h) y1 = (int32_t)g->h;
    }
    // D3D9 / Nine clear the colour through the sRGB view while SRGBWRITEENABLE (194) is set: always an NxOp_Clear then
    const bool srgb = (mode & CLEAR_BACKBUFFER) && state_.GetRenderState(194) != 0;
    if (!srgb && x0 == 0 && y0 == 0 && x1 == (int32_t)g->w && y1 == (int32_t)g->h) { OpenPass(mode); return; }   // a pass load
    if (x1 <= x0 || y1 <= y0) return;
    OpenPass(0);   // a new pass keeps the order against earlier draws; the clear sorts first in it (layer 0, key 0)
    if (!passOpen_) return;
    uint32_t flags = (mode & CLEAR_BACKBUFFER ? 1u : 0u) | (mode & CLEAR_ZBUFFER && g->depth ? 2u : 0u) |
                     (mode & CLEAR_STENCIL && g->depth ? 4u : 0u) | (srgb ? (uint32_t)NxClear_Srgb : 0u);
    uint32_t rgba = (uint32_t)u8_(bg_[0]) | (uint32_t)u8_(bg_[1]) << 8 | (uint32_t)u8_(bg_[2]) << 16 | (uint32_t)u8_(bg_[3]) << 24;
    frame_.Clear((uint8_t)pass_, 0, 0, flags, rgba, clearDepth_, clearStencil_, (uint32_t)x0, (uint32_t)y0,
                 (uint32_t)(x1 - x0), (uint32_t)(y1 - y0));
}

void Renderer::Do_SetCameraData(const NiPoint3f& loc, const NiPoint3f& dir, const NiPoint3f& up, const NiPoint3f& right,
                                const NiFrustumF& f, const NiRectF& port) {
    CameraView(loc, dir, up, right, view_);
    CameraProjection(f, proj_);
    const Group* g = cur_ ? cur_ : &groups_[0];
    vp_ = PortViewport(port, g->w, g->h);
}

void Renderer::Do_SetScreenSpaceCameraData(const NiRectF* port) {
    const Group* g = cur_ ? cur_ : &groups_[0];
    ident(view_);
    ScreenSpaceProjection(proj_, g->w, g->h);
    if (port) vp_ = PortViewport(*port, g->w, g->h);
    else vp_ = {0, 0, g->w, g->h, 0.0f, 1.0f};
}

bool Renderer::Do_BeginUsingRenderTargetGroup(const void* group, uint32_t clearMode) {
    if (!inFrame_) return false;
    const Group* g = FindGroup(group);
    if (!g) return false;
    cur_ = g;
    OpenPass(clearMode);
    vp_ = {0, 0, g->w, g->h, 0.0f, 1.0f};
    return passOpen_;
}

bool Renderer::Do_EndUsingRenderTargetGroup() {
    if (!passOpen_) return false;
    passOpen_ = false;
    cur_ = nullptr;
    return true;
}

bool Renderer::Render(const Draw& d) {
    if (!inFrame_) return false;
    if (!passOpen_) OpenPass(0);
    if (!passOpen_) return false;
    const Group* g = cur_ ? cur_ : &groups_[0];
    NxD3D9Rect sc = scissorSet_ ? scissor_ : NxD3D9Rect{0, 0, (int32_t)g->w, (int32_t)g->h};
    const StateTracker::Resolved& r = state_.Resolve(vp_, sc, g->depth ? 75 : 0);
    uint16_t st = sc_.State(d.vs, d.ps, d.layout, r.rs);
    Packet* p = frame_.Emit();
    if (!st || !p) return false;
    uint32_t h[StateTracker::kStages], n = d.textures ? state_.Handles(be_, h, d.textures) : 0;
    uint32_t tb = n ? frame_.AddTextures(h, n) : 0;
    p->key = SortKey((uint8_t)pass_, d.layer, st, (uint16_t)tb, d.depthKey);
    p->state = st; p->texCount = (uint16_t)n; p->texBlock = (uint16_t)tb;
    p->vtxBlock = d.vtxBlock; p->vtxOffset = d.vtxOffset; p->vtxSize = d.vtxSize;
    p->idxBlock = d.idxBlock; p->idxOffset = d.idxOffset; p->idxSize = d.idxSize; p->idxType = (uint8_t)d.idxType;
    p->first = d.first; p->count = d.count; p->baseVertex = d.baseVertex; p->prim = d.prim; p->instances = 1;
    void* u = frame_.Constants(&p->uboOffset);
    if (!u) return false;
    memset(u, 0, 512);
    if (d.ubo) memcpy(u, d.ubo, d.uboSize > 512 ? 512 : d.uboSize);
    NxEpilogue* e = frame_.Epilogue(p);
    if (e) nx_epilogue_fill(e, &r.dyn, r.shaderBits);
    DynRec dr;
    DynFromD3D9(&dr, r.dyn);
    p->dyn = frame_.AddDyn(dr);
    return true;
}

} // namespace nx
