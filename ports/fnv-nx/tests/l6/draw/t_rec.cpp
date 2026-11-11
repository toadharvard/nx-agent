// the recording backend, the harness main and the device helpers of tests/l6/draw.
#include "t_common.h"
#include "../t_keep.h"
int g_checks[8], g_fails[8], g_pi = 0; const char* g_data = ".";
void t_part(char p) { g_pi = p - 'a'; }
bool t_check(bool ok, const char* what, const char* file, int line) {
    g_checks[g_pi]++;
    if (!ok && g_fails[g_pi]++ < 12) printf("FAIL %c: %s (%s:%d)\n", 'a' + g_pi, what, strrchr(file, '/') ? strrchr(file, '/') + 1 : file, line);
    return ok;
}
FILE* t_open(const char* name) { char p[1024]; snprintf(p, sizeof p, "%s/%s", g_data, name); FILE* f = fopen(p, "r"); if (!f) { printf("FAIL: cannot open %s\n", p); g_fails[g_pi]++; } return f; }
// ---- backend memory: aligned blocks, zero filled
void* nxd3d_gpu_alloc(size_t n) { n = (n + 4095) & ~(size_t)4095; void* p = aligned_alloc(4096, n); if (p) memset(p, 0, n); return p; }
void nxd3d_gpu_free(void* p) { free(p); }
struct Mem { uint8_t* p; uint32_t size; bool live; }; static Mem g_mem[8192]; static uint32_t g_nmem = 0, g_next_img = 1, g_next_smp = 1, g_next_sh = 1;
std::vector<Ev> g_ev; std::vector<Call> g_calls; uint32_t g_shader_fail = 0;
static uint32_t g_lays[257][50], g_st_lay[4097], g_cur_state = 0;   // the replay's tables, kept across submits (ids are global)
static void decode(const uint32_t* w, uint32_t words) {
    for (uint32_t i = 0; i < words;) {
        uint32_t op = w[i] & 0xFFFF, n = w[i] >> 16; Ev e; e.op = op; e.n = n; memset(e.a, 0, sizeof e.a); for (uint32_t k = 0; k < n && k < 64; k++) e.a[k] = w[i + 1 + k];
        const uint32_t* a = w + i + 1; uint32_t blk = 0, off = 0, size = 0;
        if (op == NxOp_BindUbo) { blk = a[2]; off = a[3]; size = a[4]; } else if (op == NxOp_BindVtxN) { blk = a[1]; off = a[2]; size = a[3]; } else if (op == NxOp_BindIdx) { blk = a[0]; off = a[1]; size = a[2]; }
        if (size && blk && blk < g_nmem && g_mem[blk].p) { if ((uint64_t)off + size <= g_mem[blk].size) e.blob.assign(g_mem[blk].p + off, g_mem[blk].p + off + size); else printf("decoder: block %u range out of bounds\n", blk); }
        memset(e.lay, 0, sizeof e.lay); e.state = 0;
        if (op == NxOp_DefineLayout4 && a[0] && a[0] <= 256) memcpy(g_lays[a[0]], a + 1, 200);
        if (op == NxOp_DefineState && a[0] && a[0] <= 4096) g_st_lay[a[0]] = a[3];
        if (op == NxOp_BindState && a[0] <= 4096) g_cur_state = a[0];
        if (op == NxOp_BeginPass4) g_cur_state = 0;
        if (op == NxOp_Draw || op == NxOp_DrawIndexed) { e.state = g_cur_state; memcpy(e.lay, g_lays[g_st_lay[g_cur_state] <= 256 ? g_st_lay[g_cur_state] : 0], 200); }
        if (op != NxOp_End) g_ev.push_back(e);
        i += 1 + n;
    }
}
uint64_t nxd3d_gpu_call(uint32_t fn, const uint64_t* a, uint32_t n) {
    Call c; c.fn = fn; memset(c.a, 0, sizeof c.a); memset(&c.sd, 0, sizeof c.sd); memset(c.l, 0, sizeof c.l); c.ret = 0; for (uint32_t i = 0; i < n && i < 3; i++) c.a[i] = a[i];
    uint64_t r = 0;
    switch (fn) {
    case NXGPU_FN_MEM_CREATE: { const NxMemBlockDesc* d = (const NxMemBlockDesc*)(uintptr_t)a[0]; g_nmem = g_nmem ? g_nmem : 1; g_mem[g_nmem] = {(uint8_t*)(uintptr_t)d->storage, d->size, true}; r = g_nmem++; break; }
    case NXGPU_FN_MEM_DESTROY: if (a[0] && a[0] < g_nmem) g_mem[a[0]].live = false; break;
    case NXGPU_FN_IMAGE_CREATE4: if (g_next_img > 4000) g_next_img = 1; r = g_next_img; g_next_img += 2; break;                  // ids step by 2: id + 1 is the sRGB view
    case NXGPU_FN_SAMPLER_CREATE: r = g_next_smp++; if (r >= NX_MAX_SAMPLERS) r = NXGPU_BAD_CALL; break;
    case NXGPU_FN_SHADER_CREATE_D3D: { const NxShaderD3D* s = (const NxShaderD3D*)(uintptr_t)a[0]; c.sd = *s; if (s->n_link_pairs) memcpy(c.l, (const void*)(uintptr_t)s->link_pairs, s->n_link_pairs); c.code.assign((const uint8_t*)(uintptr_t)s->bytecode, (const uint8_t*)(uintptr_t)s->bytecode + s->size);
        if (g_shader_fail) { g_shader_fail = 0; r = 0; } else r = 1000 + g_next_sh++; break; }
    case NXGPU_FN_SUBMIT: { const NxSubmit* s = (const NxSubmit*)(uintptr_t)a[0]; decode((const uint32_t*)(uintptr_t)s->stream, s->words); return 0; }
    default: break;
    }
    c.ret = (uint32_t)r; if (fn != NXGPU_FN_MEM_CREATE && fn != NXGPU_FN_SUBMIT && fn != NXGPU_FN_WAIT_FRAME) g_calls.push_back(c); else if (fn == NXGPU_FN_MEM_CREATE) g_calls.push_back(c);
    return r;
}
void t_flush() { nxs_flush(); }
void t_clear() { nxs_flush(); g_ev.clear(); g_calls.clear(); }
int t_count(uint32_t op) { int n = 0; for (auto& e : g_ev) n += e.op == op; return n; }
const Ev* t_nth(uint32_t op, int n) { for (auto& e : g_ev) if (e.op == op && n-- == 0) return &e; return nullptr; }
int t_ncalls(uint32_t fn) { int n = 0; for (auto& c : g_calls) n += c.fn == fn; return n; }
// ---- FFP test doubles (D5b's functions, draw_ffp.h)
uint32_t g_ffp_vs_id = 90, g_ffp_ps_id = 91; uint8_t g_ffp_ubo_v[64], g_ffp_ubo_p[32];
uint32_t nx_ffp_vs_program(NxDeviceCore*, NxFfpVsBind* o) {
    memset((void*)o, 0, sizeof *o); o->n_in = 3; o->in_pair[0] = FNVNX_DK_PAIR(0, 0); o->in_loc[0] = 0; o->in_pair[1] = FNVNX_DK_PAIR(3, 0); o->in_loc[1] = 3; o->in_pair[2] = FNVNX_DK_PAIR(5, 0); o->in_loc[2] = 6;
    o->n_ubo = 1; o->ubo[0] = {4, sizeof g_ffp_ubo_v, g_ffp_ubo_v}; return g_ffp_vs_id;
}
uint32_t nx_ffp_ps_program(NxDeviceCore*, NxFfpPsBind* o) {
    memset((void*)o, 0, sizeof *o); o->n_in = 3; o->in_pair[0] = FNVNX_DK_PAIR(10, 0); o->in_pair[1] = FNVNX_DK_PAIR(5, 0); o->in_pair[2] = FNVNX_DK_PAIR(11, 0); o->tex_mask = 1;
    o->n_ubo = 1; o->ubo[0] = {3, sizeof g_ffp_ubo_p, g_ffp_ubo_p}; return g_ffp_ps_id;
}
int nx_env_profile_of(NxObj*) { return 0; }
// ---- device helpers
TDev t_dev(bool ex, uint32_t w, uint32_t h) {
    TDev e = {}; NxCreateParams cp = {0, w, h, D3DCREATE_MIXED_VERTEXPROCESSING};
    if (ex) { NxDirect3D9Ex* d = t_new<NxDirect3D9Ex>(); d->asRoot(1); e.devex = t_new<NxDevice9Ex>(); e.devex->asDevice(d); e.devex->init(cp); e.core = e.devex; e.i9 = e.devex; }
    else { e.d3d = t_new<NxDirect3D9>(); e.d3d->asRoot(1); e.dev = t_new<NxDevice9>(); e.dev->asDevice(e.d3d); e.dev->init(cp); e.core = e.dev; e.i9 = e.dev; }
    return e;
}
void t_target(TDev& d, uint32_t w, uint32_t h, bool depth) {
    IDirect3DSurface9* s = nullptr; CHECK_HR(d.i9->CreateRenderTarget(w, h, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_NONE, 0, FALSE, &s, nullptr), D3D_OK); CHECK_HR(d.i9->SetRenderTarget(0, s), D3D_OK);
    if (depth) { IDirect3DSurface9* z = nullptr; CHECK_HR(d.i9->CreateDepthStencilSurface(w, h, D3DFMT_D24S8, D3DMULTISAMPLE_NONE, 0, FALSE, &z, nullptr), D3D_OK); CHECK_HR(d.i9->SetDepthStencilSurface(z), D3D_OK); }
}
IDirect3DVertexBuffer9* t_vb(TDev& d, uint32_t bytes, uint8_t fill) {
    IDirect3DVertexBuffer9* v = nullptr; CHECK_HR(d.i9->CreateVertexBuffer(bytes, 0, 0, d.devex ? D3DPOOL_DEFAULT : D3DPOOL_MANAGED, &v, nullptr), D3D_OK);
    void* p = nullptr; if (v && v->Lock(0, 0, &p, 0) == D3D_OK) { memset(p, fill, bytes); v->Unlock(); } return v;
}
IDirect3DIndexBuffer9* t_ib(TDev& d, uint32_t bytes, bool is32) {
    IDirect3DIndexBuffer9* v = nullptr; CHECK_HR(d.i9->CreateIndexBuffer(bytes, 0, is32 ? D3DFMT_INDEX32 : D3DFMT_INDEX16, d.devex ? D3DPOOL_DEFAULT : D3DPOOL_MANAGED, &v, nullptr), D3D_OK); return v;
}
IDirect3DVertexDeclaration9* t_decl(TDev& d, const D3DVERTEXELEMENT9* e) { IDirect3DVertexDeclaration9* v = nullptr; CHECK_HR(d.i9->CreateVertexDeclaration(e, &v), D3D_OK); return v; }
std::vector<uint32_t> t_hex(const char* s) { std::vector<uint32_t> v; char* e; for (;;) { while (*s == ' ' || *s == '\n') s++; if (!*s) break; v.push_back((uint32_t)strtoul(s, &e, 16)); if (e == s) break; s = e; } return v; }
IDirect3DVertexShader9* t_vs(TDev& d, const char* hex) { auto v = t_hex(hex); IDirect3DVertexShader9* s = nullptr; CHECK_HR(d.i9->CreateVertexShader((const DWORD*)v.data(), &s), D3D_OK); return s; }
IDirect3DPixelShader9* t_ps(TDev& d, const char* hex) { auto v = t_hex(hex); IDirect3DPixelShader9* s = nullptr; CHECK_HR(d.i9->CreatePixelShader((const DWORD*)v.data(), &s), D3D_OK); return s; }
uint32_t t_lcg(uint32_t* s) { *s = *s * 1664525u + 1013904223u; return *s >> 8; }
int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IOLBF, 0); if (argc > 1) g_data = argv[1];
    t_a(); t_b(); t_c(); t_d(); t_e(); t_f();
    uint32_t errs = nxs_errors(); if (errs) printf("backend call errors: %u\n", errs);
    int bad = errs != 0;
    for (int i = 0; i < 8; i++) if (g_checks[i] || g_fails[i]) { printf("PART %c: %d checks, %d failed\n", 'a' + i, g_checks[i], g_fails[i]); bad += g_fails[i] != 0 || g_checks[i] == 0; }
    return bad ? 1 : 0;
}
