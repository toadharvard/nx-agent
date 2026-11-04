// fnv-nx native renderer, front end implementation (ILP32 guest; LP64 for host programs). See nx/front.h.
#include "nx/front.h"
#include "d3d9_state.h"
#include <stdlib.h>
#include <string.h>
#include <new>
#if defined(__ILP32__)
#include "fnvnx/hostapi.h"
#endif

namespace nx {

// ---- backend over NxGpuApi / ext_call ----------------------------------------------------------------------
namespace {
struct ApiBe final : Backend {
    const NxGpuApi* api;
    NxGpuInfo info;
    explicit ApiBe(const NxGpuApi* a) : api(a) { memset(&info, 0, sizeof info); }
    uint64_t Call(uint32_t fn, uint64_t a0) {
        uint64_t args[1] = {a0};
#if defined(__ILP32__)
        return fnvnx_hcall4(fnvnx_host->ext_call, NXGPU_EXT_MODULE, fn, FNVNX_ADDR(args), 1);
#else
        return nxgpu_dispatch(api, fn, args, 1);
#endif
    }
    uint32_t MemCreate(const NxMemBlockDesc& d) override { return (uint32_t)Call(NXGPU_FN_MEM_CREATE, FNVNX_ADDR_(&d)); }
    uint64_t MemGpuAddr(uint32_t id) override { return Call(NXGPU_FN_MEM_GPU_ADDR, id); }
    uint32_t ImageCreate(const NxImageDesc& d) override { return (uint32_t)Call(NXGPU_FN_IMAGE_CREATE, FNVNX_ADDR_(&d)); }
    uint32_t SamplerCreate(const NxSamplerState& s) override {
        return (uint32_t)Call(NXGPU_FN_SAMPLER_CREATE, FNVNX_ADDR_(&s));
    }
    uint32_t ShaderCreate(const NxShaderDesc& d) override { return (uint32_t)Call(NXGPU_FN_SHADER_CREATE, FNVNX_ADDR_(&d)); }
    void Submit(const NxSubmit& s) override { Call(NXGPU_FN_SUBMIT, FNVNX_ADDR_(&s)); }
    void WaitFrame(uint32_t f) override { Call(NXGPU_FN_WAIT_FRAME, f); }
    bool Readback(const NxReadback& r) override { return Call(NXGPU_FN_READBACK, FNVNX_ADDR_(&r)) == 0; }
    void Shutdown() override {
#if defined(__ILP32__)
        fnvnx_hcall4(fnvnx_host->ext_call, NXGPU_EXT_MODULE, NXGPU_FN_SHUTDOWN, 0, 0);
#else
        nxgpu_dispatch(api, NXGPU_FN_SHUTDOWN, nullptr, 0);
#endif
    }
    const NxGpuInfo& Info() const override { return info; }
};
alignas(16) unsigned char g_be[sizeof(ApiBe)];
}

Backend* GpuBackend(const NxGpuApi* api) {
    ApiBe* be = new (g_be) ApiBe(api);
    if (be->Call(NXGPU_FN_INFO, FNVNX_ADDR_(&be->info)) != 0 || be->info.magic != NXGPU_MAGIC ||
        be->info.version != NXGPU_VERSION || !be->info.frames_in_flight)
        return nullptr;
    return be;
}

static void* aligned_alloc_(uint32_t align, uint32_t size) {
    void* p = nullptr;
    if (posix_memalign(&p, align, size)) return nullptr;
    return p;
}

// ---- ring / static buffer ------------------------------------------------------------------------------------
bool Ring::Init(Backend* be, uint32_t bytes, uint32_t kind) {
    be_ = be;
    frames_ = be->Info().frames_in_flight;
    region_ = (bytes / frames_) & ~0xFFFu;
    uint32_t total = region_ * frames_;
    cpu_ = (uint8_t*)aligned_alloc_(0x1000, total);
    if (!cpu_) return false;
    NxMemBlockDesc d = {FNVNX_ADDR_(cpu_), total, kind};
    block_ = be->MemCreate(d);
    return block_ != 0;
}

void Ring::BeginFrame(uint32_t frame) {
    if (started_ && frame >= frames_) be_->WaitFrame(frame - frames_);  // region of frame - N must be retired
    regionStart_ = (frame % frames_) * region_;
    head_ = regionStart_;
    started_ = true;
}

void* Ring::Alloc(uint32_t size, uint32_t align, uint32_t* offset) {
    uint32_t at = (head_ + align - 1) & ~(align - 1);
    if (at + size > regionStart_ + region_) return nullptr;
    head_ = at + size;
    *offset = at;
    return cpu_ + at;
}

bool StaticBuffer::Init(Backend* be, uint32_t bytes) {
    size_ = (bytes + 0xFFF) & ~0xFFFu;
    cpu_ = (uint8_t*)aligned_alloc_(0x1000, size_);
    if (!cpu_) return false;
    memset(cpu_, 0, size_);
    NxMemBlockDesc d = {FNVNX_ADDR_(cpu_), size_, NxMem_Generic};
    block_ = be->MemCreate(d);
    return block_ != 0;
}

void* StaticBuffer::Alloc(uint32_t size, uint32_t align, uint32_t* offset) {
    uint32_t at = (head_ + align - 1) & ~(align - 1);
    if (at + size > size_) return nullptr;
    head_ = at + size;
    *offset = at;
    return cpu_ + at;
}

// ---- state --------------------------------------------------------------------------------------------------
uint16_t StateCache::Layout(const NxVtxLayout& l) {
    for (uint32_t i = 1; i <= nLayouts_; i++)
        if (!memcmp(&layouts_[i], &l, sizeof l)) return (uint16_t)i;
    if (nLayouts_ >= kMaxLayouts) return 0;
    layouts_[++nLayouts_] = l;
    return (uint16_t)nLayouts_;
}

static uint32_t hash_state(uint16_t vs, uint16_t ps, uint16_t lay, const NxRenderState& r) {
    uint64_t h = ((uint64_t)vs << 32 | (uint64_t)ps << 16 | lay) * 0xC2B2AE3D27D4EB4Full;
    for (int i = 0; i < 4; i++) h = (h ^ r.w[i]) * 0x9E3779B97F4A7C15ull;
    return (uint32_t)(h >> 52);  // 12 bits
}

uint16_t StateCache::State(uint16_t vs, uint16_t ps, uint16_t layout, const NxRenderState& rs) {
    uint32_t h = hash_state(vs, ps, layout, rs);
    for (uint32_t probe = 0; probe < 4096; probe++, h = (h + 1) & 4095) {
        uint16_t slot = hash_[h];
        if (!slot) break;
        const Entry& e = states_[slot - 1];
        if (!memcmp(&e.rs, &rs, sizeof rs) && e.vs == vs && e.ps == ps && e.layout == layout) return e.id;
    }
    if (nStates_ >= kMaxStates) return 0;
    Entry& e = states_[nStates_];
    e.rs = rs; e.vs = vs; e.ps = ps; e.layout = layout; e.id = (uint16_t)(nStates_ + 1);
    hash_[h] = (uint16_t)(++nStates_);
    return e.id;
}

void StateCache::FlushDefines(Encoder& enc) {
    for (; flushedLayouts_ < nLayouts_; flushedLayouts_++) {
        uint32_t id = flushedLayouts_ + 1;
        uint32_t* a = enc.Op(NxOp_DefineLayout, 1 + sizeof(NxVtxLayout) / 4);
        a[0] = id;
        memcpy(a + 1, &layouts_[id], sizeof(NxVtxLayout));
    }
    for (; flushedStates_ < nStates_; flushedStates_++) {
        const Entry& e = states_[flushedStates_];
        uint32_t* a = enc.Op(NxOp_DefineState, 12);
        a[0] = e.id; a[1] = e.vs; a[2] = e.ps; a[3] = e.layout;
        for (int i = 0; i < 4; i++) { a[4 + 2 * i] = (uint32_t)e.rs.w[i]; a[5 + 2 * i] = (uint32_t)(e.rs.w[i] >> 32); }
    }
}

// ---- encoder ------------------------------------------------------------------------------------------------
uint32_t* Encoder::Op(uint32_t op, uint32_t argc) {
    if (overflow_ || argc > 63 || n_ + 1 + argc + 1 > cap_) { overflow_ = true; return dummy_; }  // +1: room for End
    w_[n_] = op | (argc << 16);
    uint32_t* a = w_ + n_ + 1;
    n_ += 1 + argc;
    return a;
}

// ---- sorter ---------------------------------------------------------------------------------------------------
void SortPackets(const Packet* p, uint32_t n, uint32_t* order, uint32_t* tmp, uint64_t* keys, uint64_t* ktmp) {
    uint64_t diff = 0;
    for (uint32_t i = 0; i < n; i++) { order[i] = i; keys[i] = p[i].key; diff |= keys[i] ^ keys[0]; }
    static uint32_t count[65536];
    for (int shift = 0; shift < 64; shift += 16) {
        if (!((diff >> shift) & 0xFFFF)) continue;  // digit identical everywhere: nothing to do
        memset(count, 0, sizeof count);
        for (uint32_t i = 0; i < n; i++) count[(keys[i] >> shift) & 0xFFFF]++;
        uint32_t sum = 0;
        for (uint32_t d = 0; d < 65536; d++) { uint32_t c = count[d]; count[d] = sum; sum += c; }
        for (uint32_t i = 0; i < n; i++) {
            uint32_t pos = count[(keys[i] >> shift) & 0xFFFF]++;
            tmp[pos] = order[i]; ktmp[pos] = keys[i];
        }
        memcpy(order, tmp, n * sizeof *order);
        memcpy(keys, ktmp, n * sizeof *keys);
    }
}

// ---- dynamic records ----------------------------------------------------------------------------------------------
void DefaultDyn(DynRec* d, uint32_t w, uint32_t h) {
    memset(d, 0, sizeof *d);
    d->viewport.x = 0.5f; d->viewport.y = 0.5f; d->viewport.w = (float)w; d->viewport.h = (float)h;
    d->viewport.minZ = 0.0f; d->viewport.maxZ = 1.0f;
    d->scissor.w = (int32_t)w; d->scissor.h = (int32_t)h;
    for (int i = 0; i < 4; i++) d->blendConstant[i] = 1.0f;
    d->stencilRef = DynRec::kStateRef;
}

void DynFromD3D9(DynRec* d, const NxDynamicState& s) {
    d->viewport = s.viewport;
    d->scissor = s.scissor;
    memcpy(d->blendConstant, s.blendConstant, sizeof d->blendConstant);
    d->biasUnits = s.depthBiasUnits;
    d->biasSlope = s.depthBiasSlope;
    d->stencilRef = DynRec::kStateRef;   // the translator's ref is already in the state word
}

// ---- frame -----------------------------------------------------------------------------------------------------
bool Frame::Init(Backend* be, uint32_t uboBytes, uint32_t uboSize) {
    be_ = be;
    uboSize_ = (uboSize + 255) & ~255u;  // UBO offsets must be 256-byte aligned on Maxwell
    packets_ = (Packet*)aligned_alloc_(64, kMaxPackets * sizeof(Packet));
    order_ = (uint32_t*)malloc(kMaxPackets * 4); tmp_ = (uint32_t*)malloc(kMaxPackets * 4);
    keys_ = (uint64_t*)malloc(kMaxPackets * 8); ktmp_ = (uint64_t*)malloc(kMaxPackets * 8);
    tex_ = (uint32_t*)malloc(kMaxTex * 4);
    dyn_ = (DynRec*)malloc(kMaxDyn * sizeof(DynRec));
    words_[0] = (uint32_t*)malloc(kWords * 4); words_[1] = (uint32_t*)malloc(kWords * 4);
    if (!packets_ || !order_ || !tmp_ || !keys_ || !ktmp_ || !tex_ || !dyn_ || !words_[0] || !words_[1]) return false;
    return ubo_.Init(be, uboBytes);
}

void Frame::Begin(uint32_t frame) {
    frame_ = frame;
    nPasses_ = nPackets_ = nTex_ = nDyn_ = 0;
    ubo_.BeginFrame(frame);
}

uint8_t Frame::AddPass(const PassDesc& p) {
    if (nPasses_ >= kMaxPasses) return 0xFF;
    passes_[nPasses_] = p;
    passUboSize_[nPasses_] = 0;
    return (uint8_t)nPasses_++;
}

uint32_t Frame::AddTextures(const uint32_t* h, uint32_t n) {
    // Reuse the previous block when identical (common: consecutive draws with the same material).
    if (nTex_ >= n && !memcmp(tex_ + nTex_ - n, h, n * 4)) return nTex_ - n;
    if (nTex_ + n > kMaxTex) return 0;
    memcpy(tex_ + nTex_, h, n * 4);
    nTex_ += n;
    return nTex_ - n;
}

uint16_t Frame::AddDyn(const DynRec& d) {
    if (nDyn_ && !memcmp(&dyn_[nDyn_ - 1], &d, sizeof d)) return (uint16_t)nDyn_;
    if (nDyn_ >= kMaxDyn) return 0;
    dyn_[nDyn_++] = d;
    return (uint16_t)nDyn_;
}

Packet* Frame::Emit() {
    if (nPackets_ >= kMaxPackets) return nullptr;
    Packet* p = &packets_[nPackets_++];
    memset(p, 0, sizeof *p);
    return p;
}

void* Frame::Constants(uint32_t* offset) { return ubo_.Alloc(uboSize_, 256, offset); }

void* Frame::PassConstants(uint8_t pass, uint32_t size) {
    uint32_t off;
    void* p = ubo_.Alloc((size + 255) & ~255u, 256, &off);
    if (p && pass < nPasses_) { passUbo_[pass] = off; passUboSize_[pass] = (size + 255) & ~255u; }
    return p;
}

NxEpilogue* Frame::Epilogue(Packet* p) {
    uint32_t off;
    NxEpilogue* e = (NxEpilogue*)ubo_.Alloc(256, 256, &off);
    if (e) p->epi = off + 1;
    return e;
}

static inline uint32_t fbits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }

void Frame::Finish(StateCache& sc, NxFrameStats* stats, bool present) {
    enc_.Reset(words_[frame_ & 1], kWords);
    sc.FlushDefines(enc_);
    SortPackets(packets_, nPackets_, order_, tmp_, keys_, ktmp_);

    stateBinds_ = 0;
    uint32_t i = 0;
    for (uint32_t pass = 0; pass < nPasses_; pass++) {
        const PassDesc& pd = passes_[pass];
        uint32_t* a = enc_.Op(NxOp_BeginPass, 8);
        a[0] = pd.color; a[1] = pd.depth; a[2] = pd.clearFlags; a[3] = pd.clearColor; a[4] = fbits(pd.clearDepth);
        a[5] = pd.clearStencil; a[6] = pd.width; a[7] = pd.height;
        if (passUboSize_[pass]) {
            a = enc_.Op(NxOp_BindUbo, 5);
            a[0] = 3; a[1] = 0; a[2] = ubo_.block(); a[3] = passUbo_[pass]; a[4] = passUboSize_[pass];
        }
        DynRec def, cur;
        DefaultDyn(&def, pd.width, pd.height);
        cur = def;
        // Redundant-bind elimination: every bind is compared with the last value encoded in this pass.
        uint32_t curState = 0, curTex = ~0u, curTexN = 0, curVtxB = 0, curVtxO = ~0u, curIdxB = 0, curIdxO = ~0u;
        uint32_t curEpi = 0;
        for (; i < nPackets_ && (keys_[i] >> 56) == pass; i++) {
            const Packet& p = packets_[order_[i]];
            const DynRec& d = p.dyn && p.dyn <= nDyn_ ? dyn_[p.dyn - 1] : def;
            bool rebound = false;
            if (p.state != curState) {
                a = enc_.Op(NxOp_BindState, 1); a[0] = p.state; curState = p.state; stateBinds_++;
                rebound = true;
            }
            if (memcmp(&d.viewport, &cur.viewport, sizeof d.viewport)) {
                a = enc_.Op(NxOp_Viewport, 6); memcpy(a, &d.viewport, 24); cur.viewport = d.viewport;
            }
            if (memcmp(&d.scissor, &cur.scissor, sizeof d.scissor)) {
                a = enc_.Op(NxOp_Scissor, 4); memcpy(a, &d.scissor, 16); cur.scissor = d.scissor;
            }
            if (memcmp(d.blendConstant, cur.blendConstant, sizeof d.blendConstant)) {
                a = enc_.Op(NxOp_BlendConst, 4); memcpy(a, d.blendConstant, 16);
                memcpy(cur.blendConstant, d.blendConstant, 16);
            }
            if (d.biasUnits != cur.biasUnits || d.biasSlope != cur.biasSlope) {
                a = enc_.Op(NxOp_DepthBias, 3); a[0] = fbits(d.biasUnits); a[1] = fbits(d.biasSlope); a[2] = 0;
                cur.biasUnits = d.biasUnits; cur.biasSlope = d.biasSlope;
            }
            if (d.stencilRef != DynRec::kStateRef && (rebound || d.stencilRef != cur.stencilRef)) {
                a = enc_.Op(NxOp_StencilRef, 1); a[0] = d.stencilRef; cur.stencilRef = d.stencilRef;
            } else if (d.stencilRef == DynRec::kStateRef && cur.stencilRef != DynRec::kStateRef) {
                if (!rebound) { a = enc_.Op(NxOp_BindState, 1); a[0] = p.state; stateBinds_++; }  // restores ref
                cur.stencilRef = DynRec::kStateRef;
            } else if (rebound) {
                cur.stencilRef = DynRec::kStateRef;
            }
            if (p.texCount && (p.texBlock != curTex || p.texCount != curTexN)) {
                a = enc_.Op(NxOp_BindTextures, 3 + p.texCount);
                a[0] = NxStage_Fragment; a[1] = 0; a[2] = p.texCount;
                memcpy(a + 3, tex_ + p.texBlock, p.texCount * 4);
                curTex = p.texBlock; curTexN = p.texCount;
            }
            if (p.vtxBlock != curVtxB || p.vtxOffset != curVtxO) {
                a = enc_.Op(NxOp_BindVtx, 3); a[0] = p.vtxBlock; a[1] = p.vtxOffset; a[2] = p.vtxSize;
                curVtxB = p.vtxBlock; curVtxO = p.vtxOffset;
            }
            if (p.idxBlock && (p.idxBlock != curIdxB || p.idxOffset != curIdxO)) {
                a = enc_.Op(NxOp_BindIdx, 4); a[0] = p.idxBlock; a[1] = p.idxOffset; a[2] = p.idxSize; a[3] = p.idxType;
                curIdxB = p.idxBlock; curIdxO = p.idxOffset;
            }
            if (p.epi && p.epi != curEpi) {
                a = enc_.Op(NxOp_BindUbo, 5);
                a[0] = 2; a[1] = 2; a[2] = ubo_.block(); a[3] = p.epi - 1; a[4] = 256;
                curEpi = p.epi;
            }
            a = enc_.Op(NxOp_BindUbo, 5);  // per-draw slice; the backend turns this into one 'bind UBO' method
            a[0] = 3; a[1] = 1; a[2] = ubo_.block(); a[3] = p.uboOffset; a[4] = uboSize_;
            uint32_t inst = p.instances ? p.instances : 1;
            if (p.idxBlock) {
                a = enc_.Op(NxOp_DrawIndexed, 6);
                a[0] = p.prim; a[1] = p.count; a[2] = inst; a[3] = p.first; a[4] = (uint32_t)p.baseVertex; a[5] = 0;
            } else {
                a = enc_.Op(NxOp_Draw, 5);
                a[0] = p.prim; a[1] = p.count; a[2] = inst; a[3] = p.first; a[4] = 0;
            }
        }
        enc_.Op(NxOp_EndPass, 0);
        if (pd.sampledAfter) { a = enc_.Op(NxOp_Barrier, 1); a[0] = 0; }
    }
    enc_.Op(NxOp_End, 0);
    NxSubmit s;
    memset(&s, 0, sizeof s);
    s.stream = FNVNX_ADDR_(enc_.data()); s.words = enc_.size(); s.frame = frame_; s.stats = FNVNX_ADDR_(stats);
    s.present = present ? 1 : 0;
    be_->Submit(s);
}

// ---- DDS -------------------------------------------------------------------------------------------------------
static uint32_t rd32(const uint8_t* p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

bool ParseDDS(const void* file, uint32_t size, NxImageDesc* out, uint32_t* d3dFormat) {
    const uint8_t* f = (const uint8_t*)file;
    if (size < 128 || rd32(f) != 0x20534444u /* "DDS " */ || rd32(f + 4) != 124) return false;
    uint32_t h = rd32(f + 12), w = rd32(f + 16), mips = rd32(f + 28);
    uint32_t pfFlags = rd32(f + 80), fourcc = rd32(f + 84), bits = rd32(f + 88);
    uint32_t rm = rd32(f + 92), gm = rd32(f + 96), bm = rd32(f + 100), am = rd32(f + 104);
    uint32_t caps2 = rd32(f + 112);
    if (caps2 & 0x200 /* cubemap */ || caps2 & 0x200000 /* volume */) return false;
    if (w == 0 || h == 0 || w > 16384 || h > 16384) return false;
    if (!mips) mips = 1;
    uint32_t d3d = 0;
    if (pfFlags & 4) d3d = fourcc;                                          // DDPF_FOURCC: DXTn, ATIn
    else if (bits == 32 && rm == 0xFF0000 && gm == 0xFF00 && bm == 0xFF) d3d = am ? 21 : 22;
    else if (bits == 32 && rm == 0xFF && gm == 0xFF00 && bm == 0xFF0000) d3d = am ? 32 : 33;
    else if (bits == 16 && rm == 0xF800 && gm == 0x7E0 && bm == 0x1F) d3d = 23;
    else if (bits == 16 && rm == 0x7C00 && gm == 0x3E0 && bm == 0x1F) d3d = am ? 25 : 24;
    else if (bits == 16 && rm == 0xF00 && gm == 0xF0 && bm == 0xF) d3d = am ? 26 : 30;
    else if (bits == 16 && (pfFlags & 0x80000)) d3d = 60;                    // DDPF_BUMPDUDV: V8U8
    else if (bits == 16 && (pfFlags & 0x20001) == 0x20001) d3d = 51;         // luminance + alpha: A8L8
    else if (bits == 8 && (pfFlags & 2) && !(pfFlags & 0x20000)) d3d = 28;   // DDPF_ALPHA: A8
    else if (bits == 8) d3d = 50;                                            // L8
    NxFormatInfo fi = nx_d3d9_format(d3d);
    if (fi.format == NxFmt_None || (fi.flags & NXFMT_DEPTH)) return false;
    uint32_t total = 0;
    for (uint32_t m = 0, mw = w, mh = h; m < mips; m++, mw = mw > 1 ? mw / 2 : 1, mh = mh > 1 ? mh / 2 : 1)
        total += (fi.flags & NXFMT_COMPRESSED) ? ((mw + 3) / 4) * ((mh + 3) / 4) * fi.bytes : mw * mh * fi.bytes;
    if (128 + (uint64_t)total > size) return false;
    memset(out, 0, sizeof *out);
    out->width = (uint16_t)w; out->height = (uint16_t)h; out->format = fi.format; out->mips = (uint8_t)mips;
    out->swizzle = NXIMG_SWIZZLE(fi.swizzle[0], fi.swizzle[1], fi.swizzle[2], fi.swizzle[3]);
    out->data = FNVNX_ADDR_(f + 128); out->data_size = total;
    if (d3dFormat) *d3dFormat = d3d;
    return true;
}

} // namespace nx
