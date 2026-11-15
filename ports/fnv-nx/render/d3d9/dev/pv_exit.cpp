// pv_exit.cpp: SetClipStatus, GetClipStatus of both device classes.
// Owner decision of 2026-11-11 ("Wine 9.0 as an acceptable implementation"): where Wine 9.0 implements an API in a simplified way the body does
// exactly what Wine 9.0 does, with one log line per call (fnvnx_wine_fixme, loader/include/fnvnx_reach.h; ids = rows of verify/wine_fixme.tsv).
//
// SetClipStatus / GetClipStatus: wine-9.0 dlls/d3d9/device.c d3d9_device_SetClipStatus / d3d9_device_GetClipStatus forward to
// dlls/wined3d/device.c wined3d_device_set_clip_status / wined3d_device_get_clip_status, which are:
//     FIXME("... stub!"); if (!clip_status) return WINED3DERR_INVALIDCALL; return WINED3D_OK;
// Nothing is stored and GetClipStatus writes nothing to *clip_status.
//
// ProcessVertices is in pvw_api.cpp (task PVW).
#include "../gen/d9impl.gen.h"
#include "const_fixme.h"   // fnvnx_wine_fixme and the NXW_ ids of verify/wine_fixme.tsv
#define NX_PV_BODIES(C)                                                                                                                                            \
    HRESULT STDMETHODCALLTYPE C::SetClipStatus(const D3DCLIPSTATUS9* s) { fnvnx_wine_fixme(NXW_wined3d_device_set_clip_status); return s ? D3D_OK : D3DERR_INVALIDCALL; } \
    HRESULT STDMETHODCALLTYPE C::GetClipStatus(D3DCLIPSTATUS9* s) { fnvnx_wine_fixme(NXW_wined3d_device_get_clip_status); return s ? D3D_OK : D3DERR_INVALIDCALL; }      
NX_PV_BODIES(NxDevice9)
NX_PV_BODIES(NxDevice9Ex)
