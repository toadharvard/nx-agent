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
    t.Check(quad(0.25f, 0.25f, 0.75f, 0.75f, red, 0), "render opaque quad");
    r.state().SetRenderState(RS_ALPHABLENDENABLE, 1);
    r.state().SetRenderState(RS_SRCBLEND, 5); r.state().SetRenderState(RS_DESTBLEND, 6);
    t.Check(quad(0.5f, 0.0f, 1.0f, 0.5f, half, 1), "render blended quad");
    r.state().SetRenderState(RS_ALPHABLENDENABLE, 0);
    t.Check(r.Do_EndUsingRenderTargetGroup(), "EndUsingRenderTargetGroup");
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
    ers->SetRenderState(RS_ALPHABLENDENABLE, 1, true);
    t.Check(r.state().GetRenderState(RS_ALPHABLENDENABLE) == 1 && ers->GetRenderState(RS_ALPHABLENDENABLE) == 1,
            "stub: SetRenderState reaches the StateTracker");
    ers->RestoreRenderState(RS_ALPHABLENDENABLE);
    t.Check(ers->GetRenderState(RS_ALPHABLENDENABLE) == 0, "stub: RestoreRenderState");
    t.Check(eng->Do_BeginFrame(), "stub: BeginFrame");
    r.SetBackgroundColor(0, 0, 1, 1);
    t.Check(eng->Do_BeginUsingRenderTargetGroup(nullptr, Renderer::CLEAR_ALL), "stub: BeginUsingRenderTargetGroup");
    r.SetBackgroundColor(1, 1, 0, 1);
    eng->Do_ClearBuffer(&quarter, Renderer::CLEAR_BACKBUFFER);
    t.Check(eng->Do_EndUsingRenderTargetGroup() && eng->Do_EndFrame() && eng->Do_DisplayFrame(), "stub: End + Display");
    t.Check(be->Readback(rb), "readback back buffer (frame 2)");
    p = px(10, 70); q = px(100, 10);
    t.Check(p[2] == 255 && p[0] == 0 && q[0] == 255 && q[1] == 255 && q[2] == 0, "stub frame: clear + partial clear",
            p[2], q[1]);
    *checks = t.checks;
    return t.fails;
}
