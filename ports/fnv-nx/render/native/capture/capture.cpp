// fnv-nx native renderer: command-stream capture. See capture.h.
#include "capture.h"
#include <string.h>

namespace nx {

static const uint8_t kMagic[8] = {'N', 'X', 'C', 'A', 'P', 0, 0, 1};

void CaptureBackend::Put(const void* p, uint32_t n) {
    if (!f_ || !n) return;
    fwrite(p, 1, n, f_);
    bytes_ += n;
    static const uint8_t z[4] = {0, 0, 0, 0};
    if (n & 3) { fwrite(z, 1, 4 - (n & 3), f_); bytes_ += 4 - (n & 3); }
}
void CaptureBackend::Rec(uint32_t tag, uint32_t size) { uint32_t h[2] = {tag, size}; Put(h, 8); }
static uint32_t pad4(uint32_t n) { return (n + 3) & ~3u; }

bool CaptureBackend::Open(Backend* inner, FILE* f) {
    inner_ = inner; f_ = f;
    if (!f_) return false;
    memset(&info_, 0, sizeof info_);
    if (inner) info_ = inner->Info();
    else { info_.magic = NXGPU_MAGIC; info_.version = NXGPU_VERSION; info_.frames_in_flight = 2; }
    Put(kMagic, 8);
    Rec(kCapInfo, sizeof info_); Put(&info_, sizeof info_);
    return true;
}

void CaptureBackend::Close() { if (f_) { fclose(f_); f_ = nullptr; } }

uint32_t CaptureBackend::MemCreate(const NxMemBlockDesc& d) {
    uint32_t id = inner_ ? inner_->MemCreate(d) : ++nextMem_;
    if (id && id <= kMaxMem) { memCpu_[id] = d.storage; memSize_[id] = d.size; }
    uint32_t a[3] = {id, d.size, d.kind};
    Rec(kCapMem, 12); Put(a, 12);
    return id;
}

uint32_t CaptureBackend::ImageCreate(const NxImageDesc& d) {
    uint32_t id;
    if (inner_) id = inner_->ImageCreate(d);
    else { id = ++nextImage_; if (nx_format_has_srgb(d.format)) nextImage_++; }
    NxImageDesc c = d;
    c.data = 0;
    Rec(kCapImage, 4 + sizeof c + pad4(d.data ? d.data_size : 0));
    Put(&id, 4); Put(&c, sizeof c);
    if (d.data) Put((const void*)(uintptr_t)d.data, d.data_size);
    return id;
}

uint32_t CaptureBackend::SamplerCreate(const NxSamplerState& s) {
    uint32_t id = inner_ ? inner_->SamplerCreate(s) : ++nextSampler_;
    Rec(kCapSampler, 4 + sizeof s); Put(&id, 4); Put(&s, sizeof s);
    return id;
}

uint32_t CaptureBackend::ShaderCreate(const NxShaderDesc& d) {
    uint32_t id = inner_ ? inner_->ShaderCreate(d) : ++nextShader_;
    uint32_t dk = d.dksh ? d.dksh_size : 0, gl = d.glsl ? d.glsl_size : 0;
    uint32_t a[4] = {id, d.stage, dk, gl};
    Rec(kCapShader, 16 + pad4(dk) + pad4(gl)); Put(a, 16);
    if (dk) Put((const void*)(uintptr_t)d.dksh, dk);
    if (gl) Put((const void*)(uintptr_t)d.glsl, gl);
    return id;
}

void CaptureBackend::Submit(const NxSubmit& s) {
    // the memory ranges this stream binds: copied as the GPU will see them
    const uint32_t* w = (const uint32_t*)(uintptr_t)s.stream;
    struct R { uint32_t block, off, size; };
    static R ranges[16384];
    uint32_t n = 0, payload = 12 + s.words * 4 + 4;
    for (uint32_t i = 0; i < s.words && n < 16384;) {
        uint32_t op = w[i] & 0xFFFF, argc = w[i] >> 16;
        const uint32_t* a = w + i + 1;
        R r = {0, 0, 0};
        if (op == NxOp_BindUbo && argc >= 5) r = {a[2], a[3], a[4]};
        else if ((op == NxOp_BindVtx && argc >= 3) || (op == NxOp_BindIdx && argc >= 4)) r = {a[0], a[1], a[2]};
        if (r.block && r.block <= kMaxMem && memCpu_[r.block] && r.off < memSize_[r.block]) {
            if (r.size > memSize_[r.block] - r.off) r.size = memSize_[r.block] - r.off;
            ranges[n++] = r;
            payload += 12 + pad4(r.size);
        }
        if (op == NxOp_End) break;
        i += 1 + argc;
    }
    uint32_t h[3] = {s.frame, s.present, s.words};
    Rec(kCapSubmit, payload);
    Put(h, 12); Put(w, s.words * 4); Put(&n, 4);
    for (uint32_t i = 0; i < n; i++) {
        Put(&ranges[i], 12);
        Put((const uint8_t*)(uintptr_t)memCpu_[ranges[i].block] + ranges[i].off, ranges[i].size);
    }
    frames_++;
    if (inner_) inner_->Submit(s);
}

bool CaptureBackend::Readback(const NxReadback& r) {
    bool ok = inner_ && inner_->Readback(r);
    NxReadback c = r;
    c.dst = 0;
    uint32_t bytes = ok ? r.w * r.h * nx_readback_bpp(r.format) : 0;
    Rec(kCapReadback, sizeof c + 4 + pad4(bytes)); Put(&c, sizeof c); Put(&bytes, 4);
    if (bytes) Put((const void*)(uintptr_t)r.dst, bytes);
    return ok;
}

void CaptureBackend::Shutdown() { if (inner_) inner_->Shutdown(); Close(); }

} // namespace nx
