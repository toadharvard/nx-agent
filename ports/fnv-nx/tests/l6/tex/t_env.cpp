#include "t_common.h"
const char* g_data = "."; int g_checks[8], g_fails[8], g_pi = 0;
void t_part(char p) { g_pi = p - 'a'; }
bool t_check(bool ok, const char* what, const char* file, int line) {
    g_checks[g_pi]++;
    if (!ok && g_fails[g_pi]++ < 12) printf("FAIL %c: %s (%s:%d)\n", 'a' + g_pi, what, strrchr(file, '/') ? strrchr(file, '/') + 1 : file, line);
    return ok;
}
FILE* t_open(const char* name) { char p[1024]; snprintf(p, sizeof p, "%s/%s", g_data, name); FILE* f = fopen(p, "r"); if (!f) { printf("FAIL: cannot open %s\n", p); g_fails[g_pi]++; } return f; }
TEnv t_env(bool ex) {
    TEnv e = {}; NxCreateParams cp = {1, 640, 480, D3DCREATE_MIXED_VERTEXPROCESSING};
    if (ex) { NxDirect3D9Ex* d = new NxDirect3D9Ex; d->asRoot(1); e.devex = new NxDevice9Ex; e.devex->asDevice(d); e.devex->init(cp); e.core = e.devex; e.obj = e.devex; }
    else { e.d3d = new NxDirect3D9; e.d3d->asRoot(1); e.dev = new NxDevice9; e.dev->asDevice(e.d3d); e.dev->init(cp); e.core = e.dev; e.obj = e.dev; }
    return e;
}
// backend hooks of the test: record every call
Rec g_rec[1 << 16]; int g_nrec = 0, g_profile = 0; bool g_refuse = false; static uint32_t g_next = 1;
int t_count(int op) { int n = 0; for (int i = 0; i < g_nrec; i++) n += g_rec[i].op == op; return n; }
void t_clear() { g_nrec = 0; }
static Rec* rec(int op, uint32_t image) { if (g_nrec >= (1 << 16)) { g_nrec = 0; } Rec* r = &g_rec[g_nrec++]; memset(r, 0, sizeof *r); r->op = op; r->image = image; return r; }
uint32_t nxd3d_image_create(NxObj*, const NxImageDesc4* d) { if (g_refuse) return 0; uint32_t id = g_next++; rec(R_CREATE, id)->desc = *d; return id; }
void nxd3d_image_destroy(NxObj*, uint32_t id) { rec(R_DESTROY, id); }
void nxd3d_image_update(NxObj*, uint32_t id, uint32_t lv, uint32_t ly, const NxBox3* b, const void* data, uint32_t row, uint32_t slice) {
    Rec* r = rec(R_UPDATE, id); r->level = lv; r->layer = ly; r->box = *b; r->row = row; r->slice = slice; r->b0 = *(const uint8_t*)data;
}
void nxd3d_image_read(NxObj*, uint32_t id, uint32_t lv, uint32_t ly, void* dst, uint32_t row, uint32_t slice) { Rec* r = rec(R_READ, id); r->level = lv; r->layer = ly; r->row = row; r->slice = slice; ((uint8_t*)dst)[0] = 0xC0 + lv; }
void nxd3d_gen_mips(NxObj*, uint32_t id, uint32_t filter) { rec(R_GEN, id)->row = filter; }
int nx_env_profile_of(NxObj*) { return g_profile; }
void nxd3d_preload(NxObj* o) { nx_tex_preload(o); }
bool nx_env_decltype_supported(uint32_t t) { return t != D3DDECLTYPE_FLOAT16_4; }
bool nx_env_query_supported(uint32_t t) { return t != D3DQUERYTYPE_VCACHE; }
int main(int argc, char** argv) {
    if (argc > 1) g_data = argv[1];
    t_a(); t_b(); t_c(); t_d(); t_e(); t_h();
    int bad = 0;
    for (int i = 0; i < 8; i++) if (g_checks[i] || g_fails[i]) { printf("PART %c: %d checks, %d failed\n", 'a' + i, g_checks[i], g_fails[i]); bad += g_fails[i] != 0 || g_checks[i] == 0; }
    return bad ? 1 : 0;
}
