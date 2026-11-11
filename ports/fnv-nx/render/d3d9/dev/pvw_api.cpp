// pvw_api.cpp: IDirect3DDevice9::ProcessVertices of NxDevice9 and NxDevice9Ex = wine-9.0 dlls/d3d9/device.c d3d9_device_ProcessVertices. Owner decision of 2026-11-11 ("Wine 9.0 as an acceptable implementation").
// The d3d9 layer of Wine validates nothing: it converts both pointers with unsafe_impl_from_* (a foreign object asserts, NULL gives NULL), forwards to wined3d_device_process_vertices with the destination buffer's CREATION FVF
// (NxBufCore::fmt) and the declaration argument, and returns its HRESULT. What this file decides:
//  P1 dst_buffer NULL or not one of our vertex buffers: Wine dereferences it (crash). Here D3DERR_INVALIDCALL, nothing happens (the README rule for pointers Windows faults on). Wine's own d3d9 tests have no row for it.
//  P2 declaration NULL: no message (Wine passes a NULL declaration, no FIXME). Not NULL and ours: FIXME "Output vertex declaration not implemented yet" (id 14) and the argument is otherwise unused. A foreign declaration: INVALIDCALL as P1.
//  P3 everything after that is pvw_core.cpp (return codes: D3D_OK, D3DERR_INVALIDCALL for a source without position element, and for the memory-unsafe cases of its rules M1 / M2).
// State is untouched: Wine rebinds sysmem streams around the call only to read them (the sources are read from the CPU memory of our buffers). Only the destination's dirty range moves (write map).
#include "pvw.h"
#include "../gen/d9impl.gen.h"

static HRESULT process(NxDeviceCore* d, UINT s, UINT di, UINT n, IDirect3DVertexBuffer9* dst, IDirect3DVertexDeclaration9* decl) {
    if (!dst || !nx_chk_obj(dst) || !nx_chk_obj(decl)) return D3DERR_INVALIDCALL;
    if (decl) fnvnx_wine_fixme(PVW_FIXME_OUT_DECL);
    return pvw_process(d, s, di, n, static_cast<NxBufCore*>(nx_obj_of((IUnknown*)dst)));
}
HRESULT STDMETHODCALLTYPE NxDevice9::ProcessVertices(UINT s, UINT di, UINT n, IDirect3DVertexBuffer9* dst, IDirect3DVertexDeclaration9* decl, DWORD) { return process(this, s, di, n, dst, decl); }
HRESULT STDMETHODCALLTYPE NxDevice9Ex::ProcessVertices(UINT s, UINT di, UINT n, IDirect3DVertexBuffer9* dst, IDirect3DVertexDeclaration9* decl, DWORD) { return process(this, s, di, n, dst, decl); }
