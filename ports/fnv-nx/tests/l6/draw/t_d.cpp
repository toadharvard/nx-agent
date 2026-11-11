// part d: state flush. d1 constant blocks and driver blocks byte-exact against the python model of the layout contract (oracle.py write_d); d2 every dirty group produces exactly its ops, nothing when clean,
// everything again after a pass epoch change (another module's pass) and for another device.
#include "t_common.h"
static std::vector<uint8_t> unhex(const char* s) { std::vector<uint8_t> v; for (; s[0] && s[1] && s[0] != '\n'; s += 2) { char b[3] = {s[0], s[1], 0}; v.push_back((uint8_t)strtoul(b, nullptr, 16)); } return v; }
static IDirect3DBaseTexture9* mktex(TDev& d, int kind) {
    IDirect3DTexture9* t = nullptr; IDirect3DCubeTexture9* c = nullptr; IDirect3DVolumeTexture9* v = nullptr;
    if (kind == 1) { d.i9->CreateTexture(4, 4, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr); return t; }
    if (kind == 2) { d.i9->CreateCubeTexture(4, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &c, nullptr); return c; }
    if (kind == 3) { d.i9->CreateVolumeTexture(4, 4, 4, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &v, nullptr); return v; }
    return nullptr;
}
static void part_d1() {
    FILE* f = t_open("d.txt"); char line[16384]; int n = 0;
    while (f && fgets(line, sizeof line, f)) {
        if (strncmp(line, "case", 4)) continue;
        TDev d = t_dev(); t_target(d); IDirect3DVertexBuffer9* vb = t_vb(d, 4096); d.i9->SetFVF(D3DFVF_XYZ); d.i9->SetStreamSource(0, vb, 0, 16);
        struct Exp { int stage, slot; std::vector<uint8_t> b; }; std::vector<Exp> exp;
        while (fgets(line, sizeof line, f) && strncmp(line, "end", 3)) {
            unsigned a, b, c, e, g; int s, t; unsigned u; float fv[4]; int iv[4];
            if (!strncmp(line, "vs ", 3)) { if (strncmp(line + 3, "ffp", 3)) { auto h = t_hex(line + 3); IDirect3DVertexShader9* x = nullptr; CHECK_HR(d.i9->CreateVertexShader((const DWORD*)h.data(), &x), D3D_OK); d.i9->SetVertexShader(x); } }
            else if (!strncmp(line, "ps ", 3)) { if (strncmp(line + 3, "ffp", 3)) { auto h = t_hex(line + 3); IDirect3DPixelShader9* x = nullptr; CHECK_HR(d.i9->CreatePixelShader((const DWORD*)h.data(), &x), D3D_OK); d.i9->SetPixelShader(x); } }
            else if (sscanf(line, "rs %u %u", &a, &b) == 2) d.i9->SetRenderState((D3DRENDERSTATETYPE)a, b);
            else if (sscanf(line, "tss %d %d %u", &s, &t, &u) == 3) d.i9->SetTextureStageState(s, (D3DTEXTURESTAGESTATETYPE)t, u);
            else if (sscanf(line, "tex %d %d", &s, &t) == 2) { IDirect3DBaseTexture9* x = mktex(d, t); d.i9->SetTexture(s, x); }
            else if (sscanf(line, "vsf %u %u %u %u %u", &a, &b, &c, &e, &g) == 5) { uint32_t w[4] = {b, c, e, g}; memcpy(fv, w, 16); d.i9->SetVertexShaderConstantF(a, fv, 1); }
            else if (sscanf(line, "psf %u %u %u %u %u", &a, &b, &c, &e, &g) == 5) { uint32_t w[4] = {b, c, e, g}; memcpy(fv, w, 16); d.i9->SetPixelShaderConstantF(a, fv, 1); }
            else if (sscanf(line, "vsi %u %d %d %d %d", &a, &iv[0], &iv[1], &iv[2], &iv[3]) == 5) d.i9->SetVertexShaderConstantI(a, iv, 1);
            else if (sscanf(line, "psi %u %d %d %d %d", &a, &iv[0], &iv[1], &iv[2], &iv[3]) == 5) d.i9->SetPixelShaderConstantI(a, iv, 1);
            else if (sscanf(line, "vsb %u %u", &a, &b) == 2) { BOOL x = (BOOL)b; d.i9->SetVertexShaderConstantB(a, &x, 1); }
            else if (sscanf(line, "psb %u %u", &a, &b) == 2) { BOOL x = (BOOL)b; d.i9->SetPixelShaderConstantB(a, &x, 1); }
            else if (!strncmp(line, "ffv ", 4)) { auto b2 = unhex(line + 4); memcpy(g_ffp_ubo_v, b2.data(), 64); }
            else if (!strncmp(line, "ffp ", 4)) { auto b2 = unhex(line + 4); memcpy(g_ffp_ubo_p, b2.data(), 32); }
            else if (sscanf(line, "ubo %d %d", &s, &t) == 2) { const char* h = strchr(strchr(line + 4, ' ') + 1, ' ') + 1; exp.push_back({s, t, unhex(h)}); }
        }
        t_clear(); CHECK_HR(d.i9->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1), D3D_OK); t_flush(); bool ok = true; int nbind = 0;
        for (auto& x : exp) { const Ev* last = nullptr; for (auto& e : g_ev) if (e.op == NxOp_BindUbo && e.a[0] == (x.stage ? 2u : 1u) && (int)e.a[1] == x.slot) last = &e; ok &= last && last->blob == x.b && last->a[4] == x.b.size(); }
        for (auto& e : g_ev) if (e.op == NxOp_BindUbo) nbind++;
        ok &= nbind == (int)exp.size();                                                                   // no other block is bound
        if (!CHECK(ok) && n < 3) printf("  case %d: %d binds, %zu expected\n", n, nbind, exp.size());
        n++;
    }
    if (f) fclose(f); CHECK(n >= 300);
}
static std::vector<uint32_t> seq() { std::vector<uint32_t> v; for (auto& e : g_ev) if (e.op != NxOp_Marker) v.push_back(e.op); return v; }
static bool is(const std::vector<uint32_t>& v, std::initializer_list<uint32_t> w) { return v == std::vector<uint32_t>(w); }
static void part_d2() {
    TDev d = t_dev(); t_target(d); IDirect3DVertexBuffer9* vb = t_vb(d, 4096); IDirect3DVertexBuffer9* vb2 = t_vb(d, 4096); IDirect3DIndexBuffer9* ib = t_ib(d, 4096); IDirect3DIndexBuffer9* ib2 = t_ib(d, 4096);
    d.i9->SetFVF(D3DFVF_XYZ); d.i9->SetStreamSource(0, vb, 0, 16); d.i9->SetIndices(ib); IDirect3DTexture9* tex = nullptr; d.i9->CreateTexture(4, 4, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &tex, nullptr);
    float c4[4] = {1, 2, 3, 4}; D3DVIEWPORT9 vp = {1, 2, 30, 31, 0.0f, 1.0f}; RECT rc = {1, 1, 9, 9};
    auto draw = [&](bool idx) { return idx ? d.i9->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 3, 0, 1) : d.i9->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1); };
    // the first draw of a pass binds everything: pass, viewport, scissor, dynamics, layout, state, blocks, streams
    t_clear(); draw(false); draw(false); t_flush(); auto s = seq(); size_t nbind = 0; for (auto o : s) nbind += o == NxOp_BindUbo;
    CHECK(s.size() > 10 && t_count(NxOp_BeginPass4) == 1 && t_count(NxOp_Viewport) == 1 && t_count(NxOp_Scissor) == 1 && t_count(NxOp_BlendConst) == 1 && t_count(NxOp_DepthBias) == 1 && t_count(NxOp_DefineLayout4) <= 1 && t_count(NxOp_BindState) == 1 && t_count(NxOp_Draw) == 2 && nbind == 8);
    // nothing when clean: the second draw of the pass is the Draw op alone; the draws after a flush belong to a new pass and rebind everything (epoch)
    t_clear(); draw(false); size_t before = g_ev.size(); (void)before; draw(false); t_flush(); s = seq(); { size_t k = s.size(); while (k && s[k - 1] != NxOp_Draw) k--; CHECK(t_count(NxOp_Draw) == 2 && t_count(NxOp_BeginPass4) == 1 && k >= 2 && s[k - 2] == NxOp_Draw); }
    d.i9->SetTexture(0, tex); draw(true); d.i9->SetTexture(0, nullptr); draw(true); t_clear();   // the first use of a texture uploads it (outside the pass): do that before the steps
    struct Step { const char* what; std::function<void()> act; std::vector<uint32_t> ops; };
    #define STEP(w, act, ...) { w, [&] { act; }, {__VA_ARGS__} }
    std::vector<Step> st = {
        STEP("viewport", d.i9->SetViewport(&vp), NxOp_Viewport, NxOp_Draw),
        STEP("scissor", d.i9->SetScissorRect(&rc), NxOp_Scissor, NxOp_Draw),
        STEP("blend factor", d.i9->SetRenderState(D3DRS_BLENDFACTOR, 0x12345678), NxOp_BlendConst, NxOp_Draw),
        STEP("depth bias", d.i9->SetRenderState(D3DRS_DEPTHBIAS, 0x3A000000), NxOp_DepthBias, NxOp_DefineState, NxOp_BindState, NxOp_Draw),
        STEP("z enable", d.i9->SetRenderState(D3DRS_ZENABLE, D3DZB_TRUE), NxOp_DefineState, NxOp_BindState, NxOp_Draw),
        STEP("vs constants", d.i9->SetVertexShaderConstantF(3, c4, 1), NxOp_BindUbo, NxOp_Draw),
        STEP("ps constants", d.i9->SetPixelShaderConstantF(3, c4, 1), NxOp_BindUbo, NxOp_Draw),
        STEP("alpha ref", d.i9->SetRenderState(D3DRS_ALPHAREF, 77), NxOp_BindUbo, NxOp_Draw),
        STEP("vertex buffer", d.i9->SetStreamSource(0, vb2, 16, 16), NxOp_BindVtxN, NxOp_Draw),
        STEP("index buffer", d.i9->SetIndices(ib2), NxOp_BindIdx, NxOp_Draw),
        STEP("texture", d.i9->SetTexture(0, tex), NxOp_BindTextures, NxOp_Draw),
    };
    for (auto& x : st) {
        t_clear(); draw(true); x.act(); draw(true); t_flush(); s = seq(); std::vector<uint32_t> tail; bool seen = false; int draws = 0;
        for (auto o : s) { if (o == NxOp_Draw || o == NxOp_DrawIndexed) { draws++; seen = true; if (draws == 2) tail.push_back(NxOp_Draw); continue; } if (draws == 1 && seen) tail.push_back(o); }
        for (auto& o : tail) if (o == NxOp_DrawIndexed) o = NxOp_Draw;
        bool ok = true;   // x.ops with NxOp_Draw standing for the (indexed) draw; the ops between the two draws are exactly the listed ones (BindIdx / BindTextures appear only for the step that changes them)
        if (std::string(x.what) == "index buffer" || std::string(x.what) == "texture" || std::string(x.what) == "vertex buffer") {}
        ok = tail == x.ops;
        if (!CHECK(ok)) { printf("  step %s:", x.what); for (auto o : tail) printf(" %u", o); printf("\n"); }
    }
    // the render-state words 3 (nxgpu4.h NXRS_M_*) and 0 (colour write masks): MSAA (default 1) | LASTPIXEL (default 1) | NOCLIP (CLIPPING 0) | MULTISAMPLEMASK << 32
    d.i9->SetRenderState(D3DRS_CLIPPING, 0); d.i9->SetRenderState(D3DRS_MULTISAMPLEMASK, 5); d.i9->SetRenderState(D3DRS_COLORWRITEENABLE, 5); d.i9->SetRenderState(D3DRS_ANTIALIASEDLINEENABLE, 1); t_clear(); draw(false); t_flush();
    { const Ev* ds = nullptr; for (auto& e : g_ev) if (e.op == NxOp_DefineState) ds = &e; CHECK(ds && ds->a[10] == ((1u << NXRS_M_MSAA) | (1u << NXRS_M_AALINE) | (1u << NXRS_M_LASTPIXEL) | (1u << NXRS_M_NOCLIP)) && ds->a[11] == 5 && (ds->a[5] & 0xF) == 5); }
    // sRGB view rule: SRGBTEXTURE set and the format has a view = image id + 1 (nxgpu4.h / gpu_abi.h)
    { NxImg* im = nx_img_of(nx_obj_of(tex)); d.i9->SetTexture(0, tex); d.i9->SetSamplerState(0, D3DSAMP_SRGBTEXTURE, 0); t_clear(); draw(false); t_flush(); const Ev* bt = t_nth(NxOp_BindTextures, 0); CHECK(bt && (bt->a[3] & 0xFFFFF) == im->gpu);
      d.i9->SetSamplerState(0, D3DSAMP_SRGBTEXTURE, 1); t_clear(); draw(false); t_flush(); bt = t_nth(NxOp_BindTextures, 0); CHECK(bt && (bt->a[3] & 0xFFFFF) == im->gpu + 1); }
    // an epoch change (a Clear by another module begins its own pass) rebinds everything; a second device too
    t_clear(); draw(false); { NxTarget tg = nx_target(nx_surf_core(d.core->cur.rt[0])); bool ok1 = nxs_pass_begin(1, &tg, nullptr) && nxs_clear(NxClear_Color, 0, 0.0f, 0, 0, 0, 8, 8); CHECK(ok1); nxs_pass_end(); }
    draw(false); t_flush();   // the first draw and the Clear-like pass above are another module's pass (what Clear does)
    CHECK(t_count(NxOp_BeginPass4) >= 3 && t_count(NxOp_BindState) == 2 && t_count(NxOp_Viewport) == 2 && t_count(NxOp_BindVtxN) >= 2);
    TDev d2 = t_dev(); t_target(d2); d2.i9->SetFVF(D3DFVF_XYZ); d2.i9->SetStreamSource(0, vb, 0, 16); t_clear(); draw(false); d2.i9->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1); t_flush();
    CHECK(t_count(NxOp_BindState) == 2 && t_count(NxOp_Draw) == 2 && t_count(NxOp_BeginPass4) == 2);
}
void t_d() { t_part('d'); part_d1(); part_d2(); }
