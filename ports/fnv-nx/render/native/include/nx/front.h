// fnv-nx native renderer, front end (stream S6n, RENDER_NATIVE.md §3). Runs in the ILP32 guest; also builds
// LP64 for host unit tests. Freestanding C++: no exceptions, RTTI, STL or global constructors.
#ifndef FNVNX_NX_FRONT_H
#define FNVNX_NX_FRONT_H

#include <stdint.h>
#include <stddef.h>
#include "nx/gpu_abi.h"

namespace nx {

// ---- backend calls (hostcalls in the guest, a recorder in unit tests) -----------------------------------------
struct Backend {
    virtual uint32_t MemCreate(const NxMemBlockDesc& d) = 0;
    virtual uint64_t MemGpuAddr(uint32_t id) = 0;
    virtual uint64_t ImageCreate(const NxImageDesc& d) = 0;     // (id << 32) | handle
    virtual uint32_t ShaderCreate(const void* dksh, uint32_t size, uint32_t stage) = 0;
    virtual void Submit(const NxSubmit& s) = 0;
    virtual void WaitFrame(uint32_t frame) = 0;
    virtual uint32_t FramesInFlight() const = 0;
    virtual void Size(uint32_t* w, uint32_t* h) const = 0;
};

// Backend over the NxGpuApi host table (guest only).
Backend* HostBackend(const NxGpuApi* api);

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
    uint32_t lastFrame_ = 0;
    bool started_ = false;
};

// ---- render state ---------------------------------------------------------------------------------------------
struct BlendDesc { uint8_t enable, src, dst, op; };            // op: 0 add
struct DepthDesc { uint8_t test, write, func; };
uint64_t PackState(const BlendDesc& b, const DepthDesc& d, uint8_t cull, uint8_t colorMask);

// (shader pair, render-state word, vertex layout) -> 16-bit state id. New ids are defined in the command stream
// once (the backend builds and caches the deko3d state blocks then); afterwards a draw only names the id.
class StateCache {
public:
    enum { kMaxStates = 1024, kMaxLayouts = 64 };
    uint16_t Layout(const NxVtxLayout& l);                       // 1.., deduplicated
    uint16_t State(uint16_t vs, uint16_t ps, uint16_t layout, uint64_t word);
    // Emits the Define ops of everything created since the last call into the encoder.
    void FlushDefines(class Encoder& e);
    uint32_t states() const { return nStates_; }
private:
    struct Entry { uint64_t word; uint16_t vs, ps, layout, id; };
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
    uint32_t dummy_[48];
};

// ---- draw packets and the sorter ----------------------------------------------------------------------------
// Sort key, high to low: pass(8) | layer(4) | state id(16) | texture block(16) | depth(16) | spare(4).
inline uint64_t SortKey(uint8_t pass, uint8_t layer, uint16_t state, uint16_t tex, uint16_t depth) {
    return ((uint64_t)pass << 56) | ((uint64_t)(layer & 15) << 52) | ((uint64_t)state << 36) |
           ((uint64_t)tex << 20) | ((uint64_t)depth << 4);
}

struct Packet {                  // 48 bytes
    uint64_t key;
    uint16_t state, texCount;
    uint32_t texBlock;           // index of the first handle in the frame's texture-handle array
    uint32_t vtxBlock, vtxOffset, vtxSize;
    uint32_t uboOffset;          // per-draw constants in the UBO ring (size fixed per frame: uboSize)
    uint32_t first, count;       // vertex range (non-indexed in N0)
    uint16_t instances, prim;
    uint32_t pad;
};

// LSD radix sort of (key, index) pairs, 16 bits per pass, skips passes whose digit is constant.
void SortPackets(const Packet* p, uint32_t n, uint32_t* order, uint32_t* tmp, uint64_t* keys, uint64_t* ktmp);

// ---- frame graph ------------------------------------------------------------------------------------------------
enum { kSwapchain = 0 };
struct PassDesc {
    uint32_t color;              // image id or kSwapchain
    uint32_t depth;              // image id or 0
    uint32_t clearFlags;         // 1 color, 2 depth
    uint32_t clearColor;         // RGBA8
    float clearDepth;
    uint16_t width, height;
    uint8_t sampledAfter;        // 1: color is sampled by a later pass (barrier after the pass)
};

// ---- frame: packets of all passes, sorted and encoded at Finish ---------------------------------------------
class Frame {
public:
    enum { kMaxPackets = 8192, kMaxPasses = 16, kMaxTex = 16384, kWords = 256 * 1024 };
    bool Init(Backend* be, uint32_t uboBytes, uint32_t uboSize);
    void Begin(uint32_t frame);
    uint8_t AddPass(const PassDesc& p);                          // returns the pass index used in sort keys
    uint32_t AddTextures(const uint32_t* handles, uint32_t n);
    Packet* Emit();                                              // 0 when full
    void* Constants(uint32_t* offset);                           // per-draw UBO slice (uboSize bytes)
    void* PassConstants(uint8_t pass, uint32_t size);            // bound at slot 0 for the whole pass
    // Sorts, encodes (with redundant-bind elimination) and submits. stats may be 0.
    void Finish(StateCache& sc, NxFrameStats* stats);
    Ring& ubo() { return ubo_; }
    uint32_t encodedWords() const { return enc_.size(); }
    uint32_t stateBindsEncoded() const { return stateBinds_; }
    uint32_t packets() const { return nPackets_; }
private:
    Backend* be_ = nullptr;
    Ring ubo_;
    uint32_t uboSize_ = 0, frame_ = 0;
    PassDesc passes_[kMaxPasses];
    uint32_t passUbo_[kMaxPasses], passUboSize_[kMaxPasses];
    uint32_t nPasses_ = 0, nPackets_ = 0, nTex_ = 0, stateBinds_ = 0;
    Packet* packets_ = nullptr;
    uint32_t* order_ = nullptr; uint32_t* tmp_ = nullptr;
    uint64_t* keys_ = nullptr; uint64_t* ktmp_ = nullptr;
    uint32_t* tex_ = nullptr;
    uint32_t* words_[2] = {nullptr, nullptr};                    // double-buffered: render thread reads N-1
    Encoder enc_;
};

// ---- DDS ---------------------------------------------------------------------------------------------------------
// Parses a DDS file in memory (DXT1/3/5, A8R8G8B8, X8R8G8B8, L8, A8L8; 2D, mips) into an NxImageDesc whose data
// points into the file. Returns false for anything else (cube/volume come in N2).
bool ParseDDS(const void* file, uint32_t size, NxImageDesc* out);

} // namespace nx

#endif
