// t_oom.cpp: the fault-injection harness of tests/l6/oom (allocation failure is a result, never a trap). Native -m32 build of the layer (everything of render/d3d9/dev except the device-lifecycle and draw files)
// with a counting, fault-injecting nxd3d_alloc / nxd3d_free / nxd3d_gpu_alloc / nxd3d_gpu_free and a fake backend (nxd3d_gpu_call) whose creations (IMAGE_CREATE4, MEM_CREATE) are allocation events too.
// One scenario touches every public method family that allocates; it is run once to COUNT the allocation events (N), then N times with event k failing (one-shot: modes "one"), then N times with every event from
// k on failing ("sticky"). Per run:  (i) a step that met a failure returns a documented failure HRESULT of its call family (the tables at each STEP) and leaves *out null;  (ii) the device state, the device /
// factory reference counts and the resource registry are the same before and after that step (snapshot through the public Get* interface);  (iii) after everything is released and nxs_shutdown() the allocation
// balance is zero (live CPU blocks, live backend memory, backend images and memory blocks);  (iv) the process never crashes (the python side reads the sentinel on stderr).
// Output: "PART b: n checks, f failed" (one-shot sweep) and "PART c: ..." (sticky sweep); "N <events>" first.
#include "d9impl.gen.h"
#include "res_tex.h"
#include "copy_util.h"
#include "glue_buf.h"
#include "d3d9_adapter.h"
#include "face.h"
#include "dev_core.h"
#include "draw_ffp.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

// ---------------------------------------------------------------------------------------------------- checks
static int g_checks[8], g_fails[8], g_pi = 1;
static const char* g_ctx = ""; static const char* g_run = "";
static bool check(bool ok, const char* what, int line) {
    g_checks[g_pi]++;
    if (!ok && g_fails[g_pi]++ < 10) printf("FAIL %c: %s [%s | %s] (t_oom.cpp:%d)\n", 'a' + g_pi, what, g_run, g_ctx, line);
    return ok;
}
#define CHECK(c) check((c), #c, __LINE__)

// ---------------------------------------------------------------------------------------------------- injection + balance
struct Live { void* p; int kind; };
static Live g_live[1 << 15]; static int g_nlive = 0, g_bad_free = 0;
static uint64_t g_count = 0, g_fail_at = 0, g_nhit = 0; static bool g_armed = false, g_sticky = false;
static uint8_t g_img[NX_MAX_IMAGES + 1]; static int g_nimg = 0, g_nblk = 0; static uint32_t g_next_img = 1, g_next_blk = 1; static uint8_t g_blk[1 << 16];
static const char* g_ctx_now = "(setup)";                       // the call of the scenario that is running (set by every STEP and by the raw calls)
struct Cov { const char* name; uint64_t hits; }; static Cov g_cov[160]; static int g_ncov = 0; static uint64_t g_hits_total = 0;
static void cover(const char* name) { for (int i = 0; i < g_ncov; i++) if (!strcmp(g_cov[i].name, name)) { g_cov[i].hits++; return; } if (g_ncov < 160) g_cov[g_ncov++] = {name, 1}; }
static bool inject() {
    if (!g_armed) return false;
    g_count++;
    if (g_fail_at && (g_sticky ? g_count >= g_fail_at : g_count == g_fail_at)) { g_nhit++; g_hits_total++; cover(g_ctx_now); return true; }
    return false;
}
static void track(void* p, int kind) { if (g_nlive < (int)(sizeof g_live / sizeof *g_live)) g_live[g_nlive++] = {p, kind}; }
static void untrack(void* p, int kind) {
    for (int i = g_nlive - 1; i >= 0; i--) if (g_live[i].p == p && g_live[i].kind == kind) { g_live[i] = g_live[--g_nlive]; return; }
    g_bad_free++;
}
void* nxd3d_alloc(size_t n) { if (inject()) return nullptr; void* p = malloc(n ? n : 1); track(p, 0); return p; }
void nxd3d_free(void* p) { if (!p) return; untrack(p, 0); free(p); }
void* nxd3d_gpu_alloc(size_t n) { if (inject()) return nullptr; void* p = nullptr; if (posix_memalign(&p, 4096, n ? n : 4096)) return nullptr; track(p, 1); return p; }
void nxd3d_gpu_free(void* p) { if (!p) return; untrack(p, 1); free(p); }
uint64_t nxd3d_gpu_call(uint32_t fn, const uint64_t* a, uint32_t) {
    switch (fn) {
    case NXGPU_FN_IMAGE_CREATE4: { if (inject()) return NXGPU_BAD_CALL; uint32_t id = g_next_img++; g_img[id % NX_MAX_IMAGES] = 1; g_nimg++; return id % (NX_MAX_IMAGES - 1) + 1; }
    case NXGPU_FN_IMAGE_DESTROY: { if (a[0] >= NX_MAX_IMAGES) return NXGPU_BAD_CALL; g_nimg--; return 0; }
    case NXGPU_FN_MEM_CREATE: { if (inject()) return NXGPU_BAD_CALL; g_nblk++; return g_next_blk++; }
    case NXGPU_FN_MEM_DESTROY: g_nblk--; return 0;
    case NXGPU_FN_SHADER_CREATE_D3D: { static uint32_t id = 1000; if (inject()) return NXGPU_BAD_CALL; return id++; }
    case NXGPU_FN_SAMPLER_CREATE: { static uint32_t sid = 1; return sid++ % 200 + 1; }
    case NXGPU_FN_QUERY_RESULT: { uint64_t* out = (uint64_t*)a[2]; *out = 1; return 0; }
    default: return 0;   // SUBMIT, WAIT_FRAME, IMAGE_READ (the staging keeps whatever it held)
    }
}
struct FakeDc { void* mem; };
void* nxd3d_dc_create(void* mem, uint32_t, uint32_t, uint32_t, uint32_t) { FakeDc* d = (FakeDc*)malloc(sizeof(FakeDc)); d->mem = mem; return d; }
void nxd3d_dc_release(void* dc) { free(dc); }
NxEnvProfile nx_env_profile() { return NX_ENV_HANDHELD; }
// platform facts of the lifecycle module (dev_hooks.h): every non-null window is an 800 x 600 client area at (10, 20)
bool nxd3d_window_client(void* h, RECT* r) { if (!h) return false; if (r) { r->left = 10; r->top = 20; r->right = 810; r->bottom = 620; } return true; }
uint64_t nxd3d_perf_counter(uint64_t* f) { static uint64_t c = 0; *f = 1000000; c += 7777; return c; }
void nxd3d_os_cursor_pos(int32_t* x, int32_t* y) { *x = 100; *y = 100; }
// the fixed-function programs are another module (ffp_*.cpp): doubles that name two backend shaders, as tests/l6/draw does
static uint8_t g_ubo_v[64], g_ubo_p[32];
uint32_t nx_ffp_vs_program(NxDeviceCore*, NxFfpVsBind* o) { memset((void*)o, 0, sizeof *o); o->n_in = 1; o->in_pair[0] = FNVNX_DK_PAIR(0, 0); o->in_loc[0] = 0; o->n_ubo = 1; o->ubo[0] = {4, sizeof g_ubo_v, g_ubo_v}; return 90; }
uint32_t nx_ffp_ps_program(NxDeviceCore*, NxFfpPsBind* o) { memset((void*)o, 0, sizeof *o); o->n_in = 1; o->in_pair[0] = FNVNX_DK_PAIR(5, 0); o->n_ubo = 1; o->ubo[0] = {3, sizeof g_ubo_p, g_ubo_p}; return 91; }
extern "C" uint32_t fnvnx_import_key(const char*, const char*, uint32_t ordinal) { return 0x1000 + ordinal; }   // the loader's import key of a method row (any nonzero key: the table is only read through the face)
HRESULT STDMETHODCALLTYPE NxDevice9Ex::CheckResourceResidency(IDirect3DResource9**, UINT32) { return D3D_OK; }   // device-lifecycle module (not linked here): the thunk under test only converts its array
extern "C" HRESULT nxb_IDirect3DDevice9Ex_CheckResourceResidency(void* nxb_this, void** resources, UINT32 resource_count);   // d9bridge.gen.cpp: the one generated thunk that allocates

// ---------------------------------------------------------------------------------------------------- the world
struct W {
    NxDirect3D9* d3d = nullptr; NxDevice9* dev = nullptr; IDirect3DDevice9* i9 = nullptr; NxDirect3D9Ex* d3dx = nullptr; NxDevice9Ex* devx = nullptr;
    IDirect3D9* f9 = nullptr; IDirect3D9Ex* f9x = nullptr;
    IDirect3DVertexBuffer9* vb[3] = {}; IDirect3DIndexBuffer9* ib[2] = {}; IDirect3DVertexDeclaration9* decl = nullptr; IDirect3DVertexShader9* vs = nullptr; IDirect3DPixelShader9* ps = nullptr;
    IDirect3DQuery9* q[4] = {};
    IDirect3DTexture9* tex[8] = {}; IDirect3DCubeTexture9* cube = nullptr; IDirect3DVolumeTexture9* vol = nullptr;
    IDirect3DSurface9* rt = nullptr; IDirect3DSurface9* rt2 = nullptr; IDirect3DSurface9* rt3 = nullptr; IDirect3DSurface9* ds = nullptr; IDirect3DSurface9* sys = nullptr; IDirect3DSurface9* sysc = nullptr; IDirect3DSurface9* defc = nullptr;
    IDirect3DStateBlock9* sb[4] = {}; bool recording = false; HDC dc = nullptr;
    IDirect3DDevice9* ld = nullptr; IDirect3DSwapChain9* sc = nullptr;   // the lifecycle device made inside the scenario and its additional swap chain
};
template <class T> static void rel(T*& p) { if (p) { p->Release(); p = nullptr; } }

struct Snap {
    uint64_t h; uint32_t dev_ref, d3d_ref, nreg;
    bool operator==(const Snap& o) const { return h == o.h && dev_ref == o.dev_ref && d3d_ref == o.d3d_ref && nreg == o.nreg; }
};
static void mix(uint64_t& h, const void* p, size_t n) { const uint8_t* b = (const uint8_t*)p; for (size_t i = 0; i < n; i++) { h ^= b[i]; h *= 0x100000001b3ull; } }
static Snap snap(W& w) {   // through the public Get* interface, plus the counts and the resource registry (every Create* resource of the device is linked there)
    Snap s = {0xcbf29ce484222325ull, 0, 0, 0}; IDirect3DDevice9* d = w.i9; NxObj* dev = w.dev;
    static const D3DRENDERSTATETYPE rs[] = {D3DRS_ZENABLE, D3DRS_FILLMODE, D3DRS_LIGHTING, D3DRS_AMBIENT, D3DRS_CULLMODE, D3DRS_ALPHABLENDENABLE, D3DRS_SCISSORTESTENABLE, D3DRS_SRGBWRITEENABLE};
    for (D3DRENDERSTATETYPE r : rs) { DWORD v = 0xdeadbeef; HRESULT hr = d->GetRenderState(r, &v); mix(s.h, &hr, 4); mix(s.h, &v, 4); }
    for (DWORD i = 0; i < 600; i++) {
        D3DLIGHT9 l; BOOL en = 7; HRESULT hr = d->GetLight(i, &l); if (SUCCEEDED(hr)) { mix(s.h, &i, 4); mix(s.h, &l, sizeof l); if (SUCCEEDED(d->GetLightEnable(i, &en))) mix(s.h, &en, 4); }
        if (i < 16) { PALETTEENTRY pe[256]; if (SUCCEEDED(d->GetPaletteEntries(i, pe))) { mix(s.h, &i, 4); mix(s.h, pe, sizeof pe); } }
    }
    DWORD fvf = 0xFFFFFFFF; HRESULT hr = d->GetFVF(&fvf); mix(s.h, &hr, 4); mix(s.h, &fvf, 4);
    IDirect3DVertexDeclaration9* vd = nullptr; hr = d->GetVertexDeclaration(&vd); mix(s.h, &vd, sizeof vd); if (vd) vd->Release();
    mix(s.h, &static_cast<NxDeviceCore*>(dev)->rec, sizeof(void*));
    s.dev_ref = dev->addref() - 1; dev->release(); NxObj* d3d = w.d3d; s.d3d_ref = d3d->addref() - 1; d3d->release();
    for (NxObj* r = dev->rhead; r; r = r->rnext) { s.nreg++; mix(s.h, &r->pub, 4); }
    return s;
}
static bool in(HRESULT hr, HRESULT a, HRESULT b = 0, HRESULT c = 0) { return hr == a || (b && hr == b) || (c && hr == c); }
static const HRESULT OOM = E_OUTOFMEMORY, OVM = D3DERR_OUTOFVIDEOMEMORY, DRV = D3DERR_DRIVERINTERNALERROR, NAV = D3DERR_NOTAVAILABLE;
static int g_nsteps = 0;
// STEP: a call of the scenario. No failure met inside it: the documented success; a failure met: one of the documented failure results, and the snapshot is unchanged.
#define STEP(WORLD, NAME, OK, FAIL_A, FAIL_B, EXPR)                                                                                                                                  \
    do {                                                                                                                                                                              \
        g_ctx = NAME; g_ctx_now = NAME; g_nsteps++; Snap s0_ = snap(WORLD); uint64_t h0_ = g_nhit; HRESULT hr_ = (EXPR); bool hit_ = g_nhit != h0_;                                                     \
        if (!hit_) CHECK(hr_ == (OK)); else { CHECK(in(hr_, FAIL_A, FAIL_B)); CHECK(snap(WORLD) == s0_); }                                                                              \
    } while (0)
// OUT: the same for a call that creates an object: on a failure *out stays null (checked by the caller of this macro through `p`)
#define CREATE(WORLD, NAME, FAIL_A, FAIL_B, PTR, EXPR) do { PTR = nullptr; uint64_t hc_ = g_nhit; STEP(WORLD, NAME, D3D_OK, FAIL_A, FAIL_B, EXPR); if (g_nhit != hc_) CHECK(PTR == nullptr); } while (0)

static const uint32_t FCC_DXT1 = 0x31545844;

static void scenario(W& w) {
    IDirect3DDevice9* d = w.i9; D3DLIGHT9 L; memset(&L, 0, sizeof L); L.Type = D3DLIGHT_POINT; L.Range = 10; L.Diffuse.r = 1;
    uint64_t hits0 = g_nhit;
    // ---- factory
    g_ctx = g_ctx_now = "Direct3DCreate9"; { Snap s0 = snap(w); uint64_t h0 = g_nhit; w.f9 = nx_Direct3DCreate9(32); bool hit = g_nhit != h0; CHECK(hit ? w.f9 == nullptr : w.f9 != nullptr); CHECK(snap(w) == s0); }
    g_ctx = g_ctx_now = "Direct3DCreate9Ex"; { uint64_t h0 = g_nhit; HRESULT hr = nx_Direct3DCreate9Ex(32, &w.f9x); bool hit = g_nhit != h0; if (!hit) CHECK(hr == D3D_OK && w.f9x); else CHECK(in(hr, OOM, NAV) && !w.f9x); }
    // ---- lights (the sparse set grows 8 -> 16 at the 9th), palettes, FVF
    for (DWORD i = 0; i < 16; i++) { L.Position.x = (float)i; STEP(w, "SetLight", D3D_OK, OOM, OOM, d->SetLight(i * 3, &L)); }
    STEP(w, "LightEnable (existing)", D3D_OK, OOM, OOM, d->LightEnable(0, TRUE));
    PALETTEENTRY pe[256]; for (int i = 0; i < 256; i++) { pe[i].peRed = (BYTE)i; pe[i].peGreen = 1; pe[i].peBlue = 2; pe[i].peFlags = 255; }
    STEP(w, "SetPaletteEntries", D3D_OK, OOM, OOM, d->SetPaletteEntries(5, pe)); STEP(w, "SetPaletteEntries", D3D_OK, OOM, OOM, d->SetPaletteEntries(9, pe));
    STEP(w, "SetFVF", D3D_OK, DRV, DRV, d->SetFVF(D3DFVF_XYZ | D3DFVF_TEX1)); STEP(w, "SetFVF", D3D_OK, DRV, DRV, d->SetFVF(D3DFVF_XYZRHW | D3DFVF_DIFFUSE));
    STEP(w, "SetFVF (cached)", D3D_OK, DRV, DRV, d->SetFVF(D3DFVF_XYZ | D3DFVF_TEX1));
    // ---- state blocks: record (lights land in the recording block), End, Apply (all or nothing), Capture, CreateStateBlock (copies the device's lights)
    g_ctx = g_ctx_now = "BeginStateBlock"; { uint64_t h0 = g_nhit; Snap s0 = snap(w); HRESULT hr = d->BeginStateBlock(); if (g_nhit == h0) { CHECK(hr == D3D_OK); w.recording = true; } else { CHECK(hr == OOM); CHECK(snap(w) == s0); } }
    if (w.recording) {
        L.Position.x = 500; STEP(w, "SetLight (recording)", D3D_OK, OOM, OOM, d->SetLight(500, &L)); STEP(w, "LightEnable (recording)", D3D_OK, OOM, OOM, d->LightEnable(501, TRUE));
        STEP(w, "SetRenderState (recording)", D3D_OK, OOM, OOM, d->SetRenderState(D3DRS_ZENABLE, FALSE));
        STEP(w, "EndStateBlock", D3D_OK, OOM, OOM, d->EndStateBlock(&w.sb[0])); w.recording = false;
    }
    if (w.sb[0]) STEP(w, "StateBlock::Apply", D3D_OK, OOM, OOM, w.sb[0]->Apply());
    CREATE(w, "CreateStateBlock(ALL)", OOM, OOM, w.sb[1], d->CreateStateBlock(D3DSBT_ALL, &w.sb[1]));
    CREATE(w, "CreateStateBlock(PIXEL)", OOM, OOM, w.sb[2], d->CreateStateBlock(D3DSBT_PIXELSTATE, &w.sb[2]));
    CREATE(w, "CreateStateBlock(VERTEX)", OOM, OOM, w.sb[3], d->CreateStateBlock(D3DSBT_VERTEXSTATE, &w.sb[3]));
    if (w.sb[1]) { STEP(w, "StateBlock::Capture", D3D_OK, OOM, OOM, w.sb[1]->Capture()); STEP(w, "StateBlock::Apply (full)", D3D_OK, OOM, OOM, w.sb[1]->Apply()); }
    STEP(w, "LightEnable (new light)", D3D_OK, OOM, OOM, d->LightEnable(100, TRUE));
    // ---- buffers, declaration, shaders, queries
    CREATE(w, "CreateVertexBuffer(MANAGED)", OVM, OOM, w.vb[0], d->CreateVertexBuffer(1024, 0, D3DFVF_XYZ, D3DPOOL_MANAGED, &w.vb[0], nullptr));
    CREATE(w, "CreateVertexBuffer(DEFAULT)", OVM, OOM, w.vb[1], d->CreateVertexBuffer(4096, D3DUSAGE_DYNAMIC | D3DUSAGE_WRITEONLY, 0, D3DPOOL_DEFAULT, &w.vb[1], nullptr));
    CREATE(w, "CreateVertexBuffer(SYSTEMMEM)", OVM, OOM, w.vb[2], d->CreateVertexBuffer(256, 0, 0, D3DPOOL_SYSTEMMEM, &w.vb[2], nullptr));
    CREATE(w, "CreateIndexBuffer(16)", OVM, OOM, w.ib[0], d->CreateIndexBuffer(512, 0, D3DFMT_INDEX16, D3DPOOL_MANAGED, &w.ib[0], nullptr));
    CREATE(w, "CreateIndexBuffer(32)", OVM, OOM, w.ib[1], d->CreateIndexBuffer(512, 0, D3DFMT_INDEX32, D3DPOOL_DEFAULT, &w.ib[1], nullptr));
    D3DVERTEXELEMENT9 el[] = {{0, 0, D3DDECLTYPE_FLOAT3, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_POSITION, 0}, {0, 12, D3DDECLTYPE_FLOAT2, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_TEXCOORD, 0}, D3DDECL_END()};
    CREATE(w, "CreateVertexDeclaration", OOM, OVM, w.decl, d->CreateVertexDeclaration(el, &w.decl));
    static const DWORD vsc[] = {0xFFFE0101, 0x00000001, 0x800F0000, 0x90E40000, 0x0000FFFF}, psc[] = {0xFFFF0101, 0x00000001, 0x800F0000, 0xA0E40000, 0x0000FFFF};
    CREATE(w, "CreateVertexShader", OOM, OOM, w.vs, d->CreateVertexShader(vsc, &w.vs)); CREATE(w, "CreatePixelShader", OOM, OOM, w.ps, d->CreatePixelShader(psc, &w.ps));
    const D3DQUERYTYPE qt[4] = {D3DQUERYTYPE_EVENT, D3DQUERYTYPE_OCCLUSION, D3DQUERYTYPE_TIMESTAMP, D3DQUERYTYPE_TIMESTAMPFREQ};
    for (int i = 0; i < 4; i++) CREATE(w, "CreateQuery", OOM, OVM, w.q[i], d->CreateQuery(qt[i], &w.q[i]));
    // ---- textures, volumes, cube maps, surfaces (every pool, compressed, a converted format, AUTOGENMIPMAP)
    CREATE(w, "CreateTexture(MANAGED, 3 levels)", OVM, OOM, w.tex[0], d->CreateTexture(64, 64, 3, 0, D3DFMT_X8R8G8B8, D3DPOOL_MANAGED, &w.tex[0], nullptr));
    CREATE(w, "CreateTexture(DEFAULT RT)", OVM, OOM, w.tex[1], d->CreateTexture(32, 32, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &w.tex[1], nullptr));
    CREATE(w, "CreateTexture(SYSTEMMEM)", OVM, OOM, w.tex[2], d->CreateTexture(64, 64, 3, 0, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &w.tex[2], nullptr));
    CREATE(w, "CreateTexture(DEFAULT, 3 levels)", OVM, OOM, w.tex[3], d->CreateTexture(64, 64, 3, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &w.tex[3], nullptr));
    CREATE(w, "CreateTexture(AUTOGENMIPMAP)", OVM, OOM, w.tex[4], d->CreateTexture(16, 16, 0, D3DUSAGE_AUTOGENMIPMAP, D3DFMT_X8R8G8B8, D3DPOOL_DEFAULT, &w.tex[4], nullptr));
    CREATE(w, "CreateTexture(DXT1)", OVM, OOM, w.tex[5], d->CreateTexture(16, 16, 3, 0, (D3DFORMAT)FCC_DXT1, D3DPOOL_MANAGED, &w.tex[5], nullptr));
    CREATE(w, "CreateTexture(SYSTEMMEM, A8, 2 levels)", OVM, OOM, w.tex[6], d->CreateTexture(16, 16, 2, 0, D3DFMT_A8, D3DPOOL_SYSTEMMEM, &w.tex[6], nullptr));
    CREATE(w, "CreateTexture(DEFAULT, A8, 2 levels)", OVM, OOM, w.tex[7], d->CreateTexture(16, 16, 2, 0, D3DFMT_A8, D3DPOOL_DEFAULT, &w.tex[7], nullptr));
    CREATE(w, "CreateCubeTexture", OVM, OOM, w.cube, d->CreateCubeTexture(16, 2, 0, D3DFMT_X8R8G8B8, D3DPOOL_MANAGED, &w.cube, nullptr));
    CREATE(w, "CreateVolumeTexture", OVM, OOM, w.vol, d->CreateVolumeTexture(8, 8, 4, 2, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &w.vol, nullptr));
    CREATE(w, "CreateRenderTarget(lockable)", OVM, OOM, w.rt, d->CreateRenderTarget(32, 32, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_NONE, 0, TRUE, &w.rt, nullptr));
    CREATE(w, "CreateRenderTarget", OVM, OOM, w.rt2, d->CreateRenderTarget(32, 32, D3DFMT_X8R8G8B8, D3DMULTISAMPLE_NONE, 0, FALSE, &w.rt2, nullptr));
    CREATE(w, "CreateRenderTarget (3)", OVM, OOM, w.rt3, d->CreateRenderTarget(32, 32, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_NONE, 0, FALSE, &w.rt3, nullptr));
    CREATE(w, "CreateDepthStencilSurface", OVM, OOM, w.ds, d->CreateDepthStencilSurface(32, 32, D3DFMT_D24S8, D3DMULTISAMPLE_NONE, 0, FALSE, &w.ds, nullptr));
    CREATE(w, "CreateOffscreenPlainSurface(SYSTEMMEM)", OVM, OOM, w.sys, d->CreateOffscreenPlainSurface(32, 32, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &w.sys, nullptr));
    CREATE(w, "CreateOffscreenPlainSurface(SYSTEMMEM, R8G8B8)", OVM, OOM, w.sysc, d->CreateOffscreenPlainSurface(16, 16, D3DFMT_R8G8B8, D3DPOOL_SYSTEMMEM, &w.sysc, nullptr));
    CREATE(w, "CreateOffscreenPlainSurface(DEFAULT, R8G8B8)", OVM, OOM, w.defc, d->CreateOffscreenPlainSurface(16, 16, D3DFMT_R8G8B8, D3DPOOL_DEFAULT, &w.defc, nullptr));
    // ---- private data (data and IUnknown entries), on objects of every kind
    static const GUID g1 = {1, 2, 3, {4, 5, 6, 7, 8, 9, 10, 11}}, g2 = {9, 2, 3, {4, 5, 6, 7, 8, 9, 10, 11}}; char blob[40]; memset(blob, 7, sizeof blob);
    if (w.tex[0]) { STEP(w, "SetPrivateData(texture)", D3D_OK, OOM, OOM, w.tex[0]->SetPrivateData(g1, blob, sizeof blob, 0)); STEP(w, "SetPrivateData(replace)", D3D_OK, OOM, OOM, w.tex[0]->SetPrivateData(g1, blob, 8, 0)); }
    if (w.vb[0] && w.q[0]) { IUnknown* u = w.q[0]; STEP(w, "SetPrivateData(IUnknown)", D3D_OK, OOM, OOM, w.vb[0]->SetPrivateData(g2, &u, sizeof u, D3DSPD_IUNKNOWN)); }
    if (w.rt) STEP(w, "SetPrivateData(surface)", D3D_OK, OOM, OOM, w.rt->SetPrivateData(g1, blob, sizeof blob, 0));
    if (w.cube) { IDirect3DSurface9* s = nullptr; if (SUCCEEDED(w.cube->GetCubeMapSurface(D3DCUBEMAP_FACE_NEGATIVE_X, 1, &s))) { STEP(w, "SetPrivateData(cube face)", D3D_OK, OOM, OOM, s->SetPrivateData(g1, blob, sizeof blob, 0)); s->Release(); } }
    // ---- locks (no allocation: the read-back staging is reserved when the image is made; the CPU copy exists), PreLoad / GenerateMipSubLevels (void: a failure leaves the dirty regions)
    D3DLOCKED_RECT lr; D3DLOCKED_BOX lb;
    if (w.tex[0]) { STEP(w, "LockRect", D3D_OK, OOM, OOM, w.tex[0]->LockRect(1, &lr, nullptr, 0)); STEP(w, "UnlockRect", D3D_OK, OOM, OOM, w.tex[0]->UnlockRect(1)); }
    if (w.vol) { STEP(w, "LockBox", D3D_OK, OOM, OOM, w.vol->LockBox(0, &lb, nullptr, 0)); STEP(w, "UnlockBox", D3D_OK, OOM, OOM, w.vol->UnlockBox(0)); }
    if (w.vb[0]) { void* p; STEP(w, "Lock(vb)", D3D_OK, OOM, OOM, w.vb[0]->Lock(0, 0, &p, 0)); STEP(w, "Unlock(vb)", D3D_OK, OOM, OOM, w.vb[0]->Unlock()); }
    g_ctx = g_ctx_now = "PreLoad";
    if (w.tex[0]) { Snap s0 = snap(w); w.tex[0]->PreLoad(); CHECK(snap(w) == s0); w.tex[0]->PreLoad(); }
    if (w.vb[0]) { Snap s0 = snap(w); w.vb[0]->PreLoad(); CHECK(snap(w) == s0); w.vb[0]->PreLoad(); }
    if (w.ib[0]) { Snap s0 = snap(w); w.ib[0]->PreLoad(); CHECK(snap(w) == s0); w.ib[0]->PreLoad(); }
    g_ctx = g_ctx_now = "GenerateMipSubLevels";
    if (w.tex[4]) { Snap s0 = snap(w); w.tex[4]->GenerateMipSubLevels(); CHECK(snap(w) == s0); }
    // ---- the generated bridge thunk with an allocation (CheckResourceResidency: the guest's face array is converted into a native array), on the Ex device
    if (w.devx && w.vb[0] && w.tex[0] && w.rt) {
        void* faces[3] = {nx_face((IUnknown*)w.vb[0]), nx_face((IUnknown*)w.tex[0]), nx_face((IUnknown*)w.rt)};
        STEP(w, "CheckResourceResidency (bridge thunk)", D3D_OK, OOM, OOM, nxb_IDirect3DDevice9Ex_CheckResourceResidency(nx_face((IUnknown*)static_cast<IDirect3DDevice9Ex*>(w.devx)), faces, 3));
    }
    // ---- copies: Clear, ColorFill, StretchRect, UpdateSurface (converted format), UpdateTexture, GetRenderTargetData (read-back), GetDC / ReleaseDC (CPU copy of a GPU-only surface)
    if (w.rt && w.ds) {
        STEP(w, "SetRenderTarget", D3D_OK, OOM, OOM, d->SetRenderTarget(0, w.rt)); STEP(w, "SetDepthStencilSurface", D3D_OK, OOM, OOM, d->SetDepthStencilSurface(w.ds));
        STEP(w, "Clear", D3D_OK, OOM, OVM, d->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER | D3DCLEAR_STENCIL, 0xFF112233, 1.0f, 0));
        D3DRECT rc[3] = {{0, 0, 8, 8}, {4, 4, 20, 20}, {30, 30, 40, 40}}; STEP(w, "Clear(rects)", D3D_OK, OOM, OVM, d->Clear(3, rc, D3DCLEAR_TARGET, 0xFF445566, 0, 0));
    }
    if (w.rt2) STEP(w, "ColorFill", D3D_OK, OOM, OVM, d->ColorFill(w.rt2, nullptr, 0xFF778899));
    if (w.rt && w.rt3) { STEP(w, "ColorFill(rt)", D3D_OK, OOM, OVM, d->ColorFill(w.rt, nullptr, 0xFF102030)); STEP(w, "StretchRect", D3D_OK, OOM, OVM, d->StretchRect(w.rt, nullptr, w.rt3, nullptr, D3DTEXF_POINT)); }
    if (w.rt && w.sys) { STEP(w, "GetRenderTargetData", D3D_OK, OOM, OVM, d->GetRenderTargetData(w.rt, w.sys)); }
    if (w.rt) { STEP(w, "LockRect(RT after a GPU write)", D3D_OK, OOM, OOM, w.rt->LockRect(&lr, nullptr, D3DLOCK_READONLY)); STEP(w, "UnlockRect(RT)", D3D_OK, OOM, OOM, w.rt->UnlockRect()); }
    if (w.sysc && w.defc) {
        D3DLOCKED_RECT l2; if (SUCCEEDED(w.sysc->LockRect(&l2, nullptr, 0))) { memset(l2.pBits, 0x5A, 16 * 3); w.sysc->UnlockRect(); }
        STEP(w, "UpdateSurface(R8G8B8)", D3D_OK, OOM, OVM, d->UpdateSurface(w.sysc, nullptr, w.defc, nullptr));
        STEP(w, "GetRenderTargetData(R8G8B8)", D3D_OK, OOM, OVM, d->GetRenderTargetData(w.defc, w.sysc));
    }
    if (w.tex[2] && w.tex[3]) {
        D3DLOCKED_RECT l2; if (SUCCEEDED(w.tex[2]->LockRect(0, &l2, nullptr, 0))) { memset(l2.pBits, 0xA5, 64 * 4); w.tex[2]->UnlockRect(0); }
        STEP(w, "UpdateTexture", D3D_OK, OOM, OVM, d->UpdateTexture(w.tex[2], w.tex[3]));
    }
    if (w.tex[6] && w.tex[7]) {
        D3DLOCKED_RECT l2; if (SUCCEEDED(w.tex[6]->LockRect(0, &l2, nullptr, 0))) { memset(l2.pBits, 0x33, 16 * 3); w.tex[6]->UnlockRect(0); }
        STEP(w, "UpdateTexture(A8: converted levels)", D3D_OK, OOM, OVM, d->UpdateTexture(w.tex[6], w.tex[7]));
    }
    if (w.rt2) {
        g_ctx = g_ctx_now = "GetDC"; Snap s0 = snap(w); uint64_t h0 = g_nhit; HRESULT hr = w.rt2->GetDC(&w.dc); bool hit = g_nhit != h0;
        if (!hit) { CHECK(hr == D3D_OK && w.dc); if (w.dc) STEP(w, "ReleaseDC", D3D_OK, OOM, OOM, w.rt2->ReleaseDC(w.dc)); w.dc = nullptr; } else { CHECK(hr == OOM && !w.dc); CHECK(snap(w) == s0); }
    }
    // ---- draw path: the four Draw* (fixed function, programs, UP paths); programs are created lazily by the first draw that names them
    if (w.rt && w.vb[1] && w.ib[0] && w.vb[2]) {
        float verts[9] = {0, 0, 0, 1, 0, 0, 0, 1, 0}; uint16_t idx[3] = {0, 1, 2}; bool bound;
        d->SetFVF(D3DFVF_XYZ); bound = SUCCEEDED(d->SetStreamSource(0, w.vb[1], 0, 12)) && SUCCEEDED(d->SetIndices(w.ib[0]));
        if (bound) {
            STEP(w, "DrawPrimitive (fixed function)", D3D_OK, OVM, OOM, d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1));
            STEP(w, "DrawPrimitive (again)", D3D_OK, OVM, OOM, d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1));
            STEP(w, "DrawIndexedPrimitive", D3D_OK, OVM, OOM, d->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 3, 0, 1));
        }
        STEP(w, "DrawPrimitiveUP", D3D_OK, OVM, OOM, d->DrawPrimitiveUP(D3DPT_TRIANGLELIST, 1, verts, 12));
        STEP(w, "DrawIndexedPrimitiveUP", D3D_OK, OVM, OOM, d->DrawIndexedPrimitiveUP(D3DPT_TRIANGLELIST, 0, 3, 1, idx, D3DFMT_INDEX16, verts, 12));
        if (w.vs && w.ps && w.decl && SUCCEEDED(d->SetStreamSource(0, w.vb[1], 0, 20))) {
            nx_ignore(d->SetVertexShader(w.vs)); nx_ignore(d->SetPixelShader(w.ps)); nx_ignore(d->SetVertexDeclaration(w.decl));
            STEP(w, "DrawPrimitive (shader program creation)", D3D_OK, OVM, OOM, d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1));
            STEP(w, "DrawPrimitive (program cached)", D3D_OK, OVM, OOM, d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1));
            nx_ignore(d->SetVertexShader(nullptr)); nx_ignore(d->SetPixelShader(nullptr));
        }
    }
    // ---- device lifecycle: CreateDevice, additional swap chains, cursor, Present, Reset (a failed Reset leaves the device NOT RESET: Wine d3d9_device_reset, MS Reset: "the device is placed in the lost state")
    {
        D3DPRESENT_PARAMETERS pp; memset(&pp, 0, sizeof pp); pp.Windowed = TRUE; pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.BackBufferCount = 2; pp.BackBufferWidth = 64; pp.BackBufferHeight = 64; pp.hDeviceWindow = (HWND)1;
        pp.EnableAutoDepthStencil = TRUE; pp.AutoDepthStencilFormat = D3DFMT_D24S8; pp.BackBufferFormat = D3DFMT_X8R8G8B8; D3DPRESENT_PARAMETERS pp0 = pp;
        IDirect3DDevice9* ld = nullptr; STEP(w, "CreateDevice", D3D_OK, OOM, OVM, w.d3d->CreateDevice(0, D3DDEVTYPE_HAL, (HWND)1, D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &ld)); (void)pp0;
        CHECK(ld != nullptr || g_nhit != 0 ? true : false); if (g_nhit && !ld) {} w.ld = ld;
        if (ld) {
            W lw; lw.d3d = w.d3d; lw.dev = static_cast<NxDevice9*>(ld); lw.i9 = ld; D3DPRESENT_PARAMETERS p2 = pp0; p2.BackBufferCount = 1; IDirect3DSwapChain9* sc = nullptr;
            CREATE(lw, "CreateAdditionalSwapChain", OOM, OVM, sc, ld->CreateAdditionalSwapChain(&p2, &sc)); w.sc = sc;
            if (w.sys) { STEP(lw, "SetCursorProperties", D3D_OK, OOM, OOM, ld->SetCursorProperties(1, 1, w.sys)); nx_ignore(ld->ShowCursor(TRUE)); ld->SetCursorPosition(20, 30, 0); }
            STEP(lw, "Present (cursor)", D3D_OK, OVM, OOM, ld->Present(nullptr, nullptr, nullptr, nullptr));
            STEP(lw, "Present (again)", D3D_OK, OVM, OOM, ld->Present(nullptr, nullptr, nullptr, nullptr));
            if (sc) STEP(lw, "SwapChain::Present", D3D_OK, OVM, OOM, sc->Present(nullptr, nullptr, nullptr, nullptr, 0));
            // Reset: all or nothing is not possible (the old buffers are gone first); the rule is the device state: NOT RESET until a Reset succeeds
            g_ctx = g_ctx_now = "Reset"; uint64_t h0 = g_nhit; D3DPRESENT_PARAMETERS pr = pp0; pr.BackBufferWidth = 32; pr.BackBufferHeight = 32; HRESULT hr = ld->Reset(&pr); bool hit = g_nhit != h0;
            if (!hit) { CHECK(hr == D3D_OK); CHECK(ld->TestCooperativeLevel() == D3D_OK); }
            else {
                CHECK(in(hr, OOM, OVM)); CHECK(ld->TestCooperativeLevel() == D3DERR_DEVICENOTRESET);
                STEP(lw, "Present (device not reset)", D3DERR_DEVICELOST, OOM, OVM, ld->Present(nullptr, nullptr, nullptr, nullptr));
                uint64_t h1 = g_nhit; D3DPRESENT_PARAMETERS pr2 = pp0; pr2.BackBufferWidth = 32; pr2.BackBufferHeight = 32; hr = ld->Reset(&pr2);   // the retry
                if (g_nhit == h1) { CHECK(hr == D3D_OK); CHECK(ld->TestCooperativeLevel() == D3D_OK); } else CHECK(in(hr, OOM, OVM) && ld->TestCooperativeLevel() == D3DERR_DEVICENOTRESET);
            }
            if (ld->TestCooperativeLevel() == D3D_OK) STEP(lw, "Present (after Reset)", D3D_OK, OVM, OOM, ld->Present(nullptr, nullptr, nullptr, nullptr));
            g_ctx_now = "(none)";
        }
    }
    g_ctx_now = "(teardown)"; CHECK(g_nhit >= hits0);
}

static uint64_t run(uint64_t fail_at, bool sticky) {
    // setup: not counted (the factory and the device are made with the injector disarmed)
    g_armed = false; g_count = 0; g_fail_at = fail_at; g_sticky = sticky; g_nhit = 0; uint32_t err0 = nxs_errors();
    W w; w.d3d = static_cast<NxDirect3D9*>(nx_Direct3DCreate9(0)); NxCreateParams cp = {0, 64, 64, D3DCREATE_MIXED_VERTEXPROCESSING};
    w.dev = new NxDevice9; NxObj* o = w.dev; o->asDevice(w.d3d); w.dev->init(cp); w.i9 = w.dev;
    IDirect3D9Ex* ex = nullptr; if (SUCCEEDED(nx_Direct3DCreate9Ex(0, &ex))) { w.d3dx = static_cast<NxDirect3D9Ex*>(ex); w.devx = new NxDevice9Ex; NxObj* ox = w.devx; ox->asDevice(w.d3dx); w.devx->init(cp); }
    g_armed = true; scenario(w); g_armed = false; uint64_t events = g_count;
    // teardown: everything created is released (public count 1 each), then the device and the factory
    rel(w.sc); rel(w.ld);
    for (auto& p : w.sb) rel(p);
    rel(w.defc); rel(w.sysc); rel(w.sys); rel(w.ds); rel(w.rt3); rel(w.rt2); rel(w.rt); rel(w.vol); rel(w.cube); for (auto& p : w.tex) rel(p); for (auto& p : w.q) rel(p);
    rel(w.ps); rel(w.vs); rel(w.decl); for (auto& p : w.ib) rel(p); for (auto& p : w.vb) rel(p); rel(w.f9x); rel(w.f9);
    for (NxObj* r = static_cast<NxObj*>(w.dev)->rhead; r; r = r->rnext) CHECK(r->binds > 0);   // what is still registered is held by the device's own bindings (render target / depth stencil), nothing else outlived its release
    if (w.devx) { static_cast<NxObj*>(w.devx)->release(); static_cast<NxObj*>(w.d3dx)->release(); }
    w.dev->release(); static_cast<NxObj*>(w.d3d)->release();
    nxs_shutdown();
    CHECK(g_nlive == 0); CHECK(g_bad_free == 0); CHECK(g_nimg == 0); CHECK(g_nblk == 0); CHECK(nxs_errors() == err0);   // no invariant of glue_stream.h E1 / E3 was violated (nxs_error) in this run
    if (g_nlive || g_nimg || g_nblk) { int n = 0; for (int i = 0; i < g_nlive && n < 3; i++, n++) printf("  leaked block kind %d at run k=%llu\n", g_live[i].kind, (unsigned long long)fail_at); }
    g_nlive = 0; g_bad_free = 0; g_nimg = 0; g_nblk = 0; return events;
}
static void sentinel(const char* tag, uint64_t k) { char b[64]; int n = snprintf(b, sizeof b, "@%s %llu\n", tag, (unsigned long long)k); if (write(2, b, n) < 0) {} }

int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IOLBF, 0);
    g_pi = 0; g_run = "base run"; uint64_t n = run(0, false);   // part a: the run without failures: every step succeeds, the balance is zero
    printf("N %llu steps %d allocation events of the base run\n", (unsigned long long)n, g_nsteps);
    g_hits_total = 0; g_ncov = 0;
    int base_hits = (int)g_nhit; CHECK(base_hits == 0);
    for (int mode = 0; mode < 2; mode++) {
        g_pi = 1 + mode; if (mode == 1 && argc > 1 && !strcmp(argv[1], "one")) break;   // "one": only the one-shot sweep (the mutation runs)
        for (uint64_t k = 1; k <= n; k++) { sentinel(mode ? "sticky" : "one", k); char c[48]; snprintf(c, sizeof c, "%s k=%llu", mode ? "sticky" : "one-shot", (unsigned long long)k); g_run = c; run(k, mode == 1); g_run = ""; }
        sentinel("done", mode);
    }
    printf("failures injected: %llu; by the call that met them:", (unsigned long long)g_hits_total); for (int i = 0; i < g_ncov; i++) printf(" %s=%llu", g_cov[i].name, (unsigned long long)g_cov[i].hits); printf("\n");
    int bad = 0;
    for (int i = 0; i < 3; i++) {
        if (i == 2 && argc > 1 && !strcmp(argv[1], "one")) break;
        printf("PART %c: %d checks, %d failed\n", 'a' + i, g_checks[i], g_fails[i]); bad += g_fails[i] != 0 || g_checks[i] == 0;
    }
    return bad ? 1 : 0;
}
