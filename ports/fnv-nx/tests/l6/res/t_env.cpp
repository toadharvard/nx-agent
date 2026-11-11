#include "t_common.h"
#include "../t_keep.h"
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
    if (ex) { NxDirect3D9Ex* d = t_new<NxDirect3D9Ex>(); d->asRoot(1); e.devex = t_new<NxDevice9Ex>(); e.devex->asDevice(d); e.devex->init(cp); e.core = e.devex; e.obj = e.devex; }
    else { e.d3d = t_new<NxDirect3D9>(); e.d3d->asRoot(1); e.dev = t_new<NxDevice9>(); e.dev->asDevice(e.d3d); e.dev->init(cp); e.core = e.dev; e.obj = e.dev; }
    return e;
}
// ENV answers and backend hooks of the test: the environment supports every type except VCACHE and decl type FLOAT16_4 (so both branches are exercised)
void nxd3d_preload(NxObj*) {}
void nxd3d_buf_destroy(NxBufCore*) {}
bool nxd3d_query_create(NxQueryCore*) { return true; }
void nxd3d_query_destroy(NxQueryCore*) {}
bool nx_env_decltype_supported(uint32_t t) { return t != D3DDECLTYPE_FLOAT16_4; }
bool nx_env_query_supported(uint32_t t) { return t != D3DQUERYTYPE_VCACHE; }
bool g_q_ready = false; int g_q_issues = 0, g_q_flushes = 0; DWORD g_q_last_flags = 0;
void nxd3d_query_issue(NxQueryCore*, DWORD f) { g_q_issues++; g_q_last_flags = f; }
bool nxd3d_query_ready(NxQueryCore*) { return g_q_ready; }
void nxd3d_query_flush(NxQueryCore*) { g_q_flushes++; }
void nxd3d_query_result(NxQueryCore* q, void* out, uint32_t n) { for (uint32_t i = 0; i < n; i++) ((uint8_t*)out)[i] = (uint8_t)(0xA0 + i + q->type); }
int main(int argc, char** argv) {
    if (argc > 1) g_data = argv[1];
    t_buf(); t_decl(); t_fvf(); t_shader(); t_query();
    int bad = 0;
    for (int i = 0; i < 5; i++) { printf("PART %c: %d checks, %d failed\n", 'a' + i, g_checks[i], g_fails[i]); bad += g_fails[i] != 0 || g_checks[i] == 0; }
    return bad ? 1 : 0;
}
