// fnv-nx native renderer: command-stream capture (stream S6n). CaptureBackend wraps any nx::Backend (or none) and
// writes every resource creation and every submitted frame - the command words plus the bytes of each memory range
// the stream binds (UBO slices, vertex and index ranges) - to a file. Runs in the ILP32 guest (newlib stdio) and on
// the host. replay.h replays a capture into any NxGpuApi (the GL backend on the host: capture/replay_gl.cpp).
//
// File: "NXCAP\0\0\1" (8 bytes), then records: u32 tag, u32 payload bytes, payload (little-endian, 4-byte aligned).
//   INFO NxGpuInfo                                 MEMC u32 id, u32 size, u32 kind
//   IMGC u32 id, NxImageDesc (data = 0), data      SMPC u32 id, NxSamplerState
//   SHDC u32 id, u32 stage, u32 dkshBytes, u32 glslBytes, dksh, glsl (each padded to 4)
//   SUBM u32 frame, u32 present, u32 words, words[], u32 nranges, { u32 block, u32 offset, u32 size, bytes } x n
//   RDBK NxReadback (dst = 0), u32 bytes, the readback result (RGBA8)
#ifndef FNVNX_NX_CAPTURE_H
#define FNVNX_NX_CAPTURE_H
#include <stdio.h>
#include "nx/front.h"

namespace nx {

enum : uint32_t {
    kCapInfo = 0x4F464E49u, kCapMem = 0x434D454Du, kCapImage = 0x43474D49u, kCapSampler = 0x43504D53u,
    kCapShader = 0x43444853u, kCapSubmit = 0x4D425553u, kCapReadback = 0x4B424452u,
};

class CaptureBackend final : public Backend {
public:
    // inner may be 0 (capture only: ids are allocated here, readbacks return false). Takes ownership of f.
    bool Open(Backend* inner, FILE* f);
    void Close();
    uint32_t MemCreate(const NxMemBlockDesc& d) override;
    uint64_t MemGpuAddr(uint32_t id) override { return inner_ ? inner_->MemGpuAddr(id) : 0; }
    uint32_t ImageCreate(const NxImageDesc& d) override;
    uint32_t SamplerCreate(const NxSamplerState& s) override;
    uint32_t ShaderCreate(const NxShaderDesc& d) override;
    void Submit(const NxSubmit& s) override;
    void WaitFrame(uint32_t frame) override { if (inner_) inner_->WaitFrame(frame); }
    bool Readback(const NxReadback& r) override;
    void Shutdown() override;
    const NxGpuInfo& Info() const override { return info_; }
    uint32_t frames() const { return frames_; }
    uint64_t bytes() const { return bytes_; }
private:
    enum { kMaxMem = 256 };
    void Rec(uint32_t tag, uint32_t size);
    void Put(const void* p, uint32_t n);
    Backend* inner_ = nullptr;
    FILE* f_ = nullptr;
    NxGpuInfo info_;
    uint64_t memCpu_[kMaxMem + 1] = {};
    uint32_t memSize_[kMaxMem + 1] = {};
    uint32_t nextMem_ = 0, nextImage_ = 0, nextSampler_ = 0, nextShader_ = 0, frames_ = 0;
    uint64_t bytes_ = 0;
};

} // namespace nx
#endif
