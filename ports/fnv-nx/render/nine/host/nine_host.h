/* fnv-nx Gallium Nine host side (LP64): shared declarations (stream S6c-nine). */
#ifndef FNVNX_NINE_HOST_H
#define FNVNX_NINE_HOST_H
#include <stdbool.h>
#include <stdint.h>
#include <switch.h>
#include <d3dadapter/d3dadapter9.h>
#include <d3dadapter/present.h>

extern const GUID IID_IDirect3D9, IID_ID3DPresent, IID_ID3DPresentGroup;

HRESULT nx_nine_create_adapter(ID3DAdapter9** out);                                   /* nine_adapter.c */
IDirect3D9* nx_direct3d_create9(UINT sdk);                                           /* nine_d3d9.c */
HRESULT nx_present_group_create(D3DPRESENT_PARAMETERS* pp, ID3DPresentGroup** out);   /* nine_present.c */
void nx_present_screen_size(int* w, int* h);

/* proxy dispatch (generated nine_proxy_host.c + nine_host_manual.c) */
uint64_t nine_dispatch(uint64_t remote, uint64_t method, const uint64_t* a);
uint64_t nine_dispatch_manual(uint64_t remote, uint64_t method, const uint64_t* a);
/* host-call entry: (remote, method, args address); remote 0 + method NINE_CREATE9 = Direct3DCreate9(args[0]) */
#define NINE_CREATE9 0xFFFFu
uint64_t nine_host_call(uint64_t remote, uint64_t method, uint64_t args);
#endif
