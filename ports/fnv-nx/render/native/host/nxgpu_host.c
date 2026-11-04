/* fnv-nx native renderer: the Switch host module's side of nxgpu (stream S6n). S5's toolchain/host/host.c built
 * with -DFNVNX_HOST_NO_CONSOLE (deko3d owns the default window) links this file: it registers NXGPU_EXT_MODULE
 * with S5's ext_call table, creates the deko3d backend at the first call (after host.c's main has set up the
 * applet, sdmc and the guest) and dispatches every call with nxgpu_dispatch(). Same pattern as render/nine's
 * nine_host_call.c. */
#include <stdio.h>
#include <string.h>
#include <switch.h>
#include "fnvnx/hostapi.h"
#include "nx/gpu_abi.h"
#include "nxgpu.h"

#ifndef NXGPU_WIDTH
#define NXGPU_WIDTH 1280
#define NXGPU_HEIGHT 720
#endif
#ifndef NXGPU_HOST_HEAP_MIB
#define NXGPU_HOST_HEAP_MIB 512   /* deko3d image pool (192 MiB), staging, readback, command memory */
#endif
size_t fnvnx_host_heap_size = (size_t)NXGPU_HOST_HEAP_MIB << 20;   /* overrides host.c's weak 64 MiB */
#ifdef NXGPU_GUEST_PATH
const char* fnvnx_guest_path = NXGPU_GUEST_PATH;                  /* overrides host.c's weak default */
#endif

static FILE* g_log;
void nxgpu_log(const char* line) {
    svcOutputDebugString(line, strlen(line));
    if (!g_log) g_log = fopen("sdmc:/fnvnx/nxgpu.log", "a");
    if (g_log) { fputs(line, g_log); fflush(g_log); }
}

static const NxGpuApi* g_api;

static uint64_t nxgpu_ext(uint32_t fn, const uint64_t* args, uint32_t nargs) {
    if (!g_api) {
        g_api = nxgpu_init(NXGPU_WIDTH, NXGPU_HEIGHT);
        nxgpu_log(g_api ? "nxgpu: deko3d backend up\n" : "nxgpu: deko3d init FAILED\n");
        if (!g_api) return NXGPU_BAD_CALL;
    }
    return nxgpu_dispatch(g_api, fn, args, nargs);
}

__attribute__((constructor)) static void nxgpu_register(void) {
    fnvnx_host_register_ext(NXGPU_EXT_MODULE, nxgpu_ext);
}
