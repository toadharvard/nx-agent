// The device methods that are state but need more than a table store (lists in state_model.py HAND). Sources: MS IDirect3DDevice9 method pages (sdk-api
// d3d9helper), Wine d3d9/device.c + wined3d/stateblock.c for behaviour the MS pages leave open (each marked "Wine:"; doubts in README.md).
#include "state_rt.h"
#include "../gen/d9impl.gen.h"
#define INV return D3DERR_INVALIDCALL

// Allocation failure (README "Allocation failure"): a light / palette entry the set cannot grow for = E_OUTOFMEMORY with the device unchanged (wined3d_stateblock_set_light: heap_alloc_zero failure ->
// E_OUTOFMEMORY, returned through d3d9_device_SetLight / LightEnable); the new entry is added only when no further step can fail.
// ---- lights. MS SetLight: any index; GetLight/GetLightEnable of an unassigned index: D3DERR_INVALIDCALL; LightEnable of an unassigned index creates a
// default light (MS LightEnable remarks: DIRECTIONAL, diffuse (1,1,1,0), direction (0,0,1), all else 0) with the requested enable. New lights are disabled.
// wine-9.0 wined3d_stateblock_set_light: types 1..5 (POINT, SPOT, DIRECTIONAL, PARALLELPOINT, GLSPOT) are accepted, any other type is INVALIDCALL; POINT / SPOT / GLSPOT with a negative
// attenuation (any of the three, NaN compares false) is INVALIDCALL as well ("Attenuation is negative"); DIRECTIONAL / PARALLELPOINT ignore attenuation.
HRESULT NxDeviceCore::SetLight(DWORD index, const D3DLIGHT9* light) {
    if (!light) INV;
    switch ((uint32_t)light->Type) {
    case 1: case 2: case 5: if (light->Attenuation0 < 0.0f || light->Attenuation1 < 0.0f || light->Attenuation2 < 0.0f) INV; break;
    case 3: case 4: break;
    default: INV;
    }
    if (rec) { NxLightEnt* e = rec->st.lights.add(index); if (!e) return E_OUTOFMEMORY; e->light = *light; e->has_light = 1; return D3D_OK; }
    NxLightEnt* e = lights.add(index); if (!e) return E_OUTOFMEMORY; e->light = *light; e->has_light = 1; dirty |= NX_G_LIGHTS; return D3D_OK;
}
HRESULT NxDeviceCore::GetLight(DWORD Index, D3DLIGHT9* pLight) {
    NxLightEnt* e = lights.find(Index); if (!pLight || !e) INV; *pLight = e->light; return D3D_OK;
}
HRESULT NxDeviceCore::LightEnable(DWORD Index, BOOL Enable) {
    if (rec) { NxLightEnt* e = rec->st.lights.add(Index); if (!e) return E_OUTOFMEMORY; e->enabled = Enable != 0; e->has_enable = 1; return D3D_OK; }
    NxLightEnt* e = lights.find(Index);
    if (!e) {
        e = lights.add(Index); if (!e) return E_OUTOFMEMORY; D3DLIGHT9 d = D3DLIGHT9(); d.Type = D3DLIGHT_DIRECTIONAL; d.Diffuse.r = d.Diffuse.g = d.Diffuse.b = 1.0f; d.Direction.z = 1.0f;
        e->light = d; e->has_light = 1;
    }
    e->enabled = Enable != 0; e->has_enable = 1; dirty |= NX_G_LIGHTS; return D3D_OK;
}
HRESULT NxDeviceCore::GetLightEnable(DWORD Index, BOOL* pEnable) {
    NxLightEnt* e = lights.find(Index); if (!pEnable || !e) INV; *pEnable = e->enabled; return D3D_OK;
}

// ---- palettes: 256 entries each, unbounded palette numbers, not in any block (MS lists only "the current texture palette"). MS SetPaletteEntries:
// without D3DPTEXTURECAPS_ALPHAPALETTE every entry's alpha (PALETTEENTRY.peFlags) must be 255, else INVALIDCALL. An unassigned palette cannot be read (INVALIDCALL).
HRESULT NxDeviceCore::SetPaletteEntries(UINT palette_idx, const PALETTEENTRY* entries) {
    if (!entries) INV;
    const uint8_t* e = (const uint8_t*)entries;
    if (!NX_D3D_TEXTURECAPS_ALPHAPALETTE) for (int i = 0; i < 256; i++) if (e[i * 4 + 3] != 255) INV;
    NxPalEnt* p = pals.add(palette_idx); if (!p) return E_OUTOFMEMORY;   // Wine's d3d9 palette API is a stub; the closed choice is wined3d's light precedent
    memcpy(p->e, e, sizeof(NxPalEnt::e)); dirty |= NX_G_PALETTE; return D3D_OK;
}
HRESULT NxDeviceCore::GetPaletteEntries(UINT PaletteNumber, PALETTEENTRY* pEntries) {
    NxPalEnt* p = pals.find(PaletteNumber); if (!pEntries || !p) INV; memcpy(pEntries, p->e, sizeof(p->e)); return D3D_OK;
}

// ---- FVF: the stored state is the declaration (Wine d3d9 device.c): SetFVF binds the device's implicit declaration of that FVF, GetFVF returns the FVF the bound
// declaration was made from (0 for an element declaration or none). SetFVF(0) changes nothing and succeeds (Wine).
HRESULT NxDeviceCore::SetFVF(DWORD FVF) {
    if (!FVF) return D3D_OK;
    IDirect3DVertexDeclaration9* d = nx_device_fvf_decl(this, FVF); if (!d) return D3DERR_DRIVERINTERNALERROR;   // d3d9_device_SetFVF: "Failed to create a vertex declaration for fvf" (invalid FVF or out of memory alike); the binding is untouched
    put(NX_S_vdecl, &d); return D3D_OK;
}
HRESULT NxDeviceCore::GetFVF(DWORD* pFVF) {
    if (!pFVF) INV;
    IDirect3DVertexDeclaration9* d = cur.vdecl[0]; *pFVF = d ? static_cast<NxDeclCore*>(nx_obj_of(d))->fvf : 0; return D3D_OK;
}

// ---- render targets / depth-stencil: device bindings, not state-block state (MS lists). MS SetRenderTarget: index < NumSimultaneousRTs (4), target 0 never NULL, surface
// needs D3DUSAGE_RENDERTARGET; SetDepthStencilSurface: surface needs D3DUSAGE_DEPTHSTENCIL; Get of an empty binding: D3DERR_NOTFOUND. Wine: SetRenderTarget(0) resets
// viewport and scissor to the target's size.
HRESULT NxDeviceCore::SetRenderTarget(DWORD RenderTargetIndex, IDirect3DSurface9* pRenderTarget) {
    if (RenderTargetIndex >= NX_D3D_MAX_RTS || (!RenderTargetIndex && !pRenderTarget) || !nx_chk_obj(pRenderTarget)) INV;
    D3DSURFACE_DESC d = D3DSURFACE_DESC();
    if (pRenderTarget) { if (FAILED(pRenderTarget->GetDesc(&d)) || !(d.Usage & D3DUSAGE_RENDERTARGET)) INV; }
    put_direct(NX_S_rt + RenderTargetIndex, &pRenderTarget);
    if (!RenderTargetIndex) {
        D3DVIEWPORT9 v = {0, 0, d.Width, d.Height, 0.0f, 1.0f}; RECT r = {0, 0, (LONG)d.Width, (LONG)d.Height};
        put_direct(NX_S_viewport, &v); put_direct(NX_S_scissor, &r);
    }
    return D3D_OK;
}
HRESULT NxDeviceCore::GetRenderTarget(DWORD RenderTargetIndex, IDirect3DSurface9** ppRenderTarget) {
    if (RenderTargetIndex >= NX_D3D_MAX_RTS || !ppRenderTarget) INV;
    IDirect3DSurface9* s = cur.rt[RenderTargetIndex]; *ppRenderTarget = s;
    if (!s) return D3DERR_NOTFOUND;
    s->AddRef(); return D3D_OK;
}
HRESULT NxDeviceCore::SetDepthStencilSurface(IDirect3DSurface9* pNewZStencil) {
    if (!nx_chk_obj(pNewZStencil)) INV;
    if (pNewZStencil) { D3DSURFACE_DESC d = D3DSURFACE_DESC(); if (FAILED(pNewZStencil->GetDesc(&d)) || !(d.Usage & D3DUSAGE_DEPTHSTENCIL)) INV; }
    put_direct(NX_S_ds, &pNewZStencil); return D3D_OK;
}
HRESULT NxDeviceCore::GetDepthStencilSurface(IDirect3DSurface9** ppZStencilSurface) {
    if (!ppZStencilSurface) INV;
    IDirect3DSurface9* s = cur.ds[0]; *ppZStencilSurface = s;
    if (!s) return D3DERR_NOTFOUND;
    s->AddRef(); return D3D_OK;
}

// ---- software vertex processing (MS SetSoftwareVertexProcessing: "not recorded by StateBlocks"; changeable only on a D3DCREATE_MIXED_VERTEXPROCESSING device,
// fixed to the creation flag otherwise)
HRESULT NxDeviceCore::SetSoftwareVertexProcessing(BOOL bSoftware) {
    if (!(cp.behavior & D3DCREATE_MIXED_VERTEXPROCESSING)) INV;
    swvp = bSoftware != 0; dirty |= NX_G_MISC; return D3D_OK;
}
BOOL NxDeviceCore::GetSoftwareVertexProcessing() {
    return cp.behavior & D3DCREATE_MIXED_VERTEXPROCESSING ? swvp : (cp.behavior & D3DCREATE_SOFTWARE_VERTEXPROCESSING) != 0;
}

// ---- state blocks. MS: CreateStateBlock captures at once; Begin/End record every Set* in between WITHOUT applying it to the device (Wine: reads still see the
// device); a nested Begin and an End without Begin are INVALIDCALL. A recording block holds no public reference until End hands it out.
HRESULT NxDeviceCore::CreateStateBlock(D3DSTATEBLOCKTYPE Type, IDirect3DStateBlock9** ppSB) {
    int t = Type == D3DSBT_ALL ? 0 : Type == D3DSBT_PIXELSTATE ? 1 : Type == D3DSBT_VERTEXSTATE ? 2 : -1;
    if (t < 0 || !ppSB) INV;
    NxStateBlock9* b = nx_make<NxStateBlock9>(); if (!b) return E_OUTOFMEMORY;   // d3d9_device_CreateStateBlock: calloc failure -> E_OUTOFMEMORY
    b->asChild(this, ~0u); b->devc = this;
    memcpy(b->st.mask, nx_tab.mask[t], sizeof b->st.mask);
    if (t != 1) for (uint32_t i = 0; i < lights.n; i++) {   // on failure nothing of the device was touched; the half-made block goes back through its public count
        NxLightEnt* e = b->st.lights.add(lights.a[i].idx); if (!e) { static_cast<NxObj*>(b)->release(); return E_OUTOFMEMORY; }
        e->light = lights.a[i].light; e->enabled = lights.a[i].enabled; e->has_light = e->has_enable = 1;
    }
    HRESULT hr = b->Capture(); if (FAILED(hr)) { static_cast<NxObj*>(b)->release(); return hr; }
    *ppSB = b; return D3D_OK;
}
HRESULT NxDeviceCore::BeginStateBlock() {
    if (rec) INV;
    NxStateBlock9* b = nx_make<NxStateBlock9>(); if (!b) return E_OUTOFMEMORY;   // wined3d_stateblock_create: heap_alloc_zero failure -> E_OUTOFMEMORY (through d3d9_device_BeginStateBlock)
    b->asDeferred(this); b->devc = this; rec = b; return D3D_OK;
}
HRESULT NxDeviceCore::EndStateBlock(IDirect3DStateBlock9** ppSB) {
    if (!rec || !ppSB) INV;
    NxStateBlockCore* b = rec; rec = nullptr; b->publish(); *ppSB = static_cast<NxStateBlock9*>(b); return D3D_OK;
}
