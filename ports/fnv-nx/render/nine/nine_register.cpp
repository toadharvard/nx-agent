// fnv-nx: d3d9.dll for the game image (stream S6c-nine, guest side, ILP32).
//
// The engine loads d3d9.dll with LoadLibraryA and resolves Direct3DCreate9(Ex) with GetProcAddress (RENDER.md §1);
// the one IAT slot of d3d9.dll (D3DPERF_SetOptions) is bound by S3's loader through S4's export table, which falls
// back to modules registered with fnvnx_register_module. toolchain/game/boot.cpp calls fnvnx_d3d9_register()
// (weak) in its priority-101 constructor, before the image's imports are bound.
//
// Direct3DCreate9 is the proxy entry (nine_proxy_rt.cpp) to Gallium Nine in the host module. Direct3DCreate9Ex is
// not offered (the engine falls back to Direct3DCreate9 when GetProcAddress returns NULL). The D3DPERF_* markers
// (PIX) are no-ops.
#include <d3d9.h>
#include <string.h>

extern "C" {
typedef FARPROC (*fnvnx_module_resolver)(const char* proc);
void fnvnx_register_module(const char* dll_name, fnvnx_module_resolver resolve);   // platform/win32/module.cpp
IDirect3D9* WINAPI Direct3DCreate9(UINT sdk);                                       // nine_proxy_rt.cpp

int WINAPI D3DPERF_BeginEvent(D3DCOLOR, LPCWSTR) { return -1; }
int WINAPI D3DPERF_EndEvent(void) { return -1; }
void WINAPI D3DPERF_SetMarker(D3DCOLOR, LPCWSTR) {}
void WINAPI D3DPERF_SetRegion(D3DCOLOR, LPCWSTR) {}
BOOL WINAPI D3DPERF_QueryRepeatFrame(void) { return FALSE; }
void WINAPI D3DPERF_SetOptions(DWORD) {}
DWORD WINAPI D3DPERF_GetStatus(void) { return 0; }
}

namespace {
struct Export { const char* name; FARPROC fn; };
const Export kExports[] = {
    {"Direct3DCreate9", (FARPROC)Direct3DCreate9},
    {"D3DPERF_BeginEvent", (FARPROC)D3DPERF_BeginEvent},
    {"D3DPERF_EndEvent", (FARPROC)D3DPERF_EndEvent},
    {"D3DPERF_SetMarker", (FARPROC)D3DPERF_SetMarker},
    {"D3DPERF_SetRegion", (FARPROC)D3DPERF_SetRegion},
    {"D3DPERF_QueryRepeatFrame", (FARPROC)D3DPERF_QueryRepeatFrame},
    {"D3DPERF_SetOptions", (FARPROC)D3DPERF_SetOptions},
    {"D3DPERF_GetStatus", (FARPROC)D3DPERF_GetStatus},
};
FARPROC resolve(const char* proc) {
    if (!proc) return nullptr;
    for (const Export& e : kExports)
        if (!strcmp(e.name, proc)) return e.fn;
    return nullptr;   // Direct3DCreate9Ex and anything else: not provided
}
int g_done;
} // namespace

extern "C" void fnvnx_d3d9_register(void) {
    if (__atomic_exchange_n(&g_done, 1, __ATOMIC_ACQ_REL)) return;
    fnvnx_register_module("d3d9.dll", resolve);
}
