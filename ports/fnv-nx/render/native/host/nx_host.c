/* fnv-nx host with the native renderer backend (stream S6n).
 *
 * Reuses S5's host (toolchain/host/host.c: heap split, layout report, guest loader, base host API) by including
 * it with its main renamed, and adds: no text console (deko3d owns the default window), the NxGpuApi extension
 * appended after FnvnxHostApi (the guest runtime accepts api->size >= its base size). The include is a stopgap:
 * request to S5 is a hook in host.c (weak fnvnx_host_ext_init(FnvnxHostApi*) + a "no console" switch), after
 * which this file shrinks to the hook.
 */
#include <switch.h>
#define consoleInit(x) ((PrintConsole*)0)
#define consoleUpdate(x) ((void)0)
#define consoleExit(x) ((void)0)
#define main fnvnx_s5_host_main
#include "../../../toolchain/host/host.c"
#undef main

#include "nxgpu.h"

static NxHostApi g_nxapi;

void nxgpu_log(const char* line) { logf_("%s", line); }

int main(int argc, char** argv) {
    (void)argc; (void)argv;
    padConfigureInput(1, HidNpadStyleSet_NpadStandard);
    padInitializeDefault(&g_pad);
    mkdir(FNVNX_LOG_DIR, 0777);
    g_log = fopen(FNVNX_LOG_PATH, "w");
    report_layout();

    u64 gbase = 0, gsize = 0, rc = 0xF00000FFu;
    if (!nxgpu_init(&g_nxapi.gpu, 1280, 720)) {
        logf_("fnvnx host: nxgpu_init failed\n");
    } else if (load_guest(fnvnx_guest_path, &gbase, &gsize)) {
        logf_("fnvnx host: guest %s mapped at %#010lx, %#lx bytes\n", fnvnx_guest_path, gbase, gsize);
        g_nxapi.base = (FnvnxHostApi){
            .magic = FNVNX_HOSTAPI_MAGIC, .version = FNVNX_HOSTAPI_VERSION, .size = sizeof(NxHostApi),
            .guest_base = gbase, .guest_size = gsize,
            .heap_base = g_guest_heap_base, .heap_size = g_guest_heap_size,
            .output_debug_string = (u64)h_output_debug_string, .sleep_ns = (u64)h_sleep_ns,
            .exit = (u64)h_exit, .console_write = (u64)h_console_write, .console_flush = (u64)h_console_flush,
            .main_loop = (u64)h_main_loop, .buttons_down = (u64)h_buttons_down,
            .system_tick = (u64)h_system_tick, .system_tick_freq = (u64)h_system_tick_freq,
        };
        u64 (*entry)(u64) = (u64 (*)(u64))gbase;
        rc = entry((u64)&g_nxapi);
        logf_("fnvnx host: guest returned %#lx\n", rc);
        ((u64 (*)(void))g_nxapi.gpu.shutdown)();
    }
    logf_("fnvnx host: exit\n");
    if (g_log) fclose(g_log);
    return (int)(rc & 0xFF);
}
