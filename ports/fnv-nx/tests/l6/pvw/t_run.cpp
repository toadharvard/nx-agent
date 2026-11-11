// t_run.cpp: runs the case lines of a data file written by test_pvw.py (grammar in the python driver): builds a device (Ex or not), sets every state the line names, binds source streams / declaration / FVF / a vertex shader,
// creates the destination buffer, captures the fixme ids written to stderr, calls ProcessVertices and compares: HRESULT, the set of fixme ids (each exactly once), the destination bytes (the oracle's, or unchanged), the dirty range,
// buffer lock counts, reference counts of every object involved, and the unchanged source buffers.
#include "t_common.h"
#include "res_core.h"
#include <unistd.h>
#include <fcntl.h>
#include <vector>
#include <string>
#include <functional>
#include <algorithm>
namespace {
struct Tk { char* p; char* sv = nullptr; char* next() { char* t = strtok_r(p, " \n", &sv); p = nullptr; return t; } uint32_t u() { char* t = next(); return t ? (uint32_t)strtoul(t, nullptr, 16) : 0; } float f() { uint32_t v = u(); float x; memcpy(&x, &v, 4); return x; } };
std::vector<uint8_t> hex(const char* s) { std::vector<uint8_t> o; if (!s || !strcmp(s, "-")) return o; for (size_t i = 0; s[i] && s[i + 1]; i += 2) { char b[3] = {s[i], s[i + 1], 0}; o.push_back((uint8_t)strtoul(b, nullptr, 16)); } return o; }
ULONG rc(IUnknown* u) { if (!u) return 0; u->AddRef(); return u->Release(); }
std::vector<uint32_t> capture_call(std::function<HRESULT()> f, HRESULT* hr) {
    char path[] = "/tmp/pvw_errXXXXXX"; int fd = mkstemp(path); fflush(stderr); int save = dup(2); dup2(fd, 2); *hr = f(); fflush(stderr); dup2(save, 2); close(save);
    std::vector<uint32_t> ids; FILE* fp = fopen(path, "r"); char line[256]; unsigned id; while (fp && fgets(line, sizeof line, fp)) if (sscanf(line, "fnvnx-wine-fixme: %u", &id) == 1) ids.push_back(id);
    if (fp) fclose(fp); close(fd); unlink(path); return ids;
}
}
namespace {
struct Snap { std::vector<uint32_t> w; std::vector<void*> p; bool operator==(const Snap& o) const { return w == o.w && p == o.p; } };
Snap snap(IDirect3DDevice9* d, int) {
    Snap s; static const uint32_t rs[] = {29, 36, 37, 38, 48, 136, 137, 139, 140, 141, 142, 143, 145, 146, 147, 148}; for (uint32_t k : rs) { DWORD v = 0; d->GetRenderState((D3DRENDERSTATETYPE)k, &v); s.w.push_back(v); }
    static const D3DTRANSFORMSTATETYPE ts[3] = {D3DTS_VIEW, D3DTS_PROJECTION, D3DTS_WORLD}; for (auto k : ts) { D3DMATRIX m; d->GetTransform(k, &m); for (int i = 0; i < 16; i++) s.w.push_back(((uint32_t*)&m)[i]); }
    D3DVIEWPORT9 vp; d->GetViewport(&vp); s.w.insert(s.w.end(), (uint32_t*)&vp, (uint32_t*)&vp + 6); D3DMATERIAL9 m; d->GetMaterial(&m); s.w.insert(s.w.end(), (uint32_t*)&m, (uint32_t*)&m + 17);
    for (uint32_t i = 0; i < 80; i++) { D3DLIGHT9 L; BOOL en = 0; if (d->GetLight(i, &L) == D3D_OK) { d->GetLightEnable(i, &en); s.w.push_back(i); s.w.push_back(en); s.w.insert(s.w.end(), (uint32_t*)&L, (uint32_t*)&L + sizeof L / 4); } }
    for (uint32_t i = 0; i < 4; i++) { IDirect3DVertexBuffer9* vb = nullptr; UINT off = 0, st = 0; d->GetStreamSource(i, &vb, &off, &st); s.p.push_back(vb); s.w.push_back(off); s.w.push_back(st); if (vb) vb->Release(); }
    IDirect3DVertexDeclaration9* vd = nullptr; d->GetVertexDeclaration(&vd); s.p.push_back(vd); if (vd) vd->Release(); DWORD fvf = 0; d->GetFVF(&fvf); s.w.push_back(fvf);
    IDirect3DVertexShader9* vs = nullptr; d->GetVertexShader(&vs); s.p.push_back(vs); if (vs) vs->Release(); return s;
}
}
static void run_line(char* line, int lineno) {
    Tk t{line}; if (strcmp(t.next(), "K")) { CHECK(false); return; }
    int ex = t.u(); uint32_t pool = t.u(), want_hr = t.u(), nfix = t.u(); std::vector<uint32_t> want_fix; for (uint32_t i = 0; i < nfix; i++) want_fix.push_back(t.u());
    IDirect3D9* d3d = t_d3d(ex); D3DPRESENT_PARAMETERS pp = t_pp(); IDirect3DDevice9* dev = t_dev(d3d, &pp); if (!dev) { CHECK(false); return; }
    t.next(); uint32_t n = t.u(); for (uint32_t i = 0; i < n; i++) { uint32_t k = t.u(), v = t.u(); dev->SetRenderState((D3DRENDERSTATETYPE)k, v); }
    t.next(); static const D3DTRANSFORMSTATETYPE ts[3] = {D3DTS_VIEW, D3DTS_PROJECTION, D3DTS_WORLD};
    for (int m = 0; m < 3; m++) { D3DMATRIX x; for (int i = 0; i < 16; i++) (&x._11)[i] = t.f(); dev->SetTransform(ts[m], &x); }
    t.next(); D3DVIEWPORT9 vp; vp.X = t.u(); vp.Y = t.u(); vp.Width = t.u(); vp.Height = t.u(); vp.MinZ = t.f(); vp.MaxZ = t.f(); CHECK_HR(dev->SetViewport(&vp), D3D_OK);
    t.next(); D3DMATERIAL9 mt; float* mf = (float*)&mt; for (int i = 0; i < 17; i++) mf[i] = t.f(); dev->SetMaterial(&mt);
    t.next(); n = t.u(); for (uint32_t i = 0; i < n; i++) { uint32_t idx = t.u(), ty = t.u(), en = t.u(); D3DLIGHT9 L; memset(&L, 0, sizeof L); L.Type = (D3DLIGHTTYPE)ty; float* lf = (float*)&L.Diffuse; for (int k = 0; k < 25; k++) lf[k] = t.f();   // Diffuse Specular Ambient Position Direction Range Falloff Att0-2 Theta Phi
        CHECK_HR(dev->SetLight(idx, &L), D3D_OK); CHECK_HR(dev->LightEnable(idx, en), D3D_OK); }
    t.next(); uint32_t nd = t.u(); IDirect3DVertexDeclaration9* decl = nullptr;
    if (nd == 0xFFFFFFFFu) { uint32_t fvf = t.u(); CHECK_HR(dev->SetFVF(fvf), D3D_OK); }
    else if (nd) { std::vector<D3DVERTEXELEMENT9> el(nd + 1); for (uint32_t i = 0; i < nd; i++) { el[i].Stream = t.u(); el[i].Offset = t.u(); el[i].Type = t.u(); el[i].Method = 0; el[i].Usage = t.u(); el[i].UsageIndex = t.u(); } el[nd] = D3DDECL_END();
        CHECK_HR(dev->CreateVertexDeclaration(el.data(), &decl), D3D_OK); CHECK_HR(dev->SetVertexDeclaration(decl), D3D_OK); }
    t.next(); n = t.u(); std::vector<IDirect3DVertexBuffer9*> src; std::vector<std::vector<uint8_t>> sdata;
    for (uint32_t i = 0; i < n; i++) {
        uint32_t idx = t.u(), stride = t.u(), off = t.u(), len = t.u(), cfvf = t.u(); std::vector<uint8_t> d = hex(t.next()); IDirect3DVertexBuffer9* vb = nullptr;
        CHECK_HR(dev->CreateVertexBuffer(len, 0, cfvf, (D3DPOOL)(cfvf ? pool : D3DPOOL_SYSTEMMEM), &vb, nullptr), D3D_OK); void* p; CHECK_HR(vb->Lock(0, 0, &p, 0), D3D_OK); memcpy(p, d.data(), d.size()); vb->Unlock();
        CHECK_HR(dev->SetStreamSource(idx, vb, off, stride), D3D_OK); src.push_back(vb); sdata.push_back(d);
    }
    t.next(); if (t.u()) { static const DWORD vs[] = {0xFFFE0101, 0x0000001F, 0x80000000, 0x900F0000, 0x00000001, 0xC00F0000, 0x90E40000, 0x0000FFFF}; IDirect3DVertexShader9* s = nullptr; CHECK_HR(dev->CreateVertexShader(vs, &s), D3D_OK); CHECK_HR(dev->SetVertexShader(s), D3D_OK); if (s) s->Release(); }
    t.next(); uint32_t dfvf = t.u(), dlen = t.u(); std::vector<uint8_t> dinit = hex(t.next()); IDirect3DVertexBuffer9* dst = nullptr;
    bool alias = dlen == 0;
    if (alias) { dst = src[0]; dst->AddRef(); dlen = (uint32_t)sdata[0].size(); dinit = sdata[0]; }
    else { CHECK_HR(dev->CreateVertexBuffer(dlen, 0, dfvf, (D3DPOOL)pool, &dst, nullptr), D3D_OK); if (!dst) return;
        void* p; CHECK_HR(dst->Lock(0, 0, &p, 0), D3D_OK); memcpy(p, dinit.data(), dinit.size()); dst->Unlock(); }
    NxBufCore* db = static_cast<NxBufCore*>(nx_obj_of((IUnknown*)dst)); uint32_t lo, hi; db->take_dirty(&lo, &hi); uint32_t rev0 = db->rev;
    t.next(); uint32_t start = t.u(), didx = t.u(), count = t.u(), declarg = t.u(), dstnull = t.u(), flags = t.u();
    t.next(); std::vector<uint8_t> exp = hex(t.next()); t.next(); char* dr = t.next(); uint32_t elo = 0, ehi = 0; bool edirty = strcmp(dr, "-") != 0; if (edirty) { elo = (uint32_t)strtoul(dr, nullptr, 16); ehi = t.u(); }
    IDirect3DVertexDeclaration9* arg = nullptr;
    if (declarg) { D3DVERTEXELEMENT9 el[2] = {{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0}, D3DDECL_END()}; CHECK_HR(dev->CreateVertexDeclaration(el, &arg), D3D_OK); }
    ULONG r_dev = rc(dev), r_dst = rc(dst), r_arg = rc(arg), r_decl = rc(decl); std::vector<ULONG> r_src; for (auto* s : src) r_src.push_back(rc(s));
    Snap before = snap(dev, ex); HRESULT hr; std::vector<uint32_t> got = capture_call([&] { return dev->ProcessVertices(start, didx, count, dstnull ? nullptr : dst, arg, flags); }, &hr);
    char what[96]; snprintf(what, sizeof what, "line %d hr", lineno); if (!t_check((uint32_t)hr == want_hr, what, __FILE__, __LINE__)) printf("  line %d: hr %08x want %08x\n", lineno, (unsigned)hr, want_hr);
    std::vector<uint32_t> g2 = got, w2 = want_fix; std::sort(g2.begin(), g2.end()); std::sort(w2.begin(), w2.end());
    if (!t_check(g2 == w2, "fixme ids", __FILE__, __LINE__)) { printf("  line %d: fixmes got", lineno); for (auto x : g2) printf(" %u", x); printf(" want"); for (auto x : w2) printf(" %u", x); printf("\n"); }
    const uint8_t* mem = db->mem; std::vector<uint8_t> after(mem, mem + dlen);
    if (want_hr == D3D_OK) { if (!t_check(after == exp, "destination bytes", __FILE__, __LINE__)) { for (uint32_t i = 0; i < dlen; i++) if (after[i] != exp[i]) { printf("  line %d: first diff at byte %u: got %02x want %02x\n", lineno, i, after[i], exp[i]); break; } } }
    else CHECK(after == dinit);
    bool dirty = db->take_dirty(&lo, &hi);
    if (want_hr == D3D_OK) { CHECK(dirty == edirty); if (dirty && edirty) { CHECK_EQ(lo, elo); CHECK_EQ(hi, ehi); } CHECK_EQ(db->rev, rev0 + (edirty ? 1 : 0)); } else { CHECK(!dirty); CHECK_EQ(db->rev, rev0); }
    { Snap after_s = snap(dev, ex); CHECK(after_s == before); }
    CHECK_EQ(db->locks, 0u); CHECK_EQ(rc(dev), r_dev); CHECK_EQ(rc(dst), r_dst); CHECK_EQ(rc(arg), r_arg); CHECK_EQ(rc(decl), r_decl);
    for (size_t i = 0; i < src.size(); i++) { NxBufCore* sb = static_cast<NxBufCore*>(nx_obj_of((IUnknown*)src[i])); CHECK_EQ(rc(src[i]), r_src[i]); CHECK_EQ(sb->locks, 0u); if (src[i] != dst) CHECK(!memcmp(sb->mem, sdata[i].data(), sdata[i].size())); }
    dst->Release(); for (auto* s : src) s->Release(); if (arg) arg->Release(); if (decl) decl->Release(); dev->Release(); d3d->Release();
}
int t_run_file(const char* name) {
    FILE* f = t_open(name); if (!f) return 0; std::string line; char buf[1 << 16]; int n = 0, ln = 0;
    while (fgets(buf, sizeof buf, f)) { line = buf; while (line.size() && line.back() != '\n' && fgets(buf, sizeof buf, f)) line += buf; ln++; if (line.size() < 3) continue; std::vector<char> m(line.begin(), line.end()); m.push_back(0); run_line(m.data(), ln); n++; }
    fclose(f); return n;
}
