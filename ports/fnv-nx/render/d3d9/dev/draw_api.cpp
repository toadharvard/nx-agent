// draw_api.cpp: DrawPrimitive, DrawIndexedPrimitive, DrawPrimitiveUP, DrawIndexedPrimitiveUP of NxDevice9 and NxDevice9Ex: validation and HRESULTs. One draw = draw_exec.cpp.
// Sources: Wine 9.0 d3d9/device.c d3d9_device_DrawPrimitive / DrawIndexedPrimitive / DrawPrimitiveUP / DrawIndexedPrimitiveUP (checks and their order) and the rows of Wine's d3d9/tests/device.c
// test_draw_primitive (the rows after "SetVertexDeclaration(device, NULL)") that are NOT todo_wine = validated on Windows.
//  D1 no vertex declaration bound = D3DERR_INVALIDCALL, before anything else (Draw*: Wine; the stream and index bindings stay). The UP draws first test stride (0 = INVALIDCALL: Wine; Windows crashes there) and primitive count
//     (0 = D3D_OK, nothing happens: Wine).
//  D2 the UP draws FAIL for lack of a declaration after unbinding stream 0's buffer (vertex buffer NULL, offset and stride kept; Windows row "todo_wine ok(!current_vb)"), DrawIndexedPrimitiveUP also the index buffer
//     (Windows row "todo_wine ok(!current_ib)"). A completed UP draw leaves stream 0 = (NULL, 0, 0) and the index buffer NULL (Windows rows; wined3d_stateblock_set_stream_source(0, NULL, 0, 0)). Neither reset is
//     recorded into a state block (put_direct; Windows row "Resetting of stream source and index buffer is not recorded in stateblocks").
//  D3 DrawIndexedPrimitive without an index buffer (declaration valid) = D3D_OK, nothing drawn (Windows row; Wine 9 returns INVALIDCALL there, marked todo_wine). A draw whose vertex shader reads a stream without a vertex buffer
//     is D3D_OK and has no vertex data either (Windows row "Valid index buffer and vertex declaration, NULL stream source": SUCCEEDED).
//  D4 a primitive type that is no D3DPRIMITIVETYPE gives 0 vertices in Wine (FIXME in vertex_count_from_primitive_count) = D3D_OK, nothing drawn; primitive count 0 = D3D_OK, nothing drawn. Counts are UINT arithmetic (Wine).
//  D5 pointers: a null `data` / `index_data` with a primitive count > 0 = D3DERR_INVALIDCALL (Windows faults; same decision as dev/README "NULL out pointers"). The UP index format: D3DFMT_INDEX16 = 16 bit, EVERY other value = 32 bit
//     (Wine: idx_fmt_size = index_format == D3DFMT_INDEX16 ? 2 : 4). DrawIndexedPrimitiveUP uploads vertex_count vertices from data + min_vertex_idx * stride and draws with base vertex -min_vertex_idx (Wine).
//  D6 DrawIndexedPrimitive's MinVertexIndex / NumVertices are ignored (Wine passes only BaseVertexIndex and startIndex to wined3d; they are hints for software vertex processing).
#include "draw_core.h"
#include "../gen/d9impl.gen.h"

namespace {
HRESULT finish_up(NxDeviceCore* d, bool indexed, HRESULT hr) {
    NxStream ns = NxStream(); d->put_direct(NX_S_stream, &ns);
    if (indexed) { IDirect3DIndexBuffer9* nul = nullptr; d->put_direct(NX_S_ib, &nul); }
    return hr;
}
HRESULT no_decl_up(NxDeviceCore* d, bool indexed) {   // D2
    NxStream ns = d->cur.stream[0]; ns.vb = nullptr; d->put_direct(NX_S_stream, &ns);
    if (indexed) { IDirect3DIndexBuffer9* nul = nullptr; d->put_direct(NX_S_ib, &nul); }
    return D3DERR_INVALIDCALL;
}
HRESULT do_dp(NxDeviceCore* d, D3DPRIMITIVETYPE t, UINT start, UINT count) {
    if (!d->cur.vdecl[0]) return D3DERR_INVALIDCALL;                      // D1
    if (!nx_prim_valid(t) || !count) return D3D_OK;                       // D4
    NxDrawReq r = {t, count, false, 0, start, nullptr}; return nx_draw_exec(d, r);
}
HRESULT do_dip(NxDeviceCore* d, D3DPRIMITIVETYPE t, INT base, UINT start, UINT count) {
    if (!d->cur.vdecl[0]) return D3DERR_INVALIDCALL;                      // D1
    if (!d->cur.ib[0]) return D3D_OK;                                     // D3
    if (!nx_prim_valid(t) || !count) return D3D_OK;                       // D4
    NxDrawReq r = {t, count, true, base, start, nullptr}; return nx_draw_exec(d, r);
}
HRESULT do_dpup(NxDeviceCore* d, D3DPRIMITIVETYPE t, UINT count, const void* data, UINT stride) {
    if (!stride) return D3DERR_INVALIDCALL;                               // D1
    if (!count) return D3D_OK;
    if (!d->cur.vdecl[0]) return no_decl_up(d, false);                    // D2
    if (!data) return D3DERR_INVALIDCALL;                                 // D5
    if (!nx_prim_valid(t)) return finish_up(d, false, D3D_OK);            // D4
    uint64_t bytes = (uint64_t)nx_prim_vertices(t, count) * stride; if (bytes > 0x7FFFFFFFull) return D3DERR_INVALIDCALL;
    NxUpData up = {data, (uint32_t)bytes, stride, nullptr, 0, 0}; NxDrawReq r = {t, count, false, 0, 0, &up};
    return finish_up(d, false, nx_draw_exec(d, r));
}
HRESULT do_dipup(NxDeviceCore* d, D3DPRIMITIVETYPE t, UINT minv, UINT nverts, UINT count, const void* idata, D3DFORMAT ifmt, const void* vdata, UINT stride) {
    if (!stride) return D3DERR_INVALIDCALL;
    if (!count) return D3D_OK;
    if (!d->cur.vdecl[0]) return no_decl_up(d, true);
    if (!vdata || !idata) return D3DERR_INVALIDCALL;
    if (!nx_prim_valid(t)) return finish_up(d, true, D3D_OK);
    uint32_t isz = ifmt == D3DFMT_INDEX16 ? 2 : 4; uint64_t ib = (uint64_t)nx_prim_vertices(t, count) * isz, vb = (uint64_t)nverts * stride;   // D5
    if (ib > 0x7FFFFFFFull || vb > 0x7FFFFFFFull) return D3DERR_INVALIDCALL;
    NxUpData up = {(const uint8_t*)vdata + (uint64_t)minv * stride, (uint32_t)vb, stride, idata, (uint32_t)ib, isz == 4 ? 1u : 0u}; NxDrawReq r = {t, count, true, -(int32_t)minv, 0, &up};
    return finish_up(d, true, nx_draw_exec(d, r));
}
}
#define DRAW_METHODS(C) \
    HRESULT STDMETHODCALLTYPE C::DrawPrimitive(D3DPRIMITIVETYPE t, UINT s, UINT n) { return do_dp(this, t, s, n); } \
    HRESULT STDMETHODCALLTYPE C::DrawIndexedPrimitive(D3DPRIMITIVETYPE t, INT b, UINT, UINT, UINT s, UINT n) { return do_dip(this, t, b, s, n); } \
    HRESULT STDMETHODCALLTYPE C::DrawPrimitiveUP(D3DPRIMITIVETYPE t, UINT n, const void* data, UINT stride) { return do_dpup(this, t, n, data, stride); } \
    HRESULT STDMETHODCALLTYPE C::DrawIndexedPrimitiveUP(D3DPRIMITIVETYPE t, UINT minv, UINT nv, UINT n, const void* idata, D3DFORMAT f, const void* vdata, UINT stride) { return do_dipup(this, t, minv, nv, n, idata, f, vdata, stride); }
DRAW_METHODS(NxDevice9) DRAW_METHODS(NxDevice9Ex)
