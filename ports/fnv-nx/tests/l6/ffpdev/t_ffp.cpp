// tests/l6/ffpdev parts a-d over the product device (dev/ffp_*.cpp) on the GL backend.
//  a/b  state sweeps: python (test_ffpdev.py) writes the device state as D3D9 calls (cases_*.txt: R T X M V L E P S C F D lines, one `Q` per evaluation) and, per Q, the INPUTS of the state modules built from the same
//       description (exp_*.txt: K = vertex module input words, I = pixel module input words, A / B = expected program inputs / stages / varyings, H = palette hash). Here: the device is driven through the D3D9 API,
//       nx_ffp_vs_program / nx_ffp_ps_program are called, and their blocks are compared byte for byte with the blocks the modules (ffp_state.c / ffp_vs_state.c, called directly by THIS file) produce from the python inputs.
//  c    end to end: `W` lines draw one triangle (DrawPrimitiveUP, FFP vertex + pixel stage) into a 64 x 64 target on llvmpipe; pixels are compared with ffp_vs_ref + ffp_ps_ref (CPU references) on the same blocks.
//  d    program ids: creation / caching / destruction counts of the backend calls.
#include "t_common.h"
#include <math.h>
extern "C" {
#include "ref/ffp_vs_ref.h"
#include "ref/ffp_ps_ref.h"
}

static IDirect3DBaseTexture9 *g_t2[8], *g_tcube, *g_tvol; static float g_texcol[8][4]; static float g_xf[512][16];   // texture pool; the colour of each 1x1 2D texture; every matrix the case files set (the reference palette)
static void make_pool(TDev& d) {
    for (int i = 0; i < 8; i++) { IDirect3DTexture9* t = nullptr; CHECK_HR(d.i9->CreateTexture(1, 1, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr), D3D_OK); g_t2[i] = t; }
    IDirect3DCubeTexture9* c = nullptr; CHECK_HR(d.i9->CreateCubeTexture(1, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &c, nullptr), D3D_OK); g_tcube = c;
    IDirect3DVolumeTexture9* v = nullptr; CHECK_HR(d.i9->CreateVolumeTexture(1, 1, 1, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &v, nullptr), D3D_OK); g_tvol = v;
}
static void fill_tex(int stage, uint32_t argb) {
    IDirect3DTexture9* t = static_cast<IDirect3DTexture9*>(g_t2[stage]); D3DLOCKED_RECT lr; if (t->LockRect(0, &lr, nullptr, 0) != D3D_OK) { CHECK(false); return; }
    *(uint32_t*)lr.pBits = argb; t->UnlockRect(0);
    g_texcol[stage][0] = ((argb >> 16) & 255) / 255.0f; g_texcol[stage][1] = ((argb >> 8) & 255) / 255.0f; g_texcol[stage][2] = (argb & 255) / 255.0f; g_texcol[stage][3] = (argb >> 24) / 255.0f;
}
static float F(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

void t_apply(TDev& d, char tag, const std::vector<uint32_t>& v, IDirect3DBaseTexture9**) {
    IDirect3DDevice9* i9 = d.i9;
    switch (tag) {
    case 'R': CHECK_HR(i9->SetRenderState((D3DRENDERSTATETYPE)v[0], v[1]), D3D_OK); break;
    case 'T': CHECK_HR(i9->SetTextureStageState(v[0], (D3DTEXTURESTAGESTATETYPE)v[1], v[2]), D3D_OK); break;
    case 'X': { D3DMATRIX m; memcpy(&m, &v[1], 64); CHECK_HR(i9->SetTransform((D3DTRANSFORMSTATETYPE)v[0], &m), D3D_OK); if (v[0] < 512) memcpy(g_xf[v[0]], &m, 64); break; }
    case 'M': { D3DMATERIAL9 m; memcpy(&m, &v[0], 68); CHECK_HR(i9->SetMaterial(&m), D3D_OK); break; }
    case 'V': { D3DVIEWPORT9 vp = {v[0], v[1], v[2], v[3], 0.0f, 1.0f}; CHECK_HR(i9->SetViewport(&vp), D3D_OK); break; }
    case 'L': {
        D3DLIGHT9 l; memset(&l, 0, sizeof l); l.Type = (D3DLIGHTTYPE)v[1]; memcpy(&l.Diffuse, &v[2], 16); memcpy(&l.Specular, &v[6], 16); memcpy(&l.Ambient, &v[10], 16);
        memcpy(&l.Position, &v[14], 12); memcpy(&l.Direction, &v[17], 12); memcpy(&l.Range, &v[20], 28); CHECK_HR(i9->SetLight(v[0], &l), D3D_OK); break; }
    case 'E': CHECK_HR(i9->LightEnable(v[0], v[1] != 0), D3D_OK); break;
    case 'P': { float p[4]; memcpy(p, &v[1], 16); CHECK_HR(i9->SetClipPlane(v[0], p), D3D_OK); break; }
    case 'S': CHECK_HR(i9->SetTexture(v[0], v[1] == 0 ? nullptr : v[1] == 1 ? g_t2[v[0]] : v[1] == 2 ? g_tcube : g_tvol), D3D_OK); break;
    case 'C': fill_tex(v[0], v[1]); break;
    case 'F': if (!CHECK_HR(i9->SetFVF(v[0]), D3D_OK)) printf("  SetFVF(%#x) refused\n", v[0]); break;
    case 'D': {
        std::vector<D3DVERTEXELEMENT9> e; for (uint32_t k = 0; k < v[0]; k++) { const uint32_t* q = &v[1 + 6 * k]; D3DVERTEXELEMENT9 x = {(WORD)q[0], (WORD)q[1], (BYTE)q[2], (BYTE)q[3], (BYTE)q[4], (BYTE)q[5]}; e.push_back(x); }
        D3DVERTEXELEMENT9 end = D3DDECL_END(); e.push_back(end);
        IDirect3DVertexDeclaration9* vd = nullptr; CHECK_HR(i9->CreateVertexDeclaration(e.data(), &vd), D3D_OK); if (vd) { CHECK_HR(i9->SetVertexDeclaration(vd), D3D_OK); vd->Release(); } break; }
    }
}

// ---- the oracle: the modules' blocks from the python input words
struct VsExp { NxFfpVsState st; };
static void oracle_vs(const std::vector<uint32_t>& K, NxFfpVsState* out) {
    size_t p = 0; NxFfpVsDecl dc; memcpy(&dc, &K[p], sizeof dc); p += sizeof dc / 4;
    uint32_t rs[NX_VS_RS_COUNT]; memset(rs, 0, sizeof rs); uint32_t nrs = K[p++]; for (uint32_t i = 0; i < nrs; i++, p += 2) rs[K[p]] = K[p + 1];
    uint32_t tci[8], ttff[8]; memcpy(tci, &K[p], 32); p += 8; memcpy(ttff, &K[p], 32); p += 8;
    const float* fl = (const float*)&K[p]; const float *view = fl, *proj = fl + 16, *w0 = fl + 32, (*tex)[16] = (const float(*)[16])(fl + 48), (*clip)[4] = (const float(*)[4])(fl + 48 + 128); p += 48 + 128 + 24;
    NxFfpVsD3d9 in; memset((void*)&in, 0, sizeof in); in.decl = &dc; in.rs = rs; memcpy(in.texCoordIndex, tci, 32); memcpy(in.texTransformFlags, ttff, 32);
    in.view = view; in.proj = proj; in.world0 = w0; in.texMatrix = tex; in.clipPlane = clip;
    memcpy(in.mDiffuse, &K[p], 16); memcpy(in.mAmbient, &K[p + 4], 16); memcpy(in.mSpecular, &K[p + 8], 16); memcpy(in.mEmissive, &K[p + 12], 16); memcpy(&in.mPower, &K[p + 16], 4); p += 17;
    memcpy(in.viewport, &K[p], 16); p += 4; uint32_t nl = K[p++]; std::vector<NxFfpD3dLight> L(nl + 1);
    for (uint32_t i = 0; i < nl; i++, p += 26) { const uint32_t* q = &K[p]; NxFfpD3dLight& l = L[i]; l.type = q[0]; memcpy(l.diffuse, q + 1, 16); memcpy(l.specular, q + 5, 16); memcpy(l.ambient, q + 9, 16);
        memcpy(l.position, q + 13, 12); memcpy(l.direction, q + 16, 12); memcpy(&l.range, q + 19, 28); }
    in.lights = L.data(); in.nLights = nl; nx_ffp_vs_state_from_d3d9(&in, out);
}
static void oracle_ps(const std::vector<uint32_t>& I, NxFfpPsState* out, uint8_t tt[8]) {
    static const uint8_t kT[17] = {NX_TSS_COLOROP, NX_TSS_COLORARG1, NX_TSS_COLORARG2, NX_TSS_ALPHAOP, NX_TSS_ALPHAARG1, NX_TSS_ALPHAARG2, NX_TSS_BUMPENVMAT00, NX_TSS_BUMPENVMAT01, NX_TSS_BUMPENVMAT10, NX_TSS_BUMPENVMAT11,
                                   NX_TSS_BUMPENVLSCALE, NX_TSS_BUMPENVLOFFSET, NX_TSS_TEXTURETRANSFORMFLAGS, NX_TSS_COLORARG0, NX_TSS_ALPHAARG0, NX_TSS_RESULTARG, NX_TSS_CONSTANT};
    uint32_t tss[8][33]; memset(tss, 0, sizeof tss); size_t p = 0;
    for (int s = 0; s < 8; s++) for (int k = 0; k < 17; k++) tss[s][kT[k]] = I[p++];
    uint32_t rs[NX_FFP_PS_RS_COUNT_]; memset(rs, 0, sizeof rs); rs[NX_RS_TEXTUREFACTOR] = I[p++]; rs[NX_RS_SPECULARENABLE] = I[p++];
    for (int s = 0; s < 8; s++) tt[s] = (uint8_t)I[p++];
    nx_ffp_ps_state_from_d3d9(tss, rs, tt, out);
}
static uint64_t fnv64(const void* b, size_t n) { uint64_t h = 1469598103934665603ull; for (size_t i = 0; i < n; i++) { h ^= ((const uint8_t*)b)[i]; h *= 1099511628211ull; } return h; }

struct Group { std::vector<uint32_t> K, I, A, B, H; };
static bool read_group(Lines& e, Group* g) {
    char t; std::vector<uint32_t>* v;
    while (e.next(&t, &v)) { switch (t) { case 'K': g->K = *v; break; case 'I': g->I = *v; break; case 'A': g->A = *v; break; case 'B': g->B = *v; break; case 'H': g->H = *v; return true; } }
    return false;
}

// ---- end to end
static uint8_t g_rb[64 * 64 * 4];
static bool readback(TDev& d, IDirect3DSurface9* rt, IDirect3DSurface9* sys) {
    if (d.i9->GetRenderTargetData(rt, sys) != D3D_OK) return false;
    D3DLOCKED_RECT lr; if (sys->LockRect(&lr, nullptr, D3DLOCK_READONLY) != D3D_OK) return false;
    for (int y = 0; y < 64; y++) memcpy(g_rb + y * 256, (uint8_t*)lr.pBits + (size_t)y * lr.Pitch, 256);
    sys->UnlockRect(); return true;
}
static void sample_tex(void*, uint32_t stage, uint32_t, const float*, float out[4]) { memcpy(out, g_texcol[stage], 16); }
static void color_of(uint32_t c, float o[4]) { o[0] = ((c >> 16) & 255) / 255.0f; o[1] = ((c >> 8) & 255) / 255.0f; o[2] = (c & 255) / 255.0f; o[3] = (c >> 24) / 255.0f; }

// W line: fvf, then 3 vertices of the FVF's fields (words). Reference: ffp_vs_eval per vertex, barycentric interpolation, ffp_ps_eval, vertex fog.
static void draw_and_check(TDev& d, IDirect3DSurface9* rt, IDirect3DSurface9* sys, const std::vector<uint32_t>& w, const Group& g, const uint32_t* rs_fog /* FOGENABLE, FOGVERTEXMODE, FOGCOLOR, SHADEMODE */) {
    uint32_t fvf = w[0]; const uint32_t* vw = &w[1];
    uint32_t pos = fvf & D3DFVF_POSITION_MASK, nb = pos >= D3DFVF_XYZB1 && pos <= D3DFVF_XYZB5 ? 1 + ((pos & 0xE) - D3DFVF_XYZB1) / 2 : 0, ntex = (fvf & D3DFVF_TEXCOUNT_MASK) >> D3DFVF_TEXCOUNT_SHIFT;
    uint32_t nw = (pos == D3DFVF_XYZRHW ? 4 : 3) + nb + ((fvf & D3DFVF_NORMAL) ? 3 : 0) + ((fvf & D3DFVF_DIFFUSE) ? 1 : 0) + ((fvf & D3DFVF_SPECULAR) ? 1 : 0) + 2 * ntex;
    NxFfpVsState st; oracle_vs(g.K, &st); NxFfpPsState ps; uint8_t tt[8]; oracle_ps(g.I, &ps, tt);
    float pal[256][16]; for (int i = 0; i < 256; i++) memcpy(pal[i], g_xf[256 + i], 64);
    float posfix[4] = {0, 0, 1, 0}; NxFfpVsOut o[3]; float sx[3], sy[3];
    for (int k = 0; k < 3; k++) {
        const uint32_t* q = vw + k * nw; NxFfpVsIn in; memset(&in, 0, sizeof in); uint32_t c = 0;
        for (int i = 0; i < (pos == D3DFVF_XYZRHW ? 4 : 3); i++) in.pos[i] = F(q[c++]);
        for (uint32_t i = 0; i < nb; i++) in.blendweight[i] = F(q[c++]);
        if (fvf & D3DFVF_NORMAL) for (int i = 0; i < 3; i++) in.normal[i] = F(q[c++]);
        if (fvf & D3DFVF_DIFFUSE) color_of(q[c++], in.color0);
        if (fvf & D3DFVF_SPECULAR) color_of(q[c++], in.color1);
        for (uint32_t s = 0; s < ntex; s++) { in.tex[s][0] = F(q[c++]); in.tex[s][1] = F(q[c++]); in.tex[s][2] = 0; in.tex[s][3] = 1; }
        nx_ffp_vs_eval(&st, pal, posfix, &in, &o[k]);
        sx[k] = (o[k].position[0] / o[k].position[3] * 0.5f + 0.5f) * 64.0f; sy[k] = (0.5f - o[k].position[1] / o[k].position[3] * 0.5f) * 64.0f;
    }
    // draw
    const uint32_t clr = 0x11223344; CHECK_HR(d.i9->Clear(0, nullptr, D3DCLEAR_TARGET, clr, 1.0f, 0), D3D_OK);
    CHECK_HR(d.i9->DrawPrimitiveUP(D3DPT_TRIANGLELIST, 1, vw, nw * 4), D3D_OK);
    if (!CHECK(readback(d, rt, sys))) return;
    float area = (sx[1] - sx[0]) * (sy[2] - sy[0]) - (sx[2] - sx[0]) * (sy[1] - sy[0]);
    static const float cand[6][2] = {{0.34f, 0.33f}, {0.6f, 0.2f}, {0.2f, 0.6f}, {0.12f, 0.12f}, {0.45f, 0.45f}, {0.1f, 0.75f}};
    float fogc[4]; color_of(rs_fog[2], fogc); bool flat = rs_fog[3] == D3DSHADE_FLAT; int tested = 0;
    for (auto& cd : cand) {
        float l1 = cd[0], l2 = cd[1], l0 = 1 - l1 - l2, px = l0 * sx[0] + l1 * sx[1] + l2 * sx[2], py = l0 * sy[0] + l1 * sy[1] + l2 * sy[2];
        int ix = (int)floorf(px + 0.5f), iy = (int)floorf(py + 0.5f);   // D3D9: pixel centres at integer window coordinates (the backend's viewport carries the half pixel, draw_state.cpp S4) if (ix < 0 || iy < 0 || ix > 63 || iy > 63) continue;
        float cx = (float)ix, cy = (float)iy, b[3];
        b[1] = ((cx - sx[0]) * (sy[2] - sy[0]) - (cy - sy[0]) * (sx[2] - sx[0])) / area; b[2] = ((sx[1] - sx[0]) * (cy - sy[0]) - (sy[1] - sy[0]) * (cx - sx[0])) / area; b[0] = 1 - b[1] - b[2];
        if (b[0] < 0.03f || b[1] < 0.03f || b[2] < 0.03f) continue;                                    // too close to an edge: the coverage rule is not what is tested
        float iw[3], sw = 0; for (int k = 0; k < 3; k++) { iw[k] = b[k] / o[k].position[3]; sw += iw[k]; }
        auto lerp = [&](auto get, int ch) { float r = 0; for (int k = 0; k < 3; k++) r += iw[k] * get(o[k], ch); return r / sw; };
        NxFfpPsInputs pi; memset(&pi, 0, sizeof pi); pi.sample = sample_tex;
        for (int ch = 0; ch < 4; ch++) {
            pi.diffuse[ch] = flat ? o[0].color0[ch] : lerp([](const NxFfpVsOut& v, int c) { return v.color0[c]; }, ch);
            pi.specular[ch] = flat ? o[0].color1[ch] : lerp([](const NxFfpVsOut& v, int c) { return v.color1[c]; }, ch);
            for (int s = 0; s < 8; s++) pi.texcoord[s][ch] = lerp([s](const NxFfpVsOut& v, int c) { return v.tex[s][c]; }, ch);
        }
        float fog = lerp([](const NxFfpVsOut& v, int) { return v.fog; }, 0), col[4]; nx_ffp_ps_eval(&ps, &pi, col);
        if (rs_fog[0] != 0) { float f = fog < 0 ? 0 : fog > 1 ? 1 : fog; for (int ch = 0; ch < 3; ch++) col[ch] = fogc[ch] + (col[ch] - fogc[ch]) * f; }
        const uint8_t* px4 = g_rb + (iy * 64 + ix) * 4; float got[4] = {px4[2] / 255.0f, px4[1] / 255.0f, px4[0] / 255.0f, px4[3] / 255.0f}; bool ok = true; tested++;
        for (int ch = 0; ch < 4; ch++) { float e = col[ch] < 0 ? 0 : col[ch] > 1 ? 1 : col[ch]; if (fabsf(got[ch] - e) > 1.5f / 255.0f) ok = false; }
        if (!CHECK(ok)) printf("  pixel (%d,%d) got %.3f %.3f %.3f %.3f want %.3f %.3f %.3f %.3f\n", ix, iy, got[0], got[1], got[2], got[3], col[0], col[1], col[2], col[3]);
    }
    CHECK(tested >= 2);
}

static void run(const char* cases, const char* exps, bool draw, bool want_progs) {
    TDev d = t_dev(); t_target(d); make_pool(d); Lines c, e; if (!c.open(cases) || !e.open(exps)) return;
    IDirect3DSurface9 *rt = nullptr, *sys = nullptr;
    if (draw) { CHECK_HR(d.i9->GetRenderTarget(0, &rt), D3D_OK); CHECK_HR(d.i9->CreateOffscreenPlainSurface(64, 64, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &sys, nullptr), D3D_OK); }
    char t; std::vector<uint32_t>* v; uint32_t rsfog[4] = {0, 0, 0, D3DSHADE_GOURAUD};
    while (c.next(&t, &v)) {
        if (t == 'R') { if ((*v)[0] == D3DRS_FOGENABLE) rsfog[0] = (*v)[1]; if ((*v)[0] == D3DRS_FOGCOLOR) rsfog[2] = (*v)[1]; if ((*v)[0] == D3DRS_SHADEMODE) rsfog[3] = (*v)[1]; }
        if (t != 'Q' && t != 'W') { t_apply(d, t, *v, nullptr); continue; }
        Group g; if (!CHECK(read_group(e, &g))) break;
        if (t == 'W') draw_and_check(d, rt, sys, *v, g, rsfog);
        NxFfpVsBind vb; NxFfpPsBind pb; uint32_t vid = nx_ffp_vs_program(d.core, &vb), pid = nx_ffp_ps_program(d.core, &pb);
        CHECK(vid != 0 && pid != 0); if (!vid || !pid) continue;
        NxFfpVsState st; oracle_vs(g.K, &st); NxFfpPsState ps; uint8_t tt[8]; oracle_ps(g.I, &ps, tt);
        CHECK_EQ(vb.n_ubo, 2); CHECK_EQ(vb.ubo[0].slot, 4); CHECK_EQ(vb.ubo[0].bytes, sizeof st); CHECK(!memcmp(vb.ubo[0].data, &st, sizeof st));
        CHECK_EQ(vb.ubo[1].slot, 5); CHECK_EQ(vb.ubo[1].bytes, 16384); CHECK_EQ(fnv64(vb.ubo[1].data, 16384), ((uint64_t)g.H[1] << 32) | g.H[0]);
        CHECK_EQ(pb.n_ubo, 1); CHECK_EQ(pb.ubo[0].slot, 3); CHECK_EQ(pb.ubo[0].bytes, sizeof ps); CHECK(!memcmp(pb.ubo[0].data, &ps, sizeof ps));
        CHECK_EQ(vb.n_in, g.A[0]); bool ok = vb.n_in == g.A[0]; for (uint32_t i = 0; ok && i < vb.n_in; i++) ok = vb.in_pair[i] == g.A[1 + 2 * i] && vb.in_loc[i] == g.A[2 + 2 * i]; CHECK(ok);
        CHECK_EQ(pb.tex_mask, g.B[0]); CHECK_EQ(pb.n_in, g.B[1]); ok = pb.n_in == g.B[1]; for (uint32_t i = 0; ok && i < pb.n_in; i++) ok = pb.in_pair[i] == g.B[2 + i]; CHECK(ok);
        (void)want_progs;
    }
    if (rt) rt->Release(); if (sys) sys->Release();
    d.i9->Release();
}

void t_a() { t_part('a'); run("cases_a.txt", "exp_a.txt", false, true); }
void t_b() { t_part('b'); run("cases_b.txt", "exp_b.txt", false, true); }
void t_c() { t_part('c'); run("cases_c.txt", "exp_c.txt", true, true); }

// ---- d: program ids. One smooth draw state: a vertex and a pixel program; repeated calls make nothing; the flat variant makes a second pair; the device's death destroys all four, a second device has its own.
void t_d() {
    t_part('d'); int c0 = g_calls[NXGPU_FN_SHADER_CREATE], x0 = g_calls[NXGPU_FN_SHADER_DESTROY];
    TDev d = t_dev(); t_target(d); CHECK_HR(d.i9->SetFVF(D3DFVF_XYZ | D3DFVF_DIFFUSE | D3DFVF_TEX1), D3D_OK);
    NxFfpVsBind vb; NxFfpPsBind pb; uint32_t v1 = nx_ffp_vs_program(d.core, &vb), p1 = nx_ffp_ps_program(d.core, &pb);
    CHECK(v1 && p1 && v1 != p1); CHECK_EQ(g_calls[NXGPU_FN_SHADER_CREATE] - c0, 2); CHECK_EQ(g_calls[NXGPU_FN_INFO] > 0, 1);
    for (int i = 0; i < 5; i++) { CHECK_EQ(nx_ffp_vs_program(d.core, &vb), v1); CHECK_EQ(nx_ffp_ps_program(d.core, &pb), p1); }
    CHECK_EQ(g_calls[NXGPU_FN_SHADER_CREATE] - c0, 2);
    CHECK_HR(d.i9->SetRenderState(D3DRS_ALPHATESTENABLE, 1), D3D_OK); CHECK_HR(d.i9->SetTextureStageState(0, D3DTSS_COLOROP, D3DTOP_ADD), D3D_OK);   // no effect on the program ids
    CHECK_EQ(nx_ffp_vs_program(d.core, &vb), v1); CHECK_EQ(nx_ffp_ps_program(d.core, &pb), p1); CHECK_EQ(g_calls[NXGPU_FN_SHADER_CREATE] - c0, 2);
    CHECK_HR(d.i9->SetRenderState(D3DRS_SHADEMODE, D3DSHADE_FLAT), D3D_OK);
    uint32_t v2 = nx_ffp_vs_program(d.core, &vb), p2 = nx_ffp_ps_program(d.core, &pb); CHECK(v2 && p2 && v2 != v1 && p2 != p1 && v2 != p2); CHECK_EQ(g_calls[NXGPU_FN_SHADER_CREATE] - c0, 4);
    CHECK_HR(d.i9->SetRenderState(D3DRS_SHADEMODE, 0x7777), D3D_OK);                                          // names no shade mode: the previous (draw path latch; this test never drew) = smooth
    CHECK_EQ(nx_ffp_vs_program(d.core, &vb), v1);
    CHECK_HR(d.i9->SetRenderState(D3DRS_SHADEMODE, D3DSHADE_PHONG), D3D_OK); CHECK_EQ(nx_ffp_ps_program(d.core, &pb), p1);
    CHECK_HR(d.i9->SetRenderState(D3DRS_SHADEMODE, D3DSHADE_FLAT), D3D_OK); CHECK_EQ(nx_ffp_vs_program(d.core, &vb), v2); CHECK_EQ(g_calls[NXGPU_FN_SHADER_CREATE] - c0, 4);
    CHECK_EQ(g_calls[NXGPU_FN_SHADER_DESTROY] - x0, 0);
    TDev e = t_dev(); t_target(e); CHECK_HR(e.i9->SetFVF(D3DFVF_XYZ), D3D_OK); uint32_t v3 = nx_ffp_vs_program(e.core, &vb); CHECK(v3 != 0 && v3 != v1 && v3 != v2); CHECK_EQ(g_calls[NXGPU_FN_SHADER_CREATE] - c0, 5);
    CHECK_EQ(d.i9->Release(), 0); CHECK_EQ(g_calls[NXGPU_FN_SHADER_DESTROY] - x0, 4);                         // the first device's four programs, no more
    CHECK_EQ(e.i9->Release(), 0); CHECK_EQ(g_calls[NXGPU_FN_SHADER_DESTROY] - x0, 5);
    TDev f = t_dev(); NxFfpVsBind vb2; CHECK_EQ(nx_ffp_vs_program(f.core, &vb2), 0);                          // no declaration bound: no program (the draw is invalid before it gets here)
    CHECK_EQ(g_calls[NXGPU_FN_SHADER_CREATE] - c0, 5); f.i9->Release();
}
