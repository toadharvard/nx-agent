// m1_stats.cpp: IDirect3DSwapChain9Ex::GetPresentStats and the present counter it shares with GetLastPresentCount. Sources: MS D3DPRESENTSTATS page (the only MS text: there is no method page; "GetPresentStatistics will be
// processed after it is called, regardless of the state of FLIPEX mode PresentEx calls" = every chain answers, no FLIPEX precondition; env: CreateDeviceEx admits D3DSWAPEFFECT_FLIPEX, dev_params.cpp nx_pp_check),
// MS IDirect3DSwapChain9Ex::GetLastPresentCount page, nxgpu4.h rule P5 (60 Hz modelled panel, blanks at k / 60 s of the monotonic clock) and the GetRasterStatus convention (refresh rate 0 in the mode = 60).
//  PresentCount        = the count of successful presents of this chain ("Present ID of the last Present"); GetLastPresentCount reports the same number (SetDialogBoxMode, which MS says also increments it, is not wired: its own contract is open).
//  PresentRefreshCount = the vblank counter V sampled when the last present returned (nxgpu4.h P5: a present with interval >= 1 returns at the blank where its frame becomes visible; interval 0 shows within the current blank interval); 0 before the first.
//  SyncRefreshCount / SyncQPCTime = V of the QPC sample taken when GetPresentStats runs (the scheduler samples on request) and that sample. SyncGPUTime = 0 ("This value is not used").
//  V(c) = floor(c * R / f): c the nxd3d_perf_counter value, f its frequency, R the refresh rate; ASSUMPTION (stated, platform fact): the counter and the display clock of NXGPU_FN_WAIT_VBLANK have one origin, so blank k is at c = k * f / R.
#include "res_tex.h"
#include "../gen/d9impl.gen.h"
#include "dev_core.h"
#include "dev_hooks.h"
#include "../gen/env_table.gen.h"

static uint32_t vblank(NxChainCore* c, uint64_t qpc, uint64_t f) {
    NxEnvMode m; NxDevLife* L = nx_life(c->d); uint64_t r = nxenv_display_mode((int)L->prof, L->cp.AdapterOrdinal, &m) == 0 && m.RefreshRate ? m.RefreshRate : 60;
    if (!f) return 0;
    return (uint32_t)((qpc / f) * r + (qpc % f) * r / f);   // no 64-bit overflow: (qpc % f) * r < f * r, f < 2^57
}
void NxChainCore::presented() { uint64_t f = 0, c = nxd3d_perf_counter(&f); pcount++; pres_vb = vblank(this, c, f); }
HRESULT NxChainCore::present_stats(D3DPRESENTSTATS* s) {
    if (!s) return D3DERR_INVALIDCALL;
    uint64_t f = 0, c = nxd3d_perf_counter(&f); memset(s, 0, sizeof *s);
    s->PresentCount = pcount; s->PresentRefreshCount = pres_vb; s->SyncRefreshCount = vblank(this, c, f); s->SyncQPCTime.QuadPart = (LONGLONG)c; s->SyncGPUTime.QuadPart = 0; return D3D_OK;
}
HRESULT STDMETHODCALLTYPE NxSwapChain9Ex::GetPresentStats(D3DPRESENTSTATS* s) { return present_stats(s); }
