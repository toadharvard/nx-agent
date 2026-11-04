// fnv-nx native renderer, N1: the native side of NiDX9Renderer and NiDX9RenderState (stream S6n, N1_PLAN.md §2-3).
//
// nx::Renderer implements the NiRenderer protected virtuals (Do_BeginFrame ... Do_EndUsingRenderTargetGroup) on the
// front end (nx/front.h). The engine-facing override stubs (overrides.tsv, once R1 lands) are one-line forwards from
// the engine's `this` to nx::Renderer::Get(); the engine object keeps its layout. nx::StateTracker is the record-only
// replacement of NiDX9RenderState: it records D3D9 render/sampler states (with Gamebryo's save/restore) and resolves
// them through the D3D9 translator into a state id, a dynamic record and the D3D9 epilogue values at draw time.
// ILP32 guest code (libnxrender.a, n1/build.sh); also builds LP64 for host tests.
#ifndef FNVNX_NX_RENDERER_H
#define FNVNX_NX_RENDERER_H

#include "nx/front.h"
#include "d3d9_state.h"

namespace nx {

// Gamebryo value types the virtuals take (layouts as Gamebryo 2.2 declares them).
struct NiPoint3f { float x, y, z; };
struct NiRectF { float left, right, top, bottom; };          // NiRect<float>; viewports: [0,1], top > bottom
struct NiFrustumF { float left, right, top, bottom, nearZ, farZ; bool ortho; };
static_assert(sizeof(NiPoint3f) == 12 && sizeof(NiRectF) == 16 && sizeof(NiFrustumF) == 28, "Gamebryo layouts");

// ---- record-only NiDX9RenderState / the D3D9 device state ------------------------------------------------------
// The state a D3D9 device holds, recorded with the device's semantics (docs/RENDER_NATIVE.md §2.5): render, sampler
// and texture stage states, textures, shader constants (float / int / bool, with the shaders' def'd constants
// applied by EffectiveConstantsF/I/B), vertex declaration / FVF, streams with their frequency, index buffer, shaders,
// render targets and depth-stencil (SetRenderTarget(0) resets viewport and scissor rectangle to the target), viewport,
// scissor rectangle, user clip planes (into the translator). Defaults: D3D9's (nx_d3d9_default_*). Objects (shaders,
// declarations, buffers, surfaces) are opaque pointers: the tracker records them, it never dereferences them.
// Samplers are D3D9 sampler numbers: 0..15, D3DDMAPSAMPLER (256), D3DVERTEXTEXTURESAMPLER0..3 (257..260).
class StateTracker {
public:
    enum { kStages = 16, kSamplers = NXD3D_SAMPLERS, kTssStages = 8, kStreams = 16, kRTs = 4,
           kVsConstF = 256, kPsConstF = 224, kConstI = 16, kConstB = 16 };
    // D3D9 defaults, nothing saved. autoDepthStencil: the device has an automatic depth-stencil surface (ZENABLE
    // TRUE); w x h: its back buffer (the initial viewport and scissor rectangle; 0 x 0 when unknown).
    void Reset(bool autoDepthStencil = false, uint32_t w = 0, uint32_t h = 0);
    void SetRenderState(uint32_t state, uint32_t value, bool save = false);
    uint32_t GetRenderState(uint32_t state) const { return state < NXD3D_RS_COUNT ? rs_[state] : 0; }
    void RestoreRenderState(uint32_t state);                       // value saved by the last SetRenderState(.., true)
    void SetSamplerState(uint32_t sampler, uint32_t type, uint32_t value, bool save = false);
    uint32_t GetSamplerState(uint32_t sampler, uint32_t type) const;
    void RestoreSamplerState(uint32_t sampler, uint32_t type);
    void SetTextureStageState(uint32_t stage, uint32_t type, uint32_t value);   // stages 0..7, types 1..32
    uint32_t GetTextureStageState(uint32_t stage, uint32_t type) const;
    // The texture bound to a sampler: image id (0 = none), its D3DFORMAT and kind, for the sampler translation.
    void SetTexture(uint32_t sampler, uint32_t image, const NxD3D9Texture& kind);
    uint32_t Texture(uint32_t sampler) const { int i = nx_d3d9_sampler_index(sampler); return i >= 0 ? image_[i] : 0; }
    NxD3D9Texture TextureKind(uint32_t sampler) const {
        int i = nx_d3d9_sampler_index(sampler);
        return i >= 0 ? kind_[i] : NxD3D9Texture{0, 0, 0, 0, 0};
    }
    void SetPixelShaderVersion(uint32_t v) { psVersion_ = v; dirty_ = true; }

    // ---- shader constants: false (D3DERR_INVALIDCALL, nothing changed) when start + count passes the register file
    bool SetShaderConstantF(bool ps, uint32_t start, const float* data, uint32_t count);
    bool GetShaderConstantF(bool ps, uint32_t start, float* data, uint32_t count) const;
    bool SetShaderConstantI(bool ps, uint32_t start, const int32_t* data, uint32_t count);
    bool GetShaderConstantI(bool ps, uint32_t start, int32_t* data, uint32_t count) const;
    bool SetShaderConstantB(bool ps, uint32_t start, const uint32_t* data, uint32_t count);   // BOOL: != 0 -> TRUE (1)
    bool GetShaderConstantB(bool ps, uint32_t start, uint32_t* data, uint32_t count) const;
    // What the shader reads: the set registers with its def / defi / defb constants in place (D3D9: a def'd register
    // ignores the application's value while that shader runs; Get*Constant still returns the application's).
    // defs: from nx_d3d9_shader_defs (0 / ndefs = 0: none). Registers 0..n-1 are written.
    void EffectiveConstantsF(bool ps, const NxD3D9Def* defs, uint32_t ndefs, float (*out)[4], uint32_t n) const;
    void EffectiveConstantsI(bool ps, const NxD3D9Def* defs, uint32_t ndefs, int32_t (*out)[4], uint32_t n) const;
    void EffectiveConstantsB(bool ps, const NxD3D9Def* defs, uint32_t ndefs, uint32_t* out, uint32_t n) const;
    uint32_t constantGeneration(bool ps) const { return constGen_[ps]; }   // changes with every constant change

    // ---- shaders, vertex input
    void SetShader(bool ps, const void* shader) { shader_[ps] = shader; }
    const void* Shader(bool ps) const { return shader_[ps]; }
    void SetVertexDeclaration(const void* decl) { decl_ = decl; fvf_ = 0; }   // an application declaration: FVF 0
    void SetFVF(uint32_t fvf) { if (fvf) { fvf_ = fvf; decl_ = nullptr; } }  // 0 is ignored (D3D_OK; Nine, Wine)
    const void* VertexDeclaration() const { return decl_; }       // 0 after SetFVF (the FVF's own declaration)
    uint32_t FVF() const { return fvf_; }
    bool SetStreamSource(uint32_t stream, const void* vb, uint32_t offset, uint32_t stride);
    bool SetStreamSourceFreq(uint32_t stream, uint32_t setting);   // D3D9's checks; default 1
    struct Stream { const void* vb; uint32_t offset, stride, freq; };
    Stream GetStream(uint32_t stream) const { return stream < kStreams ? stream_[stream] : Stream{nullptr, 0, 0, 0}; }
    void SetIndices(const void* ib) { indices_ = ib; }
    const void* Indices() const { return indices_; }

    // ---- targets, viewport, scissor rectangle, clip planes
    // index 0 needs a target and resets the viewport to (0, 0, w, h, 0, 1) and the scissor rectangle to (0, 0, w, h)
    bool SetRenderTarget(uint32_t index, const void* surface, uint32_t w, uint32_t h);
    const void* RenderTarget(uint32_t index) const { return index < kRTs ? rt_[index] : nullptr; }
    void SetDepthStencilSurface(const void* ds) { ds_ = ds; }
    const void* DepthStencilSurface() const { return ds_; }
    void SetViewport(const NxD3D9Viewport& vp) { viewport_ = vp; }
    const NxD3D9Viewport& Viewport() const { return viewport_; }
    void SetScissorRect(const NxD3D9Rect& r) { scissorRect_ = r; }
    const NxD3D9Rect& ScissorRect() const { return scissorRect_; }
    bool SetClipPlane(uint32_t index, const float plane[4]);      // D3DMAXUSERCLIPPLANES = 6; into Resolve's dyn
    bool GetClipPlane(uint32_t index, float plane[4]) const;

    // Translates what is recorded (only when something changed). viewport/scissor in D3D9 pixels.
    struct Resolved {
        NxRenderState rs;
        NxDynamicState dyn;
        uint32_t shaderBits;
    };
    const Resolved& Resolve(const NxD3D9Viewport& vp, const NxD3D9Rect& scissor, uint32_t depthFormat);
    // Texture handles of stages 0..n-1 (sampler objects created through be and cached by NxSamplerState).
    uint32_t Handles(Backend* be, uint32_t* out, uint32_t n);
    uint32_t resolves() const { return resolves_; }
    uint32_t samplerTranslations() const { return smpTranslations_; }   // nx_d3d9_sampler calls so far
    // changes whenever a stage's image, texture kind or sampler states change (Handles' result may differ)
    uint32_t textureGeneration() const { return texGen_; }
    bool lastHandlesComplete() const { return handlesComplete_; }       // every sampler of the last Handles exists
    const uint32_t* rs() const { return rs_; }
private:
    uint32_t rs_[NXD3D_RS_COUNT], rsSaved_[NXD3D_RS_COUNT];
    uint32_t samp_[kSamplers][NXD3D_SAMP_COUNT], sampSaved_[kSamplers][NXD3D_SAMP_COUNT];
    uint32_t tss_[kTssStages][NXD3D_TSS_COUNT];
    uint32_t image_[kSamplers];
    NxD3D9Texture kind_[kSamplers];
    float constF_[2][kVsConstF][4];
    int32_t constI_[2][kConstI][4];
    uint32_t constB_[2][kConstB];
    uint32_t constGen_[2] = {1, 1};
    const void* shader_[2] = {nullptr, nullptr};
    const void* decl_ = nullptr;
    uint32_t fvf_ = 0;
    Stream stream_[kStreams];
    const void* indices_ = nullptr;
    const void* rt_[kRTs] = {};
    const void* ds_ = nullptr;
    NxD3D9Viewport viewport_ = {0, 0, 0, 0, 0.0f, 1.0f};
    NxD3D9Rect scissorRect_ = {0, 0, 0, 0};
    float clip_[NXD3D_MAX_CLIP][4];
    uint32_t psVersion_ = 0x0200;
    bool dirty_ = true;
    NxD3D9Viewport lastVp_;
    NxD3D9Rect lastSc_;
    uint32_t lastDepth_ = 0, resolves_ = 0;
    Resolved out_;
    enum { kSamplerCache = 128 };
    NxSamplerState smpKey_[kSamplerCache];
    uint32_t smpId_[kSamplerCache], nSmp_ = 0;
    // per stage: the translated sampler of the current sampler states + texture kind (P6n F4: re-translated only
    // after a SetSamplerState that changes a value, a restore, or a texture of another kind)
    uint32_t stageSmp_[kSamplers] = {};    // sampler id, 0 = not translated since the last change
    bool stageSrgb_[kSamplers] = {};
    uint8_t srgbClass_[kSamplers] = {};    // of kind_[s].format: 1 sRGB-capable D3D format, 2 has an sRGB view
    uint32_t smpTranslations_ = 0;
    uint32_t texGen_ = 1;
    bool handlesComplete_ = false;
};

// ---- the renderer ----------------------------------------------------------------------------------------------
class Renderer {
public:
    enum { kMaxGroups = 32 };
    enum ClearMode { CLEAR_BACKBUFFER = 1, CLEAR_STENCIL = 2, CLEAR_ZBUFFER = 4, CLEAR_ALL = 7 };   // NiRenderer
    // One renderer per process (the engine has one NiDX9Renderer). be: the backend (GpuBackend() in the guest).
    bool Init(Backend* be, uint32_t width, uint32_t height, uint32_t backbufferImage, uint32_t depthImage);
    static Renderer* Get();

    // NiRenderer protected virtuals (slots: N1_PLAN.md §2)
    bool Do_BeginFrame();
    bool Do_EndFrame();
    bool Do_DisplayFrame();
    void Do_ClearBuffer(const NiRectF* rect, uint32_t mode);
    void Do_SetCameraData(const NiPoint3f& loc, const NiPoint3f& dir, const NiPoint3f& up, const NiPoint3f& right,
                          const NiFrustumF& frustum, const NiRectF& port);
    void Do_SetScreenSpaceCameraData(const NiRectF* port);
    bool Do_BeginUsingRenderTargetGroup(const void* group, uint32_t clearMode);
    bool Do_EndUsingRenderTargetGroup();

    // NiRenderer state the engine sets through non-virtual setters
    void SetBackgroundColor(float r, float g, float b, float a);
    void SetDepthClear(float z) { clearDepth_ = z; }
    void SetStencilClear(uint32_t s) { clearStencil_ = s; }
    // IDirect3DDevice9::SetScissorRect equivalent (used while D3DRS_SCISSORTESTENABLE is set); 0 = whole target
    void SetScissorRect(const NxD3D9Rect* r) { scissorSet_ = r != nullptr; if (r) scissor_ = *r; }
    // Native description of an engine NiRenderTargetGroup (0 = the default group: back buffer + depth).
    bool RegisterTargetGroup(const void* group, uint32_t color, uint32_t depth, uint32_t width, uint32_t height);
    // The colour image and size of a registered group (engine render targets: BSRenderedTexture / target groups).
    bool GroupColor(const void* group, uint32_t* image, uint32_t* w, uint32_t* h) const {
        const Group* g = FindGroup(group);
        if (!g) return false;
        *image = g->color; *w = g->w; *h = g->h;
        return true;
    }

    // Family entry: one draw with the recorded state. ubo: per-draw block (copied, <= the frame's slice size).
    struct Draw {
        uint16_t vs, ps, layout;
        uint8_t prim, layer;
        uint16_t depthKey;
        uint32_t vtxBlock, vtxOffset, vtxSize, first, count;
        uint32_t idxBlock, idxOffset, idxSize, idxType;
        int32_t baseVertex;
        uint32_t textures;          // texture stages 0..n-1 from the state tracker
        const void* ubo;
        uint32_t uboSize;
        uint16_t enginePass;        // engine pass id (docs/RENDER_NATIVE.md §1.3) with family != 0, else the current
        uint16_t family;            // NxFamilyId; 0: use SetEnginePass's
    };
    // The engine pass the following draws belong to (family NxFam_None: none). Tagged into the stream as NxOp_Marker.
    void SetEnginePass(uint16_t enginePass, uint16_t family) { curPass_ = enginePass; curFamily_ = family; }
    bool Render(const Draw& d);
    // A fragment shader of the package with its compile-time epilogue permutations (build.py: name__e0 none,
    // __ea alpha test, __ef fog). Render() then binds the cheapest permutation the draw's D3D9 state allows and
    // skips the epilogue UBO when it is not used. Returns the base (full runtime epilogue) shader id.
    uint16_t FragmentShader(const char* name);
    uint16_t lastPs() const { return lastPs_; }
    // Tests: off = always the dynamic shaders the caller gave (lit light loop, runtime epilogue).
    void SetPermutations(bool on) { permutations_ = on; }

    // N1 geometry path (Do_RenderShape / Do_RenderTristrips): an engine NiTriShape / NiTriStrips described
    // engine-independently (n1/engine_stubs.cpp reads the engine objects). Drawn with the tile family (tile_vc):
    // position x world x camera, vertex colour (white without), uv set 0, the stage-0 texture of the state tracker
    // (a white texel without), tint; strips become a triangle list. Geometry goes into a per-frame vertex ring.
    struct GeomView {
        float rot[9], trans[3], scale;   // NiTransform: world = rot * (scale * p) + trans (rot row-major)
        uint32_t vertices;
        const float* pos;                // 3 floats per vertex
        const float* color;              // 4 floats per vertex (NiColorA) or 0
        const float* uv;                 // 2 floats per vertex (set 0) or 0
        const uint16_t* indices;         // triangle list (indexCount = 3 x triangles), or
        uint32_t indexCount;
        const uint16_t* stripLengths;    // strips: stripCount lengths, then the concatenated strip lists
        const uint16_t* strips;
        uint32_t stripCount;
        float tint[4];
        uint32_t image = 0, d3dfmt = 0;  // texture (0: the state tracker's stage 0, else a white texel)
        uint32_t alphaMap = 0, alphaMapFmt = 0;   // TileShaderProperty's second texture: TILE1001 ALPHAMAP
        bool hasAlpha = false;           // NiAlphaProperty present: blend / alpha test from alphaFlags / alphaRef
        uint16_t alphaFlags = 0;
        uint8_t alphaRef = 0;
        bool hasScroll = false;          // TileShaderProperty TexScroll: uv += scroll
        float scroll[4] = {0, 0, 1, 1};
        bool hasScissor = false;         // the tile's scissor rectangle (target pixels)
        NxD3D9Rect scissor = {0, 0, 0, 0};
        bool hasStencil = false;         // NiStencilProperty: flags (enable, fail/zfail/pass actions, func), ref, mask
        uint16_t stencilFlags = 0;
        uint32_t stencilRef = 0, stencilMask = 0xFFFFFFFFu;
    };
    bool RenderGeometry(const GeomView& g);
    // Menu background image-space effect (menubg family): the scene image toned through a lookup texture, drawn over
    // the current pass with the tracked blend state. scene: w x h image; tone: lookup image (D3DFORMATs for sRGB).
    bool RenderMenuBackground(uint32_t scene, uint32_t sceneFmt, uint32_t w, uint32_t h, uint32_t tone, uint32_t toneFmt,
                              const float luma[3], float blend, float alpha);
    uint32_t geometryDraws() const { return geomDraws_; }

    StateTracker& state() { return state_; }
    StateCache& cache() { return sc_; }
    Frame& frame() { return frame_; }
    Backend* backend() { return be_; }
    // Matrices of the current camera (column-major, clip = proj * view * world; D3D9 clip space).
    const float* view() const { return view_; }
    const float* proj() const { return proj_; }
    const NxD3D9Viewport& viewport() const { return vp_; }
    uint32_t frameNumber() const { return frameNo_; }
    uint32_t lastStateBinds() const { return lastBinds_; }
    bool inFrame() const { return inFrame_; }

private:
    struct Group { const void* key; uint32_t color, depth, w, h; };
    const Group* FindGroup(const void* g) const;
    void OpenPass(uint32_t clearMode);
    Backend* be_ = nullptr;
    uint16_t curPass_ = 0, curFamily_ = 0, lastPs_ = 0;
    struct EpiSet { uint16_t base, e0, ea, ef; };
    EpiSet epi_[64];
    uint32_t nEpi_ = 0;
    uint16_t noEpi_[64];
    uint32_t nNoEpi_ = 0;
    uint16_t litBase_[2] = {0, 0};                   // shader ids of the package's lit / lit_spec (learned)
    uint16_t litVar_[2][2][5] = {};                  // [spec][dir][points 0,1,2,4,8] -> permutation id (0xFFFF none)
    bool permutations_ = true;
    uint16_t LitPermutation(int spec, int dir, int pointsIdx);
    bool EpiKnown(uint16_t ps) const;
    void LearnEpilogueVariants(uint16_t ps);
    int EpiIndex(uint16_t ps);                       // epi_ index of a fragment shader's permutations, -1: none
    void NoteEpi(uint16_t ps, int index);
    // fragment shader id -> 0 not looked up yet, 1 no permutations, 2 + epi_ index (ids >= kEpiIds: the lists above)
    enum { kEpiIds = 4096 };
    uint8_t epiOf_[kEpiIds] = {};
    Ring geomRing_;
    uint32_t white_ = 0, geomDraws_ = 0;
    uint16_t geomVsScroll_ = 0, geomVsPlain_ = 0, geomVsPlainScroll_ = 0, geomPsPlain_ = 0, geomPsAlphaMap_ = 0;
    uint16_t geomVs_ = 0, geomPs_ = 0, geomLayout_ = 0, bgVs_ = 0, bgPs_ = 0, bgLayout_ = 0;
    StateCache sc_;
    Frame frame_;
    StateTracker state_;
    Group groups_[kMaxGroups];
    uint32_t nGroups_ = 0;
    const Group* cur_ = nullptr;
    int pass_ = -1;
    bool inFrame_ = false, passOpen_ = false;
    uint32_t pendingClear_ = 0;
    uint32_t frameNo_ = 0, lastBinds_ = 0;
    // reuse of the previous draw's state id and texture block while nothing they depend on changed (P6n: Render)
    uint32_t lastResolve_ = ~0u, lastStFrameKey_ = 0;
    uint16_t lastVs_ = 0, lastPsSt_ = 0, lastLayout_ = 0, lastSt_ = 0;
    uint32_t lastTexGen_ = 0, lastTexN_ = 0, lastTexFrame_ = ~0u, lastTb_ = 0, lastTexCount_ = 0;
    uint32_t lastEpiResolve_ = ~0u, lastEpiFrame_ = ~0u, lastEpiBlock_ = 0;
    uint16_t lastDynId_ = 0;
    float bg_[4] = {0, 0, 0, 1};
    float clearDepth_ = 1.0f;
    uint32_t clearStencil_ = 0;
    float view_[16], proj_[16];
    NxD3D9Viewport vp_;
    NxD3D9Rect scissor_ = {0, 0, 0, 0};
    bool scissorSet_ = false;
};

// D3D9 matrices (column-major for GLSL) as NiDX9Renderer builds them; exposed for tests.
void CameraView(const NiPoint3f& loc, const NiPoint3f& dir, const NiPoint3f& up, const NiPoint3f& right, float out[16]);
void CameraProjection(const NiFrustumF& f, float out[16]);
// Gamebryo screen space (x, y in [0,1], y down) of a w x h target, with the engine's D3D9 half-pixel correction and
// z/9999 (0x00e6cf40). The defaults are the game's 1280x720 back buffer; nx::Renderer always passes its target size.
void ScreenSpaceProjection(float out[16], uint32_t w = 1280, uint32_t h = 720);

} // namespace nx
#endif
