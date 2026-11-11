// const_fixme.h: ids of the constant-result d3d9 bodies "as Wine 9.0" (rows of const_fixme.tsv, in L4's format of verify/wine_fixme.tsv).
// Each body is vacuous: exactly one fnvnx_wine_fixme(id) per call, then the fixed result of the Wine function named in the row.
#pragma once
#include <stdint.h>
extern "C" void fnvnx_wine_fixme(uint32_t id);   // loader/include/fnvnx_reach.h: writes one line and returns
enum : uint32_t {
    NX_CF_D3DPERF_GetStatus = 41, NX_CF_D3DPERF_QueryRepeatFrame = 42, NX_CF_D3DPERF_SetMarker = 43, NX_CF_D3DPERF_SetOptions = 44, NX_CF_D3DPERF_SetRegion = 45,
    NX_CF_SetDialogBoxMode = 46, NX_CF_ComposeRects = 47
};
