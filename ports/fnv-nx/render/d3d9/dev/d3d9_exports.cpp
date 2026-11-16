// d3d9_exports.cpp: the export surface of our d3d9.dll (the names an import slot or GetProcAddress can bind).
// Reference: Wine 9.0 dlls/d3d9/d3d9_main.c (rule "one reference environment", render/d3d9/env/README.md).
//
// Rule: the set of names is the set of exports that carry a signature in the interface table (bridgefacts/d3d9/d3d9_exports.py).
// Each export has one FNVNX_EXPORT record right after its definer (the only place the fact is written; contract/exportgen.py reads the records);
// render/d3d9/tests/test_exports.py fails when the records of the built object and the spec differ, so a spec export cannot lack a body here.
//
// D3DPERF_*: the PIX event interface. Its only state is the event nesting level; every other answer is the environment fact
// "no PIX capture is attached to the process" (ENV-TRUTH: the console has no PIX), for which the documented results are:
//   BeginEvent  returns the level before the call, then level + 1      EndEvent  level - 1, returns the new level
//   GetStatus   0 (nonzero only while PIX is attached)                 QueryRepeatFrame  FALSE (TRUE only while PIX replays a frame)
//   SetMarker / SetRegion / SetOptions  no result; they address the attached profiler only (SetOptions(1) forbids attaching one),
//   so with no profiler there is no observable effect on any later d3d9 call.
// GetStatus / QueryRepeatFrame / SetMarker / SetOptions / SetRegion are Wine 9.0 FIXME stubs (d3d9_main.c, same-named functions): one fnvnx_wine_fixme per call, rows 41-45 of verify/wine_fixme.tsv.
// BeginEvent / EndEvent are real (the level). DebugSetMute: Wine does nothing (`/* nothing to do */`), id 52 (NX_WINE_FIXME_VOID).
// Wine 9.0 citations (dlls/d3d9/d3d9_main.c, tag wine-9.0): DebugSetMute :31-33 (`/* nothing to do */`), D3DPERF_BeginEvent :201-205, D3DPERF_EndEvent :211-214,
// D3DPERF_GetStatus :220-221 (FIXME stub), D3DPERF_SetOptions :230-232 (FIXME), D3DPERF_QueryRepeatFrame :238-239 (FIXME), D3DPERF_SetMarker :247-249 (FIXME),
// D3DPERF_SetRegion :255-257 (FIXME); Direct3DCreate9 :35, Direct3DCreate9Ex :56, Direct3DShaderValidatorCreate9 :191. Signatures: dlls/d3d9/d3d9.spec :1,:5-:10,:12-:14.
// The level is one object per process as in the reference; updates are atomic (the reference's plain int is a data race there).
#include <atomic>
#include <string.h>
#include "d3d9_adapter.h"
#include "const_fixme.h"
#include <fnvnx_export.h>

static std::atomic<int> g_perf_level{0};

extern "C" {
int WINAPI D3DPERF_BeginEvent(D3DCOLOR, LPCWSTR) { return g_perf_level.fetch_add(1, std::memory_order_relaxed); }
FNVNX_EXPORT("d3d9.dll", "D3DPERF_BeginEvent", D3DPERF_BeginEvent, "func");
int WINAPI D3DPERF_EndEvent(void) { return g_perf_level.fetch_sub(1, std::memory_order_relaxed) - 1; }
FNVNX_EXPORT("d3d9.dll", "D3DPERF_EndEvent", D3DPERF_EndEvent, "func");
DWORD WINAPI D3DPERF_GetStatus(void) { fnvnx_wine_fixme(NXW_D3DPERF_GetStatus); return 0; }
FNVNX_EXPORT("d3d9.dll", "D3DPERF_GetStatus", D3DPERF_GetStatus, "func");
BOOL WINAPI D3DPERF_QueryRepeatFrame(void) { fnvnx_wine_fixme(NXW_D3DPERF_QueryRepeatFrame); return FALSE; }
FNVNX_EXPORT("d3d9.dll", "D3DPERF_QueryRepeatFrame", D3DPERF_QueryRepeatFrame, "func");
void WINAPI D3DPERF_SetMarker(D3DCOLOR, LPCWSTR) { NX_WINE_FIXME_VOID(NXW_D3DPERF_SetMarker); }
FNVNX_EXPORT("d3d9.dll", "D3DPERF_SetMarker", D3DPERF_SetMarker, "func");
void WINAPI D3DPERF_SetOptions(DWORD) { NX_WINE_FIXME_VOID(NXW_D3DPERF_SetOptions); }
FNVNX_EXPORT("d3d9.dll", "D3DPERF_SetOptions", D3DPERF_SetOptions, "func");
void WINAPI D3DPERF_SetRegion(D3DCOLOR, LPCWSTR) { NX_WINE_FIXME_VOID(NXW_D3DPERF_SetRegion); }
FNVNX_EXPORT("d3d9.dll", "D3DPERF_SetRegion", D3DPERF_SetRegion, "func");
IDirect3D9* WINAPI Direct3DCreate9(UINT sdk) { return nx_Direct3DCreate9(sdk); }
FNVNX_EXPORT("d3d9.dll", "Direct3DCreate9", Direct3DCreate9, "func");
HRESULT WINAPI Direct3DCreate9Ex(UINT sdk, IDirect3D9Ex** out) { return nx_Direct3DCreate9Ex(sdk, out); }
FNVNX_EXPORT("d3d9.dll", "Direct3DCreate9Ex", Direct3DCreate9Ex, "func");
// Wine 9.0 d3d9_main.c: `void WINAPI DebugSetMute(void) { /* nothing to do */ }`; id 52 of verify/wine_fixme.tsv, one log line per call as for every simplified Wine body. D3DX pushes one word and removes it itself: the code of a 0-argument stdcall function is the same.
void WINAPI DebugSetMute(void) { NX_WINE_FIXME_VOID(NXW_DebugSetMute); }
FNVNX_EXPORT("d3d9.dll", "DebugSetMute", DebugSetMute, "func");
IDirect3DShaderValidator9* WINAPI Direct3DShaderValidatorCreate9(void) { return nx_Direct3DShaderValidatorCreate9(); }
FNVNX_EXPORT("d3d9.dll", "Direct3DShaderValidatorCreate9", Direct3DShaderValidatorCreate9, "func");
}
