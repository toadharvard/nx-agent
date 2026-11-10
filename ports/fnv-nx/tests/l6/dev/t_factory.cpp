#include "t_common.h"
#include <stdlib.h>
const char* g_ctx = ""; const char* g_part = "?"; int g_checks[8], g_fails[8], g_part_i = 0; int g_preloads = 0;
void t_part(const char* name) { g_part = name; g_part_i = name[0] - 'a'; }
bool t_check(bool ok, const char* what, const char* file, int line) {
    g_checks[g_part_i]++;
    if (!ok) { if (g_fails[g_part_i]++ < 12) printf("FAIL %s: %s [%s] (%s:%d)\n", g_part, what, g_ctx, strrchr(file, '/') ? strrchr(file, '/') + 1 : file, line); }
    return ok;
}
static const GUID g_trk = {0x7a1b2c3d, 0x1111, 0x2222, {1, 2, 3, 4, 5, 6, 7, 8}};
void TTracker::watch(IUnknown* o) { IUnknown* me = this; GUID g = g_trk; memcpy(&g.Data1, &me, sizeof me); O(o)->setPrivateData(g, &me, sizeof me, D3DSPD_IUNKNOWN); }
TEnv t_env(uint32_t ad, uint32_t beh, uint32_t w, uint32_t h) {
    TEnv e; e.d3d = new NxDirect3D9; e.d3d->asRoot(1); e.dev = new NxDevice9; e.dev->asDevice(e.d3d);
    NxCreateParams cp = {ad, w, h, beh}; e.dev->init(cp); return e;
}
// surfaces: the descriptor table behind the test-provided IDirect3DSurface9::GetDesc
static D3DSURFACE_DESC g_desc[256]; static NxSurface9* g_surf[256]; static int g_nsurf = 0;
NxSurface9* t_surface(const TEnv& e, uint32_t usage, uint32_t w, uint32_t h) {
    NxSurface9* s = t_child<NxSurface9>(e); D3DSURFACE_DESC d = D3DSURFACE_DESC(); d.Usage = usage; d.Width = w; d.Height = h;
    g_surf[g_nsurf] = s; g_desc[g_nsurf++] = d; return s;
}
NxSurface9* t_subsurface(const TEnv& e, NxObj* cont, uint32_t usage, uint32_t w, uint32_t h) {
    NxSurface9* s = new NxSurface9; s->asSubResource(e.dev, cont); D3DSURFACE_DESC d = D3DSURFACE_DESC(); d.Usage = usage; d.Width = w; d.Height = h; g_surf[g_nsurf] = s; g_desc[g_nsurf++] = d; return s;
}
HRESULT STDMETHODCALLTYPE NxSurface9::GetDesc(D3DSURFACE_DESC* p) { for (int i = 0; i < g_nsurf; i++) if (g_surf[i] == this) { *p = g_desc[i]; return D3D_OK; } return D3DERR_INVALIDCALL; }
// declaration module stand-ins required by state_rt.h: implicit FVF declarations (device-cached: one per FVF, owned by the device), PreLoad backend
IDirect3DVertexDeclaration9* nx_device_fvf_decl(NxDeviceCore* dev, DWORD fvf) {
    static NxVertexDeclaration9* cache[64]; static DWORD key[64]; static NxDeviceCore* owner[64]; static int n = 0;
    for (int i = 0; i < n; i++) if (key[i] == fvf && owner[i] == dev) return cache[i];
    NxVertexDeclaration9* d = new NxVertexDeclaration9; d->asImplicit(dev, nullptr); d->fvf = fvf; cache[n] = d; key[n] = fvf; owner[n++] = dev; return d;
}
NxVertexDeclaration9* t_decl(const TEnv& e) { return t_child<NxVertexDeclaration9>(e); }
void nxd3d_preload(NxObj*) { g_preloads++; }
