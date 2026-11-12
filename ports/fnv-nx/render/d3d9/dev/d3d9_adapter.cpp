// d3d9_adapter.cpp: NxDirect3D9 / NxDirect3D9Ex methods that are environment answers. Reference: Wine 9.0 dlls/d3d9/directx.c (the function named at each method).
// The profile (handheld / docked) is chosen once at creation and kept as a private-data entry of the object (the class layout is fixed by the generated header;
// IDirect3D9 has no SetPrivateData in its interface, so the entry is unreachable from the application).
#include <new>
#include "../gen/d9impl.gen.h"
#include "d3d9_adapter.h"
#include "const_fixme.h"
#include "../gen/env_table.gen.h"

static const GUID G_NXENV_PROFILE = { 0x6e78656e, 0x7670, 0x4d39, { 0x90, 0x01, 0x6e, 0x78, 0x65, 0x6e, 0x76, 0x70 } };
static int prof(NxObj* o) { uint32_t p = 0; DWORD sz = sizeof p; return SUCCEEDED(o->getPrivateData(G_NXENV_PROFILE, &p, &sz)) ? (int)p : 0; }
#define M(c, self) static_cast<NxObj*>(static_cast<c*>(self))
static void to_mode(D3DDISPLAYMODE* d, const NxEnvMode& m) { d->Width = m.Width; d->Height = m.Height; d->RefreshRate = m.RefreshRate; d->Format = (D3DFORMAT)m.Format; }
static void to_modex(D3DDISPLAYMODEEX* d, const NxEnvMode& m, uint32_t scan) { d->Width = m.Width; d->Height = m.Height; d->RefreshRate = m.RefreshRate; d->Format = (D3DFORMAT)m.Format; d->ScanLineOrdering = (D3DSCANLINEORDERING)scan; }

// ---- the answers shared by both classes (the Ex object is the same d3d9 object with extended = TRUE in Wine)
static UINT a_count(int p) { return nxenv_adapter_count(p); }
static HRESULT a_ident(int p, UINT ad, DWORD fl, D3DADAPTER_IDENTIFIER9* o) {   // d3d9_GetAdapterIdentifier
    if (!o) return D3DERR_INVALIDCALL;
    return (HRESULT)nxenv_identifier(p, ad, fl, o);
}
static UINT a_mcount(int p, UINT ad, D3DFORMAT f) { return nxenv_mode_count(p, ad, (uint32_t)f); }                                      // d3d9_GetAdapterModeCount
static HRESULT a_enum(int p, UINT ad, D3DFORMAT f, UINT i, D3DDISPLAYMODE* o) {                                                        // d3d9_EnumAdapterModes
    NxEnvMode m; if (!o) return D3DERR_INVALIDCALL; HRESULT hr = (HRESULT)nxenv_enum_mode(p, ad, (uint32_t)f, i, &m); if (SUCCEEDED(hr)) to_mode(o, m); return hr;
}
static HRESULT a_disp(int p, UINT ad, D3DDISPLAYMODE* o) {                                                                              // d3d9_GetAdapterDisplayMode
    NxEnvMode m; if (!o) return D3DERR_INVALIDCALL; HRESULT hr = (HRESULT)nxenv_display_mode(p, ad, &m); if (SUCCEEDED(hr)) to_mode(o, m); return hr;
}
static HRESULT a_caps(int p, UINT ad, D3DDEVTYPE t, D3DCAPS9* o) { static_assert(sizeof(D3DCAPS9) == NXENV_CAPS_SIZE, "D3DCAPS9"); if (!o) return D3DERR_INVALIDCALL; return (HRESULT)nxenv_caps(p, ad, (uint32_t)t, o); }
static HRESULT a_msaa(int p, UINT ad, D3DDEVTYPE t, D3DFORMAT f, BOOL w, D3DMULTISAMPLE_TYPE ms, DWORD* q) {                              // d3d9_CheckDeviceMultiSampleType (levels optional)
    return (HRESULT)nxenv_check_msaa(p, ad, (uint32_t)t, (uint32_t)f, w ? 1 : 0, (uint32_t)ms, (uint32_t*)q);
}
static UINT a_mcount_ex(int p, UINT ad, const D3DDISPLAYMODEFILTER* f) { return f ? nxenv_mode_count_ex(p, ad, (uint32_t)f->Format, (uint32_t)f->ScanLineOrdering) : 0; }   // d3d9_GetAdapterModeCountEx
static HRESULT a_enum_ex(int p, UINT ad, const D3DDISPLAYMODEFILTER* f, UINT i, D3DDISPLAYMODEEX* o) {                                  // d3d9_EnumAdapterModesEx
    NxEnvMode m; uint32_t sc = 0; if (!f || !o) return D3DERR_INVALIDCALL;
    HRESULT hr = (HRESULT)nxenv_enum_mode_ex(p, ad, (uint32_t)f->Format, (uint32_t)f->ScanLineOrdering, i, &m, &sc); if (SUCCEEDED(hr)) to_modex(o, m, sc); return hr;
}
static HRESULT a_disp_ex(int p, UINT ad, D3DDISPLAYMODEEX* o, D3DDISPLAYROTATION* rot) {                                                // d3d9_GetAdapterDisplayModeEx
    NxEnvMode m; uint32_t sc = 0, ro = 0; if (!o) return D3DERR_INVALIDCALL;
    HRESULT hr = (HRESULT)nxenv_display_mode_ex(p, ad, o->Size, &m, &sc, &ro); if (SUCCEEDED(hr)) { to_modex(o, m, sc); if (rot) *rot = (D3DDISPLAYROTATION)ro; } return hr;
}
static HRESULT a_luid(int p, UINT ad, LUID* o) { uint32_t lo; int32_t hi; if (!o) return D3DERR_INVALIDCALL; HRESULT hr = (HRESULT)nxenv_luid(p, ad, &lo, &hi); if (SUCCEEDED(hr)) { o->LowPart = lo; o->HighPart = hi; } return hr; }

// d3d9_RegisterSoftwareDevice -> wined3d_register_software_device (wine-9.0 wined3d/directx.c): `FIXME("wined3d %p, init_function %p stub!"); return WINED3D_OK;` = id 51; the answer is D3D_OK. A registered software device
// changes nothing in the table: CheckDeviceType / GetDeviceCaps for D3DDEVTYPE_SW answer as recorded (the recording calls it before the first query).
#define ENV_METHODS(C)                                                                                                                                                  \
    HRESULT STDMETHODCALLTYPE C::RegisterSoftwareDevice(void*) { fnvnx_wine_fixme(NXW_wined3d_register_software_device); return D3D_OK; }                                                                                       \
    UINT STDMETHODCALLTYPE C::GetAdapterCount() { return a_count(prof(M(C, this))); }                                                                                   \
    HRESULT STDMETHODCALLTYPE C::GetAdapterIdentifier(UINT Adapter, DWORD Flags, D3DADAPTER_IDENTIFIER9* pIdentifier) { return a_ident(prof(M(C, this)), Adapter, Flags, pIdentifier); } \
    UINT STDMETHODCALLTYPE C::GetAdapterModeCount(UINT Adapter, D3DFORMAT Format) { return a_mcount(prof(M(C, this)), Adapter, Format); }                                \
    HRESULT STDMETHODCALLTYPE C::EnumAdapterModes(UINT Adapter, D3DFORMAT Format, UINT Mode, D3DDISPLAYMODE* pMode) { return a_enum(prof(M(C, this)), Adapter, Format, Mode, pMode); } \
    HRESULT STDMETHODCALLTYPE C::GetAdapterDisplayMode(UINT Adapter, D3DDISPLAYMODE* pMode) { return a_disp(prof(M(C, this)), Adapter, pMode); }                          \
    HRESULT STDMETHODCALLTYPE C::CheckDeviceType(UINT iAdapter, D3DDEVTYPE DevType, D3DFORMAT DisplayFormat, D3DFORMAT BackBufferFormat, BOOL bWindowed) {                \
        return (HRESULT)nxenv_check_device_type(prof(M(C, this)), iAdapter, (uint32_t)DevType, (uint32_t)DisplayFormat, (uint32_t)BackBufferFormat, bWindowed ? 1 : 0); }      \
    HRESULT STDMETHODCALLTYPE C::CheckDeviceFormat(UINT Adapter, D3DDEVTYPE DeviceType, D3DFORMAT AdapterFormat, DWORD Usage, D3DRESOURCETYPE RType, D3DFORMAT CheckFormat) { \
        return (HRESULT)nxenv_check_device_format(prof(M(C, this)), Adapter, (uint32_t)DeviceType, (uint32_t)AdapterFormat, Usage, (uint32_t)RType, (uint32_t)CheckFormat); }   \
    HRESULT STDMETHODCALLTYPE C::CheckDeviceMultiSampleType(UINT Adapter, D3DDEVTYPE DeviceType, D3DFORMAT SurfaceFormat, BOOL Windowed, D3DMULTISAMPLE_TYPE MultiSampleType, DWORD* pQualityLevels) { \
        return a_msaa(prof(M(C, this)), Adapter, DeviceType, SurfaceFormat, Windowed, MultiSampleType, pQualityLevels); }                                               \
    HRESULT STDMETHODCALLTYPE C::CheckDepthStencilMatch(UINT Adapter, D3DDEVTYPE DeviceType, D3DFORMAT AdapterFormat, D3DFORMAT RenderTargetFormat, D3DFORMAT DepthStencilFormat) { \
        return (HRESULT)nxenv_check_ds_match(prof(M(C, this)), Adapter, (uint32_t)DeviceType, (uint32_t)AdapterFormat, (uint32_t)RenderTargetFormat, (uint32_t)DepthStencilFormat); } \
    HRESULT STDMETHODCALLTYPE C::CheckDeviceFormatConversion(UINT Adapter, D3DDEVTYPE DeviceType, D3DFORMAT SourceFormat, D3DFORMAT TargetFormat) {                       \
        return (HRESULT)nxenv_check_conversion(prof(M(C, this)), Adapter, (uint32_t)DeviceType, (uint32_t)SourceFormat, (uint32_t)TargetFormat); }                       \
    HRESULT STDMETHODCALLTYPE C::GetDeviceCaps(UINT Adapter, D3DDEVTYPE DeviceType, D3DCAPS9* pCaps) { return a_caps(prof(M(C, this)), Adapter, DeviceType, pCaps); }     \
    HMONITOR STDMETHODCALLTYPE C::GetAdapterMonitor(UINT Adapter) { return (HMONITOR)(size_t)nxenv_monitor(prof(M(C, this)), Adapter); }
ENV_METHODS(NxDirect3D9)
ENV_METHODS(NxDirect3D9Ex)
UINT STDMETHODCALLTYPE NxDirect3D9Ex::GetAdapterModeCountEx(UINT adapter_idx, const D3DDISPLAYMODEFILTER* filter) { return a_mcount_ex(prof(M(NxDirect3D9Ex, this)), adapter_idx, filter); }
HRESULT STDMETHODCALLTYPE NxDirect3D9Ex::EnumAdapterModesEx(UINT adapter_idx, const D3DDISPLAYMODEFILTER* filter, UINT mode_idx, D3DDISPLAYMODEEX* mode) { return a_enum_ex(prof(M(NxDirect3D9Ex, this)), adapter_idx, filter, mode_idx, mode); }
HRESULT STDMETHODCALLTYPE NxDirect3D9Ex::GetAdapterDisplayModeEx(UINT adapter_idx, D3DDISPLAYMODEEX* mode, D3DDISPLAYROTATION* rotation) { return a_disp_ex(prof(M(NxDirect3D9Ex, this)), adapter_idx, mode, rotation); }
HRESULT STDMETHODCALLTYPE NxDirect3D9Ex::GetAdapterLUID(UINT adapter_idx, LUID* luid) { return a_luid(prof(M(NxDirect3D9Ex, this)), adapter_idx, luid); }

// ---- creation (d3d9_main.c Direct3DCreate9 / Direct3DCreate9Ex -> d3d9_init: refcount 1, the answers are those of the profile)
// Allocation failure (README "Allocation failure"): Direct3DCreate9 returns NULL for both (d3d9_main.c: calloc and d3d9_init failure -> NULL); Direct3DCreate9Ex = E_OUTOFMEMORY for the object (calloc failure) and
// D3DERR_NOTAVAILABLE when the object cannot be initialised (d3d9_init failure; here: the profile entry); *out is null and the half-made object is released through its count.
template <class C> static HRESULT make(C** out) {
    C* c = nx_make<C>(); if (!c) return E_OUTOFMEMORY;   // NxObj::operator new: the object sits behind its guest face (face.h)
    NxObj* o = c; o->asRoot(1); uint32_t p = (uint32_t)nx_env_profile();
    if (FAILED(o->setPrivateData(G_NXENV_PROFILE, &p, sizeof p, 0))) { o->release(); return D3DERR_NOTAVAILABLE; }
    *out = c; return D3D_OK;
}
IDirect3D9* nx_Direct3DCreate9(UINT) { NxDirect3D9* c = nullptr; return SUCCEEDED(make<NxDirect3D9>(&c)) ? c : nullptr; }
HRESULT nx_Direct3DCreate9Ex(UINT, IDirect3D9Ex** out) { if (!out) return D3DERR_INVALIDCALL; NxDirect3D9Ex* c = nullptr; HRESULT hr = make<NxDirect3D9Ex>(&c); *out = SUCCEEDED(hr) ? c : nullptr; return hr; }
// D4 (res_tex.h): the profile of a device = the profile of the IDirect3D9 that created it
int nx_env_profile_of(NxObj* device) { return prof(device->owner); }
