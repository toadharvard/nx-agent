// fnv-nx native renderer, front end (stream S6n, RENDER_NATIVE.md §3). Runs in the ILP32 guest; also builds
// LP64 for host programs (GL backend tests). Freestanding C++: no exceptions, RTTI, STL or global constructors.
#ifndef FNVNX_NX_FRONT_H
#define FNVNX_NX_FRONT_H

#include <stdint.h>
#include <stddef.h>
#include "nx/gpu_abi.h"

namespace nx {

// ---- backend calls --------------------------------------------------------------------------------------------
struct Backend {
    virtual uint32_t MemCreate(const NxMemBlockDesc& d) = 0;
    virtual uint64_t MemGpuAddr(uint32_t id) = 0;
    virtual uint32_t ImageCreate(const NxImageDesc& d) = 0;
    virtual uint32_t SamplerCreate(const NxSamplerState& s) = 0;
    virtual uint32_t ShaderCreate(const NxShaderDesc& d) = 0;
    virtual void Submit(const NxSubmit& s) = 0;
    virtual void WaitFrame(uint32_t frame) = 0;
    virtual bool Readback(const NxReadback& r) = 0;
    virtual void Shutdown() = 0;
    virtual const NxGpuInfo& Info() const = 0;
    // The GLSL address a shader id was created from (0 if unknown): lets nx::Renderer find a package shader's
    // compile-time permutations by name (RENDER_NATIVE.md §4.3).
    virtual uint64_t ShaderGlsl(uint32_t) const { return 0; }
};

// The backend behind an NxGpuApi. In the ILP32 guest pass api = 0: calls go through S5's ext_call slot to the host
// module's NXGPU_EXT_MODULE. In LP64 programs api is the backend's table (nxgl_create(), nxgpu_init()).
// Returns 0 if the backend does not answer. Placement-constructed in a static buffer (one per process).
Backend* GpuBackend(const NxGpuApi* api);

// ---- persistent mapped ring ---------------------------------------------------------------------------------
// One caller-backed memory block split into frames_in_flight regions. Alloc is a bump pointer in the current
// region; BeginFrame(n) waits until frame n - frames_in_flight has completed on the GPU before reusing its region.
class Ring {
public:
    bool Init(Backend* be, uint32_t bytes, uint32_t kind = NxMem_Generic);
    void BeginFrame(uint32_t frame);
    // Returns the CPU pointer, writes the block offset; 0 when the region is full.
    void* Alloc(uint32_t size, uint32_t align, uint32_t* offset);
    uint32_t block() const { return block_; }
    uint32_t used() const { return head_ - regionStart_; }
    uint32_t region_size() const { return region_; }
private:
    Backend* be_ = nullptr;
    uint8_t* cpu_ = nullptr;
    uint32_t block_ = 0, region_ = 0, regionStart_ = 0, head_ = 0, frames_ = 0;
    bool started_ = false;
};

// One caller-backed block, bump-allocated (vertex/index data that lives as long as the program).
class StaticBuffer {
public:
    bool Init(Backend* be, uint32_t bytes);
    void* Alloc(uint32_t size, uint32_t align, uint32_t* offset);
    uint32_t block() const { return block_; }
private:
    uint8_t* cpu_ = nullptr;
    uint32_t block_ = 0, size_ = 0, head_ = 0;
};

// ---- render state ---------------------------------------------------------------------------------------------
// (shader pair, NxRenderState, vertex layout) -> 16-bit state id. New ids are defined in the command stream once
// (the backend builds and caches its state objects then); afterwards a draw only names the id.
class StateCache {
public:
    enum { kMaxStates = 1024, kMaxLayouts = 64 };
    uint16_t Layout(const NxVtxLayout& l);                       // 1.., deduplicated
    uint16_t State(uint16_t vs, uint16_t ps, uint16_t layout, const NxRenderState& rs);   // 1.., 0 when full
    void FlushDefines(class Encoder& e);                          // Define ops of everything new since the last call
    uint32_t states() const { return nStates_; }
private:
    struct Entry { NxRenderState rs; uint16_t vs, ps, layout, id; };
    Entry states_[kMaxStates];
    NxVtxLayout layouts_[kMaxLayouts + 1];
    uint32_t nStates_ = 0, nLayouts_ = 0, flushedStates_ = 0, flushedLayouts_ = 0;
    uint16_t hash_[4096] = {};                                   // open addressing over states_ (index + 1)
};

// ---- command stream encoder ----------------------------------------------------------------------------------
class Encoder {
public:
    void Reset(uint32_t* words, uint32_t capacity) { w_ = words; cap_ = capacity; n_ = 0; overflow_ = false; }
    uint32_t* Op(uint32_t op, uint32_t argc);                    // returns the argument slots
    uint32_t size() const { return n_; }
    const uint32_t* data() const { return w_; }
    bool overflow() const { return overflow_; }
private:
    uint32_t* w_ = nullptr;
    uint32_t cap_ = 0, n_ = 0;
    bool overflow_ = false;
    uint32_t dummy_[64];
};

// ---- draw packets and the sorter ----------------------------------------------------------------------------
// Sort key, high to low: pass(8) | layer(4) | state id(16) | texture block(16) | depth(16) | spare(4).
inline uint64_t SortKey(uint8_t pass, uint8_t layer, uint16_t state, uint16_t tex, uint16_t depth) {
    return ((uint64_t)pass << 56) | ((uint64_t)(layer & 15) << 52) | ((uint64_t)state << 36) |
           ((uint64_t)tex << 20) | ((uint64_t)depth << 4);
}

// Dynamic values a draw may need beyond its state id. Packet.dyn = 0 means the pass defaults (NxOp_BeginPass).
struct DynRec {
    NxViewport viewport;
    NxScissor scissor;
    float blendConstant[4];
    float biasUnits, biasSlope;
    uint32_t stencilRef;         // kStateRef = the bound state's ref
    enum : uint32_t { kStateRef = 0xFFFFFFFFu };
};
void DefaultDyn(DynRec* d, uint32_t width, uint32_t height);
// From the translator's output (viewport, scissor, blend constant, depth bias, stencil ref).
void DynFromD3D9(DynRec* d, const NxDynamicState& s);

struct Packet {                  // 64 bytes
    uint64_t key;
    uint16_t state, texCount;
    uint16_t texBlock;           // first handle in the frame's texture-handle array (fragment stage, slots 0..)
    uint16_t dyn;                // DynRec index (Frame::AddDyn), 0 = pass defaults
    uint32_t vtxBlock, vtxOffset, vtxSize;
    uint32_t idxBlock, idxOffset, idxSize;   // idxBlock 0: not indexed
    uint32_t uboOffset;          // per-draw constants in the UBO ring (size fixed per frame), both stages slot 1
    uint32_t first, count;       // vertex or index range
    int32_t baseVertex;
    uint16_t instances;
    uint8_t prim, idxType;       // NxPrim; 0 u16, 1 u32
    uint32_t epi;                // 1 + UBO ring offset of an NxEpilogue block (fragment slot 2), 0 = none
};
static_assert(sizeof(Packet) == 64, "Packet");

// LSD radix sort of (key, index) pairs, 8 bits per pass (one histogram pass), skips constant digits. Stable.
void SortPackets(const Packet* p, uint32_t n, uint32_t* order, uint32_t* tmp, uint64_t* keys, uint64_t* ktmp);

// ---- frame graph ------------------------------------------------------------------------------------------------
struct PassDesc {
    uint32_t color;              // image id (0 = swapchain)
    uint32_t depth;              // image id or 0
    uint32_t clearFlags;         // 1 color, 2 depth, 4 stencil
    uint32_t clearColor;         // RGBA8, R in the low byte
    float clearDepth;
    uint32_t clearStencil;
    uint16_t width, height;
    uint8_t sampledAfter;        // 1: colour is sampled by a later pass (barrier after the pass)
};

// ---- frame: packets of all passes, sorted and encoded at Finish ---------------------------------------------
class Frame {
public:
    enum { kMaxPackets = 8192, kMaxPasses = 16, kMaxTex = 16384, kMaxDyn = 4096, kWords = 256 * 1024 };
    // uboBytes 0: sized for kMaxPackets draws per frame in each of the backend's frames in flight (UboBytesFor).
    bool Init(Backend* be, uint32_t uboBytes, uint32_t uboSize);
    // UBO ring bytes for `draws` draws per frame: per draw the constant slice (uboSize, 256-aligned) and one
    // 256-byte epilogue, plus 64 KiB of pass constants, times frames in flight.
    static uint32_t UboBytesFor(uint32_t draws, uint32_t uboSize, uint32_t framesInFlight) {
        uint64_t per = (uint64_t)draws * (((uboSize + 255) & ~255u) + 256u) + (64u << 10);
        return (uint32_t)(per * framesInFlight);
    }
    uint32_t droppedDraws() const { return dropped_; }        // draws of the last frame the ring could not hold
    void Begin(uint32_t frame);
    uint8_t AddPass(const PassDesc& p);                          // pass index used in sort keys
    uint32_t AddTextures(const uint32_t* handles, uint32_t n);
    uint16_t AddDyn(const DynRec& d);                            // 1.. (a repeat of the last one is shared)
    Packet* Emit();                                              // zeroed packet, 0 when full
    void* Constants(uint32_t* offset);                           // per-draw UBO slice (uboSize bytes)
    void* PassConstants(uint8_t pass, uint32_t size);            // bound at slot 0, both stages, for the pass
    // A clear of a rectangle (pixels) of the pass's targets, ordered like a draw by (pass, layer, ..., depth key).
    // flags: 1 colour, 2 depth, 4 stencil. Encoded as NxOp_Clear (Packet.prim = kClearPrim).
    bool Clear(uint8_t pass, uint8_t layer, uint16_t depthKey, uint32_t flags, uint32_t rgba8, float depth,
               uint32_t stencil, uint32_t x, uint32_t y, uint32_t w, uint32_t h);
    enum { kClearPrim = 0xFF };
    // The D3D9 epilogue block for a packet (sets p->epi); 0 when the ring is full.
    NxEpilogue* Epilogue(Packet* p);
    // Engine pass id + family of a draw packet: encoded as NxOp_Marker (NX_MARKER_PASS) before the first draw of each
    // run of draws with the same tag in a pass. family 0 = untagged.
    void Tag(const Packet* p, uint16_t enginePass, uint16_t family);
    // Sorts, encodes (with redundant-bind elimination) and submits. stats may be 0.
    void Finish(StateCache& sc, NxFrameStats* stats, bool present = false);
    Ring& ubo() { return ubo_; }
    uint32_t encodedWords() const { return enc_.size(); }
    uint32_t stateBindsEncoded() const { return stateBinds_; }
    uint32_t packets() const { return nPackets_; }
    const uint32_t* encodedData() const { return enc_.data(); }
private:
    Backend* be_ = nullptr;
    Ring ubo_;
    uint32_t uboSize_ = 0, frame_ = 0, dropped_ = 0;
    PassDesc passes_[kMaxPasses];
    uint32_t passUbo_[kMaxPasses], passUboSize_[kMaxPasses];
    uint32_t nPasses_ = 0, nPackets_ = 0, nTex_ = 0, nDyn_ = 0, stateBinds_ = 0;
    Packet* packets_ = nullptr;
    uint32_t* order_ = nullptr; uint32_t* tmp_ = nullptr;
    uint64_t* keys_ = nullptr; uint64_t* ktmp_ = nullptr;
    uint32_t* tex_ = nullptr;
    uint32_t* tags_ = nullptr;                                   // per packet: pass id | family << 16, 0 = none
    DynRec* dyn_ = nullptr;
    uint32_t* words_[2] = {nullptr, nullptr};                    // double-buffered: the backend may read N-1
    Encoder enc_;
};

// ---- DDS ---------------------------------------------------------------------------------------------------------
// Parses a DDS file in memory (2D, mips) into an NxImageDesc whose data points into the file; the format goes
// through nx_d3d9_format() (D3DFMT from the FOURCC or the bit masks: A8R8G8B8, X8R8G8B8, A8B8G8R8, R5G6B5,
// A1R5G5B5, X1R5G5B5, A4R4G4B4, L8, A8, A8L8, V8U8, DXT1-5, ATI1, ATI2). Returns false for anything else.
bool ParseDDS(const void* file, uint32_t size, NxImageDesc* out, uint32_t* d3dFormat = nullptr);

} // namespace nx

#endif
