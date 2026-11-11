// platform + spy of tests/l6/present. The RPC harness of tests/l6/copy (t_rpc.cpp, compiled with nxd3d_gpu_call / nxd3d_gpu_alloc / nx_env_profile_of renamed) runs the real glue and the real device over the real GL backend;
// this file defines the platform hooks (platform_hooks.tsv) the device needs and spies on every backend call: it records the NxPresent of each NXGPU_FN_PRESENT, the call before it, the vblank calls, and can make nxd3d_gpu_alloc fail.
#include "t_pres.h"
uint64_t t_rpc_gpu_call(uint32_t fn, const uint64_t* args, uint32_t n);
void* t_rpc_gpu_alloc(size_t n);
PSpy g_ps[1024]; int g_nps = 0, g_nvbl = 0, g_nsubmit_before_present = 0; bool g_fail_alloc = false; int32_t g_ospos[2] = {0, 0};
static uint32_t g_prev = 0xFFFF;
uint64_t nxd3d_gpu_call(uint32_t fn, const uint64_t* args, uint32_t n) {
    if (fn == NXGPU_FN_PRESENT && g_nps < 1024) { PSpy& s = g_ps[g_nps++]; s.p = *(const NxPresent*)(uintptr_t)args[0]; s.prev_fn = g_prev; s.has_ramp = s.p.gamma != 0; if (s.has_ramp) memcpy(s.ramp, (const void*)(uintptr_t)s.p.gamma, sizeof s.ramp); }
    if (fn == NXGPU_FN_WAIT_VBLANK) g_nvbl++;
    g_prev = fn;
    return t_rpc_gpu_call(fn, args, n);
}
void* nxd3d_gpu_alloc(size_t n) { return g_fail_alloc ? nullptr : t_rpc_gpu_alloc(n); }
NxEnvProfile nx_env_profile() { return NX_ENV_HANDHELD; }
bool nxd3d_window_client(void* h, RECT* r) {
    RECT v;
    switch ((uintptr_t)h) { case WIN0: v = {0, 0, SWW, SWH}; break; case WIN1: v = {10, 20, 10 + SWW, 20 + SWH}; break; default: return false; }
    if (r) *r = v;
    return true;
}
uint64_t nxd3d_perf_counter(uint64_t* f) { *f = 1000000; return 0; }
void nxd3d_os_cursor_pos(int32_t* x, int32_t* y) { *x = g_ospos[0]; *y = g_ospos[1]; }
