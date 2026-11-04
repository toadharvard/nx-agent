// fnv-nx native renderer, N1: nx::Renderer and nx::StateTracker. See renderer.h.
#include "renderer.h"
#include "nx/families.h"
#include "nx_shaders.h"
#include <string.h>
#include <stdio.h>
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
    if (!frame_.Init(be, 0, 512)) return false;   // ring sized for Frame::kMaxPackets draws per frame in flight
    if (!geomRing_.Init(be, 8u << 20)) return false;
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
    geomRing_.BeginFrame(frameNo_);
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
    // compile-time epilogue permutation for this draw's alpha test / fog
    uint16_t ps = d.ps;
    bool needEpi = true;
    if (d.ps && !EpiKnown(d.ps)) LearnEpilogueVariants(d.ps);
    // lit family: static light permutation (directional first, points padded to 1/2/4/8 with zero-colour lights)
    int litDir = -1, litPoints = 0, litPad = 0;
    if (permutations_ && d.ps && d.ubo && d.uboSize >= sizeof(NxLitDraw) && (d.ps == litBase_[0] || d.ps == litBase_[1])) {
        const NxLitDraw* L = (const NxLitDraw*)d.ubo;
        const uint32_t n = L->lightCount < 8 ? L->lightCount : 8;
        int dirs = 0;
        for (uint32_t i = 0; i < n; i++) dirs += L->lights[i].color[3] > 0.5f;
        if (dirs <= 1) {
            static const int kPad[5] = {0, 1, 2, 4, 8};
            const int pts = (int)n - dirs;
            int pi = 0;
            while (pi < 4 && kPad[pi] < pts) pi++;
            uint16_t v = LitPermutation(d.ps == litBase_[1], dirs, pi);
            if (v) { ps = v; litDir = dirs; litPoints = pts; litPad = kPad[pi] - (dirs && pi == 4 ? 1 : 0); }
        }
    }
    const uint16_t epiBase = ps;
    if (ps != d.ps && !EpiKnown(ps)) LearnEpilogueVariants(ps);
    for (uint32_t i = 0; i < nEpi_; i++)
        if (epi_[i].base == epiBase) {
            const bool at = (r.shaderBits >> NXSB_ALPHATEST) & 1, fog = ((r.shaderBits >> NXSB_FOG) & 7) != 0;
            const uint16_t v = !at && !fog ? epi_[i].e0 : at && !fog ? epi_[i].ea : !at && fog ? epi_[i].ef : 0;
            if (v) { ps = v; needEpi = at || fog; }
            break;
        }
    lastPs_ = ps;
    uint16_t st = sc_.State(d.vs, ps, d.layout, r.rs);
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
    if (litDir >= 0) {   // the permutation's light order: [directional], points..., zero-colour padding
        NxLitDraw* lu = (NxLitDraw*)u;
        const NxLitDraw* L = (const NxLitDraw*)d.ubo;
        const uint32_t n = L->lightCount < 8 ? L->lightCount : 8;
        uint32_t o = 0;
        for (uint32_t i = 0; i < n; i++) if (L->lights[i].color[3] > 0.5f) lu->lights[o++] = L->lights[i];
        for (uint32_t i = 0; i < n; i++) if (!(L->lights[i].color[3] > 0.5f)) lu->lights[o++] = L->lights[i];
        for (; o < (uint32_t)(litDir + litPad) && o < 8; o++) { memset(&lu->lights[o], 0, sizeof lu->lights[o]); lu->lights[o].posRange[3] = 1.0f; }
        (void)litPoints;
    }
    if (needEpi) {
        NxEpilogue* e = frame_.Epilogue(p);
        if (e) nx_epilogue_fill(e, &r.dyn, r.shaderBits);
    }
    DynRec dr;
    DynFromD3D9(&dr, r.dyn);
    p->dyn = frame_.AddDyn(dr);
    if (d.family) frame_.Tag(p, d.enginePass, d.family);
    else if (curFamily_) frame_.Tag(p, curPass_, curFamily_);
    return true;
}

// ---- N1 geometry path ------------------------------------------------------------------------------------------
static uint16_t BlobShader(Backend* be, const char* name, uint32_t stage) {
    int i = nx_shader_find(name, stage);
    if (i < 0) return 0;
    NxShaderDesc d;
    memset(&d, 0, sizeof d);
    d.stage = stage; d.dksh = FNVNX_ADDR_(nx_shader_blobs[i].dksh); d.dksh_size = nx_shader_blobs[i].dksh_size;
    d.glsl = FNVNX_ADDR_(nx_shader_blobs[i].glsl); d.glsl_size = nx_shader_blobs[i].glsl_size;
    return (uint16_t)be->ShaderCreate(d);
}

bool Renderer::EpiKnown(uint16_t ps) const {
    for (uint32_t i = 0; i < nEpi_; i++) if (epi_[i].base == ps) return true;
    for (uint32_t i = 0; i < nNoEpi_; i++) if (noEpi_[i] == ps) return true;
    return false;
}

// a fragment shader created from the package (by anyone): find its blob by GLSL address, then its permutations
void Renderer::LearnEpilogueVariants(uint16_t ps) {
    const uint64_t glsl = be_->ShaderGlsl(ps);
    int blob = -1;
    for (uint32_t i = 0; glsl && i < nx_shader_blob_count; i++)
        if (nx_shader_blobs[i].stage == NxStage_Fragment && FNVNX_ADDR_(nx_shader_blobs[i].glsl) == glsl) { blob = (int)i; break; }
    char n[96];
    EpiSet e = {ps, 0, 0, 0};
    if (blob >= 0 && nEpi_ < sizeof epi_ / sizeof epi_[0]) {
        snprintf(n, sizeof n, "%s__e0", nx_shader_blobs[blob].name); e.e0 = BlobShader(be_, n, NxStage_Fragment);
        snprintf(n, sizeof n, "%s__ea", nx_shader_blobs[blob].name); e.ea = BlobShader(be_, n, NxStage_Fragment);
        snprintf(n, sizeof n, "%s__ef", nx_shader_blobs[blob].name); e.ef = BlobShader(be_, n, NxStage_Fragment);
    }
    if (e.e0 || e.ea || e.ef) epi_[nEpi_++] = e;
    else if (nNoEpi_ < sizeof noEpi_ / sizeof noEpi_[0]) noEpi_[nNoEpi_++] = ps;
    if (blob >= 0 && !strcmp(nx_shader_blobs[blob].name, "lit")) litBase_[0] = ps;
    if (blob >= 0 && !strcmp(nx_shader_blobs[blob].name, "lit_spec")) litBase_[1] = ps;
}

uint16_t Renderer::LitPermutation(int spec, int dir, int pi) {
    uint16_t& v = litVar_[spec][dir][pi];
    if (!v) {
        static const int kPad[5] = {0, 1, 2, 4, 8};
        int p = dir && pi == 4 ? 7 : kPad[pi];
        char n[64];
        if (!dir && !p) { v = 0xFFFF; return 0; }   // no lights: the dynamic shader (loop count 0) is as cheap
        snprintf(n, sizeof n, "%s_d%dp%d", spec ? "lit_spec" : "lit", dir, pi == 4 ? 8 : p);
        uint16_t id = BlobShader(be_, n, NxStage_Fragment);
        v = id ? id : 0xFFFF;
    }
    return v == 0xFFFF ? 0 : v;
}

uint16_t Renderer::FragmentShader(const char* name) {
    uint16_t base = BlobShader(be_, name, NxStage_Fragment);
    if (!base || nEpi_ >= sizeof epi_ / sizeof epi_[0]) return base;
    char n[96];
    EpiSet e = {base, 0, 0, 0};
    snprintf(n, sizeof n, "%s__e0", name); e.e0 = BlobShader(be_, n, NxStage_Fragment);
    snprintf(n, sizeof n, "%s__ea", name); e.ea = BlobShader(be_, n, NxStage_Fragment);
    snprintf(n, sizeof n, "%s__ef", name); e.ef = BlobShader(be_, n, NxStage_Fragment);
    if (e.e0 || e.ea || e.ef) epi_[nEpi_++] = e;
    return base;
}

namespace {
struct GeomVtx { float x, y, z, u, v; uint32_t rgba; };
inline uint32_t Rgba8(const float* c) {
    uint32_t o = 0;
    for (int i = 0; i < 4; i++) {
        float v = c[i] < 0 ? 0 : c[i] > 1 ? 1 : c[i];
        o |= (uint32_t)(v * 255.0f + 0.5f) << (8 * i);
    }
    return o;
}
inline void Mul(const float* a, const float* b, float* o) {   // column-major o = a * b
    for (int c = 0; c < 4; c++)
        for (int r = 0; r < 4; r++) {
            float s = 0;
            for (int k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
            o[c * 4 + r] = s;
        }
}
}  // namespace

bool Renderer::RenderGeometry(const GeomView& g) {
    if (!inFrame_ || !g.vertices || !g.pos || g.vertices > 65535) return false;
    if (!geomVs_) {
        geomVs_ = BlobShader(be_, "tile_vc", NxStage_Vertex);
        geomVsScroll_ = BlobShader(be_, "tile_vc_scroll", NxStage_Vertex);
        geomPs_ = FragmentShader("tile_vc");
        geomVsPlain_ = BlobShader(be_, "tile", NxStage_Vertex);
        geomVsPlainScroll_ = BlobShader(be_, "tile_scroll", NxStage_Vertex);
        geomPsPlain_ = FragmentShader("tile");
        geomPsAlphaMap_ = FragmentShader("tile_alphamap");
        NxVtxLayout ll;
        memset(&ll, 0, sizeof ll);
        ll.count = 3; ll.stride = sizeof(GeomVtx);
        ll.attr[0].offset = 0; ll.attr[0].type = NxAttr_Float32; ll.attr[0].components = 3;
        ll.attr[1].offset = 12; ll.attr[1].type = NxAttr_Float32; ll.attr[1].components = 2;
        ll.attr[2].offset = 20; ll.attr[2].type = NxAttr_Unorm8; ll.attr[2].components = 4;
        geomLayout_ = sc_.Layout(ll);
        static const uint32_t kWhite = 0xFFFFFFFFu;
        NxImageDesc td;
        memset(&td, 0, sizeof td);
        td.width = 1; td.height = 1; td.format = NxFmt_BGRA8; td.data = FNVNX_ADDR_(&kWhite); td.data_size = 4;
        white_ = be_->ImageCreate(td);
        if (!geomVs_ || !geomPs_ || !geomLayout_ || !white_) return false;
    }
    // triangle list (strips: alternate winding, degenerate triangles dropped)
    uint32_t nIdx = g.indices ? g.indexCount - g.indexCount % 3 : 0;
    if (!g.indices && g.strips && g.stripLengths)
        for (uint32_t s = 0; s < g.stripCount; s++) nIdx += g.stripLengths[s] > 2 ? 3u * (g.stripLengths[s] - 2u) : 0u;
    if (!nIdx) return false;
    uint32_t vOff, iOff;
    GeomVtx* v = (GeomVtx*)geomRing_.Alloc(g.vertices * sizeof(GeomVtx), 16, &vOff);
    uint16_t* ix = (uint16_t*)geomRing_.Alloc(nIdx * 2, 16, &iOff);
    if (!v || !ix) return false;
    static const float kOne[4] = {1, 1, 1, 1};
    for (uint32_t i = 0; i < g.vertices; i++) {
        v[i].x = g.pos[3 * i]; v[i].y = g.pos[3 * i + 1]; v[i].z = g.pos[3 * i + 2];
        v[i].u = g.uv ? g.uv[2 * i] : 0; v[i].v = g.uv ? g.uv[2 * i + 1] : 0;
        v[i].rgba = Rgba8(g.color ? g.color + 4 * i : kOne);
    }
    uint32_t n = 0;
    if (g.indices) {
        for (uint32_t i = 0; i < nIdx; i++) ix[n++] = g.indices[i] < g.vertices ? g.indices[i] : 0;
    } else {
        const uint16_t* s = g.strips;
        for (uint32_t k = 0; k < g.stripCount; s += g.stripLengths[k], k++)
            for (uint32_t t = 0; t + 2 < g.stripLengths[k]; t++) {
                uint16_t a = s[t], b = s[t + 1], c = s[t + 2];
                if (a == b || b == c || a == c || a >= g.vertices || b >= g.vertices || c >= g.vertices) continue;
                if (t & 1) { uint16_t x = a; a = b; b = x; }
                ix[n++] = a; ix[n++] = b; ix[n++] = c;
            }
        if (!n) return false;
    }
    float world[16];
    memset(world, 0, sizeof world);
    for (int r = 0; r < 3; r++) {
        for (int c = 0; c < 3; c++) world[c * 4 + r] = g.rot[r * 3 + c] * g.scale;
        world[12 + r] = g.trans[r];
    }
    world[15] = 1;
    NxTileDraw u;
    memset(&u, 0, sizeof u);
    float vp[16];
    Mul(proj_, view_, vp);
    Mul(vp, world, u.wvp);
    memcpy(u.tint, g.tint, 16);
    if (g.hasScroll || g.alphaMap) memcpy(u.texScroll, g.scroll, 16);
    else { u.texScroll[2] = u.texScroll[3] = 1.0f; }
    // texture: the geometry's own (tile property), else the tracker's stage 0, else a white texel
    static const NxD3D9Texture kWhiteKind = {21, 0, 0, 0, 0};   // D3DFMT_A8R8G8B8 2D
    const uint32_t prevTex = state_.Texture(0);
    const NxD3D9Texture prevKind = state_.TextureKind(0);
    const bool swapTex = g.image || prevTex == 0;
    if (g.image) { NxD3D9Texture k = {g.d3dfmt, 0, 0, 0, 0}; state_.SetTexture(0, g.image, k); }
    else if (!prevTex) state_.SetTexture(0, white_, kWhiteKind);
    const uint32_t prevTex1 = state_.Texture(1);
    const NxD3D9Texture prevKind1 = state_.TextureKind(1);
    if (g.alphaMap) { NxD3D9Texture k = {g.alphaMapFmt, 0, 0, 0, 0}; state_.SetTexture(1, g.alphaMap, k); }
    // NiAlphaProperty -> D3D9 blend and alpha-test states for this draw (the engine pipeline that would set them is
    // replaced); NiAlphaProperty::AlphaFunction -> D3DBLEND, TestFunction -> D3DCMPFUNC
    static const uint8_t kBlend[16] = {2, 1, 3, 4, 9, 10, 5, 6, 7, 8, 11, 2, 2, 2, 2, 2};
    static const uint8_t kCmp[8] = {8, 2, 3, 4, 5, 6, 7, 1};
    enum { RS_ALPHATEST = 15, RS_SRC = 19, RS_DST = 20, RS_REF = 24, RS_FUNC = 25, RS_BLEND = 27 };
    // + scissor test and the stencil states (NiStencilProperty), all restored after the draw
    static const uint8_t kSaved[15] = {RS_ALPHATEST, RS_SRC, RS_DST, RS_REF, RS_FUNC, RS_BLEND, 174,
                                       52, 53, 54, 55, 56, 57, 58, 59};
    uint32_t saved[15];
    for (int i = 0; i < 15; i++) saved[i] = state_.GetRenderState(kSaved[i]);
    const bool hadScissor = scissorSet_;
    const NxD3D9Rect prevScissor = scissor_;
    if (g.hasScissor) { state_.SetRenderState(174, 1); SetScissorRect(&g.scissor); }
    if (g.hasStencil && (g.stencilFlags & 1)) {   // NiStencilProperty::Action / TestFunction -> D3DSTENCILOP / D3DCMPFUNC
        static const uint8_t kOp[8] = {1, 2, 3, 4, 5, 6, 1, 1};
        const uint32_t f = g.stencilFlags;
        state_.SetRenderState(52, 1);
        state_.SetRenderState(53, kOp[(f >> 1) & 7]); state_.SetRenderState(54, kOp[(f >> 4) & 7]);
        state_.SetRenderState(55, kOp[(f >> 7) & 7]); state_.SetRenderState(56, ((f >> 12) & 7) + 1);
        state_.SetRenderState(57, g.stencilRef & 0xFF); state_.SetRenderState(58, g.stencilMask);
    }
    if (g.hasAlpha) {
        const uint32_t f = g.alphaFlags;
        state_.SetRenderState(RS_BLEND, f & 1);
        state_.SetRenderState(RS_SRC, kBlend[(f >> 1) & 15]);
        state_.SetRenderState(RS_DST, kBlend[(f >> 5) & 15]);
        state_.SetRenderState(RS_ALPHATEST, (f >> 9) & 1);
        state_.SetRenderState(RS_FUNC, kCmp[(f >> 10) & 7]);
        state_.SetRenderState(RS_REF, g.alphaRef);
    }
    Draw d;
    memset(&d, 0, sizeof d);
    // the engine's TileShader choice: TILE1002 (VC) for geometry with vertex colours, TILE1001 (scroll VS, ALPHAMAP PS)
    // with an alpha map, else TILE1000
    if (g.alphaMap) { d.vs = geomVsPlainScroll_; d.ps = geomPsAlphaMap_; }
    else if (g.color) { d.vs = g.hasScroll ? geomVsScroll_ : geomVs_; d.ps = geomPs_; }
    else { d.vs = g.hasScroll ? geomVsPlainScroll_ : geomVsPlain_; d.ps = geomPsPlain_; }
    d.layout = geomLayout_; d.prim = NxPrim_Triangles;
    d.vtxBlock = geomRing_.block(); d.vtxOffset = vOff; d.vtxSize = g.vertices * sizeof(GeomVtx);
    d.idxBlock = geomRing_.block(); d.idxOffset = iOff; d.idxSize = n * 2; d.idxType = 0;
    d.count = n; d.textures = g.alphaMap ? 2 : 1; d.ubo = &u; d.uboSize = sizeof u;
    bool ok = Render(d);
    if (swapTex) state_.SetTexture(0, prevTex, prevKind);
    if (g.alphaMap) state_.SetTexture(1, prevTex1, prevKind1);
    for (int i = 0; i < 15; i++) if (state_.GetRenderState(kSaved[i]) != saved[i]) state_.SetRenderState(kSaved[i], saved[i]);
    if (g.hasScissor) SetScissorRect(hadScissor ? &prevScissor : nullptr);
    if (ok) geomDraws_++;
    return ok;
}

bool Renderer::RenderMenuBackground(uint32_t scene, uint32_t sceneFmt, uint32_t w, uint32_t h, uint32_t tone,
                                    uint32_t toneFmt, const float luma[3], float blend, float alpha) {
    if (!inFrame_ || !scene || !tone || !w || !h) return false;
    if (!bgVs_) {
        bgVs_ = BlobShader(be_, "menubg", NxStage_Vertex);
        bgPs_ = BlobShader(be_, "menubg", NxStage_Fragment);
        NxVtxLayout ll;
        memset(&ll, 0, sizeof ll);
        bgLayout_ = sc_.Layout(ll);
        if (!bgVs_ || !bgPs_ || !bgLayout_) return false;
    }
    NxMenuBgDraw u;
    memset(&u, 0, sizeof u);
    u.luma[0] = luma[0]; u.luma[1] = luma[1]; u.luma[2] = luma[2];
    u.params[0] = blend; u.params[1] = alpha;
    u.uvXform[0] = 1; u.uvXform[1] = 1; u.uvXform[2] = 0.5f / (float)w; u.uvXform[3] = 0.5f / (float)h;
    // stages 0 (scene) and 1 (tone): bilinear, clamped (ImageSpaceEffect FILTER_BILERP)
    const uint32_t t0 = state_.Texture(0), t1 = state_.Texture(1);
    const NxD3D9Texture k0 = state_.TextureKind(0), k1 = state_.TextureKind(1);
    uint32_t s[2][4];
    static const uint8_t kTypes[4] = {1, 2, 5, 6};   // ADDRESSU, ADDRESSV, MAGFILTER, MINFILTER
    for (int st = 0; st < 2; st++)
        for (int i = 0; i < 4; i++) { s[st][i] = state_.GetSamplerState(st, kTypes[i]); state_.SetSamplerState(st, kTypes[i], i < 2 ? 3 : 2); }
    NxD3D9Texture ks = {sceneFmt, 0, 0, 0, 0}, kt = {toneFmt, 0, 0, 0, 0};
    state_.SetTexture(0, scene, ks); state_.SetTexture(1, tone, kt);
    Draw d;
    memset(&d, 0, sizeof d);
    d.vs = bgVs_; d.ps = bgPs_; d.layout = bgLayout_; d.prim = NxPrim_Triangles; d.count = 3; d.textures = 2;
    d.ubo = &u; d.uboSize = sizeof u; d.family = NxFam_ImageSpace; d.enginePass = 758;
    bool ok = Render(d);
    state_.SetTexture(0, t0, k0); state_.SetTexture(1, t1, k1);
    for (int st = 0; st < 2; st++) for (int i = 0; i < 4; i++) state_.SetSamplerState(st, kTypes[i], s[st][i]);
    return ok;
}

} // namespace nx

