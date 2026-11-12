// driver of test_exports.py: the export table by name and the D3DPERF contract (reference: Wine 9.0 d3d9_main.c)
#include <stdio.h>
#include <string.h>
#include <initializer_list>
#include "d3d9_adapter.h"
extern "C" { void* nx_d3d9_export(const char*); extern const int nx_d3d9_export_count; const char* nx_d3d9_export_name(int); }
// test-provided factory bodies (the product ones are d3d9_adapter.cpp): distinct marks show which body an export reaches
IDirect3D9* nx_Direct3DCreate9(UINT sdk) { return (IDirect3D9*)(uintptr_t)(0x1000 + sdk); }
HRESULT nx_Direct3DCreate9Ex(UINT sdk, IDirect3D9Ex** out) { *out = (IDirect3D9Ex*)(uintptr_t)(0x2000 + sdk); return (HRESULT)sdk; }
IDirect3DShaderValidator9* nx_Direct3DShaderValidatorCreate9(void) { return (IDirect3DShaderValidator9*)(uintptr_t)0x3000; }
extern "C" void fnvnx_wine_fixme(uint32_t) {}
static int fails = 0, checks = 0;
#define CHECK(c) do { checks++; if (!(c)) { fails++; printf("FAIL %s (line %d)\n", #c, __LINE__); } } while (0)
template <class F> static F ex(const char* n) { return (F)nx_d3d9_export(n); }
int main() {
    for (int i = 0; i < nx_d3d9_export_count; i++) { printf("EXPORT %s\n", nx_d3d9_export_name(i)); CHECK(nx_d3d9_export(nx_d3d9_export_name(i)) != nullptr); }
    CHECK(nx_d3d9_export(nullptr) == nullptr); CHECK(nx_d3d9_export("") == nullptr); CHECK(nx_d3d9_export("Direct3DCreate9E") == nullptr); CHECK(nx_d3d9_export("d3dperf_setoptions") == nullptr);
    CHECK(nx_d3d9_export_name(-1) == nullptr); CHECK(nx_d3d9_export_name(nx_d3d9_export_count) == nullptr);
    auto begin = ex<int (WINAPI*)(D3DCOLOR, LPCWSTR)>("D3DPERF_BeginEvent"); auto end = ex<int (WINAPI*)(void)>("D3DPERF_EndEvent");
    auto status = ex<DWORD (WINAPI*)(void)>("D3DPERF_GetStatus"); auto repeat = ex<BOOL (WINAPI*)(void)>("D3DPERF_QueryRepeatFrame");
    auto marker = ex<void (WINAPI*)(D3DCOLOR, LPCWSTR)>("D3DPERF_SetMarker"); auto region = ex<void (WINAPI*)(D3DCOLOR, LPCWSTR)>("D3DPERF_SetRegion");
    auto options = ex<void (WINAPI*)(DWORD)>("D3DPERF_SetOptions");
    static const WCHAR name[] = {'a', 0};
    CHECK(begin(0, nullptr) == 0); CHECK(begin(0xffffffffu, name) == 1); CHECK(begin(1, name) == 2);
    for (DWORD o : {0u, 1u, 2u, 0x80000000u, 0xffffffffu}) { options(o); CHECK(status() == 0); CHECK(repeat() == FALSE); }
    marker(0, nullptr); marker(5, name); region(0, nullptr); region(5, name);
    CHECK(end() == 2); CHECK(end() == 1); CHECK(end() == 0);
    CHECK(end() == -1); CHECK(begin(0, name) == -1); CHECK(begin(0, name) == 0); CHECK(end() == 0);     // the level is a plain signed counter in the reference: unbalanced End goes below zero
    CHECK(ex<IDirect3D9* (WINAPI*)(UINT)>("Direct3DCreate9")(32) == (IDirect3D9*)(uintptr_t)0x1020);
    IDirect3D9Ex* x = nullptr; CHECK(ex<HRESULT (WINAPI*)(UINT, IDirect3D9Ex**)>("Direct3DCreate9Ex")(7, &x) == 7 && x == (IDirect3D9Ex*)(uintptr_t)0x2007);
    ex<void (WINAPI*)(void)>("DebugSetMute")();
    CHECK(ex<IDirect3DShaderValidator9* (WINAPI*)(void)>("Direct3DShaderValidatorCreate9")() == (IDirect3DShaderValidator9*)(uintptr_t)0x3000);
    printf("PART exports: %d checks, %d failed\n", checks, fails);
    return fails != 0;
}
