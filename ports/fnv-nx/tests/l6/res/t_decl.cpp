// part a (declaration creation rules) and c (declaration objects, nx_decl_to_fvf, fixed-function view)
#include "t_common.h"
#include "res_core.h"
#include "../../../render/d3d9/ffp/ffp_vs_state.h"
typedef D3DVERTEXELEMENT9 E;
static E el(BYTE s, WORD o, BYTE t, BYTE u, BYTE ui = 0, BYTE m = 0) { E e = {s, o, t, m, u, ui}; return e; }
static E END() { E e = D3DDECL_END(); return e; }
void t_decl() {
    t_part('a'); TEnv env = t_env(); NxDevice9* d = env.dev; IDirect3DVertexDeclaration9* v = nullptr;
    struct C { E a[4]; HRESULT hr; } cs[] = {
        {{el(0, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION), el(0, 16, D3DDECLTYPE_D3DCOLOR, D3DDECLUSAGE_COLOR), END()}, D3D_OK},
        {{el(0, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION), el(0, 17, D3DDECLTYPE_D3DCOLOR, D3DDECLUSAGE_COLOR), END()}, E_FAIL},
        {{el(0, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION), el(0, 18, D3DDECLTYPE_D3DCOLOR, D3DDECLUSAGE_COLOR), END()}, E_FAIL},
        {{el(0, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION), el(0, 19, D3DDECLTYPE_D3DCOLOR, D3DDECLUSAGE_COLOR), END()}, E_FAIL},
        {{el(0, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION), el(0, 20, D3DDECLTYPE_D3DCOLOR, D3DDECLUSAGE_COLOR), END()}, D3D_OK},
        {{el(0, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION), el(0, 16, D3DDECLTYPE_UNUSED, D3DDECLUSAGE_COLOR), END()}, E_FAIL},
        {{el(0, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION), el(1, 16, D3DDECLTYPE_UNUSED, D3DDECLUSAGE_TEXCOORD, 12), END()}, E_FAIL},
        {{el(0, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION), el(0, 16, 200, D3DDECLUSAGE_NORMAL), END()}, E_FAIL},
        {{el(0, 0, D3DDECLTYPE_FLOAT16_4, D3DDECLUSAGE_POSITION), END()}, E_FAIL},                                    // environment: type unsupported (test ENV answer)
        {{el(0, 0, D3DDECLTYPE_FLOAT16_2, D3DDECLUSAGE_POSITION), END()}, D3D_OK},
        {{el(16, 1, D3DDECLTYPE_FLOAT16_4, D3DDECLUSAGE_POSITION), END()}, D3D_OK},                                  // streams >= 16: tessellator pseudo streams, no alignment / support check
        {{el(254, 3, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION), END()}, D3D_OK},
        {{el(15, 2, D3DDECLTYPE_FLOAT1, D3DDECLUSAGE_POSITION), END()}, E_FAIL},
        {{END()}, D3D_OK}};
    for (auto& c : cs) { v = (IDirect3DVertexDeclaration9*)1; HRESULT hr = d->CreateVertexDeclaration(c.a, &v); CHECK_EQ(hr, c.hr); if (hr == D3D_OK) { CHECK(v && nx_obj_of(v)); v->Release(); } else CHECK(v == nullptr); }
    for (int t = 0; t < 256; t++) { E a[2] = {el(0, 0, (BYTE)t, 0), END()}; HRESULT hr = d->CreateVertexDeclaration(a, &v); bool ok = t < 17 && t != D3DDECLTYPE_FLOAT16_4; CHECK_HR(hr, ok ? D3D_OK : E_FAIL); if (hr == D3D_OK) v->Release(); }
    for (int off = 0; off < 40; off++) for (int t = 0; t < 17; t++) { E a[2] = {el(3, (WORD)off, (BYTE)t, 0), END()}; HRESULT hr = d->CreateVertexDeclaration(a, &v); bool ok = !(off & 3) && t != D3DDECLTYPE_FLOAT16_4; CHECK_HR(hr, ok ? D3D_OK : E_FAIL); if (hr == D3D_OK) v->Release(); }
    static E big[300]; for (int n : std::initializer_list<int>{1, 64, 126}) { for (int i = 0; i < n; i++) big[i] = el(0, (WORD)(4 * i), D3DDECLTYPE_FLOAT1, 0, (BYTE)i); big[n] = END(); CHECK_HR(d->CreateVertexDeclaration(big, &v), D3D_OK); UINT cnt = 0; v->GetDeclaration(nullptr, &cnt); CHECK_EQ(cnt, n + 1); v->Release(); }
    for (int n : std::initializer_list<int>{127, 128, 200, 299}) { for (int i = 0; i < n; i++) big[i] = el(0, (WORD)(4 * i), D3DDECLTYPE_FLOAT1, 0); big[n] = END(); CHECK_HR(d->CreateVertexDeclaration(big, &v), E_FAIL); }   // Wine: END must be within the first 127
    CHECK_HR(d->CreateVertexDeclaration(nullptr, &v), D3DERR_INVALIDCALL); CHECK_HR(d->CreateVertexDeclaration(cs[0].a, nullptr), D3DERR_INVALIDCALL);
    TEnv ex = t_env(true); CHECK_HR(ex.devex->CreateVertexDeclaration(cs[0].a, &v), D3D_OK); v->Release();

    t_part('c');   // GetDeclaration, element copy semantics, stride / streams, FVF of element declarations
    E a1[3] = {el(0, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION), el(1, 4, D3DDECLTYPE_FLOAT2, D3DDECLUSAGE_TEXCOORD, 3, 2), {0xFF, 7, 17, 3, 2, 1}}; d->CreateVertexDeclaration(a1, &v);
    NxResDecl* rd = static_cast<NxResDecl*>(nx_obj_of(v)); UINT cnt = 0x1337; E out[3]; memset(out, 0xCC, sizeof out);
    CHECK_HR(v->GetDeclaration(nullptr, &cnt), D3D_OK); CHECK_EQ(cnt, 3); cnt = 0; CHECK_HR(v->GetDeclaration(out, &cnt), D3D_OK); CHECK_EQ(cnt, 3); CHECK(!memcmp(out, a1, sizeof a1));   // END verbatim
    CHECK_HR(v->GetDeclaration(out, nullptr), D3DERR_INVALIDCALL); CHECK(rd->stream_mask == 3 && rd->stride[0] == 12 && rd->stride[1] == 12 && rd->fvf == 0);
    IDirect3DDevice9* gd; CHECK_HR(v->GetDevice(&gd), D3D_OK); gd->Release(); a1[0].Offset = 99; CHECK_EQ(rd->el[0].Offset, 0);   // the object holds a copy
    CHECK_EQ(v->Release(), 0);
    struct F { E a[2]; DWORD fvf; } fv[] = {{{el(0, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION), END()}, D3DFVF_XYZ}, {{el(0, 0, D3DDECLTYPE_FLOAT4, D3DDECLUSAGE_POSITIONT), END()}, D3DFVF_XYZRHW},
        {{el(0, 0, D3DDECLTYPE_FLOAT4, D3DDECLUSAGE_POSITION), END()}, 0}, {{el(0, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION, 1), END()}, 0}, {{el(0, 4, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION), END()}, 0},
        {{el(1, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION), END()}, 0}, {{el(0, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION, 0, 1), END()}, 0}, {{el(0, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_NORMAL), END()}, 0},
        {{el(0, 0, D3DDECLTYPE_FLOAT2, D3DDECLUSAGE_TEXCOORD), END()}, 0}, {{END(), END()}, 0}, {{el(0, 0, D3DDECLTYPE_D3DCOLOR, D3DDECLUSAGE_COLOR), END()}, 0}};
    for (auto& f : fv) { CHECK_EQ(nx_decl_to_fvf(f.a), f.fvf); if (f.fvf) { NxFvfLayout L; CHECK(nx_fvf_to_decl(f.fvf, &L)); CHECK(L.n == 1 && !memcmp(L.el, f.a, sizeof(E))); } }   // round trip decl -> fvf -> decl
    E two[3] = {el(0, 0, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION), el(0, 12, D3DDECLTYPE_D3DCOLOR, D3DDECLUSAGE_COLOR), END()}; CHECK_EQ(nx_decl_to_fvf(two), 0);

    // fixed-function view: field by field, then totality over random garbage (usages / types / streams / methods outside every enum)
    E fx[] = {el(0, 0, D3DDECLTYPE_FLOAT4, D3DDECLUSAGE_POSITIONT), el(0, 16, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_BLENDWEIGHT), el(1, 0, D3DDECLTYPE_D3DCOLOR, D3DDECLUSAGE_BLENDINDICES), el(0, 28, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_NORMAL),
        el(0, 40, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_NORMAL, 1), el(0, 52, D3DDECLTYPE_FLOAT1, D3DDECLUSAGE_PSIZE), el(0, 56, D3DDECLTYPE_D3DCOLOR, D3DDECLUSAGE_COLOR, 0), el(0, 60, D3DDECLTYPE_D3DCOLOR, D3DDECLUSAGE_COLOR, 1),
        el(0, 64, D3DDECLTYPE_FLOAT2, D3DDECLUSAGE_TEXCOORD, 0), el(0, 72, D3DDECLTYPE_FLOAT4, D3DDECLUSAGE_TEXCOORD, 7), el(0, 88, D3DDECLTYPE_FLOAT4, D3DDECLUSAGE_TEXCOORD, 8), el(0, 104, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_POSITION, 1),
        el(0, 116, D3DDECLTYPE_FLOAT1, D3DDECLUSAGE_TEXCOORD, 0), el(0, 120, D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_TEXCOORD, 2, D3DDECLMETHOD_PARTIALU), END()};
    NxFfpVsDecl o; nx_ffp_decl_from_elements(fx, &o);
    CHECK(o.posT == 1 && o.nBlendW == 3 && o.blendIdx == 2 && o.hasNormal && o.hasNormal1 && o.hasPsize && o.hasColor0 && o.hasColor1 && o.hasPos1);
    CHECK(o.nTex[0] == 2 && o.nTex[7] == 4 && o.nTex[1] == 0 && o.nTex[2] == 0 && o.pad == 0 && o.pad2[0] == 0 && o.pad2[1] == 0);   // first TEXCOORD0 wins; method != DEFAULT ignored; TEXCOORD8 ignored
    E ub[] = {el(0, 0, D3DDECLTYPE_UBYTE4, D3DDECLUSAGE_BLENDINDICES), el(0, 4, D3DDECLTYPE_FLOAT1, D3DDECLUSAGE_BLENDINDICES, 1), END()}; nx_ffp_decl_from_elements(ub, &o); CHECK(o.blendIdx == 1 && o.posT == 0 && o.nBlendW == 0);
    E fl[] = {el(0, 0, D3DDECLTYPE_FLOAT1, D3DDECLUSAGE_BLENDINDICES), END()}; nx_ffp_decl_from_elements(fl, &o); CHECK_EQ(o.blendIdx, 1);
    nx_ffp_decl_from_elements(&fx[14], &o); CHECK(o.posT == 0 && o.nBlendW == 0 && o.nTex[0] == 0);   // END only
    unsigned s = 12345; static E rnd[40]; bool inv = true;
    for (int it = 0; it < 20000; it++) {
        int n = (s = s * 1664525 + 1013904223) % 38; for (int i = 0; i < n; i++) { s = s * 1664525 + 1013904223; rnd[i] = el((BYTE)(s >> 3) % 255, (WORD)(s >> 8), (BYTE)(s >> 16), (BYTE)(s >> 20), (BYTE)(s >> 12), (BYTE)(s >> 5) % 7); }
        rnd[n] = END(); nx_ffp_decl_from_elements(rnd, &o);
        inv &= o.posT <= 1 && o.nBlendW <= 4 && o.blendIdx <= 2 && o.hasNormal <= 1 && o.hasNormal1 <= 1 && o.hasPsize <= 1 && o.hasColor0 <= 1 && o.hasColor1 <= 1 && o.hasPos1 <= 1;
        for (int k = 0; k < 8; k++) inv &= o.nTex[k] <= 4;
    }
    CHECK(inv);
    for (uint32_t t = 0; t < 300; t++) CHECK(nx_decltype_comps(t) <= 4 && (t < 17) == (nx_decltype_bytes(t) != 0) && (t < 17) == (nx_decltype_comps(t) != 0));
}
