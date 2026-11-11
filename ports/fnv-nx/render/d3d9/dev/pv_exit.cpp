// pv_exit.cpp: ProcessVertices, SetClipStatus, GetClipStatus of both device classes.
// Owner decision of 2026-11-11 (relayed by the orchestrator): "as in Wine, but a crash if we do get called". These methods are NOT implemented:
// the Microsoft contract needs the bound vertex pipeline executed on the CPU (fixed function and vs_1_1 .. vs_3_0) and, under software vertex
// processing, clip codes computed for every draw; Wine 9.0 (the reference) does neither (wined3d_device_process_vertices ignores the vertex shader,
// the clip status calls have no effect). A silent success here would be a fake body, so a call ends the process through the platform's process
// exit (os_exit_process, the host exit slot) with a code that names the method. The codes are data of this file; nothing else uses the range.
#include "d9impl.gen.h"
extern "C" void os_exit_process(int code) __attribute__((noreturn));   // platform/os/os.h
enum : int {
    NX_EXIT_D3D9_ProcessVertices = 0x6E780101,   // IDirect3DDevice9::ProcessVertices, IDirect3DDevice9Ex::ProcessVertices
    NX_EXIT_D3D9_SetClipStatus = 0x6E780102,     // ::SetClipStatus
    NX_EXIT_D3D9_GetClipStatus = 0x6E780103,     // ::GetClipStatus
};
#define NX_PV_EXIT(C)                                                                                                                                              \
    HRESULT STDMETHODCALLTYPE C::ProcessVertices(UINT, UINT, UINT, IDirect3DVertexBuffer9*, IDirect3DVertexDeclaration9*, DWORD) { os_exit_process(NX_EXIT_D3D9_ProcessVertices); } \
    HRESULT STDMETHODCALLTYPE C::SetClipStatus(const D3DCLIPSTATUS9*) { os_exit_process(NX_EXIT_D3D9_SetClipStatus); }                                            \
    HRESULT STDMETHODCALLTYPE C::GetClipStatus(D3DCLIPSTATUS9*) { os_exit_process(NX_EXIT_D3D9_GetClipStatus); }
NX_PV_EXIT(NxDevice9)
NX_PV_EXIT(NxDevice9Ex)
