// RPC client of tests/l6/copy: nxd3d_gpu_call -> the LP64 replay process; nxd3d_gpu_alloc from a shared arena (same address in both processes).
#include "t_common.h"
#include <fcntl.h>
#include <signal.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
const char* g_data = "."; int g_checks[8], g_fails[8], g_pi = 0, g_calls[64], g_profile = 0;
void t_part(char p) { g_pi = p - 'a'; }
bool t_check(bool ok, const char* what, const char* file, int line) {
    g_checks[g_pi]++;
    if (!ok && g_fails[g_pi]++ < 12) printf("FAIL %c: %s (%s:%d)\n", 'a' + g_pi, what, strrchr(file, '/') ? strrchr(file, '/') + 1 : file, line);
    return ok;
}
FILE* t_open(const char* name) { char p[1024]; snprintf(p, sizeof p, "%s/%s", g_data, name); FILE* f = fopen(p, "r"); if (!f) { printf("FAIL: cannot open %s\n", p); g_fails[g_pi]++; } return f; }
// ---- arena
enum : uintptr_t { ARENA_BASE = 0x40000000u, ARENA_SIZE = 0x10000000u };   // 256 MiB
static uint8_t* g_arena; static size_t g_top = 0; struct Free { size_t off, size; }; static Free g_free[256]; static int g_nfree = 0;
static struct { void* p; size_t n; } g_sz[4096]; static int g_nsz = 0;
void* nxd3d_gpu_alloc(size_t n) {
    n = (n + 4095) & ~(size_t)4095; void* p = nullptr;
    for (int i = 0; i < g_nfree && !p; i++) if (g_free[i].size == n) { p = g_arena + g_free[i].off; g_free[i] = g_free[--g_nfree]; }
    if (!p) { if (g_top + n > ARENA_SIZE) { printf("arena exhausted\n"); exit(9); } p = g_arena + g_top; g_top += n; }
    if (g_nsz < 4096) g_sz[g_nsz++] = {p, n}; return p;
}
void nxd3d_gpu_free(void* p) { /* size unknown here: blocks are recycled by the glue's own sizes through the table below */ extern size_t t_size_of(void*); size_t n = t_size_of(p); if (g_nfree < 256) g_free[g_nfree++] = {(size_t)((uint8_t*)p - g_arena), n}; }
size_t t_size_of(void* p) { for (int i = 0; i < g_nsz; i++) if (g_sz[i].p == p) { size_t n = g_sz[i].n; g_sz[i] = g_sz[--g_nsz]; return n; } return 4096; }
// ---- server
static int g_wr = -1, g_rd = -1, g_pid = 0;
struct Req { uint32_t fn, n; uint64_t args[8]; };
static void start_server() {
    const char* srv = getenv("NXCOPY_SERVER"); const char* arena = getenv("NXCOPY_ARENA"); char b[32], s[32];
    int fd = open(arena, O_RDWR | O_CREAT, 0600); if (fd < 0 || ftruncate(fd, ARENA_SIZE)) { perror("arena"); exit(8); }
    g_arena = (uint8_t*)mmap((void*)ARENA_BASE, ARENA_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_FIXED, fd, 0); if (g_arena != (uint8_t*)ARENA_BASE) { perror("mmap"); exit(8); }
    int c2s[2], s2c[2]; if (pipe(c2s) || pipe(s2c)) exit(8);
    g_pid = fork();
    if (!g_pid) { close(c2s[1]); close(s2c[0]); char in[16], out[16]; snprintf(in, 16, "%d", c2s[0]); snprintf(out, 16, "%d", s2c[1]); snprintf(b, 32, "%x", (unsigned)ARENA_BASE); snprintf(s, 32, "%x", (unsigned)ARENA_SIZE);
        execl(srv, srv, arena, b, s, in, out, (char*)0); perror("exec"); _exit(7); }
    close(c2s[0]); close(s2c[1]); g_wr = c2s[1]; g_rd = s2c[0];
}
uint64_t nxd3d_gpu_call(uint32_t fn, const uint64_t* args, uint32_t n) {
    Req r; memset(&r, 0, sizeof r); r.fn = fn; r.n = n; for (uint32_t i = 0; i < n && i < 8; i++) r.args[i] = args[i];
    if (fn < 64) g_calls[fn]++;
    if (write(g_wr, &r, sizeof r) != (ssize_t)sizeof r) { printf("server write failed\n"); exit(6); }
    uint64_t res; if (read(g_rd, &res, 8) != 8) { printf("server died\n"); exit(6); }
    if (getenv("NXCOPY_TRACE") && ((fn == NXGPU_FN_SUBMIT && res != 0) || res == NXGPU_BAD_CALL)) printf("  call fn %u -> %llx\n", fn, (unsigned long long)res);
    return res;
}
// ---- fake platform DC: records the pixels it was made over
struct FakeDc { void* mem; uint32_t fmt, w, h, pitch; };
void* nxd3d_dc_create(void* mem, uint32_t fmt, uint32_t w, uint32_t h, uint32_t pitch) { FakeDc* d = new FakeDc{mem, fmt, w, h, pitch}; return d; }
void nxd3d_dc_release(void* dc) { delete (FakeDc*)dc; }
int nx_env_profile_of(NxObj*) { return g_profile; }
int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IOLBF, 0); if (argc > 1) g_data = argv[1]; start_server();
    t_a(); t_b(); t_c(); t_d();
    uint32_t errs = nxs_errors(); if (errs) printf("backend call errors: %u\n", errs);
    int bad = errs != 0;
    for (int i = 0; i < 8; i++) if (g_checks[i] || g_fails[i]) { printf("PART %c: %d checks, %d failed\n", 'a' + i, g_checks[i], g_fails[i]); bad += g_fails[i] != 0 || g_checks[i] == 0; }
    close(g_wr); int st; waitpid(g_pid, &st, 0); return bad ? 1 : 0;
}
