/* firstframe.nro: the nxgpu v4.1 first-frame self test on the console (libnx, LP64). It hosts nxgpu4_selftest.c over the deko3d
 * backend (libnxdk.a: nxdk_ext) and writes sdmc:/switch/fnv/firstframe.log (one line per fact, flushed; the last line is
 * `RESULT PASS` or `RESULT FAIL <reason>`) and sdmc:/switch/fnv/firstframe.bmp (the presented frame). Then it waits for +.
 * The screen belongs to the GPU backend, so nothing is printed on a console: the log file is the only output.
 * Build: build_nro.sh (the DKSH blobs ff_vs_dksh / ff_fs_dksh come from build_dksh.sh as a generated C array).
 *
 * Embedder contract (nx/dk.h, "Embedder obligations"):
 *  - Before nxdk_create the program must have libnx's vi service and default native window initialised. In an NRO nothing is left to do:
 *    libnx's default (weak) __appInit (nx/source/runtime/init.c, libnx.a init.o) calls the weak __nx_win_init when it is linked in, after
 *    fsdevMountSdmc and before userAppInit; __nx_win_init (nx/source/display/default_window.c) does viInitialize, viOpenDisplay,
 *    viCreateLayer and nwindowCreateFromLayer for the default window, which nwindowGetDefault() returns. dk_swap.c reaches the window only
 *    through nwindowGetDefault(), so it is the same window; its reference pulls default_window.o into the link. This program defines no
 *    __appInit, so the default runs, and it calls neither viInitialize nor nwindowCreate itself (the facts are checked in tests/l6/dknro:
 *    objdump of the pinned libnx.a).
 *  - nxdk_create(width, height) comes first, with the size of the declared product decision: docked 1920x1080, handheld 1280x720.
 *  - Shutdown: the selftest's SHUTDOWN stage calls nxdk_ext(NXGPU_FN_SHUTDOWN), which only waits for the queue to become idle (nxdk_shutdown).
 *    The resources go with nxdk_destroy, which main calls exactly once, after the +-wait (the presented frame stays on screen until then);
 *    after a failed nxdk_create it is NOT called (nxdk_create already released everything: "NULL = failed, nothing stays allocated").
 *  - The three embedder symbols are defined below: nxdk_guest_ptr, nxdk_on_present, nxdk_shader_pack. */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <switch.h>
#include "nx/dk.h"
#include "nxgpu4_selftest.h"
#include "selftest_expect.h"

#ifndef FNV_BUILD_ID
#error "compile with -DFNV_BUILD_ID=\"<git sha>\""
#endif
#define DIR "sdmc:/switch/fnv"

extern const uint8_t ff_vs_dksh[], ff_fs_dksh[];
extern const uint32_t ff_vs_dksh_len, ff_fs_dksh_len;

typedef struct { uint64_t a, n; } Blk;
static Blk g_blk[64];
static int g_nblk;
static FILE* g_log;
static uint8_t* g_bmp;   /* the sink's copy; written once */
static volatile uint32_t g_presents, g_last_frame;   /* nxdk_on_present: completed presents and the number of the last one */

/* no shader pack is installed: the selftest hands DKSH images to shader_create directly (dk.h: NULL when none is installed) */
const void* nxdk_shader_pack(uint64_t* size) { *size = 0; return NULL; }
void nxdk_on_present(uint32_t frame) { g_presents++; g_last_frame = frame; }

static void die(const char* what) { if (g_log) { fprintf(g_log, "RESULT FAIL %s\n", what); fflush(g_log); } diagAbortWithResult(MAKERESULT(Module_Libnx, LibnxError_IoError)); }

/* identity address space: every address the backend was given is a host pointer inside one allocation of this program */
void* nxdk_guest_ptr(uint64_t addr, uint64_t size) {
    for (int i = 0; i < g_nblk; i++)
        if (addr >= g_blk[i].a && size <= g_blk[i].a + g_blk[i].n - addr) return (void*)(uintptr_t)addr;
    if (g_log) { fprintf(g_log, "BAD_POINTER addr=%#llx size=%llu is in no allocation\n", (unsigned long long)addr, (unsigned long long)size); fflush(g_log); }
    return NULL;
}
static void* h_alloc(void* ctx, uint32_t size, uint64_t* addr) {
    (void)ctx;
    size = (size + 4095u) & ~4095u;
    void* p = g_nblk < 64 ? aligned_alloc(4096, size) : NULL;
    if (!p) return NULL;
    memset(p, 0, size);
    g_blk[g_nblk++] = (Blk){(uint64_t)(uintptr_t)p, size};
    *addr = (uint64_t)(uintptr_t)p;
    return p;
}
static void h_log(void* ctx, const char* fmt, ...) {
    (void)ctx;
    va_list ap; va_start(ap, fmt); vfprintf(g_log, fmt, ap); va_end(ap);
    fputc('\n', g_log); fflush(g_log);
}
static void le(uint8_t* p, uint32_t v, int n) { for (int i = 0; i < n; i++) p[i] = (uint8_t)(v >> (8 * i)); }
/* 24-bit BMP, bottom-up BGR rows padded to 4 bytes */
static void h_frame(void* ctx, uint32_t w, uint32_t h, const uint8_t* rgba) {
    (void)ctx;
    uint32_t row = (w * 3 + 3) & ~3u, size = 54 + row * h;
    g_bmp = calloc(1, size);
    if (!g_bmp) die("no memory for the BMP");
    g_bmp[0] = 'B'; g_bmp[1] = 'M'; le(g_bmp + 2, size, 4); le(g_bmp + 10, 54, 4); le(g_bmp + 14, 40, 4);
    le(g_bmp + 18, w, 4); le(g_bmp + 22, h, 4); le(g_bmp + 26, 1, 2); le(g_bmp + 28, 24, 2); le(g_bmp + 34, row * h, 4);
    for (uint32_t y = 0; y < h; y++)
        for (uint32_t x = 0; x < w; x++) {
            const uint8_t* s = rgba + ((size_t)y * w + x) * 4; uint8_t* d = g_bmp + 54 + (size_t)(h - 1 - y) * row + x * 3;
            d[0] = s[2]; d[1] = s[1]; d[2] = s[0];
        }
    FILE* f = fopen(DIR "/firstframe.bmp", "wb");
    if (!f || fwrite(g_bmp, 1, size, f) != size || fclose(f)) die("cannot write firstframe.bmp");
    h_log(NULL, "BMP %ux%u written to " DIR "/firstframe.bmp", w, h);
}

/* runs the stages over the created backend; returns the selftest status (0 = pass) and logs PRESENTS and RESULT */
static int run_selftest(void) {
    static const NxSelftestExpect ex[] = { NXST_EXPECT_ROWS };
    char reason[256];
    NxSelftestHost host;
    memset(&host, 0, sizeof host);
    host.ext = nxdk_ext; host.alloc = h_alloc; host.log = h_log; host.frame = h_frame;
    host.shader_form = NXST_SHADER_DKSH;
    host.shader[0].data = ff_vs_dksh; host.shader[0].size = ff_vs_dksh_len;
    host.shader[1].data = ff_fs_dksh; host.shader[1].size = ff_fs_dksh_len;
    host.expect = ex; host.nexpect = sizeof ex / sizeof ex[0];
    host.present_frames = 120;   /* two seconds at 60 Hz */
    host.reason = reason; host.reason_cap = sizeof reason;
    int st = nxgpu4_selftest(&host);
    h_log(NULL, "PRESENTS %u last_frame %u", (unsigned)g_presents, (unsigned)g_last_frame);
    if (st) h_log(NULL, "RESULT FAIL %s", reason); else h_log(NULL, "RESULT PASS");
    return st;
}

int main(void) {
    mkdir(DIR, 0777);   /* exists after the first run: the open below is the check */
    g_log = fopen(DIR "/firstframe.log", "w");
    if (!g_log) diagAbortWithResult(MAKERESULT(Module_Libnx, LibnxError_IoError));
    AppletOperationMode mode = appletGetOperationMode();
    int docked = mode == AppletOperationMode_Console;
    h_log(NULL, "firstframe build %s operation mode %s (expected backbuffer %s)", FNV_BUILD_ID, docked ? "docked" : "handheld",
          docked ? "1920x1080" : "1280x720");
    char cerr[200];
    int created = nxdk_create(docked ? 1920 : 1280, docked ? 1080 : 720, cerr, sizeof cerr) != NULL;
    int st = 1;
    if (created) st = run_selftest();
    else h_log(NULL, "RESULT FAIL nxdk_create: %s", cerr);
    fclose(g_log); g_log = NULL;
    padConfigureInput(1, HidNpadStyleSet_NpadStandard);
    PadState pad;
    padInitializeDefault(&pad);
    while (appletMainLoop()) {
        padUpdate(&pad);
        if (padGetButtonsDown(&pad) & HidNpadButton_Plus) break;
        svcSleepThread(16000000ll);
    }
    if (created) nxdk_destroy();
    return st ? 1 : 0;
}
