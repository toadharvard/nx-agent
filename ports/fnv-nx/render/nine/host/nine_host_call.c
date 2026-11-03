/* fnv-nx: the host entry of the Nine proxy (stream S6c-nine): S5's ext_call handler for NINE_EXT_MODULE. */
#include "nine_host.h"

#define D3DERR_INVALIDCALL_ 0x8876086Cu

__attribute__((visibility("default")))
uint64_t nine_ext(uint32_t fn, const uint64_t* args, uint32_t nargs) {
    if (nargs < 2 || !args) return D3DERR_INVALIDCALL_;
    uint64_t remote = args[0];
    const uint64_t* a = (const uint64_t*)(uintptr_t)args[1];
    if (fn == NINE_FN_CREATE9) return (uint64_t)(uintptr_t)nx_direct3d_create9((UINT)a[0]);
    if (!remote) return D3DERR_INVALIDCALL_;
    return nine_dispatch(remote, fn, a);
}

#ifdef __SWITCH__
/* S5 host module: register before main() runs the guest (host_v3.inc keeps a static handler table). */
#include "fnvnx/hostapi.h"
__attribute__((constructor)) static void nine_register(void) {
    fnvnx_host_register_ext(NINE_EXT_MODULE, nine_ext);
}
/* Host heap split (overrides host.c's weak 64 MiB): libdrm_nouveau allocates buffer-object memory from the host
 * heap, and nine keeps system-memory copies of MANAGED resources there; the rest of the heap region goes to the
 * guest. 512 MiB for the tests; the game's split is S4/S5's call (README §2). */
#include <stddef.h>
size_t fnvnx_host_heap_size = 512ull << 20;
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
