/* fnv-nx shim: <d3d9.h> - the Direct3D 9 API (stream S6-render, docs/RENDER.md).
 *
 * COM interfaces are C++ abstract classes whose virtual methods are declared in the SDK's vtable order: the engine
 * calls them through the vtable (by slot, often through its own stand-in classes with padded vtables), so the
 * order, not the names, is the contract. The slot number of every method is noted in front of it. Under GCC's
 * Itanium C++ ABI a class with only (non-destructor) virtual functions and single inheritance has the same slot
 * layout as MSVC's, and with ILP32 every slot is 4 bytes, so a byte offset into a vtable (0xE4 = slot 57 =
 * IDirect3DDevice9::SetRenderState) means the same method on both. `this` travels in x0 like the explicit
 * `This` argument of the C (lpVtbl) binding, so the C-style calls the decomp also contains land on the same code.
 *
 * Implementations live in render/ (the D3D9-on-Switch layer), not in the decomp. Build with -DFNVNX_D3D_TRACE to
 * mark every method and function deprecated: each call site the compiler resolves is then reported, which is how
 * render/tools/census_render.sh counts the methods the engine uses.
 */
#ifndef FNVNX_SHIM_D3D9_H
#define FNVNX_SHIM_D3D9_H
#include "fnvnx_win32.h"

#ifndef DIRECT3D_VERSION
#define DIRECT3D_VERSION 0x0900
#endif

#ifdef FNVNX_D3D_TRACE
#define FNVNX_D3D_TRACED __attribute__((deprecated("fnvnx-d3d-trace")))
#else
#define FNVNX_D3D_TRACED
#endif
#define FNVNX_D3D_METHOD(ret, name) FNVNX_D3D_TRACED virtual ret STDMETHODCALLTYPE name
#define FNVNX_D3D_GUID(name, l, w1, w2, ...) static const GUID name = {l, w1, w2, __VA_ARGS__}

/* ---- COM and GDI pieces the D3D9 API names (not in fnvnx_win32.h yet; see docs/RENDER.md "Requests to S1") */
#ifndef FNVNX_IUNKNOWN_DEFINED
#define FNVNX_IUNKNOWN_DEFINED
struct IUnknown
{
    virtual HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **ppvObject) = 0;
    virtual ULONG STDMETHODCALLTYPE AddRef() = 0;
    virtual ULONG STDMETHODCALLTYPE Release() = 0;
};
typedef IUnknown *LPUNKNOWN;
#endif
#ifndef MAKE_HRESULT
#define MAKE_HRESULT(sev, fac, code) ((HRESULT)(((unsigned long)(sev) << 31) | ((unsigned long)(fac) << 16) | ((unsigned long)(code))))
#endif
#ifndef E_NOTIMPL
#define E_NOTIMPL ((HRESULT)0x80004001L)
#endif
#ifndef E_NOINTERFACE
#define E_NOINTERFACE ((HRESULT)0x80004002L)
#endif
#ifndef E_INVALIDARG
#define E_INVALIDARG ((HRESULT)0x80070057L)
#endif
struct _RGNDATA;
typedef struct _RGNDATA RGNDATA;
struct tagPALETTEENTRY;
typedef struct tagPALETTEENTRY PALETTEENTRY;
struct _LUID;
typedef struct _LUID LUID;

#include "d3d9types.h"
#include "d3d9caps.h"

/* ---- constants and error codes */
#define D3DCREATE_FPU_PRESERVE                  0x00000002L
#define D3DCREATE_MULTITHREADED                 0x00000004L
#define D3DCREATE_PUREDEVICE                    0x00000010L
#define D3DCREATE_SOFTWARE_VERTEXPROCESSING     0x00000020L
#define D3DCREATE_HARDWARE_VERTEXPROCESSING     0x00000040L
#define D3DCREATE_MIXED_VERTEXPROCESSING        0x00000080L
#define D3DCREATE_DISABLE_DRIVER_MANAGEMENT     0x00000100L
#define D3DCREATE_ADAPTERGROUP_DEVICE           0x00000200L
#define D3DCREATE_DISABLE_DRIVER_MANAGEMENT_EX  0x00000400L
#define D3DCREATE_NOWINDOWCHANGES               0x00000800L
#define D3DCREATE_DISABLE_PSGP_THREADING        0x00002000L
#define D3DCREATE_ENABLE_PRESENTSTATS           0x00004000L
#define D3DCREATE_DISABLE_PRINTSCREEN           0x00008000L
#define D3DCREATE_SCREENSAVER                   0x10000000L
#define D3DSPD_IUNKNOWN                         0x00000001L
#define D3D_SDK_VERSION                         32
#define D3DADAPTER_DEFAULT                      0
#define D3DENUM_WHQL_LEVEL                      0x00000002L
#define D3DPRESENT_DONOTWAIT                    1L
#define D3DPRESENT_LINEAR_CONTENT               2L
#define D3DPRESENT_BACK_BUFFERS_MAX             3L
#define D3DPRESENT_BACK_BUFFERS_MAX_EX          30L
#define D3DSGR_NO_CALIBRATION                   0x00000000L
#define D3DSGR_CALIBRATE                        0x00000001L
#define D3DCURSOR_IMMEDIATE_UPDATE              0x00000001L
#define _FACD3D  0x876
#define MAKE_D3DHRESULT( code )                 MAKE_HRESULT( 1, _FACD3D, code )
#define MAKE_D3DSTATUS( code )                  MAKE_HRESULT( 0, _FACD3D, code )
#define D3D_OK                                  S_OK
#define D3DERR_WRONGTEXTUREFORMAT               MAKE_D3DHRESULT(2072)
#define D3DERR_UNSUPPORTEDCOLOROPERATION        MAKE_D3DHRESULT(2073)
#define D3DERR_UNSUPPORTEDCOLORARG              MAKE_D3DHRESULT(2074)
#define D3DERR_UNSUPPORTEDALPHAOPERATION        MAKE_D3DHRESULT(2075)
#define D3DERR_UNSUPPORTEDALPHAARG              MAKE_D3DHRESULT(2076)
#define D3DERR_TOOMANYOPERATIONS                MAKE_D3DHRESULT(2077)
#define D3DERR_CONFLICTINGTEXTUREFILTER         MAKE_D3DHRESULT(2078)
#define D3DERR_UNSUPPORTEDFACTORVALUE           MAKE_D3DHRESULT(2079)
#define D3DERR_CONFLICTINGRENDERSTATE           MAKE_D3DHRESULT(2081)
#define D3DERR_UNSUPPORTEDTEXTUREFILTER         MAKE_D3DHRESULT(2082)
#define D3DERR_CONFLICTINGTEXTUREPALETTE        MAKE_D3DHRESULT(2086)
#define D3DERR_DRIVERINTERNALERROR              MAKE_D3DHRESULT(2087)
#define D3DERR_NOTFOUND                         MAKE_D3DHRESULT(2150)
#define D3DERR_MOREDATA                         MAKE_D3DHRESULT(2151)
#define D3DERR_DEVICELOST                       MAKE_D3DHRESULT(2152)
#define D3DERR_DEVICENOTRESET                   MAKE_D3DHRESULT(2153)
#define D3DERR_NOTAVAILABLE                     MAKE_D3DHRESULT(2154)
#define D3DERR_OUTOFVIDEOMEMORY                 MAKE_D3DHRESULT(380)
#define D3DERR_INVALIDDEVICE                    MAKE_D3DHRESULT(2155)
#define D3DERR_INVALIDCALL                      MAKE_D3DHRESULT(2156)
#define D3DERR_DRIVERINVALIDCALL                MAKE_D3DHRESULT(2157)
#define D3DERR_WASSTILLDRAWING                  MAKE_D3DHRESULT(540)
#define D3DOK_NOAUTOGEN                         MAKE_D3DSTATUS(2159)
#define D3DERR_DEVICEREMOVED                    MAKE_D3DHRESULT(2160)
#define D3DERR_DEVICEHUNG                       MAKE_D3DHRESULT(2164)
#define S_NOT_RESIDENT                          MAKE_D3DSTATUS(2165)
#define S_RESIDENT_IN_SHARED_MEMORY             MAKE_D3DSTATUS(2166)
#define S_PRESENT_MODE_CHANGED                  MAKE_D3DSTATUS(2167)
#define S_PRESENT_OCCLUDED                      MAKE_D3DSTATUS(2168)
#define D3DERR_UNSUPPORTEDOVERLAY               MAKE_D3DHRESULT(2171)
#define D3DERR_UNSUPPORTEDOVERLAYFORMAT         MAKE_D3DHRESULT(2172)
#define D3DERR_CANNOTPROTECTCONTENT             MAKE_D3DHRESULT(2173)
#define D3DERR_UNSUPPORTEDCRYPTO                MAKE_D3DHRESULT(2174)
#define D3DERR_PRESENT_STATISTICS_DISJOINT      MAKE_D3DHRESULT(2180)

/* ---- interface ids */
FNVNX_D3D_GUID(IID_IDirect3D9, 0x81BDCBCA, 0x64D4, 0x426D, {0xAE, 0x8D, 0xAD, 0x1, 0x47, 0xF4, 0x27, 0x5C});
FNVNX_D3D_GUID(IID_IDirect3DDevice9, 0xd0223b96, 0xbf7a, 0x43fd, {0x92, 0xbd, 0xa4, 0x3b, 0xd, 0x82, 0xb9, 0xeb});
FNVNX_D3D_GUID(IID_IDirect3DResource9, 0x5eec05d, 0x8f7d, 0x4362, {0xb9, 0x99, 0xd1, 0xba, 0xf3, 0x57, 0xc7, 0x4});
FNVNX_D3D_GUID(IID_IDirect3DVertexBuffer9, 0xb64bb1b5, 0xfd70, 0x4df6, {0xbf, 0x91, 0x19, 0xd0, 0xa1, 0x24, 0x55, 0xe3});
FNVNX_D3D_GUID(IID_IDirect3DVolume9, 0x24f416e6, 0x1f67, 0x4aa7, {0xb8, 0x8e, 0xd3, 0x3f, 0x6f, 0x31, 0x28, 0xa1});
FNVNX_D3D_GUID(IID_IDirect3DSwapChain9, 0x794950f2, 0xadfc, 0x458a, {0x90, 0x5e, 0x10, 0xa1, 0xb, 0xb, 0x50, 0x3b});
FNVNX_D3D_GUID(IID_IDirect3DSurface9, 0xcfbaf3a, 0x9ff6, 0x429a, {0x99, 0xb3, 0xa2, 0x79, 0x6a, 0xf8, 0xb8, 0x9b});
FNVNX_D3D_GUID(IID_IDirect3DIndexBuffer9, 0x7c9dd65e, 0xd3f7, 0x4529, {0xac, 0xee, 0x78, 0x58, 0x30, 0xac, 0xde, 0x35});
FNVNX_D3D_GUID(IID_IDirect3DBaseTexture9, 0x580ca87e, 0x1d3c, 0x4d54, {0x99, 0x1d, 0xb7, 0xd3, 0xe3, 0xc2, 0x98, 0xce});
FNVNX_D3D_GUID(IID_IDirect3DTexture9, 0x85c31227, 0x3de5, 0x4f00, {0x9b, 0x3a, 0xf1, 0x1a, 0xc3, 0x8c, 0x18, 0xb5});
FNVNX_D3D_GUID(IID_IDirect3DCubeTexture9, 0xfff32f81, 0xd953, 0x473a, {0x92, 0x23, 0x93, 0xd6, 0x52, 0xab, 0xa9, 0x3f});
FNVNX_D3D_GUID(IID_IDirect3DVolumeTexture9, 0x2518526c, 0xe789, 0x4111, {0xa7, 0xb9, 0x47, 0xef, 0x32, 0x8d, 0x13, 0xe6});
FNVNX_D3D_GUID(IID_IDirect3DVertexDeclaration9, 0xdd13c59c, 0x36fa, 0x4098, {0xa8, 0xfb, 0xc7, 0xed, 0x39, 0xdc, 0x85, 0x46});
FNVNX_D3D_GUID(IID_IDirect3DVertexShader9, 0xefc5557e, 0x6265, 0x4613, {0x8a, 0x94, 0x43, 0x85, 0x78, 0x89, 0xeb, 0x36});
FNVNX_D3D_GUID(IID_IDirect3DPixelShader9, 0x6d3bdbdc, 0x5b02, 0x4415, {0xb8, 0x52, 0xce, 0x5e, 0x8b, 0xcc, 0xb2, 0x89});
FNVNX_D3D_GUID(IID_IDirect3DStateBlock9, 0xb07c4fe5, 0x310d, 0x4ba8, {0xa2, 0x3c, 0x4f, 0xf, 0x20, 0x6f, 0x21, 0x8b});
FNVNX_D3D_GUID(IID_IDirect3DQuery9, 0xd9771460, 0xa695, 0x4f26, {0xbb, 0xd3, 0x27, 0xb8, 0x40, 0xb5, 0x41, 0xcc});
FNVNX_D3D_GUID(IID_IDirect3D9Ex, 0x02177241, 0x69fc, 0x400c, {0x8f, 0xf1, 0x93, 0xa4, 0x4d, 0xf6, 0x86, 0x1d});
FNVNX_D3D_GUID(IID_IDirect3DSwapChain9Ex, 0x91886caf, 0x1c3d, 0x4d2e, {0xa0, 0xab, 0x3e, 0x4c, 0x7d, 0x8d, 0x33, 0x3});
FNVNX_D3D_GUID(IID_IDirect3DDevice9Ex, 0xb18b10ce, 0x2649, 0x405a, {0x87, 0xf, 0x95, 0xf7, 0x77, 0xd4, 0x31, 0x3a});

/* ---- interfaces */
struct IDirect3D9; struct IDirect3DDevice9; struct IDirect3DResource9; struct IDirect3DVertexBuffer9;
struct IDirect3DVolume9; struct IDirect3DSwapChain9; struct IDirect3DSurface9; struct IDirect3DIndexBuffer9;
struct IDirect3DBaseTexture9; struct IDirect3DTexture9; struct IDirect3DCubeTexture9; struct IDirect3DVolumeTexture9;
struct IDirect3DVertexDeclaration9; struct IDirect3DVertexShader9; struct IDirect3DPixelShader9;
struct IDirect3DStateBlock9; struct IDirect3DQuery9; struct IDirect3D9Ex; struct IDirect3DSwapChain9Ex;
struct IDirect3DDevice9Ex;
typedef struct IDirect3D9 *LPDIRECT3D9, *PDIRECT3D9;
typedef struct IDirect3DDevice9 *LPDIRECT3DDEVICE9, *PDIRECT3DDEVICE9;
typedef struct IDirect3DResource9 *LPDIRECT3DRESOURCE9, *PDIRECT3DRESOURCE9;
typedef struct IDirect3DVertexBuffer9 *LPDIRECT3DVERTEXBUFFER9, *PDIRECT3DVERTEXBUFFER9;
typedef struct IDirect3DVolume9 *LPDIRECT3DVOLUME9, *PDIRECT3DVOLUME9;
typedef struct IDirect3DSwapChain9 *LPDIRECT3DSWAPCHAIN9, *PDIRECT3DSWAPCHAIN9;
typedef struct IDirect3DSurface9 *LPDIRECT3DSURFACE9, *PDIRECT3DSURFACE9;
typedef struct IDirect3DIndexBuffer9 *LPDIRECT3DINDEXBUFFER9, *PDIRECT3DINDEXBUFFER9;
typedef struct IDirect3DBaseTexture9 *LPDIRECT3DBASETEXTURE9, *PDIRECT3DBASETEXTURE9;
typedef struct IDirect3DTexture9 *LPDIRECT3DTEXTURE9, *PDIRECT3DTEXTURE9;
typedef struct IDirect3DCubeTexture9 *LPDIRECT3DCUBETEXTURE9, *PDIRECT3DCUBETEXTURE9;
typedef struct IDirect3DVolumeTexture9 *LPDIRECT3DVOLUMETEXTURE9, *PDIRECT3DVOLUMETEXTURE9;
typedef struct IDirect3DVertexDeclaration9 *LPDIRECT3DVERTEXDECLARATION9, *PDIRECT3DVERTEXDECLARATION9;
typedef struct IDirect3DVertexShader9 *LPDIRECT3DVERTEXSHADER9, *PDIRECT3DVERTEXSHADER9;
typedef struct IDirect3DPixelShader9 *LPDIRECT3DPIXELSHADER9, *PDIRECT3DPIXELSHADER9;
typedef struct IDirect3DStateBlock9 *LPDIRECT3DSTATEBLOCK9, *PDIRECT3DSTATEBLOCK9;
typedef struct IDirect3DQuery9 *LPDIRECT3DQUERY9, *PDIRECT3DQUERY9;
typedef struct IDirect3D9Ex *LPDIRECT3D9EX, *PDIRECT3D9EX;
typedef struct IDirect3DSwapChain9Ex *LPDIRECT3DSWAPCHAIN9EX, *PDIRECT3DSWAPCHAIN9EX;
typedef struct IDirect3DDevice9Ex *LPDIRECT3DDEVICE9EX, *PDIRECT3DDEVICE9EX;

struct IDirect3D9 : public IUnknown
{
    /*   3 */ FNVNX_D3D_METHOD(HRESULT, RegisterSoftwareDevice)(void* pInitializeFunction) = 0;
    /*   4 */ FNVNX_D3D_METHOD(UINT, GetAdapterCount)() = 0;
    /*   5 */ FNVNX_D3D_METHOD(HRESULT, GetAdapterIdentifier)(UINT Adapter, DWORD Flags, D3DADAPTER_IDENTIFIER9* pIdentifier) = 0;
    /*   6 */ FNVNX_D3D_METHOD(UINT, GetAdapterModeCount)(UINT Adapter, D3DFORMAT Format) = 0;
    /*   7 */ FNVNX_D3D_METHOD(HRESULT, EnumAdapterModes)(UINT Adapter, D3DFORMAT Format, UINT Mode, D3DDISPLAYMODE* pMode) = 0;
    /*   8 */ FNVNX_D3D_METHOD(HRESULT, GetAdapterDisplayMode)(UINT Adapter, D3DDISPLAYMODE* pMode) = 0;
    /*   9 */ FNVNX_D3D_METHOD(HRESULT, CheckDeviceType)(UINT iAdapter, D3DDEVTYPE DevType, D3DFORMAT DisplayFormat, D3DFORMAT BackBufferFormat, BOOL bWindowed) = 0;
    /*  10 */ FNVNX_D3D_METHOD(HRESULT, CheckDeviceFormat)(UINT Adapter, D3DDEVTYPE DeviceType, D3DFORMAT AdapterFormat, DWORD Usage, D3DRESOURCETYPE RType, D3DFORMAT CheckFormat) = 0;
    /*  11 */ FNVNX_D3D_METHOD(HRESULT, CheckDeviceMultiSampleType)(UINT Adapter, D3DDEVTYPE DeviceType, D3DFORMAT SurfaceFormat, BOOL Windowed, D3DMULTISAMPLE_TYPE MultiSampleType, DWORD* pQualityLevels) = 0;
    /*  12 */ FNVNX_D3D_METHOD(HRESULT, CheckDepthStencilMatch)(UINT Adapter, D3DDEVTYPE DeviceType, D3DFORMAT AdapterFormat, D3DFORMAT RenderTargetFormat, D3DFORMAT DepthStencilFormat) = 0;
    /*  13 */ FNVNX_D3D_METHOD(HRESULT, CheckDeviceFormatConversion)(UINT Adapter, D3DDEVTYPE DeviceType, D3DFORMAT SourceFormat, D3DFORMAT TargetFormat) = 0;
    /*  14 */ FNVNX_D3D_METHOD(HRESULT, GetDeviceCaps)(UINT Adapter, D3DDEVTYPE DeviceType, D3DCAPS9* pCaps) = 0;
    /*  15 */ FNVNX_D3D_METHOD(HMONITOR, GetAdapterMonitor)(UINT Adapter) = 0;
    /*  16 */ FNVNX_D3D_METHOD(HRESULT, CreateDevice)(UINT Adapter, D3DDEVTYPE DeviceType, HWND hFocusWindow, DWORD BehaviorFlags, D3DPRESENT_PARAMETERS* pPresentationParameters, struct IDirect3DDevice9** ppReturnedDeviceInterface) = 0;
};

struct IDirect3DVolume9 : public IUnknown
{
    /*   3 */ FNVNX_D3D_METHOD(HRESULT, GetDevice)(struct IDirect3DDevice9** ppDevice) = 0;
    /*   4 */ FNVNX_D3D_METHOD(HRESULT, SetPrivateData)(REFGUID guid, const void *data, DWORD data_size, DWORD flags) = 0;
    /*   5 */ FNVNX_D3D_METHOD(HRESULT, GetPrivateData)(REFGUID refguid, void* pData, DWORD* pSizeOfData) = 0;
    /*   6 */ FNVNX_D3D_METHOD(HRESULT, FreePrivateData)(REFGUID refguid) = 0;
    /*   7 */ FNVNX_D3D_METHOD(HRESULT, GetContainer)(REFIID riid, void** ppContainer) = 0;
    /*   8 */ FNVNX_D3D_METHOD(HRESULT, GetDesc)(D3DVOLUME_DESC* pDesc) = 0;
    /*   9 */ FNVNX_D3D_METHOD(HRESULT, LockBox)(D3DLOCKED_BOX *locked_box, const D3DBOX *box, DWORD flags) = 0;
    /*  10 */ FNVNX_D3D_METHOD(HRESULT, UnlockBox)() = 0;
};

struct IDirect3DSwapChain9 : public IUnknown
{
    /*   3 */ FNVNX_D3D_METHOD(HRESULT, Present)(const RECT *src_rect, const RECT *dst_rect, HWND dst_window_override, const RGNDATA *dirty_region, DWORD flags) = 0;
    /*   4 */ FNVNX_D3D_METHOD(HRESULT, GetFrontBufferData)(struct IDirect3DSurface9 *pDestSurface) = 0;
    /*   5 */ FNVNX_D3D_METHOD(HRESULT, GetBackBuffer)(UINT iBackBuffer, D3DBACKBUFFER_TYPE Type, struct IDirect3DSurface9 **ppBackBuffer) = 0;
    /*   6 */ FNVNX_D3D_METHOD(HRESULT, GetRasterStatus)(D3DRASTER_STATUS *pRasterStatus) = 0;
    /*   7 */ FNVNX_D3D_METHOD(HRESULT, GetDisplayMode)(D3DDISPLAYMODE *pMode) = 0;
    /*   8 */ FNVNX_D3D_METHOD(HRESULT, GetDevice)(struct IDirect3DDevice9 **ppDevice) = 0;
    /*   9 */ FNVNX_D3D_METHOD(HRESULT, GetPresentParameters)(D3DPRESENT_PARAMETERS *pPresentationParameters) = 0;
};

struct IDirect3DResource9 : public IUnknown
{
    /*   3 */ FNVNX_D3D_METHOD(HRESULT, GetDevice)(struct IDirect3DDevice9** ppDevice) = 0;
    /*   4 */ FNVNX_D3D_METHOD(HRESULT, SetPrivateData)(REFGUID guid, const void *data, DWORD data_size, DWORD flags) = 0;
    /*   5 */ FNVNX_D3D_METHOD(HRESULT, GetPrivateData)(REFGUID refguid, void* pData, DWORD* pSizeOfData) = 0;
    /*   6 */ FNVNX_D3D_METHOD(HRESULT, FreePrivateData)(REFGUID refguid) = 0;
    /*   7 */ FNVNX_D3D_METHOD(DWORD, SetPriority)(DWORD PriorityNew) = 0;
    /*   8 */ FNVNX_D3D_METHOD(DWORD, GetPriority)() = 0;
    /*   9 */ FNVNX_D3D_METHOD(void, PreLoad)() = 0;
    /*  10 */ FNVNX_D3D_METHOD(D3DRESOURCETYPE, GetType)() = 0;
};

struct IDirect3DSurface9 : public IDirect3DResource9
{
    /*  11 */ FNVNX_D3D_METHOD(HRESULT, GetContainer)(REFIID riid, void** ppContainer) = 0;
    /*  12 */ FNVNX_D3D_METHOD(HRESULT, GetDesc)(D3DSURFACE_DESC* pDesc) = 0;
    /*  13 */ FNVNX_D3D_METHOD(HRESULT, LockRect)(D3DLOCKED_RECT *locked_rect, const RECT *rect, DWORD flags) = 0;
    /*  14 */ FNVNX_D3D_METHOD(HRESULT, UnlockRect)() = 0;
    /*  15 */ FNVNX_D3D_METHOD(HRESULT, GetDC)(HDC* phdc) = 0;
    /*  16 */ FNVNX_D3D_METHOD(HRESULT, ReleaseDC)(HDC hdc) = 0;
};

struct IDirect3DVertexBuffer9 : public IDirect3DResource9
{
    /*  11 */ FNVNX_D3D_METHOD(HRESULT, Lock)(UINT OffsetToLock, UINT SizeToLock, void** ppbData, DWORD Flags) = 0;
    /*  12 */ FNVNX_D3D_METHOD(HRESULT, Unlock)() = 0;
    /*  13 */ FNVNX_D3D_METHOD(HRESULT, GetDesc)(D3DVERTEXBUFFER_DESC* pDesc) = 0;
};

struct IDirect3DIndexBuffer9 : public IDirect3DResource9
{
    /*  11 */ FNVNX_D3D_METHOD(HRESULT, Lock)(UINT OffsetToLock, UINT SizeToLock, void** ppbData, DWORD Flags) = 0;
    /*  12 */ FNVNX_D3D_METHOD(HRESULT, Unlock)() = 0;
    /*  13 */ FNVNX_D3D_METHOD(HRESULT, GetDesc)(D3DINDEXBUFFER_DESC* pDesc) = 0;
};

struct IDirect3DBaseTexture9 : public IDirect3DResource9
{
    /*  11 */ FNVNX_D3D_METHOD(DWORD, SetLOD)(DWORD LODNew) = 0;
    /*  12 */ FNVNX_D3D_METHOD(DWORD, GetLOD)() = 0;
    /*  13 */ FNVNX_D3D_METHOD(DWORD, GetLevelCount)() = 0;
    /*  14 */ FNVNX_D3D_METHOD(HRESULT, SetAutoGenFilterType)(D3DTEXTUREFILTERTYPE FilterType) = 0;
    /*  15 */ FNVNX_D3D_METHOD(D3DTEXTUREFILTERTYPE, GetAutoGenFilterType)() = 0;
    /*  16 */ FNVNX_D3D_METHOD(void, GenerateMipSubLevels)() = 0;
};

struct IDirect3DCubeTexture9 : public IDirect3DBaseTexture9
{
    /*  17 */ FNVNX_D3D_METHOD(HRESULT, GetLevelDesc)(UINT Level,D3DSURFACE_DESC* pDesc) = 0;
    /*  18 */ FNVNX_D3D_METHOD(HRESULT, GetCubeMapSurface)(D3DCUBEMAP_FACES FaceType, UINT Level, IDirect3DSurface9** ppCubeMapSurface) = 0;
    /*  19 */ FNVNX_D3D_METHOD(HRESULT, LockRect)(D3DCUBEMAP_FACES face, UINT level, D3DLOCKED_RECT *locked_rect, const RECT *rect, DWORD flags) = 0;
    /*  20 */ FNVNX_D3D_METHOD(HRESULT, UnlockRect)(D3DCUBEMAP_FACES FaceType, UINT Level) = 0;
    /*  21 */ FNVNX_D3D_METHOD(HRESULT, AddDirtyRect)(D3DCUBEMAP_FACES face, const RECT *dirty_rect) = 0;
};

struct IDirect3DTexture9 : public IDirect3DBaseTexture9
{
    /*  17 */ FNVNX_D3D_METHOD(HRESULT, GetLevelDesc)(UINT Level, D3DSURFACE_DESC* pDesc) = 0;
    /*  18 */ FNVNX_D3D_METHOD(HRESULT, GetSurfaceLevel)(UINT Level, IDirect3DSurface9** ppSurfaceLevel) = 0;
    /*  19 */ FNVNX_D3D_METHOD(HRESULT, LockRect)(UINT level, D3DLOCKED_RECT *locked_rect, const RECT *rect, DWORD flags) = 0;
    /*  20 */ FNVNX_D3D_METHOD(HRESULT, UnlockRect)(UINT Level) = 0;
    /*  21 */ FNVNX_D3D_METHOD(HRESULT, AddDirtyRect)(const RECT *dirty_rect) = 0;
};

struct IDirect3DVolumeTexture9 : public IDirect3DBaseTexture9
{
    /*  17 */ FNVNX_D3D_METHOD(HRESULT, GetLevelDesc)(UINT Level, D3DVOLUME_DESC *pDesc) = 0;
    /*  18 */ FNVNX_D3D_METHOD(HRESULT, GetVolumeLevel)(UINT Level, IDirect3DVolume9** ppVolumeLevel) = 0;
    /*  19 */ FNVNX_D3D_METHOD(HRESULT, LockBox)(UINT level, D3DLOCKED_BOX *locked_box, const D3DBOX *box, DWORD flags) = 0;
    /*  20 */ FNVNX_D3D_METHOD(HRESULT, UnlockBox)(UINT Level) = 0;
    /*  21 */ FNVNX_D3D_METHOD(HRESULT, AddDirtyBox)(const D3DBOX *dirty_box) = 0;
};

struct IDirect3DVertexDeclaration9 : public IUnknown
{
    /*   3 */ FNVNX_D3D_METHOD(HRESULT, GetDevice)(struct IDirect3DDevice9** ppDevice) = 0;
    /*   4 */ FNVNX_D3D_METHOD(HRESULT, GetDeclaration)(D3DVERTEXELEMENT9*, UINT* pNumElements) = 0;
};

struct IDirect3DVertexShader9 : public IUnknown
{
    /*   3 */ FNVNX_D3D_METHOD(HRESULT, GetDevice)(struct IDirect3DDevice9** ppDevice) = 0;
    /*   4 */ FNVNX_D3D_METHOD(HRESULT, GetFunction)(void*, UINT* pSizeOfData) = 0;
};

struct IDirect3DPixelShader9 : public IUnknown
{
    /*   3 */ FNVNX_D3D_METHOD(HRESULT, GetDevice)(struct IDirect3DDevice9** ppDevice) = 0;
    /*   4 */ FNVNX_D3D_METHOD(HRESULT, GetFunction)(void*, UINT* pSizeOfData) = 0;
};

struct IDirect3DStateBlock9 : public IUnknown
{
    /*   3 */ FNVNX_D3D_METHOD(HRESULT, GetDevice)(struct IDirect3DDevice9** ppDevice) = 0;
    /*   4 */ FNVNX_D3D_METHOD(HRESULT, Capture)() = 0;
    /*   5 */ FNVNX_D3D_METHOD(HRESULT, Apply)() = 0;
};

struct IDirect3DQuery9 : public IUnknown
{
    /*   3 */ FNVNX_D3D_METHOD(HRESULT, GetDevice)(struct IDirect3DDevice9** ppDevice) = 0;
    /*   4 */ FNVNX_D3D_METHOD(D3DQUERYTYPE, GetType)() = 0;
    /*   5 */ FNVNX_D3D_METHOD(DWORD, GetDataSize)() = 0;
    /*   6 */ FNVNX_D3D_METHOD(HRESULT, Issue)(DWORD dwIssueFlags) = 0;
    /*   7 */ FNVNX_D3D_METHOD(HRESULT, GetData)(void* pData, DWORD dwSize, DWORD dwGetDataFlags) = 0;
};

struct IDirect3DDevice9 : public IUnknown
{
    /*   3 */ FNVNX_D3D_METHOD(HRESULT, TestCooperativeLevel)() = 0;
    /*   4 */ FNVNX_D3D_METHOD(UINT, GetAvailableTextureMem)() = 0;
    /*   5 */ FNVNX_D3D_METHOD(HRESULT, EvictManagedResources)() = 0;
    /*   6 */ FNVNX_D3D_METHOD(HRESULT, GetDirect3D)(IDirect3D9** ppD3D9) = 0;
    /*   7 */ FNVNX_D3D_METHOD(HRESULT, GetDeviceCaps)(D3DCAPS9* pCaps) = 0;
    /*   8 */ FNVNX_D3D_METHOD(HRESULT, GetDisplayMode)(UINT iSwapChain, D3DDISPLAYMODE* pMode) = 0;
    /*   9 */ FNVNX_D3D_METHOD(HRESULT, GetCreationParameters)(D3DDEVICE_CREATION_PARAMETERS *pParameters) = 0;
    /*  10 */ FNVNX_D3D_METHOD(HRESULT, SetCursorProperties)(UINT XHotSpot, UINT YHotSpot, IDirect3DSurface9* pCursorBitmap) = 0;
    /*  11 */ FNVNX_D3D_METHOD(void, SetCursorPosition)(int X,int Y, DWORD Flags) = 0;
    /*  12 */ FNVNX_D3D_METHOD(BOOL, ShowCursor)(BOOL bShow) = 0;
    /*  13 */ FNVNX_D3D_METHOD(HRESULT, CreateAdditionalSwapChain)(D3DPRESENT_PARAMETERS* pPresentationParameters, IDirect3DSwapChain9** pSwapChain) = 0;
    /*  14 */ FNVNX_D3D_METHOD(HRESULT, GetSwapChain)(UINT iSwapChain, IDirect3DSwapChain9** pSwapChain) = 0;
    /*  15 */ FNVNX_D3D_METHOD(UINT, GetNumberOfSwapChains)() = 0;
    /*  16 */ FNVNX_D3D_METHOD(HRESULT, Reset)(D3DPRESENT_PARAMETERS* pPresentationParameters) = 0;
    /*  17 */ FNVNX_D3D_METHOD(HRESULT, Present)(const RECT *src_rect, const RECT *dst_rect, HWND dst_window_override, const RGNDATA *dirty_region) = 0;
    /*  18 */ FNVNX_D3D_METHOD(HRESULT, GetBackBuffer)(UINT iSwapChain, UINT iBackBuffer, D3DBACKBUFFER_TYPE Type, IDirect3DSurface9** ppBackBuffer) = 0;
    /*  19 */ FNVNX_D3D_METHOD(HRESULT, GetRasterStatus)(UINT iSwapChain, D3DRASTER_STATUS* pRasterStatus) = 0;
    /*  20 */ FNVNX_D3D_METHOD(HRESULT, SetDialogBoxMode)(BOOL bEnableDialogs) = 0;
    /*  21 */ FNVNX_D3D_METHOD(void, SetGammaRamp)(UINT swapchain_idx, DWORD flags, const D3DGAMMARAMP *ramp) = 0;
    /*  22 */ FNVNX_D3D_METHOD(void, GetGammaRamp)(UINT iSwapChain, D3DGAMMARAMP* pRamp) = 0;
    /*  23 */ FNVNX_D3D_METHOD(HRESULT, CreateTexture)(UINT Width, UINT Height, UINT Levels, DWORD Usage, D3DFORMAT Format, D3DPOOL Pool, IDirect3DTexture9** ppTexture, HANDLE* pSharedHandle) = 0;
    /*  24 */ FNVNX_D3D_METHOD(HRESULT, CreateVolumeTexture)(UINT Width, UINT Height, UINT Depth, UINT Levels, DWORD Usage, D3DFORMAT Format, D3DPOOL Pool, IDirect3DVolumeTexture9** ppVolumeTexture, HANDLE* pSharedHandle) = 0;
    /*  25 */ FNVNX_D3D_METHOD(HRESULT, CreateCubeTexture)(UINT EdgeLength, UINT Levels, DWORD Usage, D3DFORMAT Format, D3DPOOL Pool, IDirect3DCubeTexture9** ppCubeTexture, HANDLE* pSharedHandle) = 0;
    /*  26 */ FNVNX_D3D_METHOD(HRESULT, CreateVertexBuffer)(UINT Length, DWORD Usage, DWORD FVF, D3DPOOL Pool, IDirect3DVertexBuffer9** ppVertexBuffer, HANDLE* pSharedHandle) = 0;
    /*  27 */ FNVNX_D3D_METHOD(HRESULT, CreateIndexBuffer)(UINT Length, DWORD Usage, D3DFORMAT Format, D3DPOOL Pool, IDirect3DIndexBuffer9** ppIndexBuffer, HANDLE* pSharedHandle) = 0;
    /*  28 */ FNVNX_D3D_METHOD(HRESULT, CreateRenderTarget)(UINT Width, UINT Height, D3DFORMAT Format, D3DMULTISAMPLE_TYPE MultiSample, DWORD MultisampleQuality, BOOL Lockable, IDirect3DSurface9** ppSurface, HANDLE* pSharedHandle) = 0;
    /*  29 */ FNVNX_D3D_METHOD(HRESULT, CreateDepthStencilSurface)(UINT Width, UINT Height, D3DFORMAT Format, D3DMULTISAMPLE_TYPE MultiSample, DWORD MultisampleQuality, BOOL Discard, IDirect3DSurface9** ppSurface, HANDLE* pSharedHandle) = 0;
    /*  30 */ FNVNX_D3D_METHOD(HRESULT, UpdateSurface)(IDirect3DSurface9 *src_surface, const RECT *src_rect, IDirect3DSurface9 *dst_surface, const POINT *dst_point) = 0;
    /*  31 */ FNVNX_D3D_METHOD(HRESULT, UpdateTexture)(IDirect3DBaseTexture9* pSourceTexture, IDirect3DBaseTexture9* pDestinationTexture) = 0;
    /*  32 */ FNVNX_D3D_METHOD(HRESULT, GetRenderTargetData)(IDirect3DSurface9* pRenderTarget, IDirect3DSurface9* pDestSurface) = 0;
    /*  33 */ FNVNX_D3D_METHOD(HRESULT, GetFrontBufferData)(UINT iSwapChain, IDirect3DSurface9* pDestSurface) = 0;
    /*  34 */ FNVNX_D3D_METHOD(HRESULT, StretchRect)(IDirect3DSurface9 *src_surface, const RECT *src_rect, IDirect3DSurface9 *dst_surface, const RECT *dst_rect, D3DTEXTUREFILTERTYPE filter) = 0;
    /*  35 */ FNVNX_D3D_METHOD(HRESULT, ColorFill)(IDirect3DSurface9 *surface, const RECT *rect, D3DCOLOR color) = 0;
    /*  36 */ FNVNX_D3D_METHOD(HRESULT, CreateOffscreenPlainSurface)(UINT Width, UINT Height, D3DFORMAT Format, D3DPOOL Pool, IDirect3DSurface9** ppSurface, HANDLE* pSharedHandle) = 0;
    /*  37 */ FNVNX_D3D_METHOD(HRESULT, SetRenderTarget)(DWORD RenderTargetIndex, IDirect3DSurface9* pRenderTarget) = 0;
    /*  38 */ FNVNX_D3D_METHOD(HRESULT, GetRenderTarget)(DWORD RenderTargetIndex, IDirect3DSurface9** ppRenderTarget) = 0;
    /*  39 */ FNVNX_D3D_METHOD(HRESULT, SetDepthStencilSurface)(IDirect3DSurface9* pNewZStencil) = 0;
    /*  40 */ FNVNX_D3D_METHOD(HRESULT, GetDepthStencilSurface)(IDirect3DSurface9** ppZStencilSurface) = 0;
    /*  41 */ FNVNX_D3D_METHOD(HRESULT, BeginScene)() = 0;
    /*  42 */ FNVNX_D3D_METHOD(HRESULT, EndScene)() = 0;
    /*  43 */ FNVNX_D3D_METHOD(HRESULT, Clear)(DWORD rect_count, const D3DRECT *rects, DWORD flags, D3DCOLOR color, float z, DWORD stencil) = 0;
    /*  44 */ FNVNX_D3D_METHOD(HRESULT, SetTransform)(D3DTRANSFORMSTATETYPE state, const D3DMATRIX *matrix) = 0;
    /*  45 */ FNVNX_D3D_METHOD(HRESULT, GetTransform)(D3DTRANSFORMSTATETYPE State, D3DMATRIX* pMatrix) = 0;
    /*  46 */ FNVNX_D3D_METHOD(HRESULT, MultiplyTransform)(D3DTRANSFORMSTATETYPE state, const D3DMATRIX *matrix) = 0;
    /*  47 */ FNVNX_D3D_METHOD(HRESULT, SetViewport)(const D3DVIEWPORT9 *viewport) = 0;
    /*  48 */ FNVNX_D3D_METHOD(HRESULT, GetViewport)(D3DVIEWPORT9* pViewport) = 0;
    /*  49 */ FNVNX_D3D_METHOD(HRESULT, SetMaterial)(const D3DMATERIAL9 *material) = 0;
    /*  50 */ FNVNX_D3D_METHOD(HRESULT, GetMaterial)(D3DMATERIAL9* pMaterial) = 0;
    /*  51 */ FNVNX_D3D_METHOD(HRESULT, SetLight)(DWORD index, const D3DLIGHT9 *light) = 0;
    /*  52 */ FNVNX_D3D_METHOD(HRESULT, GetLight)(DWORD Index, D3DLIGHT9*) = 0;
    /*  53 */ FNVNX_D3D_METHOD(HRESULT, LightEnable)(DWORD Index, BOOL Enable) = 0;
    /*  54 */ FNVNX_D3D_METHOD(HRESULT, GetLightEnable)(DWORD Index, BOOL* pEnable) = 0;
    /*  55 */ FNVNX_D3D_METHOD(HRESULT, SetClipPlane)(DWORD index, const float *plane) = 0;
    /*  56 */ FNVNX_D3D_METHOD(HRESULT, GetClipPlane)(DWORD Index, float* pPlane) = 0;
    /*  57 */ FNVNX_D3D_METHOD(HRESULT, SetRenderState)(D3DRENDERSTATETYPE State, DWORD Value) = 0;
    /*  58 */ FNVNX_D3D_METHOD(HRESULT, GetRenderState)(D3DRENDERSTATETYPE State, DWORD* pValue) = 0;
    /*  59 */ FNVNX_D3D_METHOD(HRESULT, CreateStateBlock)(D3DSTATEBLOCKTYPE Type, IDirect3DStateBlock9** ppSB) = 0;
    /*  60 */ FNVNX_D3D_METHOD(HRESULT, BeginStateBlock)() = 0;
    /*  61 */ FNVNX_D3D_METHOD(HRESULT, EndStateBlock)(IDirect3DStateBlock9** ppSB) = 0;
    /*  62 */ FNVNX_D3D_METHOD(HRESULT, SetClipStatus)(const D3DCLIPSTATUS9 *clip_status) = 0;
    /*  63 */ FNVNX_D3D_METHOD(HRESULT, GetClipStatus)(D3DCLIPSTATUS9* pClipStatus) = 0;
    /*  64 */ FNVNX_D3D_METHOD(HRESULT, GetTexture)(DWORD Stage, IDirect3DBaseTexture9** ppTexture) = 0;
    /*  65 */ FNVNX_D3D_METHOD(HRESULT, SetTexture)(DWORD Stage, IDirect3DBaseTexture9* pTexture) = 0;
    /*  66 */ FNVNX_D3D_METHOD(HRESULT, GetTextureStageState)(DWORD Stage, D3DTEXTURESTAGESTATETYPE Type, DWORD* pValue) = 0;
    /*  67 */ FNVNX_D3D_METHOD(HRESULT, SetTextureStageState)(DWORD Stage, D3DTEXTURESTAGESTATETYPE Type, DWORD Value) = 0;
    /*  68 */ FNVNX_D3D_METHOD(HRESULT, GetSamplerState)(DWORD Sampler, D3DSAMPLERSTATETYPE Type, DWORD* pValue) = 0;
    /*  69 */ FNVNX_D3D_METHOD(HRESULT, SetSamplerState)(DWORD Sampler, D3DSAMPLERSTATETYPE Type, DWORD Value) = 0;
    /*  70 */ FNVNX_D3D_METHOD(HRESULT, ValidateDevice)(DWORD* pNumPasses) = 0;
    /*  71 */ FNVNX_D3D_METHOD(HRESULT, SetPaletteEntries)(UINT palette_idx, const PALETTEENTRY *entries) = 0;
    /*  72 */ FNVNX_D3D_METHOD(HRESULT, GetPaletteEntries)(UINT PaletteNumber,PALETTEENTRY* pEntries) = 0;
    /*  73 */ FNVNX_D3D_METHOD(HRESULT, SetCurrentTexturePalette)(UINT PaletteNumber) = 0;
    /*  74 */ FNVNX_D3D_METHOD(HRESULT, GetCurrentTexturePalette)(UINT *PaletteNumber) = 0;
    /*  75 */ FNVNX_D3D_METHOD(HRESULT, SetScissorRect)(const RECT *rect) = 0;
    /*  76 */ FNVNX_D3D_METHOD(HRESULT, GetScissorRect)(RECT* pRect) = 0;
    /*  77 */ FNVNX_D3D_METHOD(HRESULT, SetSoftwareVertexProcessing)(BOOL bSoftware) = 0;
    /*  78 */ FNVNX_D3D_METHOD(BOOL, GetSoftwareVertexProcessing)() = 0;
    /*  79 */ FNVNX_D3D_METHOD(HRESULT, SetNPatchMode)(float nSegments) = 0;
    /*  80 */ FNVNX_D3D_METHOD(float, GetNPatchMode)() = 0;
    /*  81 */ FNVNX_D3D_METHOD(HRESULT, DrawPrimitive)(D3DPRIMITIVETYPE PrimitiveType, UINT StartVertex, UINT PrimitiveCount) = 0;
    /*  82 */ FNVNX_D3D_METHOD(HRESULT, DrawIndexedPrimitive)(D3DPRIMITIVETYPE, INT BaseVertexIndex, UINT MinVertexIndex, UINT NumVertices, UINT startIndex, UINT primCount) = 0;
    /*  83 */ FNVNX_D3D_METHOD(HRESULT, DrawPrimitiveUP)(D3DPRIMITIVETYPE primitive_type, UINT primitive_count, const void *data, UINT stride) = 0;
    /*  84 */ FNVNX_D3D_METHOD(HRESULT, DrawIndexedPrimitiveUP)(D3DPRIMITIVETYPE primitive_type, UINT min_vertex_idx, UINT vertex_count, UINT primitive_count, const void *index_data, D3DFORMAT index_format, const void *data, UINT stride) = 0;
    /*  85 */ FNVNX_D3D_METHOD(HRESULT, ProcessVertices)(UINT SrcStartIndex, UINT DestIndex, UINT VertexCount, IDirect3DVertexBuffer9* pDestBuffer, IDirect3DVertexDeclaration9* pVertexDecl, DWORD Flags) = 0;
    /*  86 */ FNVNX_D3D_METHOD(HRESULT, CreateVertexDeclaration)(const D3DVERTEXELEMENT9 *elements, IDirect3DVertexDeclaration9 **declaration) = 0;
    /*  87 */ FNVNX_D3D_METHOD(HRESULT, SetVertexDeclaration)(IDirect3DVertexDeclaration9* pDecl) = 0;
    /*  88 */ FNVNX_D3D_METHOD(HRESULT, GetVertexDeclaration)(IDirect3DVertexDeclaration9** ppDecl) = 0;
    /*  89 */ FNVNX_D3D_METHOD(HRESULT, SetFVF)(DWORD FVF) = 0;
    /*  90 */ FNVNX_D3D_METHOD(HRESULT, GetFVF)(DWORD* pFVF) = 0;
    /*  91 */ FNVNX_D3D_METHOD(HRESULT, CreateVertexShader)(const DWORD *byte_code, IDirect3DVertexShader9 **shader) = 0;
    /*  92 */ FNVNX_D3D_METHOD(HRESULT, SetVertexShader)(IDirect3DVertexShader9* pShader) = 0;
    /*  93 */ FNVNX_D3D_METHOD(HRESULT, GetVertexShader)(IDirect3DVertexShader9** ppShader) = 0;
    /*  94 */ FNVNX_D3D_METHOD(HRESULT, SetVertexShaderConstantF)(UINT reg_idx, const float *data, UINT count) = 0;
    /*  95 */ FNVNX_D3D_METHOD(HRESULT, GetVertexShaderConstantF)(UINT StartRegister, float* pConstantData, UINT Vector4fCount) = 0;
    /*  96 */ FNVNX_D3D_METHOD(HRESULT, SetVertexShaderConstantI)(UINT reg_idx, const int *data, UINT count) = 0;
    /*  97 */ FNVNX_D3D_METHOD(HRESULT, GetVertexShaderConstantI)(UINT StartRegister, int* pConstantData, UINT Vector4iCount) = 0;
    /*  98 */ FNVNX_D3D_METHOD(HRESULT, SetVertexShaderConstantB)(UINT reg_idx, const BOOL *data, UINT count) = 0;
    /*  99 */ FNVNX_D3D_METHOD(HRESULT, GetVertexShaderConstantB)(UINT StartRegister, BOOL* pConstantData, UINT BoolCount) = 0;
    /* 100 */ FNVNX_D3D_METHOD(HRESULT, SetStreamSource)(UINT StreamNumber, IDirect3DVertexBuffer9* pStreamData, UINT OffsetInBytes, UINT Stride) = 0;
    /* 101 */ FNVNX_D3D_METHOD(HRESULT, GetStreamSource)(UINT StreamNumber, IDirect3DVertexBuffer9** ppStreamData, UINT* OffsetInBytes, UINT* pStride) = 0;
    /* 102 */ FNVNX_D3D_METHOD(HRESULT, SetStreamSourceFreq)(UINT StreamNumber, UINT Divider) = 0;
    /* 103 */ FNVNX_D3D_METHOD(HRESULT, GetStreamSourceFreq)(UINT StreamNumber, UINT* Divider) = 0;
    /* 104 */ FNVNX_D3D_METHOD(HRESULT, SetIndices)(IDirect3DIndexBuffer9* pIndexData) = 0;
    /* 105 */ FNVNX_D3D_METHOD(HRESULT, GetIndices)(IDirect3DIndexBuffer9** ppIndexData) = 0;
    /* 106 */ FNVNX_D3D_METHOD(HRESULT, CreatePixelShader)(const DWORD *byte_code, IDirect3DPixelShader9 **shader) = 0;
    /* 107 */ FNVNX_D3D_METHOD(HRESULT, SetPixelShader)(IDirect3DPixelShader9* pShader) = 0;
    /* 108 */ FNVNX_D3D_METHOD(HRESULT, GetPixelShader)(IDirect3DPixelShader9** ppShader) = 0;
    /* 109 */ FNVNX_D3D_METHOD(HRESULT, SetPixelShaderConstantF)(UINT reg_idx, const float *data, UINT count) = 0;
    /* 110 */ FNVNX_D3D_METHOD(HRESULT, GetPixelShaderConstantF)(UINT StartRegister, float* pConstantData, UINT Vector4fCount) = 0;
    /* 111 */ FNVNX_D3D_METHOD(HRESULT, SetPixelShaderConstantI)(UINT reg_idx, const int *data, UINT count) = 0;
    /* 112 */ FNVNX_D3D_METHOD(HRESULT, GetPixelShaderConstantI)(UINT StartRegister, int* pConstantData, UINT Vector4iCount) = 0;
    /* 113 */ FNVNX_D3D_METHOD(HRESULT, SetPixelShaderConstantB)(UINT reg_idx, const BOOL *data, UINT count) = 0;
    /* 114 */ FNVNX_D3D_METHOD(HRESULT, GetPixelShaderConstantB)(UINT StartRegister, BOOL* pConstantData, UINT BoolCount) = 0;
    /* 115 */ FNVNX_D3D_METHOD(HRESULT, DrawRectPatch)(UINT handle, const float *segment_count, const D3DRECTPATCH_INFO *patch_info) = 0;
    /* 116 */ FNVNX_D3D_METHOD(HRESULT, DrawTriPatch)(UINT handle, const float *segment_count, const D3DTRIPATCH_INFO *patch_info) = 0;
    /* 117 */ FNVNX_D3D_METHOD(HRESULT, DeletePatch)(UINT Handle) = 0;
    /* 118 */ FNVNX_D3D_METHOD(HRESULT, CreateQuery)(D3DQUERYTYPE Type, IDirect3DQuery9** ppQuery) = 0;
};

struct IDirect3D9Ex : public IDirect3D9
{
    /*  17 */ FNVNX_D3D_METHOD(UINT, GetAdapterModeCountEx)(UINT adapter_idx, const D3DDISPLAYMODEFILTER *filter) = 0;
    /*  18 */ FNVNX_D3D_METHOD(HRESULT, EnumAdapterModesEx)(UINT adapter_idx, const D3DDISPLAYMODEFILTER *filter, UINT mode_idx, D3DDISPLAYMODEEX *mode) = 0;
    /*  19 */ FNVNX_D3D_METHOD(HRESULT, GetAdapterDisplayModeEx)(UINT adapter_idx, D3DDISPLAYMODEEX *mode, D3DDISPLAYROTATION *rotation) = 0;
    /*  20 */ FNVNX_D3D_METHOD(HRESULT, CreateDeviceEx)(UINT adapter_idx, D3DDEVTYPE device_type, HWND focus_window, DWORD flags, D3DPRESENT_PARAMETERS *parameters, D3DDISPLAYMODEEX *mode, struct IDirect3DDevice9Ex **device) = 0;
    /*  21 */ FNVNX_D3D_METHOD(HRESULT, GetAdapterLUID)(UINT adapter_idx, LUID *luid) = 0;
};

struct IDirect3DSwapChain9Ex : public IDirect3DSwapChain9
{
    /*  10 */ FNVNX_D3D_METHOD(HRESULT, GetLastPresentCount)(UINT *last_present_count) = 0;
    /*  11 */ FNVNX_D3D_METHOD(HRESULT, GetPresentStats)(D3DPRESENTSTATS *stats) = 0;
    /*  12 */ FNVNX_D3D_METHOD(HRESULT, GetDisplayModeEx)(D3DDISPLAYMODEEX *mode, D3DDISPLAYROTATION *rotation) = 0;
};

struct IDirect3DDevice9Ex : public IDirect3DDevice9
{
    /* 119 */ FNVNX_D3D_METHOD(HRESULT, SetConvolutionMonoKernel)(UINT width, UINT height, float *rows, float *columns) = 0;
    /* 120 */ FNVNX_D3D_METHOD(HRESULT, ComposeRects)(IDirect3DSurface9 *src_surface, IDirect3DSurface9 *dst_surface, IDirect3DVertexBuffer9 *src_descs, UINT rect_count, IDirect3DVertexBuffer9 *dst_descs, D3DCOMPOSERECTSOP operation, INT offset_x, INT offset_y) = 0;
    /* 121 */ FNVNX_D3D_METHOD(HRESULT, PresentEx)(const RECT *src_rect, const RECT *dst_rect, HWND dst_window_override, const RGNDATA *dirty_region, DWORD flags) = 0;
    /* 122 */ FNVNX_D3D_METHOD(HRESULT, GetGPUThreadPriority)(INT *priority) = 0;
    /* 123 */ FNVNX_D3D_METHOD(HRESULT, SetGPUThreadPriority)(INT priority) = 0;
    /* 124 */ FNVNX_D3D_METHOD(HRESULT, WaitForVBlank)(UINT swapchain_idx) = 0;
    /* 125 */ FNVNX_D3D_METHOD(HRESULT, CheckResourceResidency)(IDirect3DResource9 **resources, UINT32 resource_count) = 0;
    /* 126 */ FNVNX_D3D_METHOD(HRESULT, SetMaximumFrameLatency)(UINT max_latency) = 0;
    /* 127 */ FNVNX_D3D_METHOD(HRESULT, GetMaximumFrameLatency)(UINT *max_latency) = 0;
    /* 128 */ FNVNX_D3D_METHOD(HRESULT, CheckDeviceState)(HWND dst_window) = 0;
    /* 129 */ FNVNX_D3D_METHOD(HRESULT, CreateRenderTargetEx)(UINT width, UINT height, D3DFORMAT format, D3DMULTISAMPLE_TYPE multisample_type, DWORD multisample_quality, BOOL lockable, IDirect3DSurface9 **surface, HANDLE *shared_handle, DWORD usage) = 0;
    /* 130 */ FNVNX_D3D_METHOD(HRESULT, CreateOffscreenPlainSurfaceEx)(UINT width, UINT Height, D3DFORMAT format, D3DPOOL pool, IDirect3DSurface9 **surface, HANDLE *shared_handle, DWORD usage) = 0;
    /* 131 */ FNVNX_D3D_METHOD(HRESULT, CreateDepthStencilSurfaceEx)(UINT width, UINT height, D3DFORMAT format, D3DMULTISAMPLE_TYPE multisample_type, DWORD multisample_quality, BOOL discard, IDirect3DSurface9 **surface, HANDLE *shared_handle, DWORD usage) = 0;
    /* 132 */ FNVNX_D3D_METHOD(HRESULT, ResetEx)(D3DPRESENT_PARAMETERS *parameters, D3DDISPLAYMODEEX *mode) = 0;
    /* 133 */ FNVNX_D3D_METHOD(HRESULT, GetDisplayModeEx)(UINT swapchain_idx, D3DDISPLAYMODEEX *mode, D3DDISPLAYROTATION *rotation) = 0;
};

/* ---- d3d9.dll exports (the engine resolves Direct3DCreate9/Ex through GetProcAddress; S4 maps them here) */
extern "C" {
FNVNX_D3D_TRACED IDirect3D9 *WINAPI Direct3DCreate9(UINT SDKVersion);
FNVNX_D3D_TRACED HRESULT WINAPI Direct3DCreate9Ex(UINT SDKVersion, IDirect3D9Ex **ppD3D);
FNVNX_D3D_TRACED int WINAPI D3DPERF_BeginEvent(D3DCOLOR col, LPCWSTR wszName);
FNVNX_D3D_TRACED int WINAPI D3DPERF_EndEvent(void);
FNVNX_D3D_TRACED void WINAPI D3DPERF_SetMarker(D3DCOLOR col, LPCWSTR wszName);
FNVNX_D3D_TRACED void WINAPI D3DPERF_SetRegion(D3DCOLOR col, LPCWSTR wszName);
FNVNX_D3D_TRACED BOOL WINAPI D3DPERF_QueryRepeatFrame(void);
FNVNX_D3D_TRACED void WINAPI D3DPERF_SetOptions(DWORD dwOptions);
FNVNX_D3D_TRACED DWORD WINAPI D3DPERF_GetStatus(void);
}

#endif
