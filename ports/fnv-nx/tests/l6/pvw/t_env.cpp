#include "t_common.h"
const char* g_data = "."; int g_checks[8], g_fails[8], g_pi = 0, g_profile = 0; bool g_refuse = false;
void t_part(char p) { g_pi = p - 'a'; }
bool t_check(bool ok, const char* what, const char* file, int line) {
    g_checks[g_pi]++;
    if (!ok && g_fails[g_pi]++ < 12) printf("FAIL %c: %s (%s:%d)\n", 'a' + g_pi, what, strrchr(file, '/') ? strrchr(file, '/') + 1 : file, line);
    return ok;
}
FILE* t_open(const char* name) { char p[1024]; snprintf(p, sizeof p, "%s/%s", g_data, name); FILE* f = fopen(p, "r"); if (!f) { printf("FAIL: cannot open %s\n", p); g_fails[g_pi]++; } return f; }
NxEnvProfile nx_env_profile() { return (NxEnvProfile)g_profile; }
void t_screen(uint32_t* w, uint32_t* h) { *w = g_profile ? 1920 : 1280; *h = g_profile ? 1080 : 720; }
// ---- recording backend
Rec g_rec[1 << 16]; int g_nrec = 0; static uint32_t g_next = 1;
int t_count(int op) { int n = 0; for (int i = 0; i < g_nrec; i++) n += g_rec[i].op == op; return n; }
void t_clear() { g_nrec = 0; }
uint32_t t_created(int nth) { for (int i = 0; i < g_nrec; i++) if (g_rec[i].op == R_CREATE && nth-- == 0) return g_rec[i].image; return 0; }
static Rec* rec(int op, uint32_t image) { if (g_nrec >= (1 << 16)) g_nrec = 0; Rec* r = &g_rec[g_nrec++]; memset(r, 0, sizeof *r); r->op = op; r->image = image; return r; }
uint32_t nxd3d_image_create(NxObj*, const NxImageDesc4* d) { if (g_refuse) return 0; uint32_t id = g_next++; rec(R_CREATE, id)->desc = *d; return id; }
void nxd3d_image_destroy(NxObj*, uint32_t id) { rec(R_DESTROY, id); }
void nxd3d_image_update(NxObj*, uint32_t id, uint32_t, uint32_t, const NxBox3*, const void*, uint32_t, uint32_t) { rec(R_UPDATE, id); }
void nxd3d_image_read(NxObj*, uint32_t id, uint32_t, uint32_t, void* dst, uint32_t, uint32_t) { rec(R_READ, id); ((uint8_t*)dst)[0] = 0xC0; }
void nxd3d_gen_mips(NxObj*, uint32_t, uint32_t) {}
void nxd3d_preload(NxObj* o) { nx_tex_preload(o); }
void nxd3d_buf_destroy(NxBufCore*) {}
bool nxd3d_query_create(NxQueryCore*) { return true; }
void nxd3d_query_destroy(NxQueryCore*) {}
// ---- presentation hooks
PRec g_pres[256]; int g_npres = 0, g_seq = 0, g_ncur = 0, g_nidle = 0, g_nfront = 0, g_nvblank = 0, g_present_result = 0; CRec g_cur[64]; uint64_t g_qpc = 0, g_qpf = 1000000; int32_t g_ospos[2] = {77, 88};
void t_clear_hooks() { g_npres = g_ncur = g_nidle = g_nfront = g_nvblank = 0; g_seq = 0; g_present_result = 0; }
int nxd3d_present(NxObj*, const NxPresent* p) {
    PRec* r = &g_pres[g_npres++ & 255]; r->p = *p; r->seq = g_seq++; r->has_gamma = p->gamma != 0; if (p->gamma) memcpy(r->gamma, (const void*)(uintptr_t)p->gamma, sizeof r->gamma);
    if (p->cursor) { CRec* c = &g_cur[g_ncur++ & 63]; c->w = p->cw; c->h = p->ch; c->x = p->cx; c->y = p->cy; memcpy(c->px0, (const void*)(uintptr_t)p->cursor, 4); c->seq = g_seq++; }   // the cursor rides in the present (nxgpu4.h P2)
    return g_present_result;
}
void nxd3d_wait_idle(NxObj*) { g_nidle++; }
int nxd3d_frontbuffer_read(NxObj*, uint32_t w, uint32_t h, void* dst, uint32_t pitch) { g_nfront++; for (uint32_t y = 0; y < h; y++) for (uint32_t x = 0; x < w; x++) { uint8_t* p = (uint8_t*)dst + (size_t)y * pitch + x * 4; p[0] = (uint8_t)x; p[1] = (uint8_t)y; p[2] = 7; p[3] = 200; } return 0; }
int nxd3d_wait_vblank(NxObj*) { g_nvblank++; return 0; }
bool nxd3d_window_client(void* h, RECT* r) {
    uint32_t sw, sh; t_screen(&sw, &sh); RECT v;
    switch ((uintptr_t)h) { case W_A: v = {10, 20, 810, 620}; break; case W_EMPTY: v = {0, 0, 0, 0}; break; case W_SCREEN: v = {0, 0, (LONG)sw, (LONG)sh}; break; case W_SMALL: v = {5, 5, 105, 55}; break; default: return false; }
    if (r) *r = v; return true;
}
uint64_t nxd3d_perf_counter(uint64_t* f) { *f = g_qpf; return g_qpc; }
void nxd3d_os_cursor_pos(int32_t* x, int32_t* y) { *x = g_ospos[0]; *y = g_ospos[1]; }
// ---- helpers
D3DPRESENT_PARAMETERS t_pp(bool windowed, uint32_t swap, uint32_t count) { D3DPRESENT_PARAMETERS p; memset(&p, 0, sizeof p); p.Windowed = windowed; p.SwapEffect = (D3DSWAPEFFECT)swap; p.BackBufferCount = count; p.hDeviceWindow = (HWND)W_A; p.BackBufferWidth = 640; p.BackBufferHeight = 480; p.BackBufferFormat = D3DFMT_X8R8G8B8; return p; }
IDirect3D9* t_d3d(bool ex) { if (!ex) return nx_Direct3DCreate9(0); IDirect3D9Ex* e = nullptr; nx_Direct3DCreate9Ex(0, &e); return e; }
IDirect3DDevice9* t_dev(IDirect3D9* d3d, D3DPRESENT_PARAMETERS* pp, HRESULT* hr, void* focus, DWORD beh) { IDirect3DDevice9* d = nullptr; HRESULT r = d3d->CreateDevice(0, D3DDEVTYPE_HAL, (HWND)focus, beh, pp, &d); if (hr) *hr = r; return d; }
int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IOLBF, 0); if (argc > 1) g_data = argv[1];
    const char* only = argc > 2 ? argv[2] : "abcd";   // debugging: run only these parts
    if (strchr(only, 'a')) t_a();
    if (strchr(only, 'b')) t_b();
    if (strchr(only, 'c')) t_c();
    if (strchr(only, 'd')) t_d();
    int bad = 0;
    for (int i = 0; i < 8; i++) if (g_checks[i] || g_fails[i]) { printf("PART %c: %d checks, %d failed\n", 'a' + i, g_checks[i], g_fails[i]); bad += g_fails[i] != 0 || g_checks[i] == 0; }
    return bad ? 1 : 0;
}
