// The patch calls of both device classes.
// VALIDATEDEVICE moved to m1_validate.cpp (the MS result codes over one draw-legality predicate; this file had Wine's three rules only).
// PATCHES: D3DCAPS9.DevCaps of the reference environment has neither D3DDEVCAPS_RTPATCHES nor RTPATCHHANDLEZERO nor NPATCHES (gen_env.py asserts it from the recorded caps and stops when a recording
//  changes it), so no higher-order surface can be drawn: P1 DrawRectPatch / DrawTriPatch = D3DERR_INVALIDCALL (MS: the only failure code), P2 DeletePatch: no handle was ever accepted, every handle
//  is unknown = D3DERR_INVALIDCALL (Wine's DeletePatch answer). Wine's DrawRectPatch / DrawTriPatch return D3D_OK without drawing (a FIXME stub): that is a no-op and is not copied.
// NOT IMPLEMENTED (no complete contract): ProcessVertices (Wine ignores the vertex shader and the output declaration, FIXME; MS requires running the shader: needs the shader interpreter stream),
//  SetClipStatus / GetClipStatus (the status is produced by the draw path; no contract before it exists).
#include "copy_util.h"
#include "../gen/env_table.gen.h"
#include "../gen/d9impl.gen.h"

#define MISC(C) \
    HRESULT STDMETHODCALLTYPE C::DrawRectPatch(UINT, const float*, const D3DRECTPATCH_INFO*) { return D3DERR_INVALIDCALL; } \
    HRESULT STDMETHODCALLTYPE C::DrawTriPatch(UINT, const float*, const D3DTRIPATCH_INFO*) { return D3DERR_INVALIDCALL; } \
    HRESULT STDMETHODCALLTYPE C::DeletePatch(UINT) { return D3DERR_INVALIDCALL; }
MISC(NxDevice9) MISC(NxDevice9Ex)
