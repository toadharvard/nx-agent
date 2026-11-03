/* fnv-nx host module with Gallium Nine (stream S6c-nine).
 *
 * Reuses S5's host (toolchain/host/host.c: heap split, layout report, guest loader, base host API) by including it
 * with its main renamed, without the text console (Nine presents to the default nwindow), and appends the Nine
 * call slot after FnvnxHostApi. Stopgap until S5's generic ext-call slot lands. */
#include <switch.h>
#define consoleInit(x) ((PrintConsole*)0)
#define consoleUpdate(x) ((void)0)
#define consoleExit(x) ((void)0)
#define main fnvnx_s5_host_main
#include "../../../toolchain/host/host.c"
#undef main
#include "../include_nine_api.h"

uint64_t nine_host_call(uint64_t remote, uint64_t method, uint64_t args);

static FnvnxNineHostApi g_nineapi;

int main(int argc, char** argv) {
    (void)argc; (void)argv;
    padConfigureInput(1, HidNpadStyleSet_NpadStandard);
    padInitializeDefault(&g_pad);
    mkdir(FNVNX_LOG_DIR, 0777);
    g_log = fopen(FNVNX_LOG_PATH, "w");
    report_layout();
    u64 gbase = 0, gsize = 0, rc = 0xF00000FFu;
    if (load_guest(fnvnx_guest_path, &gbase, &gsize)) {
        logf_("fnvnx host: guest %s mapped at %#010lx, %#lx bytes\n", fnvnx_guest_path, gbase, gsize);
        g_nineapi.base = (FnvnxHostApi){
            .magic = FNVNX_HOSTAPI_MAGIC, .version = FNVNX_HOSTAPI_VERSION, .size = sizeof(FnvnxNineHostApi),
            .guest_base = gbase, .guest_size = gsize,
            .heap_base = g_guest_heap_base, .heap_size = g_guest_heap_size,
            .output_debug_string = (u64)h_output_debug_string, .sleep_ns = (u64)h_sleep_ns,
            .exit = (u64)h_exit, .console_write = (u64)h_console_write, .console_flush = (u64)h_console_flush,
            .main_loop = (u64)h_main_loop, .buttons_down = (u64)h_buttons_down,
            .system_tick = (u64)h_system_tick, .system_tick_freq = (u64)h_system_tick_freq,
        };
        g_nineapi.nine_magic = FNVNX_NINE_MAGIC; g_nineapi.nine_version = 1;
        g_nineapi.nine_call = (u64)nine_host_call;
        u64 (*entry)(u64) = (u64 (*)(u64))gbase;
        rc = entry((u64)&g_nineapi);
        logf_("fnvnx host: guest returned %#lx\n", rc);
    }
    logf_("fnvnx host: exit\n");
    if (g_log) fclose(g_log);
    return (int)(rc & 0xFF);
}
