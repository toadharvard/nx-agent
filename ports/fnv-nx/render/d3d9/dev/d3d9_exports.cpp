// d3d9_exports.cpp: the export surface of our d3d9.dll (the names an import slot or GetProcAddress can bind).
// Reference: Wine 9.0 dlls/d3d9/d3d9_main.c (rule "one reference environment", render/d3d9/env/README.md).
//
// Rule: the set of names is the set of exports that carry a signature in the interface table (render/d3d9/spec/d3d9_exports.py);
// tests/l6/exports/test_exports.py fails when the table below and the spec differ, so a spec export cannot lack a body here.
//
// D3DPERF_*: the PIX event interface. Its only state is the event nesting level; every other answer is the environment fact
// "no PIX capture is attached to the process" (ENV-TRUTH: the console has no PIX), for which the documented results are:
//   BeginEvent  returns the level before the call, then level + 1      EndEvent  level - 1, returns the new level
//   GetStatus   0 (nonzero only while PIX is attached)                 QueryRepeatFrame  FALSE (TRUE only while PIX replays a frame)
//   SetMarker / SetRegion / SetOptions  no result; they address the attached profiler only (SetOptions(1) forbids attaching one),
//   so with no profiler there is no observable effect on any later d3d9 call.
// GetStatus / QueryRepeatFrame / SetMarker / SetOptions / SetRegion are Wine 9.0 FIXME stubs (d3d9_main.c, same-named functions): one fnvnx_wine_fixme per call, rows 41-45 of verify/wine_fixme.tsv.
// BeginEvent / EndEvent are real (the level). DebugSetMute: Wine does nothing (`/* nothing to do */`), id 52 (NX_WINE_FIXME_VOID).
// The level is one object per process as in the reference; updates are atomic (the reference's plain int is a data race there).
#include <atomic>
#include <string.h>
#include "d3d9_adapter.h"
#include "const_fixme.h"

static std::atomic<int> g_perf_level{0};

extern "C" {
int WINAPI D3DPERF_BeginEvent(D3DCOLOR, LPCWSTR) { return g_perf_level.fetch_add(1, std::memory_order_relaxed); }
int WINAPI D3DPERF_EndEvent(void) { return g_perf_level.fetch_sub(1, std::memory_order_relaxed) - 1; }
DWORD WINAPI D3DPERF_GetStatus(void) { fnvnx_wine_fixme(NXW_D3DPERF_GetStatus); return 0; }
BOOL WINAPI D3DPERF_QueryRepeatFrame(void) { fnvnx_wine_fixme(NXW_D3DPERF_QueryRepeatFrame); return FALSE; }
void WINAPI D3DPERF_SetMarker(D3DCOLOR, LPCWSTR) { NX_WINE_FIXME_VOID(NXW_D3DPERF_SetMarker); }
void WINAPI D3DPERF_SetOptions(DWORD) { NX_WINE_FIXME_VOID(NXW_D3DPERF_SetOptions); }
void WINAPI D3DPERF_SetRegion(D3DCOLOR, LPCWSTR) { NX_WINE_FIXME_VOID(NXW_D3DPERF_SetRegion); }
IDirect3D9* WINAPI Direct3DCreate9(UINT sdk) { return nx_Direct3DCreate9(sdk); }
HRESULT WINAPI Direct3DCreate9Ex(UINT sdk, IDirect3D9Ex** out) { return nx_Direct3DCreate9Ex(sdk, out); }
// Wine 9.0 d3d9_main.c: `void WINAPI DebugSetMute(void) { /* nothing to do */ }`; id 52 of verify/wine_fixme.tsv, one log line per call as for every simplified Wine body. D3DX pushes one word and removes it itself: the code of a 0-argument stdcall function is the same.
void WINAPI DebugSetMute(void) { NX_WINE_FIXME_VOID(NXW_DebugSetMute); }
IDirect3DShaderValidator9* WINAPI Direct3DShaderValidatorCreate9(void) { return nx_Direct3DShaderValidatorCreate9(); }

// name -> body, sorted by name (the order of the spec's export table)
static const struct { const char* name; void* fn; } g_exports[] = {
    {"D3DPERF_BeginEvent", (void*)D3DPERF_BeginEvent},
    {"D3DPERF_EndEvent", (void*)D3DPERF_EndEvent},
    {"D3DPERF_GetStatus", (void*)D3DPERF_GetStatus},
    {"D3DPERF_QueryRepeatFrame", (void*)D3DPERF_QueryRepeatFrame},
    {"D3DPERF_SetMarker", (void*)D3DPERF_SetMarker},
    {"D3DPERF_SetOptions", (void*)D3DPERF_SetOptions},
    {"D3DPERF_SetRegion", (void*)D3DPERF_SetRegion},
    {"DebugSetMute", (void*)DebugSetMute},
    {"Direct3DCreate9", (void*)Direct3DCreate9},
    {"Direct3DCreate9Ex", (void*)Direct3DCreate9Ex},
    {"Direct3DShaderValidatorCreate9", (void*)Direct3DShaderValidatorCreate9},
};

// The resolver of the module "d3d9.dll" (signature of fnvnx_module_resolver, platform/include/fnvnx_win32_ext.h):
// the body of an export by name, NULL for a name the module does not export (GetProcAddress then fails with ERROR_PROC_NOT_FOUND).
void* nx_d3d9_export(const char* name) {
    if (!name) return nullptr;
    for (const auto& e : g_exports) if (!strcmp(e.name, name)) return e.fn;
    return nullptr;
}
// The export count is a constant of the table: the size of g_exports over the size of an entry (an object, not a function).
extern const int nx_d3d9_export_count = (int)(sizeof g_exports / sizeof g_exports[0]);
const char* nx_d3d9_export_name(int i) { return i >= 0 && i < nx_d3d9_export_count ? g_exports[i].name : nullptr; }
}
