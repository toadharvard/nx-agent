// NxVertexDeclaration9: CreateVertexDeclaration validation and GetDeclaration. Source: Wine 9.0 d3d9 vertexdeclaration.c convert_to_wined3d_declaration + vertexdeclaration_init,
// wined3d vertexdeclaration.c vertexdeclaration_init; HRESULTs from Wine tests device.c test_vertex_declaration_alignment / test_unused_declaration_type (Windows-validated): E_FAIL.
//  D1 the array ends at the first element with Stream == 0xFF (spec decl_bytes); END must be at index <= 126 (Wine's 128-count loop), else E_FAIL.
//  D2 Type >= D3DDECLTYPE_UNUSED (17) = E_FAIL.  D3 for Stream < 16 (other streams are tessellator pseudo streams: no further checks): Offset must be a multiple of 4 (every type is
//  4 bytes or larger: alignment = min(bytes, 4)), else E_FAIL; and the type must be supported by the environment (nx_env_decltype_supported, caps DeclTypes), else E_FAIL.
//  NOT validated (Wine does not): Usage / UsageIndex / Method ranges, duplicate usages, overlapping elements. Null elements / ppDecl = D3DERR_INVALIDCALL (Windows faults).
// Allocation failure: the declaration object = E_OUTOFMEMORY, its element copy = D3DERR_OUTOFVIDEOMEMORY (Wine d3d9 vertexdeclaration.c d3d9_vertex_declaration_create / convert_to_wined3d_declaration); nothing is left behind.
// GetDeclaration: *count = number of elements INCLUDING the END element (always written); pDecl != null copies exactly that many elements (END verbatim as passed to Create).
#include "res_core.h"
#include "../gen/d9impl.gen.h"
#include <string.h>

HRESULT nx_decl_validate(const D3DVERTEXELEMENT9* e, uint32_t* n) {
    uint32_t k = 0; while (k < 127 && e[k].Stream != 0xFF) k++;
    if (k == 127) return E_FAIL;
    for (uint32_t i = 0; i < k; i++) {
        if (e[i].Type >= D3DDECLTYPE_UNUSED) return E_FAIL;
        if (e[i].Stream < 16 && ((e[i].Offset & 3) || !nx_env_decltype_supported(e[i].Type))) return E_FAIL;
    }
    *n = k + 1; return D3D_OK;
}
static HRESULT make(NxObj* dev, const D3DVERTEXELEMENT9* e, IDirect3DVertexDeclaration9** pp) {
    if (!pp) return D3DERR_INVALIDCALL;
    *pp = nullptr; if (!e) return D3DERR_INVALIDCALL;
    uint32_t n; HRESULT hr = nx_decl_validate(e, &n); if (hr != D3D_OK) return hr;
    NxVertexDeclaration9* d = nx_make<NxVertexDeclaration9>(); if (!d) return E_OUTOFMEMORY;   // d3d9_vertex_declaration_create: calloc failure -> E_OUTOFMEMORY
    D3DVERTEXELEMENT9* el = (D3DVERTEXELEMENT9*)nxd3d_alloc(n * sizeof(D3DVERTEXELEMENT9)); if (!el) { nx_destroy(d); return D3DERR_OUTOFVIDEOMEMORY; }   // convert_to_wined3d_declaration: malloc failure -> D3DERR_OUTOFVIDEOMEMORY
    d->asChild(dev, ~0u); d->n = n; d->el = el; memcpy(d->el, e, n * sizeof(D3DVERTEXELEMENT9));
    for (uint32_t i = 0; i + 1 < n; i++) if (e[i].Stream < 16) { d->stream_mask |= 1u << e[i].Stream; uint32_t end = e[i].Offset + nx_decltype_bytes(e[i].Type); if (end > d->stride[e[i].Stream]) d->stride[e[i].Stream] = end; }
    d->fvf = nx_decl_to_fvf(e); *pp = d; return D3D_OK;
}
HRESULT STDMETHODCALLTYPE NxVertexDeclaration9::GetDeclaration(D3DVERTEXELEMENT9* p, UINT* cnt) {
    if (!cnt) return D3DERR_INVALIDCALL;
    *cnt = n; if (p) memcpy(p, el, n * sizeof(D3DVERTEXELEMENT9)); return D3D_OK;
}
HRESULT STDMETHODCALLTYPE NxDevice9::CreateVertexDeclaration(const D3DVERTEXELEMENT9* e, IDirect3DVertexDeclaration9** pp) { return make(this, e, pp); }
HRESULT STDMETHODCALLTYPE NxDevice9Ex::CreateVertexDeclaration(const D3DVERTEXELEMENT9* e, IDirect3DVertexDeclaration9** pp) { return make(this, e, pp); }
