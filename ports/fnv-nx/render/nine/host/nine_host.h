/* fnv-nx Gallium Nine host side (LP64): shared declarations (stream S6c-nine). */
#ifndef FNVNX_NINE_HOST_H
#define FNVNX_NINE_HOST_H
#include <stdbool.h>
#include <stdint.h>
#ifdef __SWITCH__
#include <switch.h>
#endif
#include <d3dadapter/d3dadapter9.h>
#include <d3dadapter/present.h>
#include "nine_ext.h"

struct pipe_resource;
struct pipe_screen;

extern const GUID IID_IDirect3D9, IID_ID3DPresent, IID_ID3DPresentGroup;

HRESULT nx_nine_create_adapter(ID3DAdapter9** out);                                   /* nine_adapter.c */
IDirect3D9* nx_direct3d_create9(UINT sdk);                                           /* nine_d3d9.c */
HRESULT nx_present_group_create(D3DPRESENT_PARAMETERS* pp, ID3DPresentGroup** out);   /* nine_present.c */
void nx_present_screen_size(int* w, int* h);

/* proxy dispatch: generated nine_proxy_host.c */
uint64_t nine_dispatch(uint64_t remote, uint64_t method, const uint64_t* a);
/* S5 ext_call handler for NINE_EXT_MODULE (nine_ext.h): args = {remote, address of the method's args[]} */
uint64_t nine_ext(uint32_t fn, const uint64_t* args, uint32_t nargs);
/* offscreen present backend of the host gate (gate/gate_winsys.c); not on Switch */
void nx_gate_capture(struct pipe_resource* res, unsigned slot);
struct pipe_screen* nx_gate_screen_create(void);
#endif
