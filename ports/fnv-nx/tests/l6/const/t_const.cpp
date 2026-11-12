// driver of test_const.py: every constant-result body of the d3d9 layer, called through its real entry (export, validator object, both device classes).
// Each call is bracketed on stderr by "@@ <case> <n>" lines; the product's fnvnx_wine_fixme (app_unk_native.cpp) writes "fnvnx-wine-fixme: <id>" to stderr between them.
// The result and the out-state of the call are printed on stdout as "R <case> <n> <hex result> [extra]"; test_const.py compares both streams with the rows of const_fixme.tsv.
#include "t_common.h"
#include <string>
extern "C" {
int WINAPI D3DPERF_BeginEvent(D3DCOLOR, LPCWSTR); int WINAPI D3DPERF_EndEvent(void); DWORD WINAPI D3DPERF_GetStatus(void); BOOL WINAPI D3DPERF_QueryRepeatFrame(void);
void WINAPI D3DPERF_SetMarker(D3DCOLOR, LPCWSTR); void WINAPI D3DPERF_SetOptions(DWORD); void WINAPI D3DPERF_SetRegion(D3DCOLOR, LPCWSTR); void WINAPI DebugSetMute(void);
IDirect3DShaderValidator9* WINAPI Direct3DShaderValidatorCreate9(void);
}
static int g_n;
static void B(const char* c) { fflush(stdout); fprintf(stderr, "@@ %s %d\n", c, g_n); fflush(stderr); }
static void E(const char* c, uint32_t r, const char* extra = "") { fflush(stderr); printf("R %s %d %08x %s\n", c, g_n, r, extra); fflush(stdout); g_n++; }
#define CALL(name, expr) do { B(name); uint32_t r_ = (uint32_t)(expr); E(name, r_); } while (0)
#define CALLV(name, expr) do { B(name); expr; E(name, 0); } while (0)
static const WCHAR kName[] = {'m', 'a', 'r', 'k', 0};
static void dev_cases(IDirect3DDevice9* d, const char* tag) {
    char c[64]; static const BOOL args[] = {TRUE, FALSE, 2, (BOOL)0x80000000, -1};
    snprintf(c, sizeof c, "SetDialogBoxMode.%s", tag);
    for (BOOL a : args) { B(c); uint32_t r = (uint32_t)d->SetDialogBoxMode(a); E(c, r); }
    B(c); uint32_t r = (uint32_t)d->SetDialogBoxMode(TRUE); E(c, r);   // the device is still usable, state untouched
    D3DCLIPSTATUS9 cs; memset(&cs, 0x5a, sizeof cs); CALL((std::string("probe.GetClipStatus.") + tag).c_str(), d->GetClipStatus(&cs));   // another Wine-stub method (id 3): a call after SetDialogBoxMode still logs one line of its own
}
int main() {
    setvbuf(stdout, nullptr, _IOLBF, 0);
    for (int i = 0; i < 3; i++) CALL("D3DPERF_GetStatus", D3DPERF_GetStatus());
    for (int i = 0; i < 3; i++) CALL("D3DPERF_QueryRepeatFrame", D3DPERF_QueryRepeatFrame());
    CALLV("D3DPERF_SetMarker", D3DPERF_SetMarker(0xff00ff00, kName)); CALLV("D3DPERF_SetMarker", D3DPERF_SetMarker(0, nullptr)); CALLV("D3DPERF_SetMarker", D3DPERF_SetMarker(0xffffffff, kName));
    CALLV("D3DPERF_SetOptions", D3DPERF_SetOptions(0)); CALLV("D3DPERF_SetOptions", D3DPERF_SetOptions(1)); CALLV("D3DPERF_SetOptions", D3DPERF_SetOptions(0xffffffff));
    CALLV("D3DPERF_SetRegion", D3DPERF_SetRegion(0xff00ff00, kName)); CALLV("D3DPERF_SetRegion", D3DPERF_SetRegion(0, nullptr)); CALLV("D3DPERF_SetRegion", D3DPERF_SetRegion(1, kName));
    // no-id rows: BeginEvent / EndEvent are real (the event level)
    CALL("D3DPERF_BeginEvent", D3DPERF_BeginEvent(0, kName)); CALL("D3DPERF_BeginEvent", D3DPERF_BeginEvent(0, nullptr)); CALL("D3DPERF_EndEvent", D3DPERF_EndEvent()); CALL("D3DPERF_EndEvent", D3DPERF_EndEvent());
    CALLV("DebugSetMute", DebugSetMute()); CALLV("DebugSetMute", DebugSetMute());
    B("Direct3DShaderValidatorCreate9"); IDirect3DShaderValidator9* v = Direct3DShaderValidatorCreate9(); E("Direct3DShaderValidatorCreate9", v != nullptr);
    for (int i = 0; i < 3; i++) { CALL("NxShaderValidator9::AddRef", v->AddRef()); CALL("NxShaderValidator9::Release", v->Release()); }
    // devices: both classes
    IDirect3D9* dx = t_d3d(false); D3DPRESENT_PARAMETERS pp = t_pp(); IDirect3DDevice9* d = t_dev(dx, &pp); if (!d) { printf("NODEV\n"); return 1; }
    IDirect3D9* dxe = t_d3d(true); D3DPRESENT_PARAMETERS pp2 = t_pp(); IDirect3DDevice9* de = t_dev(dxe, &pp2); if (!de) { printf("NODEV\n"); return 1; }
    dev_cases(d, "Dev9"); dev_cases(de, "Dev9Ex");
    // ids 48-51: the patches of both device classes (odd handles and null pointers: Wine looks at nothing) and RegisterSoftwareDevice of both IDirect3D9 classes
    { float seg[4] = {1, 1, 1, 1}; D3DRECTPATCH_INFO ri = {}; D3DTRIPATCH_INFO ti = {}; IDirect3DDevice9* ds[2] = {d, de}; const char* tg[2] = {"Dev9", "Dev9Ex"};
      for (int k = 0; k < 2; k++) { std::string t = std::string(".") + tg[k];
        CALL(("DrawRectPatch" + t).c_str(), ds[k]->DrawRectPatch(1, seg, &ri)); CALL(("DrawRectPatch" + t).c_str(), ds[k]->DrawRectPatch(0xffffffffu, nullptr, nullptr));
        CALL(("DrawTriPatch" + t).c_str(), ds[k]->DrawTriPatch(1, seg, &ti)); CALL(("DrawTriPatch" + t).c_str(), ds[k]->DrawTriPatch(0, nullptr, nullptr));
        CALL(("DeletePatch" + t).c_str(), ds[k]->DeletePatch(1)); CALL(("DeletePatch" + t).c_str(), ds[k]->DeletePatch(0xffffffffu));
        IDirect3D9* ds3 = k ? dxe : dx; CALL(("RegisterSoftwareDevice" + t).c_str(), ds3->RegisterSoftwareDevice(nullptr)); CALL(("RegisterSoftwareDevice" + t).c_str(), ds3->RegisterSoftwareDevice((void*)0x1234)); } }
    IDirect3DDevice9Ex* e = static_cast<IDirect3DDevice9Ex*>(de);
    IDirect3DSurface9* s = nullptr; d->GetRenderTarget(0, &s); IDirect3DSurface9* s2 = nullptr; e->GetRenderTarget(0, &s2);
    IDirect3DVertexBuffer9* vb = nullptr; e->CreateVertexBuffer(64, 0, 0, D3DPOOL_DEFAULT, &vb, nullptr);
    CALL("NxDevice9Ex::ComposeRects", e->ComposeRects(s2, s2, vb, 1, vb, D3DCOMPOSERECTS_COPY, 0, 0));
    CALL("NxDevice9Ex::ComposeRects", e->ComposeRects(nullptr, nullptr, nullptr, 0, nullptr, (D3DCOMPOSERECTSOP)0, 0, 0));
    CALL("NxDevice9Ex::ComposeRects", e->ComposeRects(s, nullptr, vb, 0xFFFFFFFFu, nullptr, D3DCOMPOSERECTS_NEG, 0x7fffffff, (INT)0x80000000));
    CALL("NxDevice9Ex::ComposeRects", e->ComposeRects(s2, s, nullptr, 5, vb, D3DCOMPOSERECTS_FORCE_DWORD, -1, 5));
    printf("DONE %d\n", g_n); fflush(stdout); return 0;
}
