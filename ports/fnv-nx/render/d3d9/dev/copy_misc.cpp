// The patch calls of both device classes.
// VALIDATEDEVICE moved to m1_validate.cpp (the MS result codes over one draw-legality predicate; this file had Wine's three rules only).
// PATCHES: D3DCAPS9.DevCaps of the reference environment has neither D3DDEVCAPS_RTPATCHES nor RTPATCHHANDLEZERO nor NPATCHES (gen_env.py asserts it from the recorded caps and stops when a recording
//  changes it), so a conforming application never calls them; the bodies are Wine 9.0's (below).
// NOT IMPLEMENTED (no complete contract): ProcessVertices (Wine ignores the vertex shader and the output declaration, FIXME; MS requires running the shader: needs the shader interpreter stream),
//  SetClipStatus / GetClipStatus (the status is produced by the draw path; no contract before it exists).
#include "copy_util.h"
#include "const_fixme.h"
#include "../gen/env_table.gen.h"
#include "../gen/d9impl.gen.h"

// Wine 9.0 dlls/d3d9/device.c (fetched from the wine-9.0 tag): d3d9_device_DrawRectPatch and d3d9_device_DrawTriPatch are `FIXME(...unimplemented.); return D3D_OK;`,
// d3d9_device_DeletePatch is `FIXME(...unimplemented.); return D3DERR_INVALIDCALL;`. The bodies are exactly those (owner decision 2026-11-11, Wine 9.0 as an acceptable implementation): ids 48-50 of verify/wine_fixme.tsv.
#define MISC(C) \
    HRESULT STDMETHODCALLTYPE C::DrawRectPatch(UINT, const float*, const D3DRECTPATCH_INFO*) { fnvnx_wine_fixme(NXW_d3d9_device_DrawRectPatch); return D3D_OK; } \
    HRESULT STDMETHODCALLTYPE C::DrawTriPatch(UINT, const float*, const D3DTRIPATCH_INFO*) { fnvnx_wine_fixme(NXW_d3d9_device_DrawTriPatch); return D3D_OK; } \
    HRESULT STDMETHODCALLTYPE C::DeletePatch(UINT) { fnvnx_wine_fixme(NXW_d3d9_device_DeletePatch); return D3DERR_INVALIDCALL; }
MISC(NxDevice9) MISC(NxDevice9Ex)
