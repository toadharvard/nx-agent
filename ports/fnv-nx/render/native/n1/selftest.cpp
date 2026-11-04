// fnv-nx native renderer, N1: self-test (see selftest.h). Engine-shaped call sequences against nx::Renderer, with
// expected values computed on the CPU.
#include "selftest.h"
#include "engine_stubs.h"
#include "nx/families.h"
#include "nx_shaders.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

namespace {
using namespace nx;
enum { W = 128, H = 96 };
enum { RS_ZENABLE = 7, RS_ZWRITEENABLE = 14, RS_SRCBLEND = 19, RS_DESTBLEND = 20, RS_CULLMODE = 22, RS_ZFUNC = 23,
       RS_ALPHABLENDENABLE = 27, RS_SCISSORTESTENABLE = 174 };

struct T {
    char* log; uint32_t cap, at; int checks, fails;
    void Check(bool ok, const char* what, int a = 0, int b = 0) {
        checks++;
        if (ok) return;
        fails++;
        if (at < cap) at += (uint32_t)snprintf(log + at, cap - at, "  FAIL %s (%d, %d)\n", what, a, b);
    }
};

void mul(const float* a, const float* b, float* o) {   // column-major o = a * b
    for (int c = 0; c < 4; c++)
        for (int r = 0; r < 4; r++) {
            float s = 0;
            for (int k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
            o[c * 4 + r] = s;
        }
}
void xform(const float* m, const float* v, float* o) {
    for (int r = 0; r < 4; r++) o[r] = m[r] * v[0] + m[4 + r] * v[1] + m[8 + r] * v[2] + m[12 + r] * v[3];
}

void test_math(T& t) {
    NiPoint3f loc = {10, 20, 30}, dir = {0, 1, 0}, up = {0, 0, 1}, right = {1, 0, 0};   // Gamebryo z up
    float v[16], p[16], vp[16];
    CameraView(loc, dir, up, right, v);
    NiFrustumF f = {-0.5f, 0.5f, 0.25f, -0.25f, 2.0f, 100.0f, false};
    CameraProjection(f, p);
    mul(p, v, vp);
    float a[4] = {10, 22, 30, 1}, o[4];   // on the view axis at the near plane
    xform(vp, a, o);
    t.Check(fabsf(o[2] / o[3]) < 1e-5f && fabsf(o[0]) < 1e-5f && fabsf(o[1]) < 1e-5f, "camera: near plane -> z 0");
    float b[4] = {10, 120, 30, 1};
    xform(vp, b, o);
    t.Check(fabsf(o[2] / o[3] - 1) < 1e-5f, "camera: far plane -> z 1");
    float c[4] = {10 + 0.5f * 10, 30, 30 + 0.25f * 10, 1};   // right edge, top edge at distance 10
    xform(vp, c, o);
    t.Check(fabsf(o[0] / o[3] - 1) < 1e-5f && fabsf(o[1] / o[3] - 1) < 1e-5f, "camera: frustum corner -> (1, 1)");
    f.ortho = true; f.left = -50; f.right = 50; f.top = 25; f.bottom = -25;
    CameraProjection(f, p); mul(p, v, vp);
    float d[4] = {60, 70, 55, 1};
    xform(vp, d, o);
    t.Check(fabsf(o[0] - 1) < 1e-5f && fabsf(o[1] - 1) < 1e-5f && fabsf(o[2] - 48.0f / 98.0f) < 1e-5f && o[3] == 1,
            "camera: ortho");
    float s[16], e[4] = {0.25f, 0.75f, 0.5f, 1};
    ScreenSpaceProjection(s, 128, 96);
    xform(s, e, o);   // the engine's half-pixel correction: window (x*W - 0.5, y*H - 0.5); z/9999
    t.Check(fabsf(o[0] - (-0.5f - 1.0f / 128)) < 1e-6f && fabsf(o[1] - (-0.5f + 1.0f / 96)) < 1e-6f &&
            fabsf(o[2] - 0.5f / 9999.0f) < 1e-9f, "screen space: (0.25, 0.75) -> (-0.5 - 1/W, -0.5 + 1/H, z/9999)");
}

void test_tracker(T& t, StateTracker& st) {
    st.Reset();
    NxD3D9Viewport vp = {0, 0, W, H, 0, 1};
    NxD3D9Rect sc = {0, 0, W, H};
    st.Resolve(vp, sc, 0);
    uint32_t n0 = st.resolves();
    st.Resolve(vp, sc, 0);
    t.Check(st.resolves() == n0, "tracker: unchanged state is not translated again");
    st.SetRenderState(RS_ALPHABLENDENABLE, 1, true);
    t.Check(st.GetRenderState(RS_ALPHABLENDENABLE) == 1, "tracker: set");
    const StateTracker::Resolved& r = st.Resolve(vp, sc, 0);
    t.Check(st.resolves() == n0 + 1 && (r.rs.w[0] & 1), "tracker: blend enable reaches the state word");
    st.RestoreRenderState(RS_ALPHABLENDENABLE);
    t.Check(st.GetRenderState(RS_ALPHABLENDENABLE) == 0, "tracker: restore");
    st.SetSamplerState(0, 1, 3, true); st.SetSamplerState(0, 1, 4);
    st.RestoreSamplerState(0, 1);
    t.Check(st.GetSamplerState(0, 1) == 1, "tracker: sampler restore returns the value before the saving set",
            (int)st.GetSamplerState(0, 1));
}

// per-stage sampler cache (P6n F4): translated again only after a change of that stage's states or texture kind
void test_sampler_cache(T& t, Backend* be, StateTracker& st) {
    st.Reset();
    NxD3D9Texture k; memset(&k, 0, sizeof k); k.format = 21;   // A8R8G8B8
    st.SetTexture(0, 7, k); st.SetTexture(1, 9, k);
    uint32_t h[2], h2[2];
    uint32_t n0 = st.samplerTranslations();
    st.Handles(be, h, 2);
    t.Check(st.samplerTranslations() == n0 + 2, "sampler cache: first use translates each stage");
    // the engine re-sets every stage's sampler states before each draw: same values -> no new translation
    st.SetSamplerState(0, 5, st.GetSamplerState(0, 5)); st.SetSamplerState(1, 1, st.GetSamplerState(1, 1));
    const uint32_t n2 = st.samplerTranslations();
    st.Handles(be, h2, 2);
    t.Check(st.samplerTranslations() == n2 && h2[0] == h[0] && h2[1] == h[1],
            "sampler cache: redundant sets do not translate again");
    st.SetTexture(0, 8, k);
    st.Handles(be, h2, 2);
    t.Check(st.samplerTranslations() == n2 && (h2[0] & 0xFFFFFu) == 8 && (h2[0] >> 20) == (h[0] >> 20),
            "sampler cache: another image of the same kind keeps the sampler, takes the image");
    st.SetSamplerState(1, 1, 3);   // ADDRESSU clamp on stage 1
    st.Handles(be, h2, 2);
    t.Check(st.samplerTranslations() == n2 + 1 && h2[1] != h[1], "sampler cache: a changed state translates that stage only");
    k.format = 113;   // A16B16G16R16F: another kind
    st.SetTexture(0, 8, k);
    st.Handles(be, h2, 2);
    t.Check(st.samplerTranslations() == n2 + 2, "sampler cache: another texture kind translates the stage");
    st.SetSamplerState(1, 1, 1);
    st.Handles(be, h2, 2);
    t.Check(h2[1] == h[1], "sampler cache: back to the first states, the first sampler");
    // texture generation (Render reuses the previous texture block while it is unchanged)
    const uint32_t g0 = st.textureGeneration();
    st.SetTexture(0, 8, k); st.SetSamplerState(1, 1, 1);
    t.Check(st.textureGeneration() == g0, "texture generation: redundant sets keep it");
    st.SetTexture(0, 9, k);
    const uint32_t g1 = st.textureGeneration();
    st.SetSamplerState(1, 2, 3);
    t.Check(g1 != g0 && st.textureGeneration() != g1, "texture generation: a new image or sampler state changes it");
}

uint16_t shader(Backend* be, const char* name, uint32_t stage) {
    int i = nx_shader_find(name, stage);
    if (i < 0) return 0;
    NxShaderDesc d;
    memset(&d, 0, sizeof d);
    d.stage = stage; d.dksh = FNVNX_ADDR_(nx_shader_blobs[i].dksh); d.dksh_size = nx_shader_blobs[i].dksh_size;
    d.glsl = FNVNX_ADDR_(nx_shader_blobs[i].glsl); d.glsl_size = nx_shader_blobs[i].glsl_size;
    return (uint16_t)be->ShaderCreate(d);
}

struct TileVtx { float x, y, z, u, v; uint32_t rgba; };

void Samp_point(Renderer& r) {   // stage 0: point filtering, clamp (texel-exact checks)
    r.state().SetSamplerState(0, 5, 1); r.state().SetSamplerState(0, 6, 1); r.state().SetSamplerState(0, 7, 0);
    r.state().SetSamplerState(0, 1, 3); r.state().SetSamplerState(0, 2, 3);
}

uint8_t u8(float v) { v = v < 0 ? 0 : v > 1 ? 1 : v; return (uint8_t)(v * 255.0f + 0.5f); }
}  // namespace

int n1_selftest(Backend* be, char* log, uint32_t loglen, int* checks) {
    static T t;
    t.log = log; t.cap = loglen; t.at = 0; t.checks = 0; t.fails = 0;
    if (log && loglen) log[0] = 0;
    test_math(t);
    static Renderer r;
    NxImageDesc rt;
    memset(&rt, 0, sizeof rt);
    rt.width = W; rt.height = H; rt.format = NxFmt_RGBA8; rt.flags = NxImg_RenderTarget;
    uint32_t back = be->ImageCreate(rt), menu = be->ImageCreate(rt);
    NxImageDesc ds = rt; ds.format = NxFmt_Z24S8; ds.flags = NxImg_RenderTarget | NxImg_Depth;
    uint32_t depth = be->ImageCreate(ds);
    t.Check(back && menu && depth, "targets");
    t.Check(r.Init(be, W, H, back, depth), "renderer init");
    t.Check(Renderer::Get() == &r, "Renderer::Get");
    test_tracker(t, r.state());
    test_sampler_cache(t, be, r.state());
    r.state().Reset();
    static const int kGroupKey = 0;   // stands for an engine NiRenderTargetGroup*
    t.Check(r.RegisterTargetGroup(&kGroupKey, menu, 0, W, H), "register group");
    uint16_t vs = shader(be, "tile", 0), ps = shader(be, "tile", 1);
    t.Check(vs && ps, "tile shaders");
    NxVtxLayout ll;
    memset(&ll, 0, sizeof ll);
    ll.count = 3; ll.stride = 24;
    ll.attr[0].offset = 0; ll.attr[0].type = NxAttr_Float32; ll.attr[0].components = 3;
    ll.attr[1].offset = 12; ll.attr[1].type = NxAttr_Float32; ll.attr[1].components = 2;
    ll.attr[2].offset = 20; ll.attr[2].type = NxAttr_Unorm8; ll.attr[2].components = 4;
    uint16_t lay = r.cache().Layout(ll);
    static StaticBuffer vb;
    t.Check(vb.Init(be, 64 * 1024), "vertex buffer");
    static const uint32_t white = 0xFFFFFFFFu;
    NxImageDesc td;
    memset(&td, 0, sizeof td);
    td.width = 1; td.height = 1; td.format = NxFmt_BGRA8; td.data = FNVNX_ADDR_(&white); td.data_size = 4;
    uint32_t tex = be->ImageCreate(td);
    NxD3D9Texture kind = {21, 0, 0, 0, 0};
    r.state().SetTexture(0, tex, kind);

    // screen-space quad in [x0,x1] x [y0,y1] (normalized, y down)
    auto quad = [&](float x0, float y0, float x1, float y1, const float tint[4], uint8_t layer) {
        uint32_t off;
        TileVtx* v = (TileVtx*)vb.Alloc(4 * sizeof(TileVtx), 16, &off);
        const float q[4][2] = {{x0, y0}, {x1, y0}, {x0, y1}, {x1, y1}};
        for (int i = 0; i < 4; i++) { v[i].x = q[i][0]; v[i].y = q[i][1]; v[i].z = 0.5f; v[i].u = 0; v[i].v = 0; v[i].rgba = white; }
        NxTileDraw u;
        memset(&u, 0, sizeof u);
        mul(r.proj(), r.view(), u.wvp);
        memcpy(u.tint, tint, 16);
        Renderer::Draw d;
        memset(&d, 0, sizeof d);
        d.vs = vs; d.ps = ps; d.layout = lay; d.prim = NxPrim_TriStrip; d.layer = layer;
        d.vtxBlock = vb.block(); d.vtxOffset = off; d.vtxSize = 4 * sizeof(TileVtx); d.count = 4;
        d.textures = 1; d.ubo = &u; d.uboSize = sizeof u;
        return r.Render(d);
    };

    // frame 1: a menu rendered into its own target group, then onto the back buffer
    const float red[4] = {1, 0, 0, 1}, half[4] = {0, 0, 1, 0.5f};
    t.Check(r.Do_BeginFrame(), "BeginFrame");
    r.SetBackgroundColor(0.25f, 0.5f, 0.75f, 1.0f);
    t.Check(r.Do_BeginUsingRenderTargetGroup(&kGroupKey, Renderer::CLEAR_ALL), "BeginUsingRenderTargetGroup(menu)");
    r.Do_SetScreenSpaceCameraData(nullptr);
    r.SetEnginePass(757, NxFam_Tile);          // TILE
    t.Check(quad(0.25f, 0.25f, 0.75f, 0.75f, red, 0), "render opaque quad");
    r.SetEnginePass(758, NxFam_ImageSpace);    // a second tag in the same pass
    r.state().SetRenderState(RS_ALPHABLENDENABLE, 1);
    r.state().SetRenderState(RS_SRCBLEND, 5); r.state().SetRenderState(RS_DESTBLEND, 6);
    t.Check(quad(0.5f, 0.0f, 1.0f, 0.5f, half, 1), "render blended quad");
    r.state().SetRenderState(RS_ALPHABLENDENABLE, 0);
    t.Check(r.Do_EndUsingRenderTargetGroup(), "EndUsingRenderTargetGroup");
    r.SetEnginePass(0, NxFam_None);
    r.SetBackgroundColor(0, 0, 0, 1);
    t.Check(r.Do_BeginUsingRenderTargetGroup(nullptr, Renderer::CLEAR_ALL), "BeginUsingRenderTargetGroup(default)");
    // a viewport on the right half of the back buffer
    NiRectF port = {0.5f, 1.0f, 1.0f, 0.0f};
    r.Do_SetScreenSpaceCameraData(&port);
    t.Check(quad(0, 0, 1, 1, red, 0), "render into a half viewport");
    // ClearBuffer with a rectangle mid-frame (the top-right quarter, normalized like a port): only it is cleared
    r.SetBackgroundColor(0, 1, 0, 1);
    NiRectF quarter = {0.75f, 1.0f, 1.0f, 0.5f};
    r.Do_ClearBuffer(&quarter, Renderer::CLEAR_BACKBUFFER);
    t.Check(r.Do_EndUsingRenderTargetGroup(), "EndUsing(default)");
    t.Check(r.Do_EndFrame() && r.Do_DisplayFrame(), "EndFrame + DisplayFrame");
    t.Check(!r.Do_EndFrame(), "EndFrame outside a frame fails");
    {   // NxOp_Marker PASS tags in the encoded stream: 757/Tile then 758/ImageSpace in the menu pass, none after
        const uint32_t* w = r.frame().encodedData();
        uint32_t n = r.frame().encodedWords(), found = 0, tags[4][2] = {};
        for (uint32_t k = 0; k < n; k += 1 + (w[k] >> 16)) {
            if ((w[k] & 0xFFFF) == NxOp_Marker && (w[k] >> 16) == 3 && w[k + 1] == NX_MARKER_PASS && found < 4) {
                tags[found][0] = w[k + 2]; tags[found][1] = w[k + 3]; found++;
            }
            if ((w[k] & 0xFFFF) == NxOp_End) break;
        }
        t.Check(found == 2 && tags[0][0] == 757 && tags[0][1] == NxFam_Tile && tags[1][0] == 758 &&
                tags[1][1] == NxFam_ImageSpace, "PASS markers: TILE 757 then 758, untagged pass has none", (int)found,
                (int)tags[0][0]);
    }

    static uint8_t img[W * H * 4];
    NxReadback rb;
    memset(&rb, 0, sizeof rb);
    rb.image = menu; rb.w = W; rb.h = H; rb.dst = FNVNX_ADDR_(img); rb.dst_size = sizeof img;
    t.Check(be->Readback(rb), "readback menu");
    auto px = [&](int x, int y) { return img + (y * W + x) * 4; };
    const uint8_t* p = px(4, 90);
    t.Check(p[0] == 64 && p[1] == 128 && p[2] == 191, "menu: background colour clear", p[0], p[2]);
    p = px(40, 60);
    t.Check(p[0] == 255 && p[1] == 0 && p[2] == 0, "menu: opaque quad", p[0], p[2]);
    p = px(100, 12);   // blended over the background: blue 0.5 over (0.25, 0.5, 0.75)
    t.Check(abs(p[0] - u8(0.125f)) <= 1 && abs(p[1] - u8(0.25f)) <= 1 && abs(p[2] - u8(0.5f + 0.5f * 0.75f)) <= 1,
            "menu: blended quad (+-1)", p[0], p[2]);
    p = px(80, 40);    // blended over the red quad
    t.Check(p[0] >= 127 && p[0] <= 128 && p[2] >= 127 && p[2] <= 128, "menu: blended over the opaque quad (+-1)", p[0], p[2]);
    p = px(63, 24); const uint8_t* q = px(64, 24);   // quad edge at x = 0.25 * 128 = 32 .. 96; blend from x = 64
    t.Check(q[2] > p[2], "menu: blend quad starts at x = 64", p[2], q[2]);
    rb.image = back;
    t.Check(be->Readback(rb), "readback back buffer");
    p = px(10, 70); q = px(100, 70);
    t.Check(p[0] == 0 && q[0] == 255, "back buffer: the quad fills only the right-half viewport", p[0], q[0]);
    p = px(63, 70); q = px(64, 70);
    t.Check(p[0] == 0 && q[0] == 255, "back buffer: viewport edge at x = 64", p[0], q[0]);
    p = px(100, 10); q = px(100, 60);
    t.Check(p[1] == 255 && p[0] == 0 && q[0] == 255 && q[1] == 0, "partial ClearBuffer: top-right quarter only", p[1], q[0]);
    p = px(95, 10); q = px(96, 47);
    t.Check(p[0] == 255 && q[1] == 255, "partial ClearBuffer: rectangle edges x = 96, y = 48", p[0], q[1]);
    t.Check(r.frameNumber() == 1 && r.lastStateBinds() >= 2, "one frame, states bound", (int)r.frameNumber(), (int)r.lastStateBinds());

    // frame 2 through the engine-facing override stubs (the engine's `this` is opaque, never dereferenced)
    engine::NiDX9Renderer* eng = (engine::NiDX9Renderer*)(void*)&kGroupKey;
    engine::NiDX9RenderState* ers = (engine::NiDX9RenderState*)(void*)&kGroupKey;
    ers->SetRenderState(RS_ALPHABLENDENABLE, 1, 0, 0);
    t.Check(r.state().GetRenderState(RS_ALPHABLENDENABLE) == 1 && ers->GetRenderState(RS_ALPHABLENDENABLE) == 1,
            "stub: SetRenderState reaches the StateTracker");
    ers->SetAlphaBlendEnable(0, 0, 0);
    t.Check(ers->GetRenderState(RS_ALPHABLENDENABLE) == 0, "stub: SetAlphaBlendEnable");
    ers->SetRenderState(28, 0, 0, 1);    // FOGENABLE 0, locked
    ers->SetRenderState(28, 1, 0, 0);    // ignored while locked
    t.Check(ers->GetRenderState(28) == 0, "stub: a locked render state ignores later sets");
    ers->SetBlendFunc(5, 6, 0, 0);
    t.Check(ers->GetRenderState(19) == 5 && ers->GetRenderState(20) == 6, "stub: SetBlendFunc");
    ers->SetSamplerState(0, 5, 1, 0);    // MAGFILTER POINT
    t.Check(ers->GetSamplerState(0, 5) == 1, "stub: Set/GetSamplerState");
    ers->RestoreSamplerState(0, 5);
    t.Check(ers->GetSamplerState(0, 5) == 1, "stub: RestoreSamplerState returns to the saved default (POINT)",
            (int)ers->GetSamplerState(0, 5));
    ers->SetSamplerState(1, 1, 3, 0);    // ADDRESSU CLAMP by the defaults pass (D3D9's default is WRAP)
    ers->SetSamplerState(1, 1, 1, 0);    // a draw sets WRAP
    ers->RestoreSamplerState(1, 1);
    t.Check(ers->GetSamplerState(1, 1) == 3, "stub: RestoreSamplerState re-sets the engine's saved value, not D3D9's (V1 O2)",
            (int)ers->GetSamplerState(1, 1));
    t.Check(ers->GetTextureStageState(2, 26) == 1 && ers->GetTextureStageState(2, 27) == 1 && ers->GetTextureStageState(2, 28) == 1,
            "stub: COLORARG0 / ALPHAARG0 / RESULTARG default to D3DTA_CURRENT (V1 O3)");
    // slot 48/49 are texture stage states: recorded, never sampler states
    t.Check(ers->GetTextureStageState(0, 1) == 4 && ers->GetTextureStageState(1, 1) == 1,
            "stub: texture stage state defaults (COLOROP MODULATE / DISABLE)");
    ers->SetTextureStageState(0, 5, 3, 0);   // ALPHAARG1 = D3DTA_TFACTOR: type 5 is MAGFILTER as a sampler state
    t.Check(ers->GetTextureStageState(0, 5) == 3 && ers->GetSamplerState(0, 5) == 1,
            "stub: SetTextureStageState does not touch the sampler states");

    // synthetic engine geometry (interop layout of engine_stubs.h) in an arena; embedded pointers are offsets
    static uint8_t arena[2048] __attribute__((aligned(16)));
    memset(arena, 0, sizeof arena);
    engine::SetGuestBase((uintptr_t)arena);
    auto put32 = [&](uint32_t o, uint32_t v) { memcpy(arena + o, &v, 4); };
    auto put16 = [&](uint32_t o, uint16_t v) { memcpy(arena + o, &v, 2); };
    auto putf = [&](uint32_t o, const float* f, int n) { memcpy(arena + o, f, 4 * n); };
    auto geometry = [&](uint32_t geo, uint32_t data, uint32_t verts, float x0, float y0, float x1, float y1, const float* col) {
        const float rot[9] = {1, 0, 0, 0, 1, 0, 0, 0, 1}, one = 1;
        putf(geo + 0x68, rot, 9); putf(geo + 0x68 + 48, &one, 1);
        put32(geo + 0xB8, data);
        put16(data + 0x08, 4); put32(data + 0x20, verts); put32(data + 0x28, verts + 48); put16(data + 0x40, 2);
        const float pos[12] = {x0, y0, 0.5f, x1, y0, 0.5f, x0, y1, 0.5f, x1, y1, 0.5f};
        putf(verts, pos, 12);
        for (int i = 0; i < 4; i++) putf(verts + 48 + 16 * i, col, 4);
    };
    const float magenta[4] = {1, 0, 1, 1}, cyan[4] = {0, 1, 1, 1};
    geometry(64, 512, 640, 0.0f, 0.5f, 0.25f, 0.65f, magenta);                 // NiTriShape: a triangle list
    put32(512 + 0x44, 6); put32(512 + 0x48, 800);
    const uint16_t list[6] = {0, 1, 2, 2, 1, 3};
    memcpy(arena + 800, list, sizeof list);
    geometry(1024, 1280, 1400, 0.25f, 0.75f, 0.5f, 1.0f, cyan);                 // NiTriStrips: one strip
    put16(1280 + 0x44, 1); put32(1280 + 0x48, 1600); put32(1280 + 0x4C, 1616);
    const uint16_t len = 4, strip[4] = {0, 1, 2, 3};
    memcpy(arena + 1600, &len, 2); memcpy(arena + 1616, strip, sizeof strip);
    geometry(1700, 0, 0, 0, 0, 0, 0, cyan);                                    // no model data: skipped
    Renderer::GeomView gv;
    t.Check(engine::ReadGeometry(arena + 64, &gv) && gv.indices && gv.indexCount == 6 && gv.vertices == 4,
            "interop: NiTriShapeData read as a list");
    t.Check(engine::ReadGeometry(arena + 1024, &gv) && !gv.indices && gv.stripCount == 1 && gv.stripLengths[0] == 4,
            "interop: NiTriStripsData read as strips");
    t.Check(eng->Do_BeginFrame(), "stub: BeginFrame");
    r.SetBackgroundColor(0, 0, 1, 1);
    t.Check(eng->Do_BeginUsingRenderTargetGroup(nullptr, Renderer::CLEAR_ALL), "stub: BeginUsingRenderTargetGroup");
    r.SetBackgroundColor(1, 1, 0, 1);
    eng->Do_ClearBuffer(&quarter, Renderer::CLEAR_BACKBUFFER);
    eng->Do_SetScreenSpaceCameraData(nullptr);
    const uint32_t skipped0 = engine::SkippedGeometry(), draws0 = r.geometryDraws();
    eng->Do_RenderGeometry(arena + 64);
    eng->Do_RenderGeometry(arena + 1024);
    eng->Do_RenderGeometry(arena + 1700);
    t.Check(r.geometryDraws() == draws0 + 2 && engine::SkippedGeometry() == skipped0 + 1, "stub: RenderGeometry 2 drawn, 1 skipped");
    t.Check(eng->Do_EndUsingRenderTargetGroup() && eng->Do_EndFrame() && eng->Do_DisplayFrame(), "stub: End + Display");
    t.Check(be->Readback(rb), "readback back buffer (frame 2)");
    p = px(10, 70); q = px(100, 10);
    t.Check(p[2] == 255 && p[0] == 0 && q[0] == 255 && q[1] == 255 && q[2] == 0, "stub frame: clear + partial clear",
            p[2], q[1]);
    p = px(10, 55); q = px(40, 85);
    // tile_vc (TILE1002): the vertex colour's rgb is not used, its alpha is: white texel x white tint
    t.Check(p[0] == 255 && p[1] == 255 && p[2] == 255, "geometry: triangle-list shape (tile_vc: texture x tint)", p[0], p[1]);
    t.Check(q[0] == 255 && q[1] == 255 && q[2] == 255, "geometry: strip shape", q[0], q[1]);
    p = px(10, 63); q = px(31, 55);   // y1 = 0.65 * 96 = 62.4 -> rows 48..62; x1 = 32
    const uint8_t* q2 = px(32, 55);
    t.Check(p[0] == 0 && q[0] == 255 && q2[0] == 0, "geometry: shape edges (row 63 and column 32 outside)", p[0], q2[0]);

    // ---- frame 3: a synthetic menu through the stubs, with engine textures (D3DX overrides -> NativeTexture),
    // TileShaderProperty tint, NiMaterialProperty alpha and NiAlphaProperty blend / alpha test -------------------
    {
        static uint8_t eng3[4096] __attribute__((aligned(16)));
        memset(eng3, 0, sizeof eng3);
        uintptr_t a0 = (uintptr_t)eng3, a1 = (uintptr_t)engine::NativeTexturePool();
        engine::SetGuestBase((a0 < a1 ? a0 : a1) - 64);   // below both: no object sits at guest address 0
        auto ga = [&](const void* q) { return engine::GuestAddress(q); };
        auto w32 = [&](uint32_t o, uint32_t v) { memcpy(eng3 + o, &v, 4); };
        auto w16 = [&](uint32_t o, uint16_t v) { memcpy(eng3 + o, &v, 2); };
        auto wf = [&](uint32_t o, const float* f, int n) { memcpy(eng3 + o, f, 4 * n); };
        // DDS files (A8R8G8B8): a 2x2 red/blue checker and a 2x2 with alpha 255 left, 0 right (white)
        static uint8_t dds[2][128 + 16];
        static const uint32_t texels[2][4] = {{0xFFFF0000u, 0xFF0000FFu, 0xFF0000FFu, 0xFFFF0000u},
                                              {0xFFFFFFFFu, 0x00FFFFFFu, 0xFFFFFFFFu, 0x00FFFFFFu}};
        for (int i = 0; i < 2; i++) {
            uint8_t* f = dds[i];
            memset(f, 0, 128);
            const uint32_t hdr[] = {0x20534444u, 124, 0x1007, 2, 2, 8, 0, 1};
            memcpy(f, hdr, sizeof hdr);
            const uint32_t pf[] = {32, 0x41, 0, 32, 0xFF0000u, 0xFF00u, 0xFFu, 0xFF000000u, 0x1000};
            memcpy(f + 76, pf, sizeof pf);
            memcpy(f + 128, texels[i], 16);
        }
        uint32_t info[7] = {};
        t.Check(engine::D3DXGetImageInfoFromFileInMemory(dds[0], sizeof dds[0], info) == 0 && info[0] == 2 && info[1] == 2 &&
                info[4] == 21 && info[5] == 3 && info[6] == 4, "D3DX image info from the DDS header");
        void* tx[2] = {nullptr, nullptr};
        t.Check(engine::D3DXCreateTextureFromFileInMemory(nullptr, dds[0], sizeof dds[0], &tx[0]) == 0 &&
                engine::D3DXCreateTextureFromFileInMemory(nullptr, dds[1], sizeof dds[1], &tx[1]) == 0 && tx[0] && tx[1],
                "D3DX create texture -> NativeTexture");
        // the IDirect3DTexture9 methods the engine may call, through the method table (slot 13 GetLevelCount,
        // 17 GetLevelDesc, 1 AddRef, 2 Release)
        engine::NativeTexture* nt = engine::NativeTextureFromPointer(tx[0]);
        typedef uint32_t (*Fn0)(void*);
        typedef int32_t (*FnDesc)(void*, uint32_t, uint32_t*);
        uint32_t desc[8] = {};
        t.Check(nt && ((Fn0)nt->vtbl[13])(tx[0]) == 1 && ((FnDesc)nt->vtbl[17])(tx[0], 0, desc) == 0 && desc[0] == 21 &&
                desc[6] == 2 && desc[7] == 2 && ((Fn0)nt->vtbl[1])(tx[0]) == 2 && ((Fn0)nt->vtbl[2])(tx[0]) == 1,
                "NativeTexture method table: GetLevelCount, GetLevelDesc, AddRef, Release");
        // NiTexture (+0x24 renderer data) and renderer data holding the texture pointer at +0x10
        const uint32_t kTex0 = 3200, kRd0 = 3264, kTex1 = 3328, kRd1 = 3392;
        w32(kTex0 + 0x24, ga(eng3 + kRd0)); w32(kRd0 + 0x10, ga(tx[0]));
        w32(kTex1 + 0x24, ga(eng3 + kRd1)); w32(kRd1 + 0x10, ga(tx[1]));
        auto tile = [&](uint32_t geo, uint32_t data, uint32_t verts, float x0, float y0, float x1, float y1,
                        uint32_t shp, uint32_t tex, const float tint[4], uint32_t mat, float matAlpha,
                        uint32_t alp, uint16_t alphaFlags, uint8_t ref) {
            const float rot[9] = {1, 0, 0, 0, 1, 0, 0, 0, 1}, one = 1;
            wf(geo + 0x68, rot, 9); wf(geo + 0x68 + 48, &one, 1);
            w32(geo + 0xB8, ga(eng3 + data));
            w16(data + 0x08, 4); w32(data + 0x20, ga(eng3 + verts)); w32(data + 0x2C, ga(eng3 + verts + 48));
            w16(data + 0x40, 2); w32(data + 0x44, 6); w32(data + 0x48, ga(eng3 + verts + 80));
            const float pos[12] = {x0, y0, 0.5f, x1, y0, 0.5f, x0, y1, 0.5f, x1, y1, 0.5f};
            const float uv[8] = {0, 0, 1, 0, 0, 1, 1, 1};
            wf(verts, pos, 12); wf(verts + 48, uv, 8);
            const uint16_t list[6] = {0, 1, 2, 2, 1, 3};
            memcpy(eng3 + verts + 80, list, sizeof list);
            w32(geo + 0xA8, ga(eng3 + shp)); w32(shp + 0x60, tex ? ga(eng3 + tex) : 0);
            wf(shp + 0x68, tint, 3); wf(shp + 0x78, &tint[3], 1);
            if (mat) { w32(geo + 0xA4, ga(eng3 + mat)); wf(mat + 0x3C, &matAlpha, 1); }
            if (alp) { w32(geo + 0x9C, ga(eng3 + alp)); w16(alp + 0x18, alphaFlags); eng3[alp + 0x1A] = ref; }
        };
        const float white[4] = {1, 1, 1, 1}, green[4] = {0, 1, 0, 1};
        tile(0, 256, 384, 0.0f, 0.0f, 1.0f, 1.0f, 512, kTex0, white, 0, 1, 0, 0, 0);            // background: checker
        tile(704, 960, 1088, 0.25f, 0.25f, 0.5f, 0.5f, 1216, 0, green, 1376, 0.5f,             // button: green, alpha
             1440, (uint16_t)(1 | 6 << 1 | 7 << 5), 0);                                           //  0.5, SRCALPHA blend
        tile(1536, 1792, 1920, 0.5f, 0.75f, 1.0f, 1.0f, 2048, kTex1, white, 0, 1,                // text: alpha test
             2240, (uint16_t)(1 << 9 | 4 << 10), 128);                                            //  GREATER 128
        Renderer::GeomView gv3;
        t.Check(engine::ReadGeometry(eng3 + 0, &gv3) && gv3.image == engine::NativeTextureFromPointer(tx[0])->image,
                "interop: the tile's NiTexture -> renderer data -> NativeTexture");
        t.Check(engine::ReadGeometry(eng3 + 704, &gv3) && gv3.hasAlpha && fabsf(gv3.tint[3] - 0.5f) < 1e-6f && gv3.tint[1] == 1,
                "interop: tint and material alpha, alpha property");
        r.state().SetTexture(0, 0, kind);
        ers->SetTexture(0, tx[1]);
        t.Check(ers->GetTexture(0) == tx[1] && r.state().Texture(0) == engine::NativeTextureFromPointer(tx[1])->image,
                "stub: SetTexture binds the native image");
        ers->ClearTextureUsage(tx[1]);
        t.Check(ers->GetTexture(0) == nullptr && r.state().Texture(0) == 0, "stub: ClearTextureUsage");
        Samp_point(r);
        t.Check(eng->Do_BeginFrame(), "frame 3: BeginFrame");
        r.SetBackgroundColor(0, 0, 0, 1);
        t.Check(eng->Do_BeginUsingRenderTargetGroup(nullptr, Renderer::CLEAR_ALL), "frame 3: BeginUsing");
        eng->Do_SetScreenSpaceCameraData(nullptr);
        r.SetEnginePass(757, NxFam_Tile);
        eng->Do_RenderGeometry(eng3 + 0);
        const uint16_t psBg = r.lastPs();
        eng->Do_RenderGeometry(eng3 + 704);
        eng->Do_RenderGeometry(eng3 + 1536);
        const uint16_t psText = r.lastPs();
        r.SetEnginePass(0, NxFam_None);
        t.Check(psBg != 0 && psText != 0 && psBg != psText, "epilogue permutations: no-epilogue tile vs alpha-tested tile", psBg, psText);
        t.Check(eng->Do_EndUsingRenderTargetGroup() && eng->Do_EndFrame(), "frame 3: End");
        t.Check(be->Readback(rb), "frame 3: readback");
        p = px(16, 12); q = px(80, 12);   // checker 2x2 over 128x96: texel (0,0) red, (1,0) blue
        t.Check(p[0] == 255 && p[2] == 0 && q[0] == 0 && q[2] == 255, "menu: background texture (checker)", p[0], q[2]);
        p = px(40, 30);                   // button over the red texel: 0.5 x green + 0.5 x red
        t.Check(abs(p[0] - 128) <= 1 && abs(p[1] - 128) <= 1 && p[2] == 0, "menu: button tint x material alpha blended", p[0], p[1]);
        p = px(80, 84); q = px(112, 84);  // text tile: left texel alpha 255 (white), right alpha 0 (discarded: red)
        t.Check(p[0] == 255 && p[1] == 255 && p[2] == 255 && q[0] == 255 && q[1] == 0 && q[2] == 0,
                "menu: alpha-tested text tile", p[1], q[1]);
        t.Check(r.state().GetRenderState(27) == 0 && r.state().GetRenderState(15) == 0, "alpha states restored after the draw");

        // ---- menu background image-space effect over frame 1's menu target: tone 1x1 (200, 100, 50), blend 0.5
        static const uint32_t toneTexel = 0xFFC86432u;   // ARGB
        NxImageDesc td2;
        memset(&td2, 0, sizeof td2);
        td2.width = 1; td2.height = 1; td2.format = NxFmt_BGRA8; td2.data = FNVNX_ADDR_(&toneTexel); td2.data_size = 4;
        uint32_t tone = be->ImageCreate(td2);
        const float luma[3] = {0.299f, 0.587f, 0.114f};
        t.Check(eng->Do_BeginFrame() && eng->Do_BeginUsingRenderTargetGroup(nullptr, Renderer::CLEAR_ALL), "frame 4: begin");
        t.Check(r.RenderMenuBackground(menu, 21, W, H, tone, 21, luma, 0.5f, 1.0f), "RenderMenuBackground");
        t.Check(eng->Do_EndUsingRenderTargetGroup() && eng->Do_EndFrame(), "frame 4: end");
        t.Check(be->Readback(rb), "frame 4: readback");
        p = px(4, 90);   // menu background colour (64, 128, 191) mixed half with the tone
        t.Check(abs(p[0] - 132) <= 2 && abs(p[1] - 114) <= 2 && abs(p[2] - 121) <= 2, "menubg: scene x tone mix", p[0], p[2]);
        p = px(40, 60);  // the menu's opaque red quad: (255, 0, 0) -> (228, 50, 25)
        t.Check(abs(p[0] - 228) <= 2 && abs(p[1] - 50) <= 2 && abs(p[2] - 25) <= 2, "menubg: second sample", p[0], p[1]);

        // ---- frame 5: the image-space dispatch (ImageSpaceManager::RenderEffect) for EFFECT_MENUBG, with its tone
        // texture made through the lockable CreateSurf path and filled like the engine's copy (GetSurfaceLevel,
        // GetDesc, LockRect, UnlockRect through the method tables)
        const uint32_t kMgr = 3456, kList = 3472, kFx = 3536, kIst = 3632, kNiTex = 3648, kSrcData = 3712, kPix = 3840, kDims = 3952;
        memset(eng3 + kMgr, 0, 4096 - kMgr);
        w32(kDims, 1); w32(kDims + 4, 1);
        w32(kPix + 0x54, ga(eng3 + kDims)); w32(kPix + 0x58, ga(eng3 + kDims + 4)); w32(kPix + 0x60, 1);
        w32(kSrcData + 0x20, 21);   // renderer pixel format hint: D3DFMT_A8R8G8B8
        engine::NiDX9SourceTextureData* sd = (engine::NiDX9SourceTextureData*)(void*)(eng3 + kSrcData);
        t.Check(sd->CreateSurf(eng3 + kPix), "CreateSurf -> lockable NativeTexture");
        uint32_t tAddr; memcpy(&tAddr, eng3 + kSrcData + 0x64, 4);
        void* lt = (void*)(engine::GuestBase() + tAddr);
        engine::NativeTexture* ltn = engine::NativeTextureFromPointer(lt);
        typedef int32_t (*FnSurf)(void*, uint32_t, void**);
        typedef int32_t (*FnDesc1)(void*, uint32_t*);
        typedef int32_t (*FnLock)(void*, uint32_t*, const void*, uint32_t);
        typedef int32_t (*FnUnlock)(void*);
        void* surf = nullptr;
        uint32_t sdesc[8] = {}, locked[2] = {};
        bool filled = ltn && ((FnSurf)ltn->vtbl[18])(lt, 0, &surf) == 0 && surf;
        if (filled) {
            const uintptr_t* sv = *(const uintptr_t* const*)surf;
            filled = ((FnDesc1)sv[12])(surf, sdesc) == 0 && sdesc[0] == 21 && sdesc[6] == 1 &&
                     ((FnLock)sv[13])(surf, locked, nullptr, 0) == 0 && locked[0] == 4;
            if (filled) {
                static const uint8_t bgra[4] = {50, 100, 200, 255};
                memcpy((void*)(engine::GuestBase() + locked[1]), bgra, 4);
                filled = ((FnUnlock)sv[14])(surf) == 0 && ltn->dirty;
            }
            ((Fn0)sv[2])(surf);
        }
        t.Check(filled, "lockable texture: GetSurfaceLevel, GetDesc, LockRect, UnlockRect");
        w32(kNiTex + 0x24, ga(eng3 + kSrcData));          // NiTexture -> renderer data (texture at +0x64)
        w32(kIst + 4, ga(eng3 + kNiTex));                  // ImageSpaceTexture -> NiTexture
        w32(kFx + 0x58, ga(eng3 + kIst)); const float half = 0.5f; wf(kFx + 0x5C, &half, 1);
        w32(kList + 4 * 15, ga(eng3 + kFx)); w32(kList + 4 * 3, ga(eng3 + kFx));
        w32(kMgr + 0x08, ga(eng3 + kList));
        static const int kSceneKey = 0;   // stands for the BSRenderedTexture holding the scene
        r.RegisterTargetGroup(&kSceneKey, menu, 0, W, H);
        engine::ImageSpaceManager* ism = (engine::ImageSpaceManager*)(void*)(eng3 + kMgr);
        const uint32_t nat0 = engine::ImageSpaceEffectsNative(), skip0 = engine::ImageSpaceEffectsSkipped();
        t.Check(eng->Do_BeginFrame() && eng->Do_BeginUsingRenderTargetGroup(nullptr, Renderer::CLEAR_ALL), "frame 5: begin");
        ism->RenderEffect(15, nullptr, (void*)&kSceneKey, nullptr, nullptr, false);
        ism->RenderEffect(3, nullptr, (void*)&kSceneKey, nullptr, nullptr, false);    // not native yet: skipped
        t.Check(eng->Do_EndUsingRenderTargetGroup() && eng->Do_EndFrame(), "frame 5: end");
        t.Check(engine::ImageSpaceEffectsNative() == nat0 + 1 && engine::ImageSpaceEffectsSkipped() == skip0 + 1,
                "RenderEffect: MENUBG native, others counted");
        t.Check(be->Readback(rb), "frame 5: readback");
        p = px(4, 90);
        t.Check(abs(p[0] - 132) <= 2 && abs(p[1] - 114) <= 2 && abs(p[2] - 121) <= 2 && !ltn->dirty,
                "RenderEffect(MENUBG) == the menubg mix, tone from the locked texture", p[0], p[2]);

        // ---- frame 6: stencil (NiStencilProperty), texture scroll and the tile scissor rectangle
        memset(eng3, 0, 3200);
        const float red[4] = {1, 0, 0, 1};
        tile(0, 256, 384, 0.0f, 0.0f, 0.5f, 1.0f, 512, 0, white, 0, 1, 0, 0, 0);          // stencil writer, left half
        w32(0 + 0xAC, ga(eng3 + 700)); w16(700 + 0x18, (uint16_t)(1 | 2 << 7 | 7 << 12)); w32(700 + 0x1C, 1); w32(700 + 0x20, 0xFF);
        tile(768, 1024, 1152, 0.0f, 0.0f, 1.0f, 1.0f, 1280, 0, green, 0, 1, 0, 0, 0);     // green where stencil == 1
        w32(768 + 0xAC, ga(eng3 + 1468)); w16(1468 + 0x18, (uint16_t)(1 | 2 << 12)); w32(1468 + 0x1C, 1); w32(1468 + 0x20, 0xFF);
        tile(1536, 1792, 1920, 0.5f, 0.0f, 1.0f, 0.5f, 2048, kTex0, white, 0, 1, 0, 0, 0); // checker scrolled by u + 0.5
        eng3[2048 + 0x91] = 1; const float sc2[4] = {0.5f, 0.0f, 1.0f, 1.0f}; wf(2048 + 0x7C, sc2, 4);
        tile(2304, 2560, 2688, 0.0f, 0.0f, 1.0f, 1.0f, 2816, 0, red, 0, 1, 0, 0, 0);      // red, scissored
        eng3[2816 + 0xAC] = 1; const int32_t rect[4] = {100, 60, 110, 70}; memcpy(eng3 + 2816 + 0x9C, rect, 16);
        t.Check(eng->Do_BeginFrame() && eng->Do_BeginUsingRenderTargetGroup(nullptr, Renderer::CLEAR_ALL), "frame 6: begin");
        eng->Do_SetScreenSpaceCameraData(nullptr);
        r.SetBackgroundColor(0, 0, 0, 1);
        eng->Do_RenderGeometry(eng3 + 0);
        eng->Do_RenderGeometry(eng3 + 768);
        eng->Do_RenderGeometry(eng3 + 1536);
        eng->Do_RenderGeometry(eng3 + 2304);
        t.Check(eng->Do_EndUsingRenderTargetGroup() && eng->Do_EndFrame(), "frame 6: end");
        t.Check(be->Readback(rb), "frame 6: readback");
        p = px(20, 80); q = px(100, 80);
        t.Check(p[1] == 255 && p[0] == 0 && q[1] == 0, "stencil: REPLACE then EQUAL keeps green to the left half", p[1], q[1]);
        p = px(70, 10); q = px(120, 10);
        t.Check(p[2] == 255 && p[0] == 0 && q[2] == 255, "texture scroll: u + 0.5 shows texel column 1", p[2], q[2]);
        p = px(105, 65); q = px(99, 65);
        const uint8_t* q3 = px(110, 65);
        t.Check(p[0] == 255 && p[1] == 0 && q[0] == 0 && q3[0] == 0, "tile scissor: only (100, 60)-(110, 70)", p[0], q[0]);
        t.Check(r.state().GetRenderState(52) == 0 && r.state().GetRenderState(174) == 0, "stencil and scissor restored");
    }
    // ---- lit permutations: 1 directional + 2 point lights through lit_spec, static (lit_spec_d1p2) vs dynamic loop
    {
        struct LV { float x, y, z, u, v; uint32_t rgba; float nx, ny, nz; };
        NxVtxLayout ll;
        memset(&ll, 0, sizeof ll);
        ll.count = 4; ll.stride = 36;
        ll.attr[0].offset = 0; ll.attr[0].type = NxAttr_Float32; ll.attr[0].components = 3;
        ll.attr[1].offset = 12; ll.attr[1].type = NxAttr_Float32; ll.attr[1].components = 2;
        ll.attr[2].offset = 20; ll.attr[2].type = NxAttr_Unorm8; ll.attr[2].components = 4;
        ll.attr[3].offset = 24; ll.attr[3].type = NxAttr_Float32; ll.attr[3].components = 3;
        uint16_t lay = r.cache().Layout(ll), lvs = shader(be, "lit", 0), lps = shader(be, "lit_spec", 1);
        uint32_t off;
        LV* lv = (LV*)vb.Alloc(4 * sizeof(LV), 16, &off);
        const float q[4][2] = {{0, 0}, {W, 0}, {0, H}, {W, H}};
        for (int i = 0; i < 4; i++) { lv[i] = LV{q[i][0], q[i][1], 0, 0, 0, 0xFFFFFFFFu, 0, 0, -1}; }
        static NxLitDraw u;
        memset(&u, 0, sizeof u);
        u.wvp[0] = 2.0f / W; u.wvp[5] = -2.0f / H; u.wvp[10] = 0.5f; u.wvp[12] = -1; u.wvp[13] = 1; u.wvp[14] = 0.5f; u.wvp[15] = 1;
        u.world[0] = u.world[5] = u.world[10] = u.world[15] = 1;
        u.ambient[0] = u.ambient[1] = u.ambient[2] = 0.1f;
        u.diffuse[0] = u.diffuse[1] = u.diffuse[2] = 0.8f; u.diffuse[3] = 1;
        u.specular[0] = u.specular[1] = u.specular[2] = 0.5f; u.specular[3] = 8;
        u.eye[0] = 64; u.eye[1] = 48; u.eye[2] = -100;
        u.lightCount = 3;   // a point light first, the directional second: the permutation reorders
        const float P0[4] = {30, 40, -20, 80}, C0[4] = {1, 0.2f, 0.1f, 0}, D1[4] = {0, 0, 1, 0}, C1[4] = {0, 0.25f, 0.5f, 1},
                    P2[4] = {100, 60, -30, 70}, C2[4] = {0.2f, 0.9f, 0.3f, 0};
        memcpy(u.lights[0].posRange, P0, 16); memcpy(u.lights[0].color, C0, 16);
        memcpy(u.lights[1].posRange, D1, 16); memcpy(u.lights[1].color, C1, 16);
        memcpy(u.lights[2].posRange, P2, 16); memcpy(u.lights[2].color, C2, 16);
        static uint8_t imgs[2][W * H * 4];
        uint16_t used[2];
        for (int pass2 = 0; pass2 < 2; pass2++) {
            r.SetPermutations(pass2 == 0);
            r.state().Reset();
            r.state().SetTexture(0, tex, kind);
            t.Check(r.Do_BeginFrame() && r.Do_BeginUsingRenderTargetGroup(nullptr, Renderer::CLEAR_ALL), "lit frame: begin");
            Renderer::Draw d;
            memset(&d, 0, sizeof d);
            d.vs = lvs; d.ps = lps; d.layout = lay; d.prim = NxPrim_TriStrip; d.vtxBlock = vb.block(); d.vtxOffset = off;
            d.vtxSize = 4 * sizeof(LV); d.count = 4; d.textures = 1; d.ubo = &u; d.uboSize = sizeof u;
            t.Check(r.Render(d), "lit draw");
            used[pass2] = r.lastPs();
            t.Check(r.Do_EndUsingRenderTargetGroup() && r.Do_EndFrame(), "lit frame: end");
            rb.image = back; rb.dst = FNVNX_ADDR_(imgs[pass2]);
            t.Check(be->Readback(rb), "lit readback");
        }
        r.SetPermutations(true);
        rb.dst = FNVNX_ADDR_(img);
        int worst = 0;
        for (int i = 0; i < W * H * 4; i++) { int dd = abs((int)imgs[0][i] - (int)imgs[1][i]); if (dd > worst) worst = dd; }
        t.Check(used[0] != used[1] && worst <= 1, "lit_spec_d1p2__e0 (static, reordered) == dynamic lit_spec within 1/255", worst, (int)used[0]);
    }
    *checks = t.checks;
    return t.fails;
}
