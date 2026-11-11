// The script player of tests/l6/drawgl: a case script is lines of `tag` + whitespace separated hex words (floats travel as bit patterns), one D3D9 API call or a short sequence per tag:
//  n            new case: a device with a 32 x 32 A8R8G8B8 render target 0 (the previous case's objects and device are released first)
//  v id W..     CreateVertexShader (W = the bytecode dwords)         p id W..   CreatePixelShader
//  d id n E..   CreateVertexDeclaration, n elements of 6 words (stream offset type method usage index)
//  s vs ps decl SetVertexShader / SetPixelShader / SetVertexDeclaration by id (0 = NULL)          f fvf    SetFVF
//  k st idx n W.. Set{Vertex,Pixel}ShaderConstantF (st 0 / 1), n registers      i st idx n W.. ...ConstantI          b st idx n W.. ...ConstantB
//  r state v    SetRenderState      y stage type v   SetSamplerState        t stage type v   SetTextureStageState
//  x id kind w h d fmt bpp W..  CreateTexture / CreateCubeTexture / CreateVolumeTexture (kind 0 / 1 / 2), level 0, filled with the words (cube: six faces +X..-Z one after the other)
//  e stage id   SetTexture (0 = NULL)                      m idx   a second render target (A8R8G8B8, 32 x 32) at index idx
//  c argb       Clear(TARGET)                             u stride nprim W..   DrawPrimitiveUP(TRIANGLELIST)
//  w id stride W..  a vertex buffer made from the words    a id W..  a 16-bit index buffer, one index per word
//  q stream vb offset stride   SetStreamSource            j ib   SetIndices
//  g nprim first   DrawPrimitive(TRIANGLELIST)            h base minidx nverts startidx nprim   DrawIndexedPrimitive(TRIANGLELIST)
//  z kind id    release the vertex (0) / pixel (1) shader object id (the device destroys its backend shaders)
//  l idx a b c d  SetClipPlane (plane words are float bit patterns)   U prim n stride W..  DrawPrimitiveUP(prim = D3DPRIMITIVETYPE, n primitives)
//  R id fmt     CreateTexture(32 x 32, level 0, D3DUSAGE_RENDERTARGET, fmt, DEFAULT) as texture id    T idx id   SetRenderTarget(idx, level 0 of texture id)    B idx   SetRenderTarget(idx, the case's own target 0)
//  o nrt        read back render targets 0..nrt-1 (GetRenderTargetData) into <data>/o<case>_<rt>.bin (BGRA bytes, 32 x 32)
#include "t_common.h"
#include <string.h>
#include <string>

namespace {
enum { SZ = 32 };
struct Ctx {
    TDev d; bool live; int caseno;
    IDirect3DVertexShader9* vs[16]; IDirect3DPixelShader9* ps[16]; IDirect3DVertexDeclaration9* decl[16]; IDirect3DBaseTexture9* tex[16];
    IDirect3DVertexBuffer9* vb[8]; IDirect3DIndexBuffer9* ib[8]; IDirect3DSurface9* rt[4]; IDirect3DSurface9* sys;
} g;

template <class T> void rel(T*& p) { if (p) { p->Release(); p = nullptr; } }
void finish() {
    if (!g.live) return;
    for (auto& x : g.vs) rel(x); for (auto& x : g.ps) rel(x); for (auto& x : g.decl) rel(x); for (auto& x : g.tex) rel(x); for (auto& x : g.vb) rel(x); for (auto& x : g.ib) rel(x);
    for (auto& x : g.rt) rel(x); rel(g.sys);
    CHECK(g.d.i9->Release() == 0); g.live = false;
}
float F(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

void begin() {
    finish(); int c = g.caseno + 1; memset(&g, 0, sizeof g); g.caseno = c; g.live = true;
    g.d = t_dev(SZ, SZ); t_target(g.d, SZ, SZ);
    CHECK_HR(g.d.i9->GetRenderTarget(0, &g.rt[0]), D3D_OK);
    CHECK_HR(g.d.i9->CreateOffscreenPlainSurface(SZ, SZ, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &g.sys, nullptr), D3D_OK);
}
void make_tex(const std::vector<uint32_t>& v) {
    uint32_t id = v[0], kind = v[1], w = v[2], h = v[3], dd = v[4], fmt = v[5], bpp = v[6]; const uint8_t* data = (const uint8_t*)&v[7]; size_t at = 0;
    IDirect3DDevice9* i9 = g.d.i9;
    if (kind == 0) {
        IDirect3DTexture9* t = nullptr; if (!CHECK_HR(i9->CreateTexture(w, h, 1, 0, (D3DFORMAT)fmt, D3DPOOL_MANAGED, &t, nullptr), D3D_OK)) return;
        D3DLOCKED_RECT lr; if (!CHECK_HR(t->LockRect(0, &lr, nullptr, 0), D3D_OK)) return;
        for (uint32_t y = 0; y < h; y++) { memcpy((uint8_t*)lr.pBits + (size_t)y * lr.Pitch, data + at, w * bpp); at += w * bpp; }
        t->UnlockRect(0); g.tex[id] = t;
    } else if (kind == 1) {
        IDirect3DCubeTexture9* t = nullptr; if (!CHECK_HR(i9->CreateCubeTexture(w, 1, 0, (D3DFORMAT)fmt, D3DPOOL_MANAGED, &t, nullptr), D3D_OK)) return;
        for (int f = 0; f < 6; f++) {
            D3DLOCKED_RECT lr; if (!CHECK_HR(t->LockRect((D3DCUBEMAP_FACES)f, 0, &lr, nullptr, 0), D3D_OK)) return;
            for (uint32_t y = 0; y < w; y++) { memcpy((uint8_t*)lr.pBits + (size_t)y * lr.Pitch, data + at, w * bpp); at += w * bpp; }
            t->UnlockRect((D3DCUBEMAP_FACES)f, 0);
        }
        g.tex[id] = t;
    } else {
        IDirect3DVolumeTexture9* t = nullptr; if (!CHECK_HR(i9->CreateVolumeTexture(w, h, dd, 1, 0, (D3DFORMAT)fmt, D3DPOOL_MANAGED, &t, nullptr), D3D_OK)) return;
        D3DLOCKED_BOX lb; if (!CHECK_HR(t->LockBox(0, &lb, nullptr, 0), D3D_OK)) return;
        for (uint32_t z = 0; z < dd; z++) for (uint32_t y = 0; y < h; y++) { memcpy((uint8_t*)lb.pBits + (size_t)z * lb.SlicePitch + (size_t)y * lb.RowPitch, data + at, w * bpp); at += w * bpp; }
        t->UnlockBox(0); g.tex[id] = t;
    }
}
void output(uint32_t nrt) {
    for (uint32_t r = 0; r < nrt; r++) {
        if (!g.rt[r] || !CHECK_HR(g.d.i9->GetRenderTargetData(g.rt[r], g.sys), D3D_OK)) continue;
        D3DLOCKED_RECT lr; if (!CHECK_HR(g.sys->LockRect(&lr, nullptr, D3DLOCK_READONLY), D3D_OK)) continue;
        char p[1024]; snprintf(p, sizeof p, "%s/o%d_%u.bin", g_data, g.caseno, r); FILE* f = fopen(p, "wb"); if (!CHECK(f != nullptr)) { g.sys->UnlockRect(); continue; }
        for (int y = 0; y < SZ; y++) fwrite((uint8_t*)lr.pBits + (size_t)y * lr.Pitch, 4, SZ, f);
        fclose(f); g.sys->UnlockRect();
    }
}
void line(char tag, const std::vector<uint32_t>& v) {
    IDirect3DDevice9* i9 = g.d.i9; const DWORD* code = v.size() > 1 ? (const DWORD*)&v[1] : nullptr;
    switch (tag) {
    case 'n': begin(); break;
    case 'v': CHECK_HR(i9->CreateVertexShader(code, &g.vs[v[0]]), D3D_OK); break;
    case 'p': CHECK_HR(i9->CreatePixelShader(code, &g.ps[v[0]]), D3D_OK); break;
    case 'd': { std::vector<D3DVERTEXELEMENT9> e; for (uint32_t k = 0; k < v[1]; k++) { const uint32_t* q = &v[2 + 6 * k]; D3DVERTEXELEMENT9 x = {(WORD)q[0], (WORD)q[1], (BYTE)q[2], (BYTE)q[3], (BYTE)q[4], (BYTE)q[5]}; e.push_back(x); }
        D3DVERTEXELEMENT9 end = D3DDECL_END(); e.push_back(end); CHECK_HR(i9->CreateVertexDeclaration(e.data(), &g.decl[v[0]]), D3D_OK); break; }
    case 's': CHECK_HR(i9->SetVertexShader(v[0] ? g.vs[v[0]] : nullptr), D3D_OK); CHECK_HR(i9->SetPixelShader(v[1] ? g.ps[v[1]] : nullptr), D3D_OK);
        if (v[2]) CHECK_HR(i9->SetVertexDeclaration(g.decl[v[2]]), D3D_OK); break;
    case 'f': CHECK_HR(i9->SetFVF(v[0]), D3D_OK); break;
    case 'k': if (v[0] == 0) CHECK_HR(i9->SetVertexShaderConstantF(v[1], (const float*)&v[3], v[2]), D3D_OK); else CHECK_HR(i9->SetPixelShaderConstantF(v[1], (const float*)&v[3], v[2]), D3D_OK); break;
    case 'i': if (v[0] == 0) CHECK_HR(i9->SetVertexShaderConstantI(v[1], (const int*)&v[3], v[2]), D3D_OK); else CHECK_HR(i9->SetPixelShaderConstantI(v[1], (const int*)&v[3], v[2]), D3D_OK); break;
    case 'b': if (v[0] == 0) CHECK_HR(i9->SetVertexShaderConstantB(v[1], (const BOOL*)&v[3], v[2]), D3D_OK); else CHECK_HR(i9->SetPixelShaderConstantB(v[1], (const BOOL*)&v[3], v[2]), D3D_OK); break;
    case 'r': CHECK_HR(i9->SetRenderState((D3DRENDERSTATETYPE)v[0], v[1]), D3D_OK); break;
    case 'y': CHECK_HR(i9->SetSamplerState(v[0], (D3DSAMPLERSTATETYPE)v[1], v[2]), D3D_OK); break;
    case 't': CHECK_HR(i9->SetTextureStageState(v[0], (D3DTEXTURESTAGESTATETYPE)v[1], v[2]), D3D_OK); break;
    case 'x': make_tex(v); break;
    case 'e': CHECK_HR(i9->SetTexture(v[0], v[1] ? g.tex[v[1]] : nullptr), D3D_OK); break;
    case 'm': CHECK_HR(i9->CreateRenderTarget(SZ, SZ, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_NONE, 0, FALSE, &g.rt[v[0]], nullptr), D3D_OK); CHECK_HR(i9->SetRenderTarget(v[0], g.rt[v[0]]), D3D_OK); break;
    case 'c': CHECK_HR(i9->Clear(0, nullptr, D3DCLEAR_TARGET, v[0], 1.0f, 0), D3D_OK); break;
    case 'u': CHECK_HR(i9->DrawPrimitiveUP(D3DPT_TRIANGLELIST, v[1], &v[2], v[0]), D3D_OK); break;
    case 'l': CHECK_HR(i9->SetClipPlane(v[0], (const float*)&v[1]), D3D_OK); break;
    case 'U': CHECK_HR(i9->DrawPrimitiveUP((D3DPRIMITIVETYPE)v[0], v[1], &v[3], v[2]), D3D_OK); break;
    case 'w': { uint32_t bytes = (uint32_t)(v.size() - 2) * 4; IDirect3DVertexBuffer9*& b = g.vb[v[0]];
        if (!CHECK_HR(i9->CreateVertexBuffer(bytes, 0, 0, D3DPOOL_MANAGED, &b, nullptr), D3D_OK)) break;
        void* p = nullptr; if (CHECK_HR(b->Lock(0, bytes, &p, 0), D3D_OK)) { memcpy(p, &v[2], bytes); b->Unlock(); } break; }
    case 'a': { uint32_t n = (uint32_t)v.size() - 1; IDirect3DIndexBuffer9*& b = g.ib[v[0]];
        if (!CHECK_HR(i9->CreateIndexBuffer(n * 2, 0, D3DFMT_INDEX16, D3DPOOL_MANAGED, &b, nullptr), D3D_OK)) break;
        void* p = nullptr; if (CHECK_HR(b->Lock(0, n * 2, &p, 0), D3D_OK)) { for (uint32_t k = 0; k < n; k++) ((uint16_t*)p)[k] = (uint16_t)v[1 + k]; b->Unlock(); } break; }
    case 'q': CHECK_HR(i9->SetStreamSource(v[0], g.vb[v[1]], v[2], v[3]), D3D_OK); break;
    case 'j': CHECK_HR(i9->SetIndices(g.ib[v[0]]), D3D_OK); break;
    case 'g': CHECK_HR(i9->DrawPrimitive(D3DPT_TRIANGLELIST, v[1], v[0]), D3D_OK); break;
    case 'h': CHECK_HR(i9->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, (INT)v[0], v[1], v[2], v[3], v[4]), D3D_OK); break;
    case 'z': if (v[0] == 0) rel(g.vs[v[1]]); else rel(g.ps[v[1]]); break;
    case 'R': { IDirect3DTexture9* t = nullptr; if (CHECK_HR(i9->CreateTexture(SZ, SZ, 1, D3DUSAGE_RENDERTARGET, (D3DFORMAT)v[1], D3DPOOL_DEFAULT, &t, nullptr), D3D_OK)) g.tex[v[0]] = t; break; }
    case 'T': { IDirect3DSurface9* s = nullptr; if (!g.tex[v[1]] || !CHECK_HR(static_cast<IDirect3DTexture9*>(g.tex[v[1]])->GetSurfaceLevel(0, &s), D3D_OK)) break;
        CHECK_HR(i9->SetRenderTarget(v[0], s), D3D_OK); s->Release(); break; }
    case 'B': CHECK_HR(i9->SetRenderTarget(v[0], g.rt[0]), D3D_OK); break;
    case 'o': output(v[0]); break;
    default: CHECK(false); break;
    }
}
}   // namespace

void t_play(const char* script) {
    FILE* f = fopen(script, "r"); if (!f) { printf("FAIL: cannot open %s\n", script); g_fails++; return; }
    std::string text; char buf[1 << 16]; size_t n; while ((n = fread(buf, 1, sizeof buf, f)) > 0) text.append(buf, n); fclose(f);
    size_t at = 0;
    while (at < text.size()) {
        size_t e = text.find('\n', at); if (e == std::string::npos) e = text.size();
        const char* s = text.c_str() + at; const char* end = text.c_str() + e; at = e + 1;
        while (s < end && *s == ' ') s++;
        if (s >= end || *s == '#') continue;
        char tag = *s++; std::vector<uint32_t> v; char* q;
        for (;;) { while (s < end && (*s == ' ' || *s == '\t')) s++; if (s >= end) break; v.push_back((uint32_t)strtoul(s, &q, 16)); if (q == s) break; s = q; }
        line(tag, v);
    }
    finish();
}
