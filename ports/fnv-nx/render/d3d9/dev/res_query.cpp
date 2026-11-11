// NxQuery9 object part. Sources: Wine 9.0 d3d9 query.c (query_init, GetData, GetDataSize), wined3d query.c (wined3d_query_get_data: BUILDING -> S_FALSE, CREATED -> INVALIDCALL; cs.c
// wined3d_cs_issue_query: BEGIN flag -> BUILDING, anything else -> SIGNALLED); MS D3DQUERYTYPE / IDirect3DQuery9 pages for the data sizes (spec tables.query_type agrees: test).
// CreateQuery: type with no documented data size (7, 20+, ...) or not supported by the ENV (nx_env_query_supported) = D3DERR_NOTAVAILABLE; ppQuery null = support check (D3D_OK / NOTAVAILABLE, nothing created).
// Issue(flags): any flags succeed (Wine); state BEGIN -> building, else signaled (END); the backend is told (nxd3d_query_issue) after the state change.
// GetData: building -> S_FALSE; created (never issued) -> S_OK, data = 0xdd in min(size, data_size) bytes, 0 beyond (Wine d3d9 query.c; deterministic); signaled -> backend ready? no: D3DGETDATA_FLUSH
// Allocation failure: the object = E_OUTOFMEMORY (d3d9_device_CreateQuery), the backend's id / stream memory = D3DERR_OUTOFVIDEOMEMORY (nxd3d_query_create false); nothing left behind.
// calls nxd3d_query_flush, S_FALSE; yes: copy min(size, data_size) result bytes, S_OK. pData may be null (status only). The result values are the backend's (hooks declared in res_core.h, not defined).
#include "res_core.h"
#include "../gen/d9impl.gen.h"
#include <string.h>

uint32_t nx_query_data_size(uint32_t t) {
    switch (t) {
    case D3DQUERYTYPE_VCACHE: return sizeof(D3DDEVINFO_VCACHE);
    case D3DQUERYTYPE_RESOURCEMANAGER: return sizeof(D3DDEVINFO_D3DRESOURCEMANAGER);
    case D3DQUERYTYPE_VERTEXSTATS: return sizeof(D3DDEVINFO_D3DVERTEXSTATS);
    case D3DQUERYTYPE_EVENT: return sizeof(BOOL);
    case D3DQUERYTYPE_OCCLUSION: return sizeof(DWORD);
    case D3DQUERYTYPE_TIMESTAMP: return 8;
    case D3DQUERYTYPE_TIMESTAMPDISJOINT: return sizeof(BOOL);          // d3d9 returns only the BOOL of Wine's disjoint struct
    case D3DQUERYTYPE_TIMESTAMPFREQ: return 8;
    case D3DQUERYTYPE_PIPELINETIMINGS: return sizeof(D3DDEVINFO_D3D9PIPELINETIMINGS);
    case D3DQUERYTYPE_INTERFACETIMINGS: return sizeof(D3DDEVINFO_D3D9INTERFACETIMINGS);
    case D3DQUERYTYPE_VERTEXTIMINGS: case D3DQUERYTYPE_PIXELTIMINGS: return sizeof(D3DDEVINFO_D3D9STAGETIMINGS);
    case D3DQUERYTYPE_BANDWIDTHTIMINGS: return sizeof(D3DDEVINFO_D3D9BANDWIDTHTIMINGS);
    case D3DQUERYTYPE_CACHEUTILIZATION: return sizeof(D3DDEVINFO_D3D9CACHEUTILIZATION);
    case D3DQUERYTYPE_MEMORYPRESSURE: return sizeof(D3DMEMORYPRESSURE);
    }
    return 0;
}
D3DQUERYTYPE STDMETHODCALLTYPE NxQuery9::GetType() { return (D3DQUERYTYPE)type; }
DWORD STDMETHODCALLTYPE NxQuery9::GetDataSize() { return data_size; }
HRESULT STDMETHODCALLTYPE NxQuery9::Issue(DWORD flags) { state = (flags & D3DISSUE_BEGIN) ? NXQ_BUILDING : NXQ_SIGNALED; nxd3d_query_issue(this, flags); return D3D_OK; }
HRESULT STDMETHODCALLTYPE NxQuery9::GetData(void* p, DWORD size, DWORD flags) {
    if (state == NXQ_BUILDING) return S_FALSE;
    if (state == NXQ_CREATED) { if (p) { memset(p, 0, size); memset(p, 0xdd, size < data_size ? size : data_size); } return S_OK; }
    if (!nxd3d_query_ready(this)) { if (flags & D3DGETDATA_FLUSH) nxd3d_query_flush(this); return S_FALSE; }
    if (p) nxd3d_query_result(this, p, size < data_size ? size : data_size);
    return S_OK;
}
static HRESULT make(NxObj* dev, D3DQUERYTYPE t, IDirect3DQuery9** pp) {
    uint32_t sz = nx_query_data_size(t);
    if (!sz || !nx_env_query_supported(t)) { if (pp) *pp = nullptr; return D3DERR_NOTAVAILABLE; }
    if (!pp) return D3D_OK;
    NxQuery9* q = nx_make<NxQuery9>(); if (!q) return E_OUTOFMEMORY;                                    // d3d9_device_CreateQuery: calloc failure -> E_OUTOFMEMORY
    q->asChild(dev, ~0u); q->type = t; q->data_size = sz;
    if (!nxd3d_query_create(q)) { NxObj* o = q; o->release(); return D3DERR_OUTOFVIDEOMEMORY; }   // the backend has no id (nxgpu4.h: ids 1..4096) or no memory for the stream it needs
    *pp = q; return D3D_OK;
}
HRESULT STDMETHODCALLTYPE NxDevice9::CreateQuery(D3DQUERYTYPE t, IDirect3DQuery9** pp) { return make(this, t, pp); }
HRESULT STDMETHODCALLTYPE NxDevice9Ex::CreateQuery(D3DQUERYTYPE t, IDirect3DQuery9** pp) { return make(this, t, pp); }
