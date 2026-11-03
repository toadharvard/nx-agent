// fnv-nx: guest (ILP32) side of the D3D9 -> Gallium Nine proxy (stream S6c-nine). Used by the generated
// nine_proxy_guest.cpp; see gen_proxy.py for the model.
#ifndef FNVNX_NINE_PROXY_H
#define FNVNX_NINE_PROXY_H
#include <stdint.h>
#include "fnvnx/hostapi.h"

#include "nine_ext.h"

// Host call through S5's generic extension slot (host API v3): ext_call(NINE_EXT_MODULE, method id,
// {remote LP64 object, address of uint64_t args[]}, 2). Method id = interface index << 8 | vtable slot.
static inline uint64_t nx_call(uint64_t remote, uint32_t method, uint64_t args) {
    uint64_t blk[2] = {remote, args};
    return fnvnx_hcall4(fnvnx_host->ext_call, NINE_EXT_MODULE, method, FNVNX_ADDR(blk), 2);
}

struct NxProxy {
    uint32_t remote;        // LP64 object address: below 4 GiB (32-bit address space), so 32 bits hold it
    explicit NxProxy(uint64_t r) : remote((uint32_t)r) {}
    virtual ~NxProxy() {}   // Px layout: interface vptr +0, NxProxy vptr +4, remote +8 (all 4-aligned)
};
// Interface in: proxy -> remote (0 for NULL). Every proxy is (interface vptr, NxProxy) with single
// inheritance, so 'remote' sits at the same offset (8: vptr 4 + alignment) in every proxy class; a base-interface
// pointer (IDirect3DBaseTexture9* to a cube texture proxy) unwraps the same way. Checked in nine_proxy_rt.cpp.
static inline uint64_t nx_unwrap(const void* p) {
    return p ? *(const uint32_t*)((const char*)p + 8) : 0;
}
// Implemented in nine_proxy_rt.cpp: per-interface remote -> unique proxy map (identity for COM equality and
// the engine's pointer comparisons), created on first sight, removed when Release reaches 0.
#define NX_WRAP_DECL(I) I* nx_wrap_##I(uint64_t remote);
NX_WRAP_DECL(IDirect3D9) NX_WRAP_DECL(IDirect3DDevice9) NX_WRAP_DECL(IDirect3DSwapChain9) NX_WRAP_DECL(IDirect3DSurface9)
NX_WRAP_DECL(IDirect3DVolume9) NX_WRAP_DECL(IDirect3DTexture9) NX_WRAP_DECL(IDirect3DCubeTexture9)
NX_WRAP_DECL(IDirect3DVolumeTexture9) NX_WRAP_DECL(IDirect3DBaseTexture9) NX_WRAP_DECL(IDirect3DVertexBuffer9)
NX_WRAP_DECL(IDirect3DIndexBuffer9) NX_WRAP_DECL(IDirect3DVertexDeclaration9) NX_WRAP_DECL(IDirect3DVertexShader9)
NX_WRAP_DECL(IDirect3DPixelShader9) NX_WRAP_DECL(IDirect3DStateBlock9) NX_WRAP_DECL(IDirect3DQuery9)
NX_WRAP_DECL(IDirect3DResource9) NX_WRAP_DECL(IDirect3D9Ex) NX_WRAP_DECL(IDirect3DDevice9Ex)
NX_WRAP_DECL(IDirect3DSwapChain9Ex)

// Wide (LP64-layout) copies of the structs with pointer members, and their converters.
struct NxW_D3DLOCKED_RECT { int32_t Pitch; uint32_t pad; uint64_t pBits; };
struct NxW_D3DLOCKED_BOX { int32_t RowPitch, SlicePitch; uint64_t pBits; };
struct NxW_D3DPRESENT_PARAMETERS {
    uint32_t BackBufferWidth, BackBufferHeight, BackBufferFormat, BackBufferCount, MultiSampleType,
             MultiSampleQuality, SwapEffect, pad0;
    uint64_t hDeviceWindow;
    int32_t Windowed, EnableAutoDepthStencil;
    uint32_t AutoDepthStencilFormat, Flags, FullScreen_RefreshRateInHz, PresentationInterval;
};
struct NxW_D3DDEVICE_CREATION_PARAMETERS { uint32_t AdapterOrdinal, DeviceType; uint64_t hFocusWindow; uint32_t BehaviorFlags, pad; };
static_assert(sizeof(NxW_D3DLOCKED_RECT) == 16 && sizeof(NxW_D3DLOCKED_BOX) == 16, "wide lock structs");
static_assert(sizeof(NxW_D3DPRESENT_PARAMETERS) == 64 && sizeof(NxW_D3DDEVICE_CREATION_PARAMETERS) == 24, "wide params");

static inline void nx_to_wide_D3DLOCKED_RECT(NxW_D3DLOCKED_RECT* w, const D3DLOCKED_RECT* n) { w->Pitch = n->Pitch; w->pad = 0; w->pBits = FNVNX_ADDR(n->pBits); }
static inline void nx_from_wide_D3DLOCKED_RECT(D3DLOCKED_RECT* n, const NxW_D3DLOCKED_RECT* w) { n->Pitch = w->Pitch; n->pBits = (void*)(uintptr_t)w->pBits; }
static inline void nx_to_wide_D3DLOCKED_BOX(NxW_D3DLOCKED_BOX* w, const D3DLOCKED_BOX* n) { w->RowPitch = n->RowPitch; w->SlicePitch = n->SlicePitch; w->pBits = FNVNX_ADDR(n->pBits); }
static inline void nx_from_wide_D3DLOCKED_BOX(D3DLOCKED_BOX* n, const NxW_D3DLOCKED_BOX* w) { n->RowPitch = w->RowPitch; n->SlicePitch = w->SlicePitch; n->pBits = (void*)(uintptr_t)w->pBits; }
static inline void nx_to_wide_D3DPRESENT_PARAMETERS(NxW_D3DPRESENT_PARAMETERS* w, const D3DPRESENT_PARAMETERS* n) {
    w->BackBufferWidth = n->BackBufferWidth; w->BackBufferHeight = n->BackBufferHeight; w->BackBufferFormat = n->BackBufferFormat;
    w->BackBufferCount = n->BackBufferCount; w->MultiSampleType = n->MultiSampleType; w->MultiSampleQuality = n->MultiSampleQuality;
    w->SwapEffect = n->SwapEffect; w->pad0 = 0; w->hDeviceWindow = FNVNX_ADDR(n->hDeviceWindow); w->Windowed = n->Windowed;
    w->EnableAutoDepthStencil = n->EnableAutoDepthStencil; w->AutoDepthStencilFormat = n->AutoDepthStencilFormat;
    w->Flags = n->Flags; w->FullScreen_RefreshRateInHz = n->FullScreen_RefreshRateInHz; w->PresentationInterval = n->PresentationInterval;
}
static inline void nx_from_wide_D3DPRESENT_PARAMETERS(D3DPRESENT_PARAMETERS* n, const NxW_D3DPRESENT_PARAMETERS* w) {
    n->BackBufferWidth = w->BackBufferWidth; n->BackBufferHeight = w->BackBufferHeight; n->BackBufferFormat = (D3DFORMAT)w->BackBufferFormat;
    n->BackBufferCount = w->BackBufferCount; n->MultiSampleType = (D3DMULTISAMPLE_TYPE)w->MultiSampleType;
    n->MultiSampleQuality = w->MultiSampleQuality; n->SwapEffect = (D3DSWAPEFFECT)w->SwapEffect;
    n->hDeviceWindow = (HWND)(uintptr_t)w->hDeviceWindow; n->Windowed = w->Windowed;
    n->EnableAutoDepthStencil = w->EnableAutoDepthStencil; n->AutoDepthStencilFormat = (D3DFORMAT)w->AutoDepthStencilFormat;
    n->Flags = w->Flags; n->FullScreen_RefreshRateInHz = w->FullScreen_RefreshRateInHz; n->PresentationInterval = w->PresentationInterval;
}
static inline void nx_to_wide_D3DDEVICE_CREATION_PARAMETERS(NxW_D3DDEVICE_CREATION_PARAMETERS* w, const D3DDEVICE_CREATION_PARAMETERS* n) {
    w->AdapterOrdinal = n->AdapterOrdinal; w->DeviceType = n->DeviceType; w->hFocusWindow = FNVNX_ADDR(n->hFocusWindow); w->BehaviorFlags = n->BehaviorFlags; w->pad = 0;
}
static inline void nx_from_wide_D3DDEVICE_CREATION_PARAMETERS(D3DDEVICE_CREATION_PARAMETERS* n, const NxW_D3DDEVICE_CREATION_PARAMETERS* w) {
    n->AdapterOrdinal = w->AdapterOrdinal; n->DeviceType = (D3DDEVTYPE)w->DeviceType; n->hFocusWindow = (HWND)(uintptr_t)w->hFocusWindow; n->BehaviorFlags = w->BehaviorFlags;
}
#endif
