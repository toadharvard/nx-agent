/* fnv-nx: the host entry of the Nine proxy (stream S6c-nine): S5's ext_call handler for NINE_EXT_MODULE. */
#include "nine_host.h"

#define D3DERR_INVALIDCALL_ 0x8876086Cu

#ifdef __SWITCH__
static void nine_host_init_once(void);
#endif

__attribute__((visibility("default")))
uint64_t nine_ext(uint32_t fn, const uint64_t* args, uint32_t nargs) {
    if (nargs < 2 || !args) return D3DERR_INVALIDCALL_;
    uint64_t remote = args[0];
    const uint64_t* a = (const uint64_t*)(uintptr_t)args[1];
    if (fn == NINE_FN_CREATE9) {
#ifdef __SWITCH__
        nine_host_init_once();   /* before the first device (and so the first shader) exists */
#endif
        return (uint64_t)(uintptr_t)nx_direct3d_create9((UINT)a[0]);
    }
    if (!remote) return D3DERR_INVALIDCALL_;
    return nine_dispatch(remote, fn, a);
}

#ifdef __SWITCH__
/* S5 host module: register before main() runs the guest (host_v3.inc keeps a static handler table). */
#include "fnvnx/hostapi.h"
__attribute__((constructor)) static void nine_register(void) {
    fnvnx_host_register_ext(NINE_EXT_MODULE, nine_ext);
}
/* nvc0 precompiled shader cache (S6d, patch 0005, host/nine_shader_cache.c): configured once, at the first
 * Direct3DCreate9 (sdmc is mounted by then, and no shader exists before a device); counters logged at exit. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nine_shader_cache.h"

static void nine_host_log(const char* line) {   /* host.c's log handle is private to it: append to the same file */
    svcOutputDebugString(line, strlen(line));
    FILE* f = fopen("sdmc:/fnvnx/host.log", "a");
    if (f) { fputs(line, f); fclose(f); }
}

static void nine_shader_cache_report(void) {
    unsigned entries = 0, hits = 0, misses = 0, stores = 0;
    nx_nine_shader_cache_stats(&entries, &hits, &misses, &stores);
    char buf[160];
    snprintf(buf, sizeof buf, "nine: nvc0 shader cache: %u entries, %u hits, %u misses, %u stored\n",
             entries, hits, misses, stores);
    nine_host_log(buf);
}

static void nine_host_init_once(void) {
    static int done;
    if (done) return;
    done = 1;
    int rc = nx_nine_shader_cache_init(NULL);   /* $NVC0_SHADER_CACHE, else NX_NINE_SHADER_CACHE_PATH */
    nine_host_log(rc ? "nine: nvc0 shader cache off (cannot create its directory)\n"
                     : "nine: nvc0 shader cache on (" NX_NINE_SHADER_CACHE_PATH ", read-write)\n");
    atexit(nine_shader_cache_report);
}

/* Host heap split (overrides host.c's weak 64 MiB): libdrm_nouveau allocates buffer-object memory from the host
 * heap, and nine keeps system-memory copies of MANAGED resources there; the rest of the heap region goes to the
 * guest. NINE_HOST_HEAP_MIB (switch_host.sh: HOST_HEAP_MIB) sets it at build time; the proposed split is in
 * README §6. */
#include <stddef.h>
#ifndef NINE_HOST_HEAP_MIB
#define NINE_HOST_HEAP_MIB 512
#endif
_Static_assert(NINE_HOST_HEAP_MIB % 2 == 0 && NINE_HOST_HEAP_MIB >= 64 && NINE_HOST_HEAP_MIB <= 1536,
               "host heap: a multiple of 2 MiB, 64..1536 MiB (the heap region is 2 GiB)");
size_t fnvnx_host_heap_size = (size_t)NINE_HOST_HEAP_MIB << 20;
#endif

/* The proxy passes POD structs by address, so their LP64 (Mesa header) layout must equal the x86/ILP32 SDK layout
 * (shim/include asserts the SDK sizes); the converted structs must match nine_proxy.h's NxW_ copies. */
#include <stddef.h>
_Static_assert(sizeof(D3DCAPS9) == 304, "D3DCAPS9");
_Static_assert(sizeof(D3DADAPTER_IDENTIFIER9) == 1100, "D3DADAPTER_IDENTIFIER9");
_Static_assert(sizeof(D3DSURFACE_DESC) == 32, "D3DSURFACE_DESC");
_Static_assert(sizeof(D3DVOLUME_DESC) == 28, "D3DVOLUME_DESC");
_Static_assert(sizeof(D3DVERTEXBUFFER_DESC) == 24 && sizeof(D3DINDEXBUFFER_DESC) == 20, "VB/IB desc");
_Static_assert(sizeof(D3DLIGHT9) == 104 && sizeof(D3DMATERIAL9) == 68, "D3DLIGHT9/MATERIAL9");
_Static_assert(sizeof(D3DVIEWPORT9) == 24 && sizeof(D3DMATRIX) == 64, "VIEWPORT/MATRIX");
_Static_assert(sizeof(D3DVERTEXELEMENT9) == 8 && sizeof(D3DDISPLAYMODE) == 16, "VERTEXELEMENT/DISPLAYMODE");
_Static_assert(sizeof(D3DRASTER_STATUS) == 8 && sizeof(D3DGAMMARAMP) == 1536 && sizeof(RECT) == 16, "misc");
_Static_assert(sizeof(D3DPRESENT_PARAMETERS) == 64 && offsetof(D3DPRESENT_PARAMETERS, hDeviceWindow) == 32 &&
               offsetof(D3DPRESENT_PARAMETERS, Windowed) == 40, "D3DPRESENT_PARAMETERS = NxW_");
_Static_assert(sizeof(D3DLOCKED_RECT) == 16 && offsetof(D3DLOCKED_RECT, pBits) == 8, "D3DLOCKED_RECT = NxW_");
_Static_assert(sizeof(D3DLOCKED_BOX) == 16 && offsetof(D3DLOCKED_BOX, pBits) == 8, "D3DLOCKED_BOX = NxW_");
_Static_assert(sizeof(D3DDEVICE_CREATION_PARAMETERS) == 24 &&
               offsetof(D3DDEVICE_CREATION_PARAMETERS, hFocusWindow) == 8, "D3DDEVICE_CREATION_PARAMETERS = NxW_");
