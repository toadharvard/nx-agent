// const_fixme.h: ids of the constant-result d3d9 bodies "as Wine 9.0" (rows of const_fixme.tsv, in L4's format of verify/wine_fixme.tsv).
// Each body is vacuous: exactly one fnvnx_wine_fixme(id) per call, then the fixed result of the Wine function named in the row.
#pragma once
#include <stdint.h>
extern "C" void fnvnx_wine_fixme(uint32_t id);   // loader/include/fnvnx_reach.h: writes one line and returns
enum : uint32_t {
    NX_CF_D3DPERF_GetStatus = 41, NX_CF_D3DPERF_QueryRepeatFrame = 42, NX_CF_D3DPERF_SetMarker = 43, NX_CF_D3DPERF_SetOptions = 44, NX_CF_D3DPERF_SetRegion = 45,
    NX_CF_SetDialogBoxMode = 46, NX_CF_ComposeRects = 47,
    NX_CF_DrawRectPatch = 48, NX_CF_DrawTriPatch = 49, NX_CF_DeletePatch = 50, NX_CF_RegisterSoftwareDevice = 51,
    NX_CF_DebugSetMute = 52, NX_CF_ShaderValidatorAddRef = 53, NX_CF_ShaderValidatorRelease = 54
};
// A function without a result whose body is the log line only: the compiler would make the call a tail call (a `b`), and the gate (objstub W) takes a branch to fnvnx_wine_fixme
// for a real body. The empty asm statement after the call is a barrier that keeps the call a `bl` followed by `ret`; it emits no instruction.
#define NX_WINE_FIXME_VOID(id) do { fnvnx_wine_fixme(id); __asm__ __volatile__("" ::: "memory"); } while (0)
