// pv_exit.cpp: SetClipStatus, GetClipStatus, ProcessVertices of both device classes.
// Owner decision of 2026-11-11 ("Wine 9.0 as an acceptable implementation"): where Wine 9.0 implements an API in a simplified way the body does
// exactly what Wine 9.0 does, with one log line per call (fnvnx_wine_fixme, loader/include/fnvnx_reach.h; ids = rows of verify/wine_fixme.tsv).
//
// SetClipStatus / GetClipStatus: wine-9.0 dlls/d3d9/device.c d3d9_device_SetClipStatus / d3d9_device_GetClipStatus forward to
// dlls/wined3d/device.c wined3d_device_set_clip_status / wined3d_device_get_clip_status, which are:
//     FIXME("... stub!"); if (!clip_status) return WINED3DERR_INVALIDCALL; return WINED3D_OK;
// Nothing is stored and GetClipStatus writes nothing to *clip_status.
//
// ProcessVertices: Wine 9.0 is NOT a no-op here: d3d9_device_ProcessVertices calls wined3d_device_process_vertices -> process_vertices_strided,
// which transforms the vertices on the CPU with the fixed-function state (it ignores a bound vertex shader) and writes the destination buffer.
// "As Wine" therefore means that algorithm (task PVW, in progress). Until it lands a call ends the process through the platform's process exit
// with a code naming the method: returning D3D_OK without writing the destination buffer would be neither Wine's behaviour nor the contract.
#include "d9impl.gen.h"
extern "C" void fnvnx_wine_fixme(uint32_t id);                          // loader/include/fnvnx_reach.h: writes one line and returns
extern "C" void os_exit_process(int code) __attribute__((noreturn));   // platform/os/os.h
enum : uint32_t { NX_WINE_FIXME_SetClipStatus = 2, NX_WINE_FIXME_GetClipStatus = 3 };   // verify/wine_fixme.tsv
enum : int { NX_EXIT_D3D9_ProcessVertices = 0x6E780101 };
#define NX_PV_BODIES(C)                                                                                                                                            \
    HRESULT STDMETHODCALLTYPE C::SetClipStatus(const D3DCLIPSTATUS9* s) { fnvnx_wine_fixme(NX_WINE_FIXME_SetClipStatus); return s ? D3D_OK : D3DERR_INVALIDCALL; } \
    HRESULT STDMETHODCALLTYPE C::GetClipStatus(D3DCLIPSTATUS9* s) { fnvnx_wine_fixme(NX_WINE_FIXME_GetClipStatus); return s ? D3D_OK : D3DERR_INVALIDCALL; }       \
    HRESULT STDMETHODCALLTYPE C::ProcessVertices(UINT, UINT, UINT, IDirect3DVertexBuffer9*, IDirect3DVertexDeclaration9*, DWORD) { os_exit_process(NX_EXIT_D3D9_ProcessVertices); }
NX_PV_BODIES(NxDevice9)
NX_PV_BODIES(NxDevice9Ex)
