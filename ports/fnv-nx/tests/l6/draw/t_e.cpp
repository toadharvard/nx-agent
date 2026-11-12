// part e: program pairs: backend shader creation per (shader, variant bit, link set L) against the python reimplementation of the fnvnx_dk_layout.h rules (oracle.py write_e); lazy creation, caching, destruction.
// Case lines: vs <hex> / ps <hex|ffp> / expect <variant> <pair codes of L> / end
#include "t_common.h"
#include "fnvnx_sha256.h"
static bool key_of(const uint8_t* key, const std::vector<uint8_t>& b) { uint8_t k[32]; fnvnx_sha256(b.data(), b.size(), k); return !memcmp(key, k, 32); }
void t_e() {
    t_part('e'); FILE* f = t_open("e.txt"); char line[8192]; int n = 0;
    while (f && fgets(line, sizeof line, f)) {
        if (strncmp(line, "case", 4)) continue;
        std::vector<uint32_t> vs, ps; bool ffp = false; unsigned variant = 0; std::vector<uint8_t> L;
        while (fgets(line, sizeof line, f) && strncmp(line, "end", 3)) {
            if (!strncmp(line, "vs ", 3)) vs = t_hex(line + 3);
            else if (!strncmp(line, "ps ", 3)) { if (!strncmp(line + 3, "ffp", 3)) ffp = true; else ps = t_hex(line + 3); }
            else if (!strncmp(line, "expect ", 7)) { char* p = line + 7; variant = strtoul(p, &p, 10); for (;;) { while (*p == ' ') p++; if (*p == '\n' || !*p) break; L.push_back((uint8_t)strtoul(p, &p, 10)); } }
        }
        TDev d = t_dev(); t_target(d); IDirect3DVertexBuffer9* vb = t_vb(d, 4096); d.i9->SetFVF(D3DFVF_XYZ); d.i9->SetStreamSource(0, vb, 0, 16);
        IDirect3DVertexShader9* v = nullptr; IDirect3DPixelShader9* p = nullptr; CHECK_HR(d.i9->CreateVertexShader((const DWORD*)vs.data(), &v), D3D_OK);
        if (!ffp) CHECK_HR(d.i9->CreatePixelShader((const DWORD*)ps.data(), &p), D3D_OK);
        d.i9->SetVertexShader(v); d.i9->SetPixelShader(p);
        t_clear(); CHECK_HR(d.i9->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1), D3D_OK); t_flush();
        std::vector<const Call*> cr; for (auto& c : g_calls) if (c.fn == NXGPU_FN_SHADER_CREATE_D3D) cr.push_back(&c);
        bool ok = cr.size() == (ffp ? 1u : 2u) && t_count(NxOp_Draw) == 1;
        if (ok) {
            const Call& cv = *cr[0]; ok = cv.sd.stage == 0 && cv.sd.variant == variant && cv.sd.n_link_pairs == L.size() && !memcmp(cv.l, L.data(), L.size()) && key_of(cv.sd.key, cv.code) && cv.code.size() == vs.size() * 4 && !memcmp(cv.code.data(), vs.data(), vs.size() * 4);
            if (!ffp) { const Call& cp = *cr[1]; ok = ok && cp.sd.stage == 1 && cp.sd.variant == 0 && cp.sd.n_link_pairs == L.size() && !memcmp(cp.l, L.data(), L.size()) && key_of(cp.sd.key, cp.code) && cp.code.size() == ps.size() * 4 && !memcmp(cp.code.data(), ps.data(), ps.size() * 4); }
        }
        if (ok) {   // the state names the created ids (vs, ps) and a second draw creates nothing
            const Ev* ds = t_nth(NxOp_DefineState, 0); ok = ds && ds->a[1] == cr[0]->ret && ds->a[2] == (ffp ? g_ffp_ps_id : cr[1]->ret);
            t_clear(); d.i9->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1); t_flush(); ok = ok && t_ncalls(NXGPU_FN_SHADER_CREATE_D3D) == 0;
        }
        if (ok && !ffp) {   // a second pixel shader object of the same bytecode: one creation (the vertex program of (variant, L) is reused)
            IDirect3DPixelShader9* p2 = nullptr; d.i9->CreatePixelShader((const DWORD*)ps.data(), &p2); d.i9->SetPixelShader(p2); t_clear(); d.i9->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1); t_flush();
            ok = t_ncalls(NXGPU_FN_SHADER_CREATE_D3D) == 1 && g_calls[0].sd.stage == 1; d.i9->SetPixelShader(p); p2->Release();
        }
        if (!CHECK(ok) && n < 4) printf("  case %d: %zu creates\n", n, cr.size());
        t_clear(); d.i9->SetVertexShader(nullptr); d.i9->SetPixelShader(nullptr); uint32_t idv = cr.empty() ? 0 : cr[0]->ret; int destroyed_before = 0; (void)destroyed_before; v->Release(); if (p) p->Release();
        t_clear(); int dn = t_ncalls(NXGPU_FN_SHADER_DESTROY); (void)dn; n++; (void)idv;
    }
    if (f) fclose(f); CHECK(n >= 300);
    // a backend that refuses a shader = D3DERR_DRIVERINTERNALERROR and nothing recorded after it
    TDev d = t_dev(); t_target(d); IDirect3DVertexBuffer9* vb = t_vb(d, 4096); d.i9->SetFVF(D3DFVF_XYZ); d.i9->SetStreamSource(0, vb, 0, 16); auto h = t_hex("fffe0200 200001f 80000000 900f0000 2000001 c00f0000 90e40000 ffff");
    IDirect3DVertexShader9* v = nullptr; CHECK_HR(d.i9->CreateVertexShader((const DWORD*)h.data(), &v), D3D_OK); d.i9->SetVertexShader(v); t_clear(); g_shader_fail = 1;
    CHECK_HR(d.i9->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1), D3DERR_DRIVERINTERNALERROR); t_flush(); CHECK(t_count(NxOp_Draw) == 0 && t_count(NxOp_BindState) == 0 && t_count(NxOp_DefineState) == 0);
    CHECK_HR(d.i9->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1), D3D_OK);   // the failed creation was not cached
    // shader_destroy: the ids of a destroyed shader object are destroyed with it, after the stream that names them was submitted
    t_clear(); d.i9->SetVertexShader(nullptr); uint32_t id = 0; for (auto& c : g_calls) (void)c; v->Release(); (void)id; CHECK(t_ncalls(NXGPU_FN_SHADER_DESTROY) == 1);
}
