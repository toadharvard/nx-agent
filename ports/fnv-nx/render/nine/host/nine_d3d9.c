/* fnv-nx: IDirect3D9 over Gallium Nine's ID3DAdapter9 (stream S6c-nine). LP64 host side.
 * One adapter (the Tegra X1 through nouveau), display modes 1280x720 and 1920x1080 at 60 Hz in X8R8G8B8 / A8R8G8B8.
 * Everything format/caps related is answered by nine's adapter; this object only adds enumeration and CreateDevice
 * with our nwindow present group (nine_present.c). Our own code (the Wine-side d3d9-nine is not used). */
#include <stdlib.h>
#include <string.h>
#include "nine_host.h"

typedef struct NxD3D9 {
    IDirect3D9 iface;
    LONG refs;
    ID3DAdapter9* adapter;
} NxD3D9;

static const D3DDISPLAYMODE kModes[] = {
    {1280, 720, 60, D3DFMT_X8R8G8B8},
    {1920, 1080, 60, D3DFMT_X8R8G8B8},
};
#define NMODES (sizeof kModes / sizeof kModes[0])

static NxD3D9* self(IDirect3D9* p) { return (NxD3D9*)p; }

static HRESULT WINAPI d_QueryInterface(IDirect3D9* This, REFIID riid, void** ppv) {
    if (!ppv) return E_POINTER;
    if (!memcmp(riid, &IID_IDirect3D9, sizeof(GUID)) || !memcmp(riid, &IID_IUnknown, sizeof(GUID))) {
        *ppv = This; This->lpVtbl->AddRef(This); return S_OK;
    }
    *ppv = NULL; return E_NOINTERFACE;
}
static ULONG WINAPI d_AddRef(IDirect3D9* This) { return (ULONG)__atomic_add_fetch(&self(This)->refs, 1, __ATOMIC_SEQ_CST); }
static ULONG WINAPI d_Release(IDirect3D9* This) {
    LONG r = __atomic_sub_fetch(&self(This)->refs, 1, __ATOMIC_SEQ_CST);
    if (!r) { ID3DAdapter9_Release(self(This)->adapter); free(This); }
    return (ULONG)r;
}
static HRESULT WINAPI d_RegisterSoftwareDevice(IDirect3D9* This, void* f) { (void)This; (void)f; return D3DERR_NOTAVAILABLE; }
static UINT WINAPI d_GetAdapterCount(IDirect3D9* This) { (void)This; return 1; }
static HRESULT WINAPI d_GetAdapterIdentifier(IDirect3D9* This, UINT a, DWORD flags, D3DADAPTER_IDENTIFIER9* id) {
    if (a != 0 || !id) return D3DERR_INVALIDCALL;
    return ID3DAdapter9_GetAdapterIdentifier(self(This)->adapter, flags, id);
}
static int fmt_ok(D3DFORMAT f) { return f == D3DFMT_X8R8G8B8 || f == D3DFMT_A8R8G8B8; }
static UINT WINAPI d_GetAdapterModeCount(IDirect3D9* This, UINT a, D3DFORMAT f) { (void)This; return a == 0 && fmt_ok(f) ? NMODES : 0; }
static HRESULT WINAPI d_EnumAdapterModes(IDirect3D9* This, UINT a, D3DFORMAT f, UINT m, D3DDISPLAYMODE* out) {
    (void)This;
    if (a != 0 || !fmt_ok(f) || m >= NMODES || !out) return D3DERR_INVALIDCALL;
    *out = kModes[m]; out->Format = f; return D3D_OK;
}
static HRESULT WINAPI d_GetAdapterDisplayMode(IDirect3D9* This, UINT a, D3DDISPLAYMODE* out) {
    (void)This;
    if (a != 0 || !out) return D3DERR_INVALIDCALL;
    int w, h; nx_present_screen_size(&w, &h);
    *out = kModes[h >= 1080 ? 1 : 0];
    return D3D_OK;
}
static HRESULT WINAPI d_CheckDeviceType(IDirect3D9* This, UINT a, D3DDEVTYPE t, D3DFORMAT af, D3DFORMAT bf, BOOL win) {
    if (a != 0) return D3DERR_INVALIDCALL;
    return ID3DAdapter9_CheckDeviceType(self(This)->adapter, t, af, bf, win);
}
static HRESULT WINAPI d_CheckDeviceFormat(IDirect3D9* This, UINT a, D3DDEVTYPE t, D3DFORMAT af, DWORD u, D3DRESOURCETYPE rt, D3DFORMAT cf) {
    if (a != 0) return D3DERR_INVALIDCALL;
    return ID3DAdapter9_CheckDeviceFormat(self(This)->adapter, t, af, u, rt, cf);
}
static HRESULT WINAPI d_CheckDeviceMultiSampleType(IDirect3D9* This, UINT a, D3DDEVTYPE t, D3DFORMAT sf, BOOL w, D3DMULTISAMPLE_TYPE ms, DWORD* q) {
    if (a != 0) return D3DERR_INVALIDCALL;
    return ID3DAdapter9_CheckDeviceMultiSampleType(self(This)->adapter, t, sf, w, ms, q);
}
static HRESULT WINAPI d_CheckDepthStencilMatch(IDirect3D9* This, UINT a, D3DDEVTYPE t, D3DFORMAT af, D3DFORMAT rf, D3DFORMAT df) {
    if (a != 0) return D3DERR_INVALIDCALL;
    return ID3DAdapter9_CheckDepthStencilMatch(self(This)->adapter, t, af, rf, df);
}
static HRESULT WINAPI d_CheckDeviceFormatConversion(IDirect3D9* This, UINT a, D3DDEVTYPE t, D3DFORMAT sf, D3DFORMAT tf) {
    if (a != 0) return D3DERR_INVALIDCALL;
    return ID3DAdapter9_CheckDeviceFormatConversion(self(This)->adapter, t, sf, tf);
}
static HRESULT WINAPI d_GetDeviceCaps(IDirect3D9* This, UINT a, D3DDEVTYPE t, D3DCAPS9* caps) {
    if (a != 0 || !caps) return D3DERR_INVALIDCALL;
    HRESULT hr = ID3DAdapter9_GetDeviceCaps(self(This)->adapter, t, caps);
    if (SUCCEEDED(hr)) caps->AdapterOrdinal = 0;
    return hr;
}
static HMONITOR WINAPI d_GetAdapterMonitor(IDirect3D9* This, UINT a) { (void)This; return a == 0 ? (HMONITOR)(uintptr_t)0x4D4F4E31 : NULL; }
static HRESULT WINAPI d_CreateDevice(IDirect3D9* This, UINT a, D3DDEVTYPE t, HWND focus, DWORD flags,
                                     D3DPRESENT_PARAMETERS* pp, IDirect3DDevice9** out) {
    if (a != 0 || !pp || !out) return D3DERR_INVALIDCALL;
    ID3DPresentGroup* group = NULL;
    HRESULT hr = nx_present_group_create(pp, &group);
    if (FAILED(hr)) return hr;
    hr = ID3DAdapter9_CreateDevice(self(This)->adapter, 0, t, focus, flags, pp, This, group, out);
    /* On success the device owns our reference: NineDevice9_ctor takes and drops one of its own, and its dtor
     * releases the group once (the Wine-side d3d9 hands the reference over the same way). Found by the gate. */
    if (FAILED(hr)) ID3DPresentGroup_Release(group);
    return hr;
}

static IDirect3D9Vtbl g_vtbl = {
    d_QueryInterface, d_AddRef, d_Release, d_RegisterSoftwareDevice, d_GetAdapterCount, d_GetAdapterIdentifier,
    d_GetAdapterModeCount, d_EnumAdapterModes, d_GetAdapterDisplayMode, d_CheckDeviceType, d_CheckDeviceFormat,
    d_CheckDeviceMultiSampleType, d_CheckDepthStencilMatch, d_CheckDeviceFormatConversion, d_GetDeviceCaps,
    d_GetAdapterMonitor, d_CreateDevice,
};

IDirect3D9* nx_direct3d_create9(UINT sdk) {
    (void)sdk;
    NxD3D9* d = calloc(1, sizeof *d);
    if (!d) return NULL;
    if (FAILED(nx_nine_create_adapter(&d->adapter))) { free(d); return NULL; }
    d->iface.lpVtbl = &g_vtbl;
    d->refs = 1;
    return &d->iface;
}
