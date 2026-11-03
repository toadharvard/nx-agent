/* fnv-nx: the single host-call entry of the Nine proxy (stream S6c-nine). */
#include "nine_host.h"

uint64_t nine_host_call(uint64_t remote, uint64_t method, uint64_t args) {
    const uint64_t* a = (const uint64_t*)(uintptr_t)args;
    if (method == NINE_CREATE9) return (uint64_t)(uintptr_t)nx_direct3d_create9((UINT)a[0]);
    if (!remote) return 0x8876086Cu;   /* D3DERR_INVALIDCALL */
    return nine_dispatch(remote, method, a);
}

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
