// part d: state blocks. Oracle for membership is t_tables.h + the rules below (MS "Saving All/Pixel/Vertex ... With a StateBlock"), not state_model.py.
#include "t_tables.h"
struct Objs {  // object sets per tag 1..3
    NxTexture9* tx[4][21]; NxVertexBuffer9* vb[4][16]; NxIndexBuffer9* ib[4]; NxVertexDeclaration9* vd[4]; NxVertexShader9* vs[4]; NxPixelShader9* ps[4]; IDirect3DSurface9 *rt[4], *ds[4];
    explicit Objs(const TEnv& e) { for (int t = 1; t < 4; t++) { for (int i = 0; i < 21; i++) tx[t][i] = t_child<NxTexture9>(e); for (int i = 0; i < 16; i++) vb[t][i] = t_child<NxVertexBuffer9>(e);
        ib[t] = t_child<NxIndexBuffer9>(e); vd[t] = t_decl(e); vs[t] = t_child<NxVertexShader9>(e, ~0u); ps[t] = t_child<NxPixelShader9>(e, ~0u); rt[t] = t_surface(e, D3DUSAGE_RENDERTARGET, 100 + t, 50); ds[t] = t_surface(e, D3DUSAGE_DEPTHSTENCIL, 100 + t, 50); } }
};
static D3DMATRIX mat(int s) { D3DMATRIX m = D3DMATRIX(); for (int i = 0; i < 16; i++) (&m._11)[i] = (float)(s + i); return m; }
static const DWORD LIDX[3] = {1, 5, 1000};
static bool lit_on(int tag, DWORD idx) { return ((tag + idx) & 1) != 0; }
static void set_all(NxDevice9* d, Objs& o, int tag) {
    d->SetRenderTarget(0, o.rt[tag]); d->SetDepthStencilSurface(o.ds[tag]);  // first: RT0 resets viewport and scissor
    for (int i = 0; i < T_NRS; i++) d->SetRenderState((D3DRENDERSTATETYPE)T_RS[i].k, 0x100 * tag + T_RS[i].k);
    for (int s = 0; s < 21; s++) { for (uint32_t t = 1; t < 14; t++) d->SetSamplerState(t_samp_id(s), (D3DSAMPLERSTATETYPE)t, tag * 10000 + s * 16 + t); d->SetTexture(t_samp_id(s), o.tx[tag][s]); }
    for (int s = 0; s < 8; s++) for (uint32_t t : T_TSS) d->SetTextureStageState(s, (D3DTEXTURESTAGESTATETYPE)t, tag * 10000 + s * 100 + t);
    for (int i = 0; i < 266; i++) { D3DMATRIX m = mat(tag * 100000 + i * 20); uint32_t id = i == 0 ? 2 : i == 1 ? 3 : i < 10 ? 16 + (i - 2) : 256 + (i - 10); d->SetTransform((D3DTRANSFORMSTATETYPE)id, &m); }
    D3DMATERIAL9 mt = D3DMATERIAL9(); mt.Power = (float)tag * 10; d->SetMaterial(&mt);
    D3DVIEWPORT9 vp = {(DWORD)tag, (DWORD)tag + 1, 100u + tag, 200u + tag, 0.0f, 1.0f}; d->SetViewport(&vp); RECT sc = {tag, tag, tag + 10, tag + 20}; d->SetScissorRect(&sc);
    for (UINT i = 0; i < 6; i++) { float p[4] = {(float)tag, (float)i, 0, 1}; d->SetClipPlane(i, p); }
    for (UINT i = 0; i < 16; i++) { d->SetStreamSource(i, o.vb[tag][i], tag * 100 + i, tag * 10 + i); d->SetStreamSourceFreq(i, D3DSTREAMSOURCE_INDEXEDDATA | (tag * 100 + i + 1)); }
    d->SetIndices(o.ib[tag]); d->SetVertexDeclaration(o.vd[tag]); d->SetVertexShader(o.vs[tag]); d->SetPixelShader(o.ps[tag]);
    float f[256 * 4]; int in[64]; BOOL b[16];
    for (int i = 0; i < 1024; i++) f[i] = (float)(tag * 1000 + i); for (int i = 0; i < 64; i++) in[i] = tag * 10 + i; for (int i = 0; i < 16; i++) b[i] = tag * 3 + i;
    d->SetVertexShaderConstantF(0, f, 256); d->SetVertexShaderConstantI(0, in, 16); d->SetVertexShaderConstantB(0, b, 16);
    for (int i = 0; i < 1024; i++) f[i] += 5000; d->SetPixelShaderConstantF(0, f, 224); d->SetPixelShaderConstantI(0, in, 16); d->SetPixelShaderConstantB(0, b, 16);
    d->SetNPatchMode((float)tag + 0.5f); d->SetCurrentTexturePalette(tag);
    for (DWORD idx : LIDX) { D3DLIGHT9 l = D3DLIGHT9(); l.Type = D3DLIGHT_POINT; l.Range = (float)(tag * 10 + idx % 10); d->SetLight(idx, &l); d->LightEnable(idx, lit_on(tag, idx)); }
    d->SetSoftwareVertexProcessing(tag & 1);
    uint8_t pe[1024]; for (int i = 0; i < 1024; i++) pe[i] = (i & 3) == 3 ? 255 : (uint8_t)(tag * 16 + (i & 7)); d->SetPaletteEntries(7, (PALETTEENTRY*)pe);
}
// which block types hold which state (MS pages; vertex: freq, vdecl, vs, constants, npatch, lights, DMAPOFFSET, two TSS types; pixel: ps, constants, 12 sampler types, 17 TSS types)
static bool mem(char fam, int a, int b, int t) {
    switch (fam) {
    case 'R': return t_rs_member(T_RS[a], t);
    case 'S': return t == 0 || (t == 1 && b != 13) || (t == 2 && b == 13);
    case 'T': return t_tss_member(b, t);
    case 'Q': case 'D': case 'W': case 'f': case 'i': case 'b': case 'N': case 'L': return t == 0 || t == 2;
    case 'Z': case 'g': case 'j': case 'c': return t == 0 || t == 1;
    default: return t == 0;  // textures, transforms, material, viewport, scissor, clip, streams, ib, current palette: ALL only
    }
}
struct Pred { int type; int tm, to; bool (*rec)(char, int, int); int tag(char f, int a, int b) const { bool m = type >= 0 ? mem(f, a, b, type) : rec(f, a, b); return m ? tm : to; } };
static int mismatches(NxDevice9* d, Objs& o, const Pred& P, int nonmember_tag) {
    int bad = 0; DWORD v; g_ctx = "rs"; auto chk = [&](bool ok) { bad += !ok; CHECK(ok); };
    for (int i = 0; i < T_NRS; i++) { d->GetRenderState((D3DRENDERSTATETYPE)T_RS[i].k, &v); chk(v == (DWORD)(0x100 * P.tag('R', i, 0) + T_RS[i].k)); }
    g_ctx = "samp/tex"; for (int s = 0; s < 21; s++) { for (uint32_t t = 1; t < 14; t++) { d->GetSamplerState(t_samp_id(s), (D3DSAMPLERSTATETYPE)t, &v); chk(v == (DWORD)(P.tag('S', s, t) * 10000 + s * 16 + t)); }
        IDirect3DBaseTexture9* g; d->GetTexture(t_samp_id(s), &g); chk(g == o.tx[P.tag('X', s, 0)][s]); g->Release(); }
    g_ctx = "tss"; for (int s = 0; s < 8; s++) for (uint32_t t : T_TSS) { d->GetTextureStageState(s, (D3DTEXTURESTAGESTATETYPE)t, &v); chk(v == (DWORD)(P.tag('T', s, t) * 10000 + s * 100 + t)); }
    g_ctx = "xf"; for (int i = 0; i < 266; i++) { D3DMATRIX m, w = mat(P.tag('M', i, 0) * 100000 + i * 20); uint32_t id = i == 0 ? 2 : i == 1 ? 3 : i < 10 ? 16 + (i - 2) : 256 + (i - 10); d->GetTransform((D3DTRANSFORMSTATETYPE)id, &m); chk(!memcmp(&m, &w, sizeof m)); }
    g_ctx = "material"; D3DMATERIAL9 mt; d->GetMaterial(&mt); chk(mt.Power == (float)P.tag('A', 0, 0) * 10);
    g_ctx = "viewport"; int tv = P.tag('V', 0, 0); D3DVIEWPORT9 vp; d->GetViewport(&vp); chk(vp.X == (DWORD)tv && vp.Width == 100u + tv && vp.Height == 200u + tv);
    g_ctx = "scissor"; int ts = P.tag('C', 0, 0); RECT sc; d->GetScissorRect(&sc); chk(sc.left == ts && sc.right == ts + 10 && sc.bottom == ts + 20);
    g_ctx = "clip"; for (UINT i = 0; i < 6; i++) { float p[4]; d->GetClipPlane(i, p); chk(p[0] == (float)P.tag('P', i, 0) && p[1] == (float)i); }
    g_ctx = "stream"; for (UINT i = 0; i < 16; i++) { IDirect3DVertexBuffer9* g; UINT off, st, fq; int tb = P.tag('B', i, 0); d->GetStreamSource(i, &g, &off, &st); chk(g == o.vb[tb][i] && off == (UINT)(tb * 100 + i) && st == (UINT)(tb * 10 + i)); g->Release();
        d->GetStreamSourceFreq(i, &fq); chk(fq == (D3DSTREAMSOURCE_INDEXEDDATA | (P.tag('Q', i, 0) * 100 + i + 1))); }
    g_ctx = "ib/decl/vs/ps"; IDirect3DIndexBuffer9* gi; d->GetIndices(&gi); chk(gi == o.ib[P.tag('I', 0, 0)]); gi->Release();
    IDirect3DVertexDeclaration9* gd; d->GetVertexDeclaration(&gd); chk(gd == o.vd[P.tag('D', 0, 0)]); gd->Release();
    IDirect3DVertexShader9* gv; d->GetVertexShader(&gv); chk(gv == o.vs[P.tag('W', 0, 0)]); gv->Release();
    IDirect3DPixelShader9* gp; d->GetPixelShader(&gp); chk(gp == o.ps[P.tag('Z', 0, 0)]); gp->Release();
    g_ctx = "consts"; float f[1024]; int in[64]; BOOL b[16];
    for (int pass = 0; pass < 2; pass++) {
        UINT nf = pass ? 224 : 256;
        for (UINT r = 0; r < nf; r++) { pass ? d->GetPixelShaderConstantF(r, f, 1) : d->GetVertexShaderConstantF(r, f, 1); int tg = P.tag(pass ? 'g' : 'f', r, 0);
            for (int c = 0; c < 4; c++) chk(f[c] == (float)(tg * 1000 + r * 4 + c + (pass ? 5000 : 0))); }
        for (UINT r = 0; r < 16; r++) { pass ? d->GetPixelShaderConstantI(r, in, 1) : d->GetVertexShaderConstantI(r, in, 1); pass ? d->GetPixelShaderConstantB(r, b, 1) : d->GetVertexShaderConstantB(r, b, 1);
            int ti = P.tag(pass ? 'j' : 'i', r, 0), tb = P.tag(pass ? 'c' : 'b', r, 0); chk(in[0] == ti * 10 + (int)r * 4 && in[3] == ti * 10 + (int)r * 4 + 3 && b[0] == tb * 3 + (BOOL)r); }
    }
    g_ctx = "npatch/pal/light"; chk(d->GetNPatchMode() == (float)P.tag('N', 0, 0) + 0.5f); UINT pal; d->GetCurrentTexturePalette(&pal); chk(pal == (UINT)P.tag('U', 0, 0));
    for (DWORD idx : LIDX) { D3DLIGHT9 l; BOOL en; d->GetLight(idx, &l); d->GetLightEnable(idx, &en); chk(l.Range == (float)(P.tag('L', idx, 0) * 10 + idx % 10) && (en != 0) == lit_on(P.tag('L', idx, 1), idx)); }
    // never in a block: render target, depth stencil, software vertex processing, palette entries stay at the last value set
    g_ctx = "nonmember"; IDirect3DSurface9* s; d->GetRenderTarget(0, &s); chk(s == o.rt[nonmember_tag]); s->Release(); d->GetDepthStencilSurface(&s); chk(s == o.ds[nonmember_tag]); s->Release();
    chk(d->GetSoftwareVertexProcessing() == (nonmember_tag & 1)); uint8_t pe[1024]; d->GetPaletteEntries(7, (PALETTEENTRY*)pe); chk(pe[0] == nonmember_tag * 16);
    return bad;
}
static bool rec_set(char f, int a, int b) {  // the states touched between Begin and End below
    switch (f) { case 'R': return T_RS[a].k == D3DRS_ZFUNC || T_RS[a].k == D3DRS_CULLMODE; case 'S': return (a == 3 || a == 17) && b == D3DSAMP_ADDRESSU; case 'T': return a == 1 && b == D3DTSS_COLOROP;
    case 'X': return a == 2; case 'M': return a == 0; case 'L': return (a == 5 && b == 0) || (a == 1 && b == 1); case 'f': return a == 10 || a == 11; case 'c': return a == 3; case 'B': return a == 3; case 'Q': return a == 4;
    case 'I': case 'A': case 'V': case 'N': return true; case 'P': return a == 1; default: return false; }
}
static void record_subset(NxDevice9* d, Objs& o, int tag) {
    d->SetRenderState(D3DRS_ZFUNC, 0x100 * tag + D3DRS_ZFUNC); d->SetRenderState(D3DRS_CULLMODE, 0x100 * tag + D3DRS_CULLMODE);
    d->SetSamplerState(3, D3DSAMP_ADDRESSU, tag * 10000 + 3 * 16 + D3DSAMP_ADDRESSU); d->SetSamplerState(257, D3DSAMP_ADDRESSU, tag * 10000 + 17 * 16 + D3DSAMP_ADDRESSU);
    d->SetTextureStageState(1, D3DTSS_COLOROP, tag * 10000 + 100 + D3DTSS_COLOROP); d->SetTexture(2, o.tx[tag][2]); D3DMATRIX m = mat(tag * 100000 + 0 * 20); d->SetTransform(D3DTS_VIEW, &m);
    D3DLIGHT9 l = D3DLIGHT9(); l.Type = D3DLIGHT_POINT; l.Range = (float)(tag * 10 + 5); d->SetLight(5, &l); d->LightEnable(1, lit_on(tag, 1));
    float f[8]; for (int i = 0; i < 8; i++) f[i] = (float)(tag * 1000 + 40 + i); d->SetVertexShaderConstantF(10, f, 2); BOOL b = tag * 3 + 3; d->SetPixelShaderConstantB(3, &b, 1);
    d->SetStreamSource(3, o.vb[tag][3], tag * 100 + 3, tag * 10 + 3); d->SetStreamSourceFreq(4, D3DSTREAMSOURCE_INDEXEDDATA | (tag * 100 + 5)); d->SetIndices(o.ib[tag]);
    D3DMATERIAL9 mt = D3DMATERIAL9(); mt.Power = (float)tag * 10; d->SetMaterial(&mt); D3DVIEWPORT9 vp = {(DWORD)tag, (DWORD)tag + 1, 100u + tag, 200u + tag, 0.0f, 1.0f}; d->SetViewport(&vp);
    float p[4] = {(float)tag, 1, 0, 1}; d->SetClipPlane(1, p); d->SetNPatchMode((float)tag + 0.5f);
}
void t_blocks() {
    t_part("d");
    const NxCreateParams cp = {1, 640, 480, D3DCREATE_MIXED_VERTEXPROCESSING}; (void)cp;
    for (int t = 0; t < 3; t++) {  // ALL, PIXEL, VERTEX: change everything, Capture, change again, Apply -> exactly the members restored
        TEnv e = t_env(1); NxDevice9* d = e.dev; Objs o(e); IDirect3DStateBlock9* sb;
        set_all(d, o, 1); CHECK_HR(d->CreateStateBlock((D3DSTATEBLOCKTYPE)(t + 1), &sb), D3D_OK); set_all(d, o, 2);
        D3DLIGHT9 extra = D3DLIGHT9(); extra.Type = D3DLIGHT_SPOT; d->SetLight(77, &extra);
        Pred before = {0, 2, 2, nullptr}; CHECK_EQ(mismatches(d, o, before, 2), 0);  // the block did not touch the device
        CHECK_HR(sb->Apply(), D3D_OK); Pred P = {t, 1, 2, nullptr};
        CHECK_EQ(mismatches(d, o, P, 2), 0); D3DLIGHT9 gl; CHECK_HR(d->GetLight(77, &gl), D3D_OK);  // lights created after the capture survive Apply
        // Capture refreshes the members: change to 3, Capture, change to 2, Apply -> members are 3
        set_all(d, o, 3); CHECK_HR(sb->Capture(), D3D_OK); set_all(d, o, 2); sb->Apply(); Pred P3 = {t, 3, 2, nullptr}; CHECK_EQ(mismatches(d, o, P3, 2), 0);
        sb->Release();
    }
    {   // Begin/EndStateBlock records exactly what is set in between, and does not apply it
        TEnv e = t_env(1); NxDevice9* d = e.dev; Objs o(e); IDirect3DStateBlock9* sb; set_all(d, o, 1);
        CHECK_HR(d->BeginStateBlock(), D3D_OK); record_subset(d, o, 3); d->SetRenderTarget(1, o.rt[3]);  // render target: not state-block state, applied at once (index 1: RT0 would also reset viewport and scissor)
        Pred none = {0, 1, 1, nullptr}; CHECK_EQ(mismatches(d, o, none, 1), 0); IDirect3DSurface9* r1; d->GetRenderTarget(1, &r1); CHECK(r1 == o.rt[3]); r1->Release();
        CHECK_HR(d->EndStateBlock(&sb), D3D_OK); CHECK_EQ(mismatches(d, o, none, 1), 0);
        set_all(d, o, 2); CHECK_HR(sb->Apply(), D3D_OK); Pred R = {-1, 3, 2, rec_set}; CHECK_EQ(mismatches(d, o, R, 2), 0);
        set_all(d, o, 1); sb->Capture(); set_all(d, o, 2); sb->Apply(); Pred R1 = {-1, 1, 2, rec_set}; CHECK_EQ(mismatches(d, o, R1, 2), 0);  // Capture of a recorded block: members only
        sb->Release();
    }
    {   // bind references: a captured / bound object outlives its public Release and dies with its last holder
        TEnv e = t_env(1); NxDevice9* d = e.dev; IDirect3DStateBlock9* sb; TTracker tk[4];
        NxTexture9* a = t_child<NxTexture9>(e); tk[0].watch(a); d->SetTexture(0, a); CHECK_HR(d->CreateStateBlock(D3DSBT_ALL, &sb), D3D_OK);
        d->SetTexture(0, nullptr); CHECK_EQ(a->Release(), 0); CHECK(tk[0].alive()); sb->Release(); CHECK(!tk[0].alive());
        NxVertexBuffer9* v = t_child<NxVertexBuffer9>(e); tk[1].watch(v); d->BeginStateBlock(); d->SetStreamSource(2, v, 0, 4); d->EndStateBlock(&sb); CHECK_EQ(v->Release(), 0); CHECK(tk[1].alive()); sb->Release(); CHECK(!tk[1].alive());
        NxIndexBuffer9* ib = t_child<NxIndexBuffer9>(e); tk[2].watch(ib); d->SetIndices(ib); CHECK_EQ(ib->Release(), 0); CHECK(tk[2].alive()); d->SetIndices(nullptr); CHECK(!tk[2].alive());
        NxTexture9* b = t_child<NxTexture9>(e); tk[3].watch(b); d->SetTexture(4, b); b->Release(); CHECK(tk[3].alive()); d->Release(); CHECK(tk[3].alive() == false);  // device destruction drops its bind references
    }
}
