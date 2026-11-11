// part c: every documented D3DERR_INVALIDCALL condition returns it and leaves the device state (and dirty bits, lights, palettes) untouched
#include "t_tables.h"
struct Snap {
    NxDeviceState s; uint64_t dirty; uint32_t nl, np; D3DLIGHT9 l0;
    explicit Snap(NxDeviceCore* d) { memcpy((void*)&s, (void*)&d->cur, sizeof s); dirty = d->dirty; nl = d->lights.n; np = d->pals.n; l0 = d->lights.n ? d->lights.a[0].light : D3DLIGHT9(); }
    bool same(NxDeviceCore* d) const { return !memcmp((void*)&s, (void*)&d->cur, sizeof s) && dirty == d->dirty && nl == d->lights.n && np == d->pals.n; }
};
#define BAD(call) do { Snap sn(d); CHECK_HR((call), D3DERR_INVALIDCALL); CHECK(sn.same(d)); } while (0)
#define GOOD(call) CHECK_HR((call), D3D_OK)

void t_valid() {
    t_part("c");
    TEnv e = t_env(1); NxDeviceCore* d = e.dev; NxDevice9* dv = e.dev; DWORD v = 0; D3DMATRIX m = D3DMATRIX(); float p4[4] = {1, 2, 3, 4}, f[4 * 260] = {}; int ii[4 * 20] = {}; BOOL bb[20] = {};
    NxTexture9* tx = t_child<NxTexture9>(e); NxTexture9* scratch = t_child<NxTexture9>(e, D3DPOOL_SCRATCH); NxTexture9* sysmem = t_child<NxTexture9>(e, D3DPOOL_SYSTEMMEM); NxTexture9* mgd = t_child<NxTexture9>(e, D3DPOOL_MANAGED);
    NxVertexBuffer9* vb = t_child<NxVertexBuffer9>(e); IDirect3DBaseTexture9* g;
    // ---- samplers / stages: 0-15, D3DDMAPSAMPLER, D3DVERTEXTEXTURESAMPLER0-3 are valid; everything else is not
    const DWORD badS[] = {16, 17, 100, 255, 261, 262, 1000, 0x7fffffff, 0xffffffff};
    for (DWORD s : badS) { BAD(dv->SetTexture(s, tx)); BAD(dv->GetTexture(s, &g)); BAD(dv->SetSamplerState(s, D3DSAMP_ADDRESSU, 2)); BAD(dv->GetSamplerState(s, D3DSAMP_ADDRESSU, &v)); }
    const DWORD goodS[] = {0, 15, 256, 257, 258, 259, 260};
    for (DWORD s : goodS) { GOOD(dv->SetTexture(s, tx)); GOOD(dv->GetTexture(s, &g)); g->Release(); GOOD(dv->SetSamplerState(s, D3DSAMP_ADDRESSU, 2)); GOOD(dv->GetSamplerState(s, D3DSAMP_ADDRESSU, &v)); }
    const DWORD badT[] = {8, 9, 16, 255, 0xffffffff};
    for (DWORD s : badT) { BAD(dv->SetTextureStageState(s, D3DTSS_COLOROP, 2)); BAD(dv->GetTextureStageState(s, D3DTSS_COLOROP, &v)); }
    GOOD(dv->SetTextureStageState(7, D3DTSS_COLOROP, 2)); GOOD(dv->GetTextureStageState(7, D3DTSS_COLOROP, &v));
    // textures: MS SetTexture remarks (no D3DPOOL_SCRATCH, no SYSTEMMEM without D3DDEVCAPS_TEXTURESYSTEMMEMORY); only our base textures
    BAD(dv->SetTexture(0, scratch)); BAD(dv->SetTexture(0, sysmem)); GOOD(dv->SetTexture(0, mgd)); GOOD(dv->SetTexture(0, nullptr));
    BAD(dv->SetTexture(0, (IDirect3DBaseTexture9*)(IDirect3DVertexBuffer9*)vb));
    // ---- transforms: VIEW, PROJECTION, TEXTURE0-7, WORLDMATRIX(0..255)
    const DWORD badX[] = {0, 1, 4, 15, 24, 255, 512, 0x7fffffff};
    for (DWORD s : badX) { BAD(dv->SetTransform((D3DTRANSFORMSTATETYPE)s, &m)); BAD(dv->GetTransform((D3DTRANSFORMSTATETYPE)s, &m)); BAD(dv->MultiplyTransform((D3DTRANSFORMSTATETYPE)s, &m)); }
    for (DWORD s : {2u, 3u, 16u, 23u, 256u, 511u}) { GOOD(dv->SetTransform((D3DTRANSFORMSTATETYPE)s, &m)); GOOD(dv->GetTransform((D3DTRANSFORMSTATETYPE)s, &m)); GOOD(dv->MultiplyTransform((D3DTRANSFORMSTATETYPE)s, &m)); }
    BAD(dv->SetTransform(D3DTS_VIEW, nullptr)); BAD(dv->GetTransform(D3DTS_VIEW, nullptr)); BAD(dv->MultiplyTransform(D3DTS_VIEW, nullptr));
    // ---- null pointers
    BAD(dv->SetMaterial(nullptr)); BAD(dv->GetMaterial(nullptr)); BAD(dv->SetViewport(nullptr)); BAD(dv->GetViewport(nullptr)); BAD(dv->SetScissorRect(nullptr)); BAD(dv->GetScissorRect(nullptr));
    BAD(dv->SetClipPlane(0, nullptr)); BAD(dv->GetClipPlane(0, nullptr)); BAD(dv->SetLight(0, nullptr)); BAD(dv->GetRenderState(D3DRS_ZENABLE, nullptr)); BAD(dv->GetSamplerState(0, D3DSAMP_ADDRESSU, nullptr));
    BAD(dv->GetTextureStageState(0, D3DTSS_COLOROP, nullptr)); BAD(dv->GetTexture(0, nullptr)); BAD(dv->GetCurrentTexturePalette(nullptr)); BAD(dv->GetFVF(nullptr)); BAD(dv->GetLightEnable(0, nullptr));
    // ---- lights: type must be POINT/SPOT/DIRECTIONAL; GetLight / GetLightEnable of an unassigned index
    D3DLIGHT9 l = D3DLIGHT9(); for (int t : {0, 4, 5, 100, -1}) { l.Type = (D3DLIGHTTYPE)t; BAD(dv->SetLight(3, &l)); }
    for (int t : {1, 2, 3}) { l.Type = (D3DLIGHTTYPE)t; GOOD(dv->SetLight(3, &l)); }
    BAD(dv->GetLight(4, &l)); BAD(dv->GetLightEnable(4, bb)); BAD(dv->GetLight(0xffffffff, &l)); GOOD(dv->GetLight(3, &l));
    // ---- clip planes: index < NX_D3D_MAX_CLIP_PLANES (6)
    for (DWORD i : {6u, 7u, 100u, 0xffffffffu}) { BAD(dv->SetClipPlane(i, p4)); BAD(dv->GetClipPlane(i, p4)); }
    GOOD(dv->SetClipPlane(5, p4)); GOOD(dv->GetClipPlane(5, p4));
    // ---- streams 0-15, frequency rules (divider 0, INSTANCEDATA on stream 0, INSTANCEDATA with INDEXEDDATA)
    UINT o, s, fq; IDirect3DVertexBuffer9* gvb;
    for (UINT i : {16u, 17u, 255u, 0xffffffffu}) { BAD(dv->SetStreamSource(i, vb, 0, 4)); BAD(dv->GetStreamSource(i, &gvb, &o, &s)); BAD(dv->SetStreamSourceFreq(i, 1)); BAD(dv->GetStreamSourceFreq(i, &fq)); }
    GOOD(dv->SetStreamSource(15, vb, 4, 12)); GOOD(dv->GetStreamSource(15, &gvb, &o, &s)); gvb->Release(); GOOD(dv->SetStreamSourceFreq(15, 3)); GOOD(dv->GetStreamSourceFreq(15, &fq));
    BAD(dv->SetStreamSourceFreq(2, 0)); BAD(dv->SetStreamSourceFreq(0, D3DSTREAMSOURCE_INSTANCEDATA | 1)); BAD(dv->SetStreamSourceFreq(2, D3DSTREAMSOURCE_INSTANCEDATA | D3DSTREAMSOURCE_INDEXEDDATA | 1));
    GOOD(dv->SetStreamSourceFreq(2, D3DSTREAMSOURCE_INSTANCEDATA | 1)); BAD(dv->SetStreamSource(0, (IDirect3DVertexBuffer9*)(IDirect3DIndexBuffer9*)t_child<NxIndexBuffer9>(e), 0, 4));
    // NULL vb keeps the stream's offset and stride (Wine device.c test_refcount, run against Windows)
    GOOD(dv->SetStreamSource(1, vb, 8, 24)); GOOD(dv->SetStreamSource(1, nullptr, 99, 99)); GOOD(dv->GetStreamSource(1, &gvb, &o, &s)); CHECK(gvb == nullptr && o == 8 && s == 24);
    // ---- render targets / depth stencil
    IDirect3DSurface9 *rt = t_surface(e, D3DUSAGE_RENDERTARGET, 64, 32), *plain = t_surface(e, 0, 64, 32), *dsb = t_surface(e, D3DUSAGE_DEPTHSTENCIL, 64, 32), *x;
    for (DWORD i : {4u, 5u, 0xffffffffu}) { BAD(dv->SetRenderTarget(i, rt)); BAD(dv->GetRenderTarget(i, &x)); }
    BAD(dv->SetRenderTarget(0, nullptr)); BAD(dv->SetRenderTarget(0, plain)); BAD(dv->SetRenderTarget(1, plain)); BAD(dv->SetDepthStencilSurface(plain)); BAD(dv->SetDepthStencilSurface(rt));
    GOOD(dv->SetRenderTarget(3, rt)); GOOD(dv->SetRenderTarget(3, nullptr)); GOOD(dv->SetRenderTarget(0, rt)); GOOD(dv->SetDepthStencilSurface(dsb)); GOOD(dv->SetDepthStencilSurface(nullptr));
    BAD(dv->GetRenderTarget(0, nullptr)); BAD(dv->GetDepthStencilSurface(nullptr));
    // ---- shader constants: start + count within 256 (VS) / 224 (PS) floats, 16 ints, 16 bools; null data
    struct K { bool ps; int kind; UINT n; } K[] = {{0, 0, 256}, {1, 0, 224}, {0, 1, 16}, {1, 1, 16}, {0, 2, 16}, {1, 2, 16}};
    for (auto& k : K) {
        auto set = [&](UINT st, UINT c, bool nul) { const void* dp = k.kind == 0 ? (const void*)f : k.kind == 1 ? (const void*)ii : (const void*)bb; if (nul) dp = nullptr;
            return k.kind == 0 ? (k.ps ? dv->SetPixelShaderConstantF(st, (const float*)dp, c) : dv->SetVertexShaderConstantF(st, (const float*)dp, c))
                 : k.kind == 1 ? (k.ps ? dv->SetPixelShaderConstantI(st, (const int*)dp, c) : dv->SetVertexShaderConstantI(st, (const int*)dp, c))
                               : (k.ps ? dv->SetPixelShaderConstantB(st, (const BOOL*)dp, c) : dv->SetVertexShaderConstantB(st, (const BOOL*)dp, c)); };
        auto get = [&](UINT st, UINT c) { void* dp = k.kind == 0 ? (void*)f : k.kind == 1 ? (void*)ii : (void*)bb;
            return k.kind == 0 ? (k.ps ? dv->GetPixelShaderConstantF(st, (float*)dp, c) : dv->GetVertexShaderConstantF(st, (float*)dp, c))
                 : k.kind == 1 ? (k.ps ? dv->GetPixelShaderConstantI(st, (int*)dp, c) : dv->GetVertexShaderConstantI(st, (int*)dp, c))
                               : (k.ps ? dv->GetPixelShaderConstantB(st, (BOOL*)dp, c) : dv->GetVertexShaderConstantB(st, (BOOL*)dp, c)); };
        BAD(set(k.n - 1, 2, false)); BAD(set(k.n, 1, false)); BAD(set(0, k.n + 1, false)); BAD(set(k.n + 1, 0, false)); BAD(set(0xffffffff, 1, false)); BAD(set(0, 1, true));
        BAD(get(k.n - 1, 2)); BAD(get(k.n, 1)); BAD(get(0, k.n + 1)); BAD(get(k.n + 1, 0));
        GOOD(set(k.n - 1, 1, false)); GOOD(set(0, k.n, false)); GOOD(set(k.n, 0, false)); GOOD(get(0, k.n)); GOOD(get(k.n - 1, 1));
    }
    // ---- state blocks
    IDirect3DStateBlock9* sb = nullptr;
    for (int t : {0, 4, 5, 100, 0x7fffffff}) BAD(dv->CreateStateBlock((D3DSTATEBLOCKTYPE)t, &sb));
    BAD(dv->CreateStateBlock(D3DSBT_ALL, nullptr)); BAD(dv->EndStateBlock(&sb)); GOOD(dv->BeginStateBlock()); BAD(dv->BeginStateBlock()); BAD(dv->EndStateBlock(nullptr)); GOOD(dv->EndStateBlock(&sb)); sb->Release();
    // ---- software vertex processing: only a D3DCREATE_MIXED_VERTEXPROCESSING device may switch; others report their creation flag
    TEnv hw = t_env(1, D3DCREATE_HARDWARE_VERTEXPROCESSING), sw = t_env(1, D3DCREATE_SOFTWARE_VERTEXPROCESSING); d = hw.dev;
    BAD(hw.dev->SetSoftwareVertexProcessing(TRUE)); CHECK_EQ(hw.dev->GetSoftwareVertexProcessing(), 0); BAD(sw.dev->SetSoftwareVertexProcessing(FALSE)); CHECK_EQ(sw.dev->GetSoftwareVertexProcessing(), 1);
    // ---- palettes: alpha must be 255 (the device does not report D3DPTEXTURECAPS_ALPHAPALETTE); unassigned palette cannot be read
    d = e.dev; uint8_t pe[1024]; for (int i = 0; i < 1024; i++) pe[i] = (i & 3) == 3 ? 255 : 7; GOOD(dv->SetPaletteEntries(1, (PALETTEENTRY*)pe)); pe[4 * 100 + 3] = 254;
    BAD(dv->SetPaletteEntries(2, (PALETTEENTRY*)pe)); BAD(dv->SetPaletteEntries(2, nullptr)); BAD(dv->GetPaletteEntries(2, (PALETTEENTRY*)pe)); BAD(dv->GetPaletteEntries(1, nullptr));
    // ---- states that name no state are accepted and ignored (README rule R-ignore): they change nothing, not even dirty bits
    { Snap sn(d); GOOD(dv->SetRenderState((D3DRENDERSTATETYPE)2, 5)); GOOD(dv->SetRenderState((D3DRENDERSTATETYPE)0xffffffff, 5)); GOOD(dv->SetSamplerState(0, (D3DSAMPLERSTATETYPE)0, 5)); GOOD(dv->SetTextureStageState(0, (D3DTEXTURESTAGESTATETYPE)16, 5)); CHECK(sn.same(d)); }
}
