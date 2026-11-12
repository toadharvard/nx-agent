// FVF <-> vertex declaration rules, the device's implicit FVF declarations, and the fixed-function view of a declaration.
// Sources: Wine 9.0 d3d9 vertexdeclaration.c vdecl_convert_fvf (the layout rule, checked against the Windows-validated table fvf_to_decl_tests of d3d9/tests/device.c test_fvf_decl_conversion)
// and device.c device_get_fvf_declaration / d3d9_device_SetFVF / GetFVF; MS D3DFVF page for the bit groups. Wine's d3d9 never converts a declaration back to an FVF (its GetFVF reads the
// `fvf` field set only for implicit declarations); the Windows results recorded in decl_to_fvf_tests (the rows Wine itself does not meet) are: POSITION FLOAT3 alone -> D3DFVF_XYZ, POSITIONT FLOAT4 alone ->
// D3DFVF_XYZRHW, every other element declaration -> 0. nx_decl_to_fvf is exactly that rule (it is NOT "the exact inverse of nx_fvf_to_decl": TEXCOORD / NORMAL ... declarations read 0 on Windows).
// FVF grammar (total decision): position field (fvf & 0x400E) in {0, XYZ 2, XYZRHW 4, XYZB1..5 6..0xE, XYZW 0x4002}; bit 0 (RESERVED0) and bits 13-14 (RESERVED2) clear; texture count (bits 8-11) <= 8.
// Everything else is "invalid": no layout is documented (Wine would convert it to garbage). Texture-size bits (16-31) of textures >= the count are ignored (Wine). The 4 two-bit size codes are all defined.
#include "res_core.h"
#include "../gen/d9impl.gen.h"
#include "../ffp/ffp_vs_state.h"
#include <string.h>

static const uint8_t kBytes[17] = {4, 8, 12, 16, 4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 4, 4, 8};   // D3DDECLTYPE_FLOAT1 .. FLOAT16_4 (Wine d3d_dtype_lookup: count * component size)
static const uint8_t kComps[17] = {1, 2, 3, 4, 4, 4, 2, 4, 4, 2, 4, 2, 4, 3, 3, 2, 4};
uint32_t nx_decltype_bytes(uint32_t t) { return t < 17 ? kBytes[t] : 0; }
uint32_t nx_decltype_comps(uint32_t t) { return t < 17 ? kComps[t] : 0; }

bool nx_fvf_to_decl(DWORD fvf, NxFvfLayout* out) {
    DWORD pos = fvf & D3DFVF_POSITION_MASK, ntex = (fvf & D3DFVF_TEXCOUNT_MASK) >> D3DFVF_TEXCOUNT_SHIFT;
    bool posok = pos == 0 || pos == D3DFVF_XYZW || (pos >= D3DFVF_XYZ && pos <= D3DFVF_XYZB5);
    if (!posok || (fvf & D3DFVF_RESERVED0) || (fvf & D3DFVF_RESERVED2) || ntex > 8) return false;
    NxFvfLayout L; memset(&L, 0, sizeof L); uint32_t n = 0;
    struct { D3DVERTEXELEMENT9* e; uint32_t* n; } w = {L.el, &n};
    auto add = [&](uint32_t type, uint32_t usage, uint32_t uidx) { D3DVERTEXELEMENT9& e = w.e[(*w.n)++]; e.Stream = 0; e.Offset = (WORD)L.stride; e.Type = (BYTE)type; e.Method = 0; e.Usage = (BYTE)usage; e.UsageIndex = (BYTE)uidx; L.stride += nx_decltype_bytes(type); };
    bool blend = (pos & 0xE) > D3DFVF_XYZRHW && pos != D3DFVF_XYZW;                       // XYZB1..5 (0x4002 & 0xE == XYZ)
    bool idx = blend && (pos == D3DFVF_XYZB5 || (fvf & (D3DFVF_LASTBETA_D3DCOLOR | D3DFVF_LASTBETA_UBYTE4)));
    uint32_t nbeta = blend ? 1 + ((pos & 0xE) - D3DFVF_XYZB1) / 2 : 0, nw = nbeta - (idx ? 1 : 0);
    bool b2c = pos == D3DFVF_XYZB2 && (fvf & D3DFVF_LASTBETA_D3DCOLOR);                  // Windows quirk recorded in Wine's table: XYZB2 | LASTBETA_D3DCOLOR = D3DCOLOR weight + UBYTE4 index
    if (pos) add(!blend && pos == D3DFVF_XYZRHW ? D3DDECLTYPE_FLOAT4 : !blend && pos == D3DFVF_XYZW ? D3DDECLTYPE_FLOAT4 : D3DDECLTYPE_FLOAT3, !blend && pos == D3DFVF_XYZRHW ? D3DDECLUSAGE_POSITIONT : D3DDECLUSAGE_POSITION, 0);
    if (blend && nw > 0) add(b2c ? D3DDECLTYPE_D3DCOLOR : D3DDECLTYPE_FLOAT1 + (nw - 1), D3DDECLUSAGE_BLENDWEIGHT, 0);
    if (idx) add((fvf & D3DFVF_LASTBETA_UBYTE4) || b2c ? D3DDECLTYPE_UBYTE4 : (fvf & D3DFVF_LASTBETA_D3DCOLOR) ? D3DDECLTYPE_D3DCOLOR : D3DDECLTYPE_FLOAT1, D3DDECLUSAGE_BLENDINDICES, 0);
    if (fvf & D3DFVF_NORMAL) add(D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_NORMAL, 0);
    if (fvf & D3DFVF_PSIZE) add(D3DDECLTYPE_FLOAT1, D3DDECLUSAGE_PSIZE, 0);
    if (fvf & D3DFVF_DIFFUSE) add(D3DDECLTYPE_D3DCOLOR, D3DDECLUSAGE_COLOR, 0);
    if (fvf & D3DFVF_SPECULAR) add(D3DDECLTYPE_D3DCOLOR, D3DDECLUSAGE_COLOR, 1);
    for (uint32_t i = 0; i < ntex; i++) { static const uint8_t ty[4] = {D3DDECLTYPE_FLOAT2, D3DDECLTYPE_FLOAT3, D3DDECLTYPE_FLOAT4, D3DDECLTYPE_FLOAT1}; add(ty[(fvf >> (16 + 2 * i)) & 3], D3DDECLUSAGE_TEXCOORD, i); }
    L.n = n; D3DVERTEXELEMENT9 end = D3DDECL_END(); L.el[n] = end; *out = L; return true;
}
DWORD nx_decl_to_fvf(const D3DVERTEXELEMENT9* e) {
    if (e[0].Stream == 0xFF || e[1].Stream != 0xFF) return 0;
    if (e[0].Stream != 0 || e[0].Offset != 0 || e[0].Method != D3DDECLMETHOD_DEFAULT || e[0].UsageIndex != 0) return 0;
    if (e[0].Type == D3DDECLTYPE_FLOAT3 && e[0].Usage == D3DDECLUSAGE_POSITION) return D3DFVF_XYZ;
    if (e[0].Type == D3DDECLTYPE_FLOAT4 && e[0].Usage == D3DDECLUSAGE_POSITIONT) return D3DFVF_XYZRHW;
    return 0;
}
// What the fixed-function vertex program needs (ffp_vs_state.h NxFfpVsDecl). Rules (the README of ffp_vs documents the field meanings; the choices below are ours):
//  elements are read up to the first Stream == 0xFF; elements with Method != DEFAULT (tessellator inputs) are not stream inputs and are ignored; the FIRST element of a (usage, index) wins;
//  posT = a POSITIONT index 0 element exists; nBlendW = components of BLENDWEIGHT 0; blendIdx = 2 for a D3DCOLOR BLENDINDICES 0, 1 for any other type; nTex[s] = components of TEXCOORD s (s < 8); usage index >= 8 of TEXCOORD ignored;
//  components per type: nx_decltype_comps, 0 for types >= UNUSED (an element of such a type contributes its presence flag only where the field is a flag).
void nx_ffp_decl_from_elements(const D3DVERTEXELEMENT9* e, NxFfpVsDecl* o) {
    memset((void*)o, 0, sizeof *o); bool seen[14][16]; memset(seen, 0, sizeof seen);
    for (; e->Stream != 0xFF; e++) {
        if (e->Method != D3DDECLMETHOD_DEFAULT || e->Usage > 13 || e->UsageIndex > 15 || seen[e->Usage][e->UsageIndex]) continue;
        seen[e->Usage][e->UsageIndex] = true; uint32_t k = nx_decltype_comps(e->Type), u = e->UsageIndex;
        switch (e->Usage) {
        case D3DDECLUSAGE_POSITIONT: if (u == 0) o->posT = 1; break;
        case D3DDECLUSAGE_POSITION: if (u == 1) o->hasPos1 = 1; break;
        case D3DDECLUSAGE_BLENDWEIGHT: if (u == 0) o->nBlendW = k; break;
        case D3DDECLUSAGE_BLENDINDICES: if (u == 0) o->blendIdx = e->Type == D3DDECLTYPE_D3DCOLOR ? 2 : 1; break;
        case D3DDECLUSAGE_NORMAL: if (u == 0) o->hasNormal = 1; else if (u == 1) o->hasNormal1 = 1; break;
        case D3DDECLUSAGE_PSIZE: if (u == 0) o->hasPsize = 1; break;
        case D3DDECLUSAGE_COLOR: if (u == 0) o->hasColor0 = 1; else if (u == 1) o->hasColor1 = 1; break;
        case D3DDECLUSAGE_TEXCOORD: if (u < 8) o->nTex[u] = k; break;
        default: break;   // TANGENT, BINORMAL, TESSFACTOR, FOG, DEPTH, SAMPLE: not read by the fixed-function program
        }
    }
}
// ---- device-cached implicit declarations: one per distinct FVF for the device's lifetime. The cache is an IUnknown private-data entry of the device (private GUID): it dies with the device
// (NxObj::~NxObj releases it), which deletes the declarations. They are `owned` (never destroyed by their counts), public 0, device = their GetDevice.
static const GUID kCache = {0x4e784f62, 0x6a5b, 0x4cf1, {0x9d, 0x01, 0x6e, 0x3f, 0xa0, 0x5b, 0x00, 0xf7}};
struct FvfEnt { DWORD idx; NxVertexDeclaration9* d; };
struct FvfCache : IUnknown {
    long refs = 1; NxSparse<FvfEnt> m;
    ~FvfCache() { for (uint32_t i = 0; i < m.n; i++) delete m.a[i].d; }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void** pp) override { *pp = nullptr; return E_NOINTERFACE; }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() override { long r = --refs; if (!r) delete this; return r; }
    void* operator new(size_t n) noexcept NX_MUST_USE NX_USE_MAKE { return nxd3d_alloc(n); } void operator delete(void* p) { nxd3d_free(p); }
};
// null = no declaration: `fvf` is outside the grammar, or the layer is out of memory (d3d9_device_SetFVF reports both as D3DERR_DRIVERINTERNALERROR). Every step that can fail comes before the next one,
// and what a failed call made is released: a cache that could not be attached dies with this call, a declaration that could not be entered into the cache dies with it. A success is cached for the device's life.
IDirect3DVertexDeclaration9* nx_device_fvf_decl(NxDeviceCore* dev, DWORD fvf) {
    NxFvfLayout L; if (!fvf || !nx_fvf_to_decl(fvf, &L)) return nullptr;
    FvfCache* c = nullptr; DWORD sz = sizeof c;
    if (dev->getPrivateData(kCache, &c, &sz) == D3D_OK) c->Release();   // the device's entry keeps it alive
    else {
        c = nx_make_plain<FvfCache>(); if (!c) return nullptr;
        HRESULT hr = dev->setPrivateData(kCache, &c, sizeof c, D3DSPD_IUNKNOWN | NX_SPD_NATIVE);   // AddRefs it (2 on success)
        c->Release();                                                                                // our creation reference: on success the entry holds the cache, on failure this deletes it
        if (FAILED(hr)) return nullptr;
    }
    if (FvfEnt* f = c->m.find(fvf)) return f->d;
    NxVertexDeclaration9* d = nx_make<NxVertexDeclaration9>(); if (!d) return nullptr;
    d->asImplicit(dev, nullptr); d->fvf = fvf;
    d->n = L.n + 1; d->el = (D3DVERTEXELEMENT9*)nxd3d_alloc(d->n * sizeof(D3DVERTEXELEMENT9)); if (!d->el) { nx_destroy(d); return nullptr; }
    memcpy(d->el, L.el, d->n * sizeof(D3DVERTEXELEMENT9));
    d->stream_mask = 1; d->stride[0] = L.stride;
    FvfEnt* e = c->m.add(fvf); if (!e) { nx_destroy(d); return nullptr; }
    e->d = d; return d;
}
