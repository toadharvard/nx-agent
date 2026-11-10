// part b: defaults == documented tables (t_tables.h), Get-after-Set over the WHOLE key domain of every state, the disagreements with nx_d3d9_default_rs / _samp
#include "t_tables.h"
extern "C" { void nx_d3d9_default_rs(uint32_t* rs); void nx_d3d9_default_samp(uint32_t* s); }
static D3DMATRIX mat(float s) { D3DMATRIX m = D3DMATRIX(); for (int i = 0; i < 16; i++) (&m._11)[i] = s + i; return m; }
static bool named_rs(uint32_t k) { for (int i = 0; i < T_NRS; i++) if (T_RS[i].k == k) return true; return false; }
static uint32_t xf_id(int i) { return i == 0 ? D3DTS_VIEW : i == 1 ? D3DTS_PROJECTION : i < 10 ? D3DTS_TEXTURE0 + (i - 2) : 256u + (uint32_t)(i - 10); }  // 266 transform states

void t_defaults() {
    t_part("b");
    CHECK_EQ(T_NRS, 103);
    for (int ad = 0; ad < 2; ad++) {
        TEnv e = t_env(ad); NxDevice9* d = e.dev; DWORD v;
        for (int i = 0; i < T_NRS; i++) {
            d->GetRenderState((D3DRENDERSTATETYPE)T_RS[i].k, &v);
            CHECK_EQ(v, T_RS[i].k == D3DRS_ZENABLE ? (uint32_t)ad : T_RS[i].def);
        }
    }
    TEnv e = t_env(0); NxDevice9* d = e.dev; DWORD v;
    // disagreements with Gallium Nine's table as kept in render/native/front/d3d9_state.c (ZENABLE: Nine assumes no auto depth, matches ad=0)
    uint32_t nrs[NX_RS_COUNT]; nx_d3d9_default_rs(nrs); uint32_t nsm[14]; nx_d3d9_default_samp(nsm);
    int dis = 0;
    for (int i = 0; i < T_NRS; i++) if (T_RS[i].k != D3DRS_ZENABLE && nrs[T_RS[i].k] != T_RS[i].def) { printf("DISAGREE rs %d model/MS=%#x nx_d3d9=%#x\n", T_RS[i].k, T_RS[i].def, nrs[T_RS[i].k]); dis++; }
    for (int t = 1; t < 14; t++) if (nsm[t] != T_SAMP_DEF[t]) { printf("DISAGREE samp %d MS=%#x nx_d3d9=%#x\n", t, T_SAMP_DEF[t], nsm[t]); dis++; }
    CHECK_EQ(dis, 1);  // exactly DEBUGMONITORTOKEN (MS: D3DDMT_ENABLE = 0; nx_d3d9_default_rs: 0xDEADCAFE); anything else is news
    // ---- render states: every number 0..255; named ones round-trip, others are ignored (Set OK, Get 0)
    for (uint32_t k = 0; k < 256; k++) if (named_rs(k)) CHECK_HR(d->SetRenderState((D3DRENDERSTATETYPE)k, 0x1000 + k * 7), D3D_OK);
    for (uint32_t k = 0; k < 256; k++) {
        if (!named_rs(k)) { CHECK_HR(d->SetRenderState((D3DRENDERSTATETYPE)k, 0x55), D3D_OK); CHECK_HR(d->GetRenderState((D3DRENDERSTATETYPE)k, &(v = 9)), D3D_OK); CHECK_EQ(v, 0); }
        else { d->GetRenderState((D3DRENDERSTATETYPE)k, &v); CHECK_EQ(v, 0x1000 + k * 7); }
    }
    // ---- samplers: 21 x 13, then texture stage states 8 x 18 (and the 15 unnamed numbers are ignored)
    for (int s = 0; s < 21; s++) for (uint32_t t = 1; t < 14; t++) { CHECK_EQ(d->GetSamplerState(t_samp_id(s), (D3DSAMPLERSTATETYPE)t, &(v = 99)), D3D_OK); CHECK_EQ(v, T_SAMP_DEF[t]); }
    for (int s = 0; s < 21; s++) for (uint32_t t = 1; t < 14; t++) d->SetSamplerState(t_samp_id(s), (D3DSAMPLERSTATETYPE)t, s * 100 + t);
    for (int s = 0; s < 21; s++) for (uint32_t t = 1; t < 14; t++) { d->GetSamplerState(t_samp_id(s), (D3DSAMPLERSTATETYPE)t, &v); CHECK_EQ(v, (uint32_t)(s * 100 + t)); }
    CHECK_HR(d->SetSamplerState(0, (D3DSAMPLERSTATETYPE)0, 5), D3D_OK); CHECK_HR(d->GetSamplerState(0, (D3DSAMPLERSTATETYPE)14, &(v = 9)), D3D_OK); CHECK_EQ(v, 0);
    for (int st = 0; st < 8; st++) for (uint32_t t : T_TSS) { d->GetTextureStageState(st, (D3DTEXTURESTAGESTATETYPE)t, &v); CHECK_EQ(v, t_tss_def(st, t)); }
    for (int st = 0; st < 8; st++) for (uint32_t t : T_TSS) d->SetTextureStageState(st, (D3DTEXTURESTAGESTATETYPE)t, st * 1000 + t);
    for (int st = 0; st < 8; st++) for (uint32_t t : T_TSS) { d->GetTextureStageState(st, (D3DTEXTURESTAGESTATETYPE)t, &v); CHECK_EQ(v, (uint32_t)(st * 1000 + t)); }
    for (uint32_t t = 0; t < 40; t++) { bool named = false; for (uint32_t n : T_TSS) named |= n == t; if (!named) { CHECK_HR(d->SetTextureStageState(0, (D3DTEXTURESTAGESTATETYPE)t, 5), D3D_OK); d->GetTextureStageState(0, (D3DTEXTURESTAGESTATETYPE)t, &(v = 9)); CHECK_EQ(v, 0); } }
    // ---- transforms: 266 states, identity by default, distinct values round-trip, MultiplyTransform = matrix x current
    D3DMATRIX m, id = D3DMATRIX(); id._11 = id._22 = id._33 = id._44 = 1;
    TEnv e2 = t_env(0); NxDevice9* d2 = e2.dev;
    for (int i = 0; i < 266; i++) { d2->GetTransform((D3DTRANSFORMSTATETYPE)xf_id(i), &m); CHECK(memcmp(&m, &id, sizeof m) == 0); }
    for (int i = 0; i < 266; i++) { m = mat((float)i * 16); d2->SetTransform((D3DTRANSFORMSTATETYPE)xf_id(i), &m); }
    for (int i = 0; i < 266; i++) { D3DMATRIX w = mat((float)i * 16); d2->GetTransform((D3DTRANSFORMSTATETYPE)xf_id(i), &m); CHECK(memcmp(&m, &w, sizeof m) == 0); }
    D3DMATRIX a = mat(1), b = mat(100), r = D3DMATRIX();
    for (int i = 0; i < 4; i++) for (int j = 0; j < 4; j++) for (int k = 0; k < 4; k++) r.m[i][j] += a.m[i][k] * b.m[k][j];
    d2->SetTransform(D3DTS_VIEW, &b); CHECK_HR(d2->MultiplyTransform(D3DTS_VIEW, &a), D3D_OK); d2->GetTransform(D3DTS_VIEW, &m); CHECK(memcmp(&m, &r, sizeof m) == 0);
    // ---- lights: unbounded sparse indices; LightEnable of an undefined light creates the documented default light
    const DWORD li[] = {0, 1, 2, 7, 100, 1000, 65535, 0x12345678, 0x7fffffff, 0xfffffffe, 0xffffffff};
    for (DWORD i : li) { D3DLIGHT9 l = D3DLIGHT9(); l.Type = D3DLIGHT_POINT; l.Range = (float)(i & 0xffff); l.Position.x = (float)(i % 977); CHECK_HR(d->SetLight(i, &l), D3D_OK); }
    for (DWORD i : li) { D3DLIGHT9 l; BOOL en = 7; CHECK_HR(d->GetLight(i, &l), D3D_OK); CHECK(l.Range == (float)(i & 0xffff) && l.Position.x == (float)(i % 977)); CHECK_HR(d->GetLightEnable(i, &en), D3D_OK); CHECK_EQ(en, 0); }
    for (DWORD i : li) if (i & 1) d->LightEnable(i, TRUE);
    for (DWORD i : li) { BOOL en; d->GetLightEnable(i, &en); CHECK_EQ(en, (i & 1)); }
    D3DLIGHT9 dl; BOOL en = 5; CHECK_HR(d->LightEnable(555, 1), D3D_OK); CHECK_HR(d->GetLight(555, &dl), D3D_OK); d->GetLightEnable(555, &en);
    CHECK(dl.Type == D3DLIGHT_DIRECTIONAL && dl.Diffuse.r == 1 && dl.Diffuse.g == 1 && dl.Diffuse.b == 1 && dl.Diffuse.a == 0 && dl.Direction.z == 1 && dl.Direction.x == 0 && dl.Range == 0 && dl.Specular.r == 0); CHECK_EQ(en, 1);
    // ---- the rest: defaults, then distinct values over the whole domain
    D3DMATERIAL9 mt = D3DMATERIAL9(), mt2; mt.Power = 3; mt.Diffuse.g = 0.5f; d->GetMaterial(&mt2); CHECK_EQ(mt2.Power == 0 && mt2.Diffuse.g == 0, 1); d->SetMaterial(&mt); d->GetMaterial(&mt2); CHECK(memcmp(&mt, &mt2, sizeof mt) == 0);
    D3DVIEWPORT9 vp, vp2 = {1, 2, 3, 4, 0.25f, 0.75f}; d->GetViewport(&vp); CHECK(vp.X == 0 && vp.Y == 0 && vp.Width == 640 && vp.Height == 480 && vp.MinZ == 0 && vp.MaxZ == 1);
    d->SetViewport(&vp2); d->GetViewport(&vp); CHECK(memcmp(&vp, &vp2, sizeof vp) == 0);
    RECT sc, sc2 = {5, 6, 7, 8}; d->GetScissorRect(&sc); CHECK(sc.left == 0 && sc.top == 0 && sc.right == 640 && sc.bottom == 480); d->SetScissorRect(&sc2); d->GetScissorRect(&sc); CHECK(memcmp(&sc, &sc2, sizeof sc) == 0);
    float pl[4], z4[4] = {0, 0, 0, 0}; for (UINT i = 0; i < 6; i++) { d->GetClipPlane(i, pl); CHECK(memcmp(pl, z4, 16) == 0); }
    for (UINT i = 0; i < 6; i++) { float p[4] = {(float)i, 1, 2, 3}; d->SetClipPlane(i, p); }
    for (UINT i = 0; i < 6; i++) { d->GetClipPlane(i, pl); CHECK(pl[0] == (float)i && pl[1] == 1 && pl[3] == 3); }
    NxVertexBuffer9* vb[16]; UINT o, s, f;
    for (UINT i = 0; i < 16; i++) { IDirect3DVertexBuffer9* gb0 = (IDirect3DVertexBuffer9*)1; d->GetStreamSource(i, &gb0, &o, &s); d->GetStreamSourceFreq(i, &f); CHECK(gb0 == nullptr && f == 1 && o == 0 && s == 0); vb[i] = t_child<NxVertexBuffer9>(e); }
    for (UINT i = 0; i < 16; i++) { CHECK_HR(d->SetStreamSource(i, vb[i], i * 4, 8 + i), D3D_OK); CHECK_HR(d->SetStreamSourceFreq(i, D3DSTREAMSOURCE_INDEXEDDATA | (i + 1)), D3D_OK); }
    for (UINT i = 0; i < 16; i++) { IDirect3DVertexBuffer9* g = nullptr; CHECK_HR(d->GetStreamSource(i, &g, &o, &s), D3D_OK); CHECK(g == vb[i] && o == i * 4 && s == 8 + i); g->Release(); d->GetStreamSourceFreq(i, &f); CHECK_EQ(f, D3DSTREAMSOURCE_INDEXEDDATA | (i + 1)); }
    NxIndexBuffer9* ib = t_child<NxIndexBuffer9>(e); NxVertexShader9* vs = t_child<NxVertexShader9>(e, ~0u); NxPixelShader9* ps = t_child<NxPixelShader9>(e, ~0u); NxVertexDeclaration9* vd = t_decl(e);
    IDirect3DIndexBuffer9* gi = (IDirect3DIndexBuffer9*)1; d->GetIndices(&gi); CHECK(gi == nullptr); d->SetIndices(ib); d->GetIndices(&gi); CHECK(gi == ib); gi->Release();
    IDirect3DVertexShader9* gv = (IDirect3DVertexShader9*)1; d->GetVertexShader(&gv); CHECK(gv == nullptr); d->SetVertexShader(vs); d->GetVertexShader(&gv); CHECK(gv == vs); gv->Release();
    IDirect3DPixelShader9* gp = (IDirect3DPixelShader9*)1; d->GetPixelShader(&gp); CHECK(gp == nullptr); d->SetPixelShader(ps); d->GetPixelShader(&gp); CHECK(gp == ps); gp->Release();
    IDirect3DVertexDeclaration9* gd = (IDirect3DVertexDeclaration9*)1; DWORD fvf = 7; d->GetVertexDeclaration(&gd); CHECK(gd == nullptr); d->GetFVF(&fvf); CHECK_EQ(fvf, 0);
    d->SetVertexDeclaration(vd); d->GetVertexDeclaration(&gd); CHECK(gd == vd); gd->Release(); d->GetFVF(&fvf); CHECK_EQ(fvf, 0);  // an element declaration: FVF 0
    CHECK_HR(d->SetFVF(D3DFVF_XYZ | D3DFVF_DIFFUSE), D3D_OK); d->GetFVF(&fvf); CHECK_EQ(fvf, D3DFVF_XYZ | D3DFVF_DIFFUSE); d->GetVertexDeclaration(&gd); CHECK(gd != nullptr && gd != vd); gd->Release();
    CHECK_HR(d->SetFVF(0), D3D_OK); d->GetFVF(&fvf); CHECK_EQ(fvf, D3DFVF_XYZ | D3DFVF_DIFFUSE);  // SetFVF(0) changes nothing (Wine)
    NxTexture9* tx[21]; IDirect3DBaseTexture9* gt;
    for (int i = 0; i < 21; i++) { tx[i] = t_child<NxTexture9>(e); d->GetTexture(t_samp_id(i), &(gt = (IDirect3DBaseTexture9*)1)); CHECK(gt == nullptr); }
    for (int i = 0; i < 21; i++) d->SetTexture(t_samp_id(i), tx[i]);
    for (int i = 0; i < 21; i++) { d->GetTexture(t_samp_id(i), &gt); CHECK(gt == tx[i]); gt->Release(); }
    // constants: both stages, F/I/B, whole range in one call, read back by single registers and by the whole range; default zero
    float f4[256 * 4], g4[256 * 4]; int i4[16 * 4], j4[16 * 4]; BOOL b16[16], c16[16];
    for (int i = 0; i < 256 * 4; i++) f4[i] = (float)(i * 3 + 1); for (int i = 0; i < 64; i++) i4[i] = i * 5 - 7; for (int i = 0; i < 16; i++) b16[i] = i * 3;
    for (int st = 0; st < 2; st++) {
        UINT nf = st ? 224 : 256; memset(g4, 0x7f, sizeof g4);
        (st ? d->GetPixelShaderConstantF(0, g4, nf) : d->GetVertexShaderConstantF(0, g4, nf)); for (UINT i = 0; i < nf * 4; i++) CHECK(g4[i] == 0);
        CHECK_HR(st ? d->SetPixelShaderConstantF(0, f4, nf) : d->SetVertexShaderConstantF(0, f4, nf), D3D_OK);
        for (UINT r = 0; r < nf; r++) { (st ? d->GetPixelShaderConstantF(r, g4, 1) : d->GetVertexShaderConstantF(r, g4, 1)); CHECK(memcmp(g4, f4 + r * 4, 16) == 0); }
        CHECK_HR(st ? d->SetPixelShaderConstantI(0, i4, 16) : d->SetVertexShaderConstantI(0, i4, 16), D3D_OK); (st ? d->GetPixelShaderConstantI(0, j4, 16) : d->GetVertexShaderConstantI(0, j4, 16)); CHECK(memcmp(i4, j4, sizeof i4) == 0);
        CHECK_HR(st ? d->SetPixelShaderConstantB(0, b16, 16) : d->SetVertexShaderConstantB(0, b16, 16), D3D_OK); (st ? d->GetPixelShaderConstantB(0, c16, 16) : d->GetVertexShaderConstantB(0, c16, 16)); CHECK(memcmp(b16, c16, sizeof b16) == 0);
    }
    float np = 7; (void)np; d->SetNPatchMode(2.5f); CHECK(d->GetNPatchMode() == 2.5f); UINT pal = 9; d->SetCurrentTexturePalette(4); d->GetCurrentTexturePalette(&pal); CHECK_EQ(pal, 4);
    CHECK_EQ(d->GetNPatchMode() == 2.5f, 1);
    PALETTEENTRY pe[256]; uint8_t* pb = (uint8_t*)pe; for (int i = 0; i < 1024; i++) pb[i] = (i & 3) == 3 ? 255 : (uint8_t)i; uint8_t gb[1024];
    for (UINT p : {0u, 1u, 255u, 65535u, 0xffffffffu}) { pb[0] = (uint8_t)p; CHECK_HR(d->SetPaletteEntries(p, pe), D3D_OK); }
    CHECK_HR(d->GetPaletteEntries(255, (PALETTEENTRY*)gb), D3D_OK); CHECK(gb[0] == 0xff && gb[1] == 1);
    IDirect3DSurface9* sr = t_surface(e, D3DUSAGE_RENDERTARGET, 800, 600), *sr2; CHECK_HR(d->SetRenderTarget(0, sr), D3D_OK); d->GetRenderTarget(0, &sr2); CHECK(sr2 == sr); sr2->Release();
    CHECK_HR(d->GetRenderTarget(1, &sr2), D3DERR_NOTFOUND); CHECK(sr2 == nullptr);
    d->GetViewport(&vp); CHECK(vp.Width == 800 && vp.Height == 600 && vp.MinZ == 0 && vp.MaxZ == 1); d->GetScissorRect(&sc); CHECK(sc.right == 800 && sc.bottom == 600);  // RT0 resets viewport + scissor
    IDirect3DSurface9* ds = t_surface(e, D3DUSAGE_DEPTHSTENCIL, 800, 600); CHECK_HR(d->GetDepthStencilSurface(&sr2), D3DERR_NOTFOUND); d->SetDepthStencilSurface(ds); d->GetDepthStencilSurface(&sr2); CHECK(sr2 == ds); sr2->Release();
    CHECK_EQ(d->GetSoftwareVertexProcessing(), 0); d->SetSoftwareVertexProcessing(TRUE); CHECK_EQ(d->GetSoftwareVertexProcessing(), 1);
}
