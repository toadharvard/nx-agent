// fnv-nx native renderer: interfaces only (stream S6n, docs/RENDER_NATIVE.md). Our own code; no decomp content.
// Compiled ILP32 with the engine. Nothing here calls deko3d directly: the backend (ILP32 or an LP64 worker,
// decided by S5) consumes PacketArena.
#ifndef FNVNX_NX_RENDER_H
#define FNVNX_NX_RENDER_H

#include <stdint.h>
#include <stddef.h>

namespace nx {

// ---- ids -------------------------------------------------------------------------------------------------------
typedef uint16_t ShaderPairId;   // VS+PS DKSH pair (family, permutation)
typedef uint16_t VtxLayoutId;    // vertex attribute/buffer layout, decided at geometry upload
typedef uint32_t TexHandle;      // dkMakeTextureHandle(image slot, sampler slot) in the global descriptor sets
typedef uint64_t StateWord;      // packed blend/depth/stencil/raster/colour-write state (see PackState)

enum FamilyId : uint8_t {
    FamLit, FamLand, FamDepth, FamNoLight, FamGrass, FamSpeedTree, FamSky, FamWater, FamParticle, FamPrecip,
    FamBolt, FamBeam, FamDecal, FamBlood, FamVFog, FamTile, FamImageSpace, FamFixed, FamCount
};

// ---- render state ---------------------------------------------------------------------------------------------
struct RasterState {
    uint8_t blendSrc, blendDst, blendOp, alphaToCoverage;
    uint8_t depthTest, depthWrite, depthFunc, cull;
    uint8_t stencilEnable, stencilFunc, stencilPass, stencilRef;
    uint8_t stencilMask, colorWriteMask, depthBiasClass, pad;
};
StateWord PackState(const RasterState& s);

struct PipelineState {           // what a draw needs bound; compared by id, bound block by block
    ShaderPairId shaders;
    VtxLayoutId layout;
    StateWord state;
};

// ---- GPU memory (caller-backed DkMemBlocks below 4 GB) -----------------------------------------------------------
struct GpuSpan { uint32_t cpu; uint32_t gpuOffset; uint32_t size; };   // cpu = ILP32 pointer value

class Ring {                     // persistent mapped, per-frame regions, fence-protected (3 frames in flight)
public:
    virtual ~Ring() {}
    virtual GpuSpan Alloc(uint32_t size, uint32_t align) = 0;
    virtual void BeginFrame(uint32_t frame) = 0;
};

// ---- draw packets --------------------------------------------------------------------------------------------
struct DrawPacket {              // 48 bytes
    uint64_t sortKey;            // pass(8) | layer(4) | pipeline(16) | texblock(16) | depth(16) | spare(4)
    uint32_t pipeline;           // index into the frame's PipelineState table
    uint32_t vbOffset;           // static or ring buffer offset
    uint32_t ibOffset;
    uint32_t indexCount;
    uint32_t uboOffset;          // per-draw constants in the UBO ring
    uint32_t texBlock;           // offset of TexHandle[] in the frame arena
    uint16_t instanceCount;
    uint8_t  texCount;
    uint8_t  flags;
    uint32_t firstIndexBaseVertex;
};

class PacketArena {              // one per frame, handed from the main thread to the render thread
public:
    virtual ~PacketArena() {}
    virtual DrawPacket* Emit() = 0;
    virtual uint32_t AddPipeline(const PipelineState& p) = 0;
    virtual uint32_t AddTextures(const TexHandle* h, uint32_t n) = 0;
};

// ---- frame graph -------------------------------------------------------------------------------------------
enum PassKind : uint8_t { PassShadow, PassWaterReflection, PassMain, PassImageSpace, PassUI, PassOffscreen };

struct TargetDesc { uint16_t width, height; uint8_t colorFormat, depthFormat, samples, transient; };

class FrameGraph {
public:
    virtual ~FrameGraph() {}
    virtual uint16_t DeclareTarget(const TargetDesc& d) = 0;
    virtual uint8_t BeginPass(PassKind kind, uint16_t color, uint16_t depth, uint32_t clearFlags) = 0;
    virtual void EndPass() = 0;
};

// ---- families --------------------------------------------------------------------------------------------
// One per BSShader class. Input: the engine's kept pass entry (geometry, eBSSMPassEnum id, lights, property);
// output: packets. The engine types stay opaque here.
struct PassEntryView { const void* geometry; const void* property; uint16_t passId; uint8_t lightCount; uint8_t pad;
                       const void* const* lights; };

class Family {
public:
    enum Impl : uint8_t { Native, Translated };   // Translated = S6b's sdp2dksh output (parity/fallback only)
    virtual ~Family() {}
    virtual void BeginPass(PacketArena& arena, Ring& ubo, uint8_t pass) = 0;
    virtual void Submit(const PassEntryView& e, PacketArena& arena, Ring& ubo) = 0;
    virtual Impl Implementation() const = 0;
};

} // namespace nx

#endif
