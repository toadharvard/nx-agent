// NxVertexShader9 / NxPixelShader9: bytecode copy, validation (res_smcheck.cpp), content key, parsed interface (NxSmIface), GetFunction.
// Sources: Wine 9.0 d3d9 shader.c (vertexshader_init / pixelshader_init: byte_code_size = ~0, wined3d does the parse), wined3d shader.c (shader_init: NULL byte code and every parse failure =
// WINED3DERR_INVALIDCALL; wined3d_shader_get_byte_code: pData null -> size query; *size too small -> D3DERR_INVALIDCALL (MSDN says MOREDATA, Wine's comment: not actually true); copy leaves *size).
// Create*Shader: null byte_code / out pointer, no walkable END (spec sm_bytes), wrong stage word (a pixel blob to CreateVertexShader), any validator error (res_smcheck.cpp rules R1-R9) = D3DERR_INVALIDCALL.
// Allocation failure = E_OUTOFMEMORY (Wine d3d9 device.c d3d9_device_CreateVertexShader / CreatePixelShader, wined3d shader.c shader_init), nothing left behind.
// The content key is SHA-256 of the bytecode: fnvnx_sha256 of lib/sha256 (the one definition; linked by link.roots), the same function the translator and the backend use.
#include "res_core.h"
#include "../gen/d9impl.gen.h"
#include <string.h>
#include "fnvnx_sha256.h"

HRESULT NxShaderCore::getFunction(void* d, UINT* sz) {
    if (!sz) return D3DERR_INVALIDCALL;
    if (!d) { *sz = nbytes; return D3D_OK; }
    if (*sz < nbytes) return D3DERR_INVALIDCALL;
    memcpy(d, code, nbytes); return D3D_OK;
}
HRESULT STDMETHODCALLTYPE NxVertexShader9::GetFunction(void* d, UINT* sz) { return getFunction(d, sz); }
HRESULT STDMETHODCALLTYPE NxPixelShader9::GetFunction(void* d, UINT* sz) { return getFunction(d, sz); }
template <class S> static HRESULT make(NxObj* dev, const DWORD* bc, int stage, S** pp) {
    if (!pp) return D3DERR_INVALIDCALL;
    *pp = nullptr; if (!bc) return D3DERR_INVALIDCALL;
    const uint32_t* t = (const uint32_t*)bc; uint32_t nb = nx_sm_bytes(t); NxSmIface ifc;   // DWORD is a 4-byte type of the Windows ABI
    
    if (!nb || nx_sm_check(t, nb, stage, &ifc)) return D3DERR_INVALIDCALL;
    S* s = nx_make<S>(); if (!s) return E_OUTOFMEMORY;                                         // d3d9_device_CreateVertexShader / CreatePixelShader: calloc failure -> E_OUTOFMEMORY
    uint32_t* code = (uint32_t*)nxd3d_alloc(nb); if (!code) { nx_destroy(s); return E_OUTOFMEMORY; }   // wined3d shader_init: byte code copy failure -> E_OUTOFMEMORY
    s->asChild(dev, ~0u); s->code = code; memcpy(s->code, bc, nb); s->nbytes = nb; fnvnx_sha256(bc, nb, s->key); s->iface = ifc; *pp = s; return D3D_OK;
}
HRESULT STDMETHODCALLTYPE NxDevice9::CreateVertexShader(const DWORD* b, IDirect3DVertexShader9** pp) { return make<NxVertexShader9>(this, b, 0, (NxVertexShader9**)pp); }
HRESULT STDMETHODCALLTYPE NxDevice9Ex::CreateVertexShader(const DWORD* b, IDirect3DVertexShader9** pp) { return make<NxVertexShader9>(this, b, 0, (NxVertexShader9**)pp); }
HRESULT STDMETHODCALLTYPE NxDevice9::CreatePixelShader(const DWORD* b, IDirect3DPixelShader9** pp) { return make<NxPixelShader9>(this, b, 1, (NxPixelShader9**)pp); }
HRESULT STDMETHODCALLTYPE NxDevice9Ex::CreatePixelShader(const DWORD* b, IDirect3DPixelShader9** pp) { return make<NxPixelShader9>(this, b, 1, (NxPixelShader9**)pp); }
