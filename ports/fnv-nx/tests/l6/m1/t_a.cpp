// part a: ValidateDevice over the generated state sweep (val.txt, expectation from the python oracle of test_m1.py) + nx_m1_draw_legal equality + the rule D1 of the draw path
#include "t_common.h"
#include "m1_validate.h"
#include <vector>
#include <string>
static uint32_t FA = 0, FB = 0;   // formats of the non-filterable / bump-capable textures (header line of val.txt, chosen by the oracle from the env table)
static uint32_t sid(int k) { return k < 16 ? (uint32_t)k : 257u + (uint32_t)(k - 17); }   // sampler slot -> API sampler number
static IDirect3DBaseTexture9* mktex(IDirect3DDevice9* d, int id) {
    IDirect3DBaseTexture9* t = nullptr; HRESULT hr = D3DERR_INVALIDCALL;
    switch (id) {
    case 1: hr = d->CreateTexture(16, 16, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, (IDirect3DTexture9**)&t, nullptr); break;
    case 2: hr = d->CreateCubeTexture(16, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, (IDirect3DCubeTexture9**)&t, nullptr); break;
    case 3: hr = d->CreateVolumeTexture(8, 8, 8, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, (IDirect3DVolumeTexture9**)&t, nullptr); break;
    case 4: hr = d->CreateTexture(16, 16, 1, 0, (D3DFORMAT)FA, D3DPOOL_DEFAULT, (IDirect3DTexture9**)&t, nullptr); break;
    case 5: hr = d->CreateCubeTexture(16, 1, 0, (D3DFORMAT)FA, D3DPOOL_DEFAULT, (IDirect3DCubeTexture9**)&t, nullptr); break;
    case 6: hr = d->CreateTexture(16, 16, 1, 0, (D3DFORMAT)FB, D3DPOOL_DEFAULT, (IDirect3DTexture9**)&t, nullptr); break;
    case 7: hr = d->CreateVolumeTexture(8, 8, 8, 1, 0, (D3DFORMAT)FA, D3DPOOL_DEFAULT, (IDirect3DVolumeTexture9**)&t, nullptr); break;
    }
    if (hr != D3D_OK) printf("mktex id %d fmt %u/%u hr %08x\n", id, FA, FB, (unsigned)hr);
    CHECK_HR(hr, D3D_OK); return t;
}
static IDirect3DSurface9* mkrt(IDirect3DDevice9* d, int id) {   // 1 64x64, 2 32x32, 3 64x64 4 samples
    IDirect3DSurface9* s = nullptr; uint32_t n = id == 2 ? 32 : 64; HRESULT hr = d->CreateRenderTarget(n, n, D3DFMT_A8R8G8B8, id == 3 ? D3DMULTISAMPLE_4_SAMPLES : D3DMULTISAMPLE_NONE, 0, FALSE, &s, nullptr); CHECK_HR(hr, D3D_OK); return s;
}
static IDirect3DSurface9* mkds(IDirect3DDevice9* d, int id) {   // 2 64x64, 3 32x32, 4 64x64 4 samples
    IDirect3DSurface9* s = nullptr; uint32_t n = id == 3 ? 32 : 64; HRESULT hr = d->CreateDepthStencilSurface(n, n, D3DFMT_D24S8, id == 4 ? D3DMULTISAMPLE_4_SAMPLES : D3DMULTISAMPLE_NONE, 0, FALSE, &s, nullptr); CHECK_HR(hr, D3D_OK); return s;
}
static std::vector<std::string> split(const std::string& s, char c) { std::vector<std::string> o; size_t a = 0; for (;;) { size_t b = s.find(c, a); o.push_back(s.substr(a, b == std::string::npos ? b : b - a)); if (b == std::string::npos) break; a = b + 1; } return o; }
static void run_case(IDirect3D9* d3d[2], const char* line) {
    std::vector<std::string> tok = split(line, ' '); int ex = 0, lost = 0, decl = 1, ps = 0, rt0 = 0, rt1 = 0, ds = 0; uint32_t ze = 0, zw = 0, st = 0, exp = 0; bool zset = false;
    for (auto& t : tok) {
        size_t e = t.find('='); if (e == std::string::npos) continue; std::string k = t.substr(0, e), v = t.substr(e + 1);
        if (k == "ex") ex = atoi(v.c_str()); else if (k == "lost") lost = atoi(v.c_str()); else if (k == "decl") decl = atoi(v.c_str()); else if (k == "ps") ps = atoi(v.c_str());
        else if (k == "rt") { auto p = split(v, ','); rt0 = atoi(p[0].c_str()); rt1 = atoi(p[1].c_str()); } else if (k == "ds") ds = atoi(v.c_str());
        else if (k == "z") { auto p = split(v, ','); ze = atoi(p[0].c_str()); zw = atoi(p[1].c_str()); st = atoi(p[2].c_str()); zset = true; } else if (k == "e") exp = (uint32_t)strtoul(v.c_str(), nullptr, 16);
    }
    D3DPRESENT_PARAMETERS pp = t_pp(); pp.EnableAutoDepthStencil = TRUE; pp.AutoDepthStencilFormat = D3DFMT_D24S8;
    IDirect3DDevice9* d = t_dev(d3d[ex], &pp); if (!CHECK(d)) return;
    IDirect3DBaseTexture9* tx[8] = {}; IDirect3DSurface9* sf[8] = {}; int ns = 0;
    for (auto& t : tok) {
        size_t e = t.find('='); if (e == std::string::npos) continue; std::string k = t.substr(0, e), v = t.substr(e + 1);
        if (k == "s") for (auto& it : split(v, ';')) { if (it.empty()) continue; auto a = split(it, ':'); auto f = split(a[1], ','); int sl = atoi(a[0].c_str()); d->SetSamplerState(sid(sl), D3DSAMP_MINFILTER, atoi(f[0].c_str())); d->SetSamplerState(sid(sl), D3DSAMP_MAGFILTER, atoi(f[1].c_str())); d->SetSamplerState(sid(sl), D3DSAMP_MIPFILTER, atoi(f[2].c_str())); }
        else if (k == "t") for (auto& it : split(v, ';')) { if (it.empty()) continue; auto a = split(it, ':'); int id = atoi(a[1].c_str()); if (!id) continue; IDirect3DBaseTexture9* b = mktex(d, id); if (b) { d->SetTexture(sid(atoi(a[0].c_str())), b); tx[id] = b; } }
        else if (k == "g") for (auto& it : split(v, ';')) { if (it.empty()) continue; auto a = split(it, ':'); auto f = split(a[1], ','); uint32_t s = atoi(a[0].c_str()); static const D3DTEXTURESTAGESTATETYPE ty[9] = {D3DTSS_COLOROP, D3DTSS_COLORARG1, D3DTSS_COLORARG2, D3DTSS_COLORARG0, D3DTSS_ALPHAOP, D3DTSS_ALPHAARG1, D3DTSS_ALPHAARG2, D3DTSS_ALPHAARG0, D3DTSS_RESULTARG};
            for (int i = 0; i < 9; i++) d->SetTextureStageState(s, ty[i], (DWORD)strtoul(f[i].c_str(), nullptr, 10)); }
    }
    if (!decl) { d->SetVertexDeclaration(nullptr); } else d->SetFVF(D3DFVF_XYZ | D3DFVF_TEX1);
    if (ps) { static const DWORD psc[] = {0xFFFF0101, 0x00000001, 0x800F0000, 0xA0E40000, 0x0000FFFF}; IDirect3DPixelShader9* p = nullptr; CHECK_HR(d->CreatePixelShader(psc, &p), D3D_OK); d->SetPixelShader(p); if (p) p->Release(); }
    if (zset) { d->SetRenderState(D3DRS_ZENABLE, ze); d->SetRenderState(D3DRS_ZWRITEENABLE, zw); d->SetRenderState(D3DRS_STENCILENABLE, st); }
    if (rt0) { sf[ns] = mkrt(d, rt0); CHECK_HR(d->SetRenderTarget(0, sf[ns]), D3D_OK); ns++; }
    if (rt1) { sf[ns] = mkrt(d, rt1); CHECK_HR(d->SetRenderTarget(1, sf[ns]), D3D_OK); ns++; }
    if (ds == 1) CHECK_HR(d->SetDepthStencilSurface(nullptr), D3D_OK); else if (ds) { sf[ns] = mkds(d, ds); CHECK_HR(d->SetDepthStencilSurface(sf[ns]), D3D_OK); ns++; }
    if (lost) nx_life(O(d))->state = NX_DEV_LOST;
    DWORD passes = 0xdeadbeef; HRESULT hr = d->ValidateDevice(&passes);
    CHECK_HR(hr, exp); CHECK(hr == D3D_OK ? passes == 1 : passes == 0xdeadbeef); CHECK_HR(nx_m1_draw_legal(static_cast<NxDeviceCore*>(static_cast<NxDevice9*>(d))), hr);
    if (!lost) { HRESULT dr = d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 0); CHECK_EQ(dr == D3DERR_INVALIDCALL, !decl); }   // draw rule D1 (draw_api.cpp) = V1: no declaration <=> the draw is refused and ValidateDevice says INVALIDCALL
    if (!lost) CHECK_EQ(!decl, hr == D3DERR_INVALIDCALL);
    if (lost) nx_life(O(d))->state = NX_DEV_OK;
    for (int i = 0; i < 8; i++) if (tx[i]) tx[i]->Release();
    for (int i = 0; i < ns; i++) sf[i]->Release();
    d->Release();
}
void t_a() {
    t_part('a'); g_profile = 0; IDirect3D9* d3d[2] = {t_d3d(false), t_d3d(true)}; FILE* f = t_open("val.txt"); if (!f) return; char buf[4096]; int n = 0;
    while (fgets(buf, sizeof buf, f)) {
        if (buf[0] == 'F') { sscanf(buf, "F %u %u", &FA, &FB); continue; }
        if (buf[0] != 'C') continue; size_t l = strlen(buf); while (l && (buf[l - 1] == '\n' || buf[l - 1] == '\r')) buf[--l] = 0;
        run_case(d3d, buf + 2); n++;
    }
    fclose(f); CHECK(n > 5000); printf("a: %d cases\n", n); d3d[0]->Release(); d3d[1]->Release();
}
