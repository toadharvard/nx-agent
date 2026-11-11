// D7: the Ex-only device methods that have a source. Sources: Wine 9.0 d3d9/device.c d3d9_device_{PresentEx, SetMaximumFrameLatency, GetMaximumFrameLatency, CheckDeviceState, GetDisplayModeEx} +
// wined3d_device_{set,get}_max_frame_latency (0 = the default 3, > 30 INVALIDCALL at d3d9); Microsoft IDirect3DDevice9Ex pages for what Wine leaves E_NOTIMPL: GetGPUThreadPriority / SetGPUThreadPriority (range -7..7,
// default 0), WaitForVBlank, CheckResourceResidency (a resource of OURS lives in memory the device manages: always resident). Env rows DEV.GetDisplayModeEx / GetMaximumFrameLatency / CheckDeviceState.
// SetConvolutionMonoKernel, ComposeRects, IDirect3DSwapChain9Ex::GetPresentStats: m1_misc.cpp / m1_stats.cpp (M1). DECISIONS (doubt): GPU thread priority out of range = D3DERR_INVALIDCALL (MS page lists no code); WaitForVBlank on a swap chain index >= 1 = INVALIDCALL.
#include "res_tex.h"
#include "../gen/d9impl.gen.h"
#include "dev_core.h"
#include "dev_hooks.h"
#define INV D3DERR_INVALIDCALL

HRESULT STDMETHODCALLTYPE NxDevice9Ex::PresentEx(const RECT* s, const RECT* dr, HWND ov, const RGNDATA*, DWORD flags) {
    NxDevLife* L = nx_life(this); if (L->state != NX_DEV_OK) return S_PRESENT_OCCLUDED;
    return L->chains[0]->present(s, dr, ov, flags);
}
HRESULT STDMETHODCALLTYPE NxDevice9Ex::GetGPUThreadPriority(INT* p) { if (!p) return INV; *p = nx_life(this)->gpu_prio; return D3D_OK; }
HRESULT STDMETHODCALLTYPE NxDevice9Ex::SetGPUThreadPriority(INT p) { if (p < -7 || p > 7) return INV; nx_life(this)->gpu_prio = p; return D3D_OK; }
HRESULT STDMETHODCALLTYPE NxDevice9Ex::WaitForVBlank(UINT i) { NxDevLife* L = nx_life(this); if (i >= 1 || !L->nchains) return INV; return nxd3d_wait_vblank(L->chains[0]) ? D3DERR_DRIVERINTERNALERROR : D3D_OK; }
HRESULT STDMETHODCALLTYPE NxDevice9Ex::CheckResourceResidency(IDirect3DResource9** r, UINT32 n) {
    if (!r || !n) return INV;
    for (UINT32 i = 0; i < n; i++) { NxObj* o = nx_obj_of(r[i]); if (!o || o->dev != this || !o->ci->rtype) return INV; }
    return D3D_OK;
}
HRESULT STDMETHODCALLTYPE NxDevice9Ex::SetMaximumFrameLatency(UINT n) { if (n > 30) return INV; nx_life(this)->latency = n ? n : 3; return D3D_OK; }
HRESULT STDMETHODCALLTYPE NxDevice9Ex::GetMaximumFrameLatency(UINT* n) { if (!n) return INV; *n = nx_life(this)->latency; return D3D_OK; }
HRESULT STDMETHODCALLTYPE NxDevice9Ex::CheckDeviceState(HWND w) {
    NxDevLife* L = nx_life(this); NxChainCore* c = L->chains[0]; if (c->pp.Windowed) return D3D_OK;
    if (w != c->pp.hDeviceWindow) return L->state == NX_DEV_OK ? S_PRESENT_OCCLUDED : D3D_OK;   // "supposed to check if any other device is in fullscreen mode" (Wine FIXME)
    return L->state == NX_DEV_OK ? D3D_OK : S_PRESENT_OCCLUDED;
}
HRESULT STDMETHODCALLTYPE NxDevice9Ex::GetDisplayModeEx(UINT i, D3DDISPLAYMODEEX* m, D3DDISPLAYROTATION* r) { NxDevLife* L = nx_life(this); if (i >= 1 || !L->nchains) return INV; return L->chains[0]->display_ex(m, r); }
