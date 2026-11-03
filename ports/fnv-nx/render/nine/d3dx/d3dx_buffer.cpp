// fnv-nx D3DX9 (stream S6c-nine, guest ILP32): ID3DXBuffer and D3DXCreateBuffer.
#include <malloc.h>
#include <new>
#include "d3dx_internal.h"

namespace {

const GUID kIID_IUnknown = {0x00000000, 0x0000, 0x0000, {0xC0, 0, 0, 0, 0, 0, 0, 0x46}};

class Buffer final : public ID3DXBuffer {
public:
    explicit Buffer(DWORD size) : size_(size) {}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        if (!memcmp(&riid, &kIID_IUnknown, sizeof(GUID)) || !memcmp(&riid, &IID_ID3DXBuffer, sizeof(GUID))) {
            AddRef();
            *ppv = this;
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return __atomic_add_fetch(&refs_, 1, __ATOMIC_RELAXED); }
    ULONG STDMETHODCALLTYPE Release() override {
        ULONG n = __atomic_sub_fetch(&refs_, 1, __ATOMIC_ACQ_REL);
        if (!n) { this->~Buffer(); free(this); }
        return n;
    }
    void* STDMETHODCALLTYPE GetBufferPointer() override { return data(); }
    DWORD STDMETHODCALLTYPE GetBufferSize() override { return size_; }
    void* data() { return (uint8_t*)this + kHeader; }
    static constexpr size_t kHeader = 16;   // data 16-byte aligned behind the object (malloc gives 8 on ILP32)

private:
    ULONG refs_ = 1;
    DWORD size_;
};
static_assert(sizeof(Buffer) <= Buffer::kHeader, "Buffer header");

} // namespace

namespace fnvnx_d3dx {
// A buffer holding `size` bytes (copied from `src` when given).
ID3DXBuffer* make_buffer(const void* src, DWORD size) {
    void* mem = memalign(16, Buffer::kHeader + (size ? size : 1));
    if (!mem) return nullptr;
    Buffer* b = new (mem) Buffer(size);
    if (src) memcpy(b->data(), src, size);
    else memset(b->data(), 0, size);
    return b;
}
} // namespace fnvnx_d3dx

extern "C" HRESULT WINAPI D3DXCreateBuffer(DWORD NumBytes, LPD3DXBUFFER* ppBuffer) {
    if (!ppBuffer) return D3DERR_INVALIDCALL;
    *ppBuffer = fnvnx_d3dx::make_buffer(nullptr, NumBytes);
    return *ppBuffer ? D3D_OK : E_OUTOFMEMORY;
}
