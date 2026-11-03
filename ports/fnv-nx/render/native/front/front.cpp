// fnv-nx native renderer, front end implementation (ILP32 guest; LP64 for host tests). See nx/front.h.
#include "nx/front.h"
#include <stdlib.h>
#include <string.h>
#include <new>

namespace nx {

// ---- host backend ------------------------------------------------------------------------------------------
#if defined(__ILP32__)
namespace {
struct HostBe final : Backend {
    const NxGpuApi* a;
    explicit HostBe(const NxGpuApi* api) : a(api) {}
    uint32_t MemCreate(const NxMemBlockDesc& d) override { return (uint32_t)fnvnx_hcall1(a->mem_create, FNVNX_ADDR(&d)); }
    uint64_t MemGpuAddr(uint32_t id) override { return fnvnx_hcall1(a->mem_gpu_addr, id); }
    uint64_t ImageCreate(const NxImageDesc& d) override { return fnvnx_hcall1(a->image_create, FNVNX_ADDR(&d)); }
    uint32_t ShaderCreate(const void* p, uint32_t n, uint32_t st) override {
        return (uint32_t)fnvnx_hcall3(a->shader_create, FNVNX_ADDR(p), n, st);
    }
    void Submit(const NxSubmit& s) override { fnvnx_hcall1(a->submit, FNVNX_ADDR(&s)); }
    void WaitFrame(uint32_t f) override { fnvnx_hcall1(a->wait_frame, f); }
    uint32_t FramesInFlight() const override { return a->frames_in_flight; }
    void Size(uint32_t* w, uint32_t* h) const override { *w = a->width; *h = a->height; }
};
alignas(8) unsigned char g_hostBe[sizeof(HostBe)];
}
Backend* HostBackend(const NxGpuApi* api) { return new (g_hostBe) HostBe(api); }
#endif

static void* aligned_alloc_(uint32_t align, uint32_t size) {
    void* p = nullptr;
    if (posix_memalign(&p, align, size)) return nullptr;
    return p;
}

// ---- ring --------------------------------------------------------------------------------------------------
bool Ring::Init(Backend* be, uint32_t bytes, uint32_t kind) {
    be_ = be;
    frames_ = be->FramesInFlight();
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
    lastFrame_ = frame;
    started_ = true;
}

void* Ring::Alloc(uint32_t size, uint32_t align, uint32_t* offset) {
    uint32_t at = (head_ + align - 1) & ~(align - 1);
    if (at + size > regionStart_ + region_) return nullptr;
    head_ = at + size;
    *offset = at;
    return cpu_ + at;
}

// ---- state --------------------------------------------------------------------------------------------------
uint64_t PackState(const BlendDesc& b, const DepthDesc& d, uint8_t cull, uint8_t colorMask) {
    uint64_t blend = b.enable ? (1u | (b.src & 15u) << 1 | (b.dst & 15u) << 5 | (b.op & 7u) << 9) : 0;
    uint64_t depth = (d.test & 1u) | (d.write & 1u) << 1 | (d.func & 7u) << 2;
    return blend << NXRS_BLEND_SHIFT | depth << NXRS_DEPTH_SHIFT | (uint64_t)(cull & 3u) << NXRS_RASTER_SHIFT |
           (uint64_t)(colorMask & 15u) << NXRS_CWRITE_SHIFT;
}

uint16_t StateCache::Layout(const NxVtxLayout& l) {
    for (uint32_t i = 1; i <= nLayouts_; i++)
        if (!memcmp(&layouts_[i], &l, sizeof l)) return (uint16_t)i;
    if (nLayouts_ >= kMaxLayouts) return 0;
    layouts_[++nLayouts_] = l;
    return (uint16_t)nLayouts_;
}

static uint32_t hash_state(uint16_t vs, uint16_t ps, uint16_t lay, uint64_t w) {
    uint64_t h = w * 0x9E3779B97F4A7C15ull ^ ((uint64_t)vs << 32 | (uint64_t)ps << 16 | lay) * 0xC2B2AE3D27D4EB4Full;
    return (uint32_t)(h >> 52);  // 12 bits
}

uint16_t StateCache::State(uint16_t vs, uint16_t ps, uint16_t layout, uint64_t word) {
    uint32_t h = hash_state(vs, ps, layout, word);
    for (uint32_t probe = 0; probe < 4096; probe++, h = (h + 1) & 4095) {
        uint16_t slot = hash_[h];
        if (!slot) break;
        const Entry& e = states_[slot - 1];
        if (e.word == word && e.vs == vs && e.ps == ps && e.layout == layout) return e.id;
    }
    if (nStates_ >= kMaxStates) return 0;
    Entry& e = states_[nStates_];
    e = {word, vs, ps, layout, (uint16_t)(nStates_ + 1)};
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
        uint32_t* a = enc.Op(NxOp_DefineState, 6);
        a[0] = e.id; a[1] = e.vs; a[2] = e.ps; a[3] = e.layout;
        a[4] = (uint32_t)e.word; a[5] = (uint32_t)(e.word >> 32);
    }
}

// ---- encoder ------------------------------------------------------------------------------------------------
uint32_t* Encoder::Op(uint32_t op, uint32_t argc) {
    if (overflow_ || n_ + 1 + argc + 1 > cap_) { overflow_ = true; return dummy_; }  // +1 keeps room for End
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

// ---- frame -----------------------------------------------------------------------------------------------------
bool Frame::Init(Backend* be, uint32_t uboBytes, uint32_t uboSize) {
    be_ = be;
    uboSize_ = (uboSize + 255) & ~255u;  // UBO offsets must be 256-byte aligned on Maxwell
    packets_ = (Packet*)aligned_alloc_(64, kMaxPackets * sizeof(Packet));
    order_ = (uint32_t*)malloc(kMaxPackets * 4); tmp_ = (uint32_t*)malloc(kMaxPackets * 4);
    keys_ = (uint64_t*)malloc(kMaxPackets * 8); ktmp_ = (uint64_t*)malloc(kMaxPackets * 8);
    tex_ = (uint32_t*)malloc(kMaxTex * 4);
    words_[0] = (uint32_t*)malloc(kWords * 4); words_[1] = (uint32_t*)malloc(kWords * 4);
    if (!packets_ || !order_ || !tmp_ || !keys_ || !ktmp_ || !tex_ || !words_[0] || !words_[1]) return false;
    return ubo_.Init(be, uboBytes);
}

void Frame::Begin(uint32_t frame) {
    frame_ = frame;
    nPasses_ = nPackets_ = nTex_ = 0;
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

Packet* Frame::Emit() { return nPackets_ < kMaxPackets ? &packets_[nPackets_++] : nullptr; }

void* Frame::Constants(uint32_t* offset) { return ubo_.Alloc(uboSize_, 256, offset); }

void* Frame::PassConstants(uint8_t pass, uint32_t size) {
    uint32_t off;
    void* p = ubo_.Alloc((size + 255) & ~255u, 256, &off);
    if (p && pass < nPasses_) { passUbo_[pass] = off; passUboSize_[pass] = (size + 255) & ~255u; }
    return p;
}

static inline uint32_t fbits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }

void Frame::Finish(StateCache& sc, NxFrameStats* stats) {
    enc_.Reset(words_[frame_ & 1], kWords);
    sc.FlushDefines(enc_);
    SortPackets(packets_, nPackets_, order_, tmp_, keys_, ktmp_);

    stateBinds_ = 0;
    uint32_t i = 0;
    for (uint32_t pass = 0; pass < nPasses_; pass++) {
        const PassDesc& pd = passes_[pass];
        uint32_t* a = enc_.Op(NxOp_BeginPass, 7);
        a[0] = pd.color; a[1] = pd.depth; a[2] = pd.clearFlags; a[3] = pd.clearColor; a[4] = fbits(pd.clearDepth);
        a[5] = pd.width; a[6] = pd.height;
        if (passUboSize_[pass]) {
            a = enc_.Op(NxOp_BindUbo, 5);
            a[0] = 3; a[1] = 0; a[2] = ubo_.block(); a[3] = passUbo_[pass]; a[4] = passUboSize_[pass];
        }
        // Redundant-bind elimination: every bind is compared with the last value encoded in this pass.
        uint32_t curState = 0, curTex = ~0u, curTexN = 0, curVtxB = 0, curVtxO = ~0u;
        for (; i < nPackets_ && (keys_[i] >> 56) == pass; i++) {
            const Packet& p = packets_[order_[i]];
            if (p.state != curState) {
                a = enc_.Op(NxOp_BindState, 1); a[0] = p.state; curState = p.state; stateBinds_++;
            }
            if (p.texCount && (p.texBlock != curTex || p.texCount != curTexN)) {
                a = enc_.Op(NxOp_BindTextures, 3 + p.texCount);
                a[0] = 1; a[1] = 0; a[2] = p.texCount;
                memcpy(a + 3, tex_ + p.texBlock, p.texCount * 4);
                curTex = p.texBlock; curTexN = p.texCount;
            }
            if (p.vtxBlock != curVtxB || p.vtxOffset != curVtxO) {
                a = enc_.Op(NxOp_BindVtx, 3); a[0] = p.vtxBlock; a[1] = p.vtxOffset; a[2] = p.vtxSize;
                curVtxB = p.vtxBlock; curVtxO = p.vtxOffset;
            }
            a = enc_.Op(NxOp_BindUbo, 5);  // per-draw slice; the backend turns this into one 'bind UBO' method
            a[0] = 3; a[1] = 1; a[2] = ubo_.block(); a[3] = p.uboOffset; a[4] = uboSize_;
            a = enc_.Op(NxOp_Draw, 5);
            a[0] = p.prim; a[1] = p.count; a[2] = p.instances ? p.instances : 1; a[3] = p.first; a[4] = 0;
        }
        enc_.Op(NxOp_EndPass, 0);
        if (pd.sampledAfter) { a = enc_.Op(NxOp_Barrier, 1); a[0] = 0; }
    }
    enc_.Op(NxOp_End, 0);
    NxSubmit s = {FNVNX_ADDR_(enc_.data()), enc_.size(), frame_, FNVNX_ADDR_(stats)};
    be_->Submit(s);
}

// ---- DDS -------------------------------------------------------------------------------------------------------
static uint32_t rd32(const uint8_t* p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

bool ParseDDS(const void* file, uint32_t size, NxImageDesc* out) {
    const uint8_t* f = (const uint8_t*)file;
    if (size < 128 || rd32(f) != 0x20534444u /* "DDS " */ || rd32(f + 4) != 124) return false;
    uint32_t h = rd32(f + 12), w = rd32(f + 16), mips = rd32(f + 28);
    uint32_t pfFlags = rd32(f + 80), fourcc = rd32(f + 84), bits = rd32(f + 88);
    uint32_t caps2 = rd32(f + 112);
    if (caps2 & 0x200 /* cubemap */ || caps2 & 0x200000 /* volume */) return false;
    if (!mips) mips = 1;
    uint8_t fmt = 0, sw = 0; uint32_t bpp = 0, blockBytes = 0;
    if (pfFlags & 4) {  // DDPF_FOURCC
        if (fourcc == 0x31545844u) { fmt = NxFmt_BC1; blockBytes = 8; }
        else if (fourcc == 0x33545844u) { fmt = NxFmt_BC2; blockBytes = 16; }
        else if (fourcc == 0x35545844u) { fmt = NxFmt_BC3; blockBytes = 16; }
        else return false;
    } else if (bits == 32) { fmt = NxFmt_BGRA8; bpp = 4; }     // A8R8G8B8 / X8R8G8B8 (alpha mask decides use)
    else if (bits == 8) { fmt = NxFmt_R8; bpp = 1; sw = (pfFlags & 2) ? 2 : 1; }  // A8 (DDPF_ALPHA) / L8
    else if (bits == 16 && (pfFlags & 0x20001) == 0x20001) { fmt = NxFmt_RG8; bpp = 2; sw = 3; }  // A8L8
    else return false;
    uint32_t total = 0;
    for (uint32_t m = 0, mw = w, mh = h; m < mips; m++, mw = mw > 1 ? mw / 2 : 1, mh = mh > 1 ? mh / 2 : 1)
        total += blockBytes ? ((mw + 3) / 4) * ((mh + 3) / 4) * blockBytes : mw * mh * bpp;
    if (128 + total > size) return false;
    memset(out, 0, sizeof *out);
    out->width = (uint16_t)w; out->height = (uint16_t)h; out->format = fmt; out->mips = (uint8_t)mips;
    out->swizzle = sw; out->data = FNVNX_ADDR_(f + 128); out->data_size = total;
    return true;
}

} // namespace nx
