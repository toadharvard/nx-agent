// fnv-nx native renderer, N1/N2: the engine-path world frame of the self-test (selftest.h). Synthetic engine objects
// in the interop layout of engine_stubs.h, one quad per engine pass, each drawn through the Do_RenderShape stub with
// the engine's current-pass globals pointing at its BSShaderProperty::RenderPass; the frame's DrawStats are the
// metric "engine draws skipped or misrendered per frame".
#include "selftest.h"
#include "engine_stubs.h"
#include "nx/families.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

namespace {
using namespace nx;
enum { W = 128, H = 96, kCell = 16, kCols = 8 };

struct C {
    char* log; uint32_t cap; uint32_t* at; int checks, fails;
    void Check(bool ok, const char* what, int a = 0, int b = 0) {
        checks++;
        if (ok) return;
        fails++;
        if (*at < cap) *at += (uint32_t)snprintf(log + *at, cap - *at, "  FAIL %s (%d, %d)\n", what, a, b);
    }
    void Info(const char* s) { if (*at < cap) *at += (uint32_t)snprintf(log + *at, cap - *at, "%s", s); }
};

// guest-address arena (embedded pointers are 32-bit offsets from engine::GuestBase())
struct Arena {
    uint8_t* a; uint32_t size, top;
    uint32_t Alloc(uint32_t n) { uint32_t o = (top + 15) & ~15u; top = o + n; return top <= size ? o : 0; }
    uint32_t G(uint32_t o) const { return engine::GuestAddress(a + o); }
    void P32(uint32_t o, uint32_t v) { memcpy(a + o, &v, 4); }
    void P16(uint32_t o, uint16_t v) { memcpy(a + o, &v, 2); }
    void PF(uint32_t o, const float* f, int n) { memcpy(a + o, f, 4 * n); }
};

// one engine draw: a quad over a cell of the target
struct Cell {
    uint16_t pass;                      // engine pass id (0: no RenderPass record)
    const char* what;
    uint32_t flags;                     // Q_* below
};
enum { Q_VC = 1, Q_NBT = 2, Q_SKIN = 4, Q_LIGHT = 8, Q_TEX = 16, Q_NOLIGHTPROP = 32, Q_TILEPROP = 64 };

const Cell kCells[] = {
    {383, "3XLIGHTING", Q_NBT | Q_LIGHT | Q_TEX},
    {398, "3XLIGHTING_Spc", Q_NBT | Q_LIGHT | Q_TEX},
    {392, "3XLIGHTING_Vc", Q_NBT | Q_LIGHT | Q_TEX | Q_VC},
    {147, "LANDAD", Q_NBT | Q_LIGHT | Q_TEX | Q_VC},
    {569, "SKY", Q_VC},
    {573, "SKY_CLOUDS", Q_VC | Q_TEX},
    {16, "DEPTHMAP", 0},
    {1, "ZONLY", 0},
    {2, "ZONLY_At", Q_TEX},
    {66, "NOLIGHTING", Q_TEX | Q_NOLIGHTPROP},
    {67, "NOLIGHTING_VC", Q_VC | Q_NOLIGHTPROP},
    {757, "TILE", Q_TEX | Q_TILEPROP},
    {0, "(no pass: menu path)", Q_TEX | Q_TILEPROP},
    {384, "3XLIGHTING_S (skinned)", Q_NBT | Q_LIGHT | Q_SKIN},
    // passes with no native family: the translated route
    {22, "GRASS_NOALPHA_DIRONLY_LF", Q_TEX | Q_VC},
    {62, "LEAVES_AD", Q_TEX | Q_NBT},
    {64, "FRONDS", Q_TEX | Q_NBT},
    {566, "DISTANT_TREE_BASE", Q_TEX},
    {648, "WATER_STENCIL", Q_NBT},
    {575, "WATER_LOD", Q_TEX},
    {588, "GEOMDECAL", Q_TEX | Q_NBT},
    {719, "PRECIPITATION_RAIN", Q_TEX},
    {560, "LANDLO_A (LOD)", Q_TEX | Q_NBT},
    {386, "3XLIGHTING_Px", Q_NBT | Q_LIGHT | Q_TEX},
    {503, "LAND1O", Q_NBT | Q_TEX | Q_VC},
    {149, "ADTS", Q_NBT | Q_LIGHT | Q_TEX},
    {82, "NOLIGHTING_PSYS", Q_TEX | Q_VC},
    {9, "AMBIENT_OCCLUSION", 0},
};
const int kNumCells = (int)(sizeof kCells / sizeof kCells[0]);
const int kEffects = 3;   // image-space effects of the frame (HDR, blur, cinematic stand-ins)

struct World {
    Arena ar;
    uint32_t geo[32], rp[32];
    uint32_t imgMgr, effectList;
    void* tex[2];
};

void Build(World& w, C& c) {
    Arena& A = w.ar;
    // textures: a white 2x2 (diffuse; alpha 1) and a flat normal map (0.5, 0.5, 1) through the D3DX override
    static uint8_t dds[2][128 + 16];
    static const uint32_t texels[2][4] = {{0xFFFFFFFFu, 0xFFFFFFFFu, 0xFFFFFFFFu, 0xFFFFFFFFu},
                                          {0xFF8080FFu, 0xFF8080FFu, 0xFF8080FFu, 0xFF8080FFu}};
    for (int i = 0; i < 2; i++) {
        uint8_t* f = dds[i];
        memset(f, 0, 128);
        const uint32_t hdr[] = {0x20534444u, 124, 0x1007, 2, 2, 8, 0, 1};
        memcpy(f, hdr, sizeof hdr);
        const uint32_t pf[] = {32, 0x41, 0, 32, 0xFF0000u, 0xFF00u, 0xFFu, 0xFF000000u, 0x1000};
        memcpy(f + 76, pf, sizeof pf);
        memcpy(f + 128, texels[i], 16);
        w.tex[i] = nullptr;
        c.Check(engine::D3DXCreateTextureFromFileInMemory(nullptr, f, sizeof dds[i], &w.tex[i]) == 0 && w.tex[i],
                "world: texture");
    }
    uint32_t niTex[2];
    for (int i = 0; i < 2; i++) {   // NiTexture (+0x24 renderer data) -> renderer data holding the NativeTexture
        niTex[i] = A.Alloc(0x40); const uint32_t rd = A.Alloc(0x40);
        A.P32(niTex[i] + 0x24, A.G(rd)); A.P32(rd + 0x10, engine::GuestAddress(w.tex[i]));
    }
    // lights: the sun (directional, +z: into the screen) and a point light; ShadowSceneLight +0xF8 -> NiLight
    uint32_t ssl[2], lightList = A.Alloc(8);
    for (int i = 0; i < 2; i++) {
        ssl[i] = A.Alloc(0x100);
        const uint32_t nl = A.Alloc(0x100);
        A.P32(ssl[i] + 0xF8, A.G(nl));
        A.a[nl + 0x9D] = i == 0 ? 2 : 1;                       // NiDynamicEffect type: DIR_LIGHT 2, POINT_LIGHT 1
        const float one = 1, amb[3] = {0.1f, 0.1f, 0.1f};
        const float diff[2][3] = {{1.0f, 0.5f, 0.25f}, {0.0f, 0.0f, 0.0f}};
        A.PF(nl + 0xC4, &one, 1); A.PF(nl + 0xC8, amb, 3); A.PF(nl + 0xD4, diff[i], 3);
        const float dir[3] = {0, 0, 1}, pos[3] = {0.5f, 0.5f, -1.0f}, radius = 0.01f;
        if (i == 0) A.PF(nl + 0xF0, dir, 3);                      // NiDirectionalLight world direction
        else { A.PF(nl + 0x8C, pos, 3); A.PF(nl + 0xF0, &radius, 1); }   // world translate; radius (zero colour)
        A.P32(lightList + 4 * i, A.G(ssl[i]));
    }
    for (int i = 0; i < kNumCells; i++) {
        const Cell& k = kCells[i];
        const float x0 = (float)((i % kCols) * kCell) / W, y0 = (float)((i / kCols) * kCell) / H;
        const float x1 = x0 + (float)kCell / W, y1 = y0 + (float)kCell / H;
        const uint32_t g = A.Alloc(0xC8), d = A.Alloc(0x50), v = A.Alloc(4 * 12), n = A.Alloc(3 * 4 * 12),
                       col = A.Alloc(4 * 16), uv = A.Alloc(4 * 8), ix = A.Alloc(12);
        w.geo[i] = g;
        const float rot[9] = {1, 0, 0, 0, 1, 0, 0, 0, 1}, one = 1;
        A.PF(g + 0x68, rot, 9); A.PF(g + 0x68 + 48, &one, 1);
        A.P32(g + 0xB8, A.G(d));
        const float pos[12] = {x0, y0, 0.5f, x1, y0, 0.5f, x0, y1, 0.5f, x1, y1, 0.5f};
        A.PF(v, pos, 12);
        // normals (towards the viewer, -z), binormals (+y), tangents (+x): NiGeometryData NBT order
        for (int q = 0; q < 4; q++) {
            const float nn[3] = {0, 0, -1}, bb[3] = {0, 1, 0}, tt[3] = {1, 0, 0};
            A.PF(n + 12 * q, nn, 3); A.PF(n + 48 + 12 * q, bb, 3); A.PF(n + 96 + 12 * q, tt, 3);
            const float vc[4] = {1.0f, 0.0f, 0.0f, 1.0f};        // sky: all weight on blend colour 0
            A.PF(col + 16 * q, vc, 4);
        }
        const float uvs[8] = {0, 0, 1, 0, 0, 1, 1, 1};
        A.PF(uv, uvs, 8);
        const uint16_t list[6] = {0, 1, 2, 2, 1, 3};
        memcpy(A.a + ix, list, 12);
        A.P16(d + 0x08, 4);
        A.P16(d + 0x0C, (uint16_t)(1 | ((k.flags & Q_NBT) ? 0x1000 : 0)));   // one uv set (+ NBT)
        A.P32(d + 0x20, A.G(v));
        A.P32(d + 0x24, A.G(n));
        if (k.flags & Q_VC) A.P32(d + 0x28, A.G(col));
        A.P32(d + 0x2C, A.G(uv));
        A.P16(d + 0x40, 2); A.P32(d + 0x44, 6); A.P32(d + 0x48, A.G(ix));
        if (k.flags & Q_SKIN) A.P32(g + 0xBC, A.G(A.Alloc(0x40)));
        // the shader property (+0xA8)
        const uint32_t sp = A.Alloc(0x110);
        A.P32(g + 0xA8, A.G(sp));
        const float alpha = 1.0f;
        A.PF(sp + 0x28, &alpha, 1);                               // BSShaderProperty alpha
        if (k.flags & Q_TILEPROP) {                               // TileShaderProperty: texture +0x60, tint +0x68/+0x78
            const float tint[3] = {1, 1, 1};
            A.P32(sp + 0x60, A.G(niTex[0])); A.PF(sp + 0x68, tint, 3); A.PF(sp + 0x78, &alpha, 1);
        } else if (k.flags & Q_NOLIGHTPROP) {                     // BSShaderNoLightingProperty: texture +0x68
            const float emit[4] = {1, 1, 1, 1};
            A.P32(sp + 0x68, (k.flags & Q_TEX) ? A.G(niTex[0]) : 0); A.PF(sp + 0x78, emit, 4);
        } else {                                                  // BSShaderPPLightingProperty: texture slots +0xB4
            const uint32_t s0 = A.Alloc(4), s1 = A.Alloc(4);
            A.P32(s0, A.G(niTex[0])); A.P32(s1, A.G(niTex[1]));
            if (k.flags & Q_TEX) { A.P32(sp + 0xB4, A.G(s0)); A.P32(sp + 0xB8, A.G(s1)); }
            const int32_t layers = 1; memcpy(A.a + sp + 0xF0, &layers, 4);
            const float spec[4] = {1, 1, 1, 1}, gloss = 8;
            A.PF(sp + 0x9C, spec, 4); A.PF(sp + 0x88, &gloss, 1);
        }
        // BSShaderProperty::RenderPass {geometry, pass, light index, ..., light count +9, lights +0xC}
        w.rp[i] = 0;
        if (k.pass) {
            const uint32_t r = A.Alloc(16);
            A.P32(r, A.G(g)); A.P16(r + 4, k.pass);
            if (k.flags & Q_LIGHT) { A.a[r + 9] = 2; A.P32(r + 0xC, A.G(lightList)); }
            w.rp[i] = r;
        }
    }
    // ImageSpaceManager (+0x08 the effect array) with three effects that have no native family
    w.imgMgr = A.Alloc(0x40); w.effectList = A.Alloc(4 * 8);
    A.P32(w.imgMgr + 0x08, A.G(w.effectList));
    for (int e = 0; e < kEffects; e++) A.P32(w.effectList + 4 * (e + 1), A.G(A.Alloc(0x80)));
}

void Report(C& c, const DrawStats& s, const char* label) {
    char b[512];
    int n = snprintf(b, sizeof b, "%s: engine draws %u, native %u (", label, (unsigned)s.engineDraws, (unsigned)s.Native());
    for (int i = 0; i < Route_Count; i++)
        if (s.native[i]) n += snprintf(b + n, sizeof b - n, "%s %u ", RouteName((PassRoute)i), (unsigned)s.native[i]);
    n += snprintf(b + n, sizeof b - n, "), translated %u (%u partial), misrendered %u, skipped %u (", (unsigned)s.translated,
                  (unsigned)s.translatedPartial, (unsigned)s.misrendered, (unsigned)s.Skipped());
    for (int i = 1; i < Skip_Count; i++)
        if (s.skipped[i]) n += snprintf(b + n, sizeof b - n, "%s %u ", SkipName((DrawSkip)i), (unsigned)s.skipped[i]);
    snprintf(b + n, sizeof b - n, ")\n");
    c.Info(b);
}
}  // namespace

int n1_selftest_world(Backend* be, Renderer& r, uint32_t back, char* log, uint32_t cap, uint32_t* at, int* checks) {
    C c = {log, cap, at, 0, 0};
    static uint8_t mem[64 * 1024] __attribute__((aligned(16)));
    memset(mem, 0, sizeof mem);
    uintptr_t a0 = (uintptr_t)mem, a1 = (uintptr_t)engine::NativeTexturePool();
    engine::SetGuestBase((a0 < a1 ? a0 : a1) - 64);
    static World w;
    w.ar = {mem, sizeof mem, 16};
    Build(w, c);
    c.Check(w.ar.top <= w.ar.size, "world: arena");
    static uint32_t passId, renderPass;
    engine::BindPassGlobals(&passId, &renderPass);
    const uint32_t routes0 = engine::NativeFamilies();
    engine::NiDX9Renderer* eng = (engine::NiDX9Renderer*)&r;
    engine::ImageSpaceManager* ism = (engine::ImageSpaceManager*)(w.ar.a + w.imgMgr);
    static const float sky[12] = {0.2f, 0.4f, 0.6f, 1, 0, 1, 0, 1, 0, 0, 1, 1};   // SkyShader Color0-2
    engine::BindSkyColors(sky);
    auto frame = [&]() {
        r.state().Reset();
        r.SetBackgroundColor(0, 0, 1, 1);
        c.Check(eng->Do_BeginFrame() && eng->Do_BeginUsingRenderTargetGroup(nullptr, Renderer::CLEAR_ALL), "world: begin");
        eng->Do_SetScreenSpaceCameraData(nullptr);
        for (int i = 0; i < kNumCells; i++) {
            passId = kCells[i].pass;
            renderPass = w.rp[i] ? w.ar.G(w.rp[i]) : 0;
            eng->Do_RenderGeometry(w.ar.a + w.geo[i]);
        }
        passId = 0; renderPass = 0;
        for (int e = 1; e <= kEffects; e++) ism->RenderEffect(e, &r, nullptr, nullptr, nullptr, false);
        c.Check(eng->Do_EndUsingRenderTargetGroup() && eng->Do_EndFrame(), "world: end");
        return r.lastFrameStats();
    };
    // the default routing: lit / depth / land / sky passes go to the translated route
    const DrawStats s0 = frame();
    Report(c, s0, "world frame (default routes)");
    c.Check(s0.Native() == 4 && s0.native[Route_NoLight] == 2 && s0.translated + s0.skipped[Skip_NoFamily] +
            s0.skipped[Skip_NoProgram] + s0.skipped[Skip_NoEngineShader] == (uint32_t)(kNumCells - 5),
            "world (default): native tile + nolight, the rest translated", (int)s0.Native());
    engine::SetNativeFamilies(~0u);   // the hand-written world families too
    const DrawStats s = frame();
    Report(c, s, "world frame (all native families)");
    c.Check(s.engineDraws == (uint32_t)(kNumCells + kEffects), "world: every engine draw counted", (int)s.engineDraws);
    c.Check(s.Native() + s.translated + s.misrendered + s.Skipped() == s.engineDraws, "world: counters add up");
    // native families: lit 3, land 1, sky 2, depth 3, nolight 2, tile 1 + the menu path; the skinned lit pass and
    // the passes without a native family are skipped (no translated path yet), as are the image-space effects
    c.Check(s.native[Route_Lit] == 3 && s.native[Route_Land] == 1 && s.native[Route_Sky] == 2 &&
            s.native[Route_Depth] == 3 && s.native[Route_NoLight] == 2 && s.native[Route_Tile] == 1 &&
            s.native[Route_Legacy] == 1, "world: native routes", (int)s.Native());
    c.Check(s.misrendered == 0 && s.skipped[Skip_Skinned] == 1 && s.skipped[Skip_NoFamily] == 14 &&
            s.skipped[Skip_ImageSpace] == (uint32_t)kEffects, "world: misrendered / skipped", (int)s.misrendered,
            (int)s.Skipped());
    // pixels: one cell per draw (clear blue)
    static uint8_t img[W * H * 4];
    NxReadback rb;
    memset(&rb, 0, sizeof rb);
    rb.image = back; rb.w = W; rb.h = H; rb.dst = FNVNX_ADDR_(img); rb.dst_size = sizeof img;
    c.Check(be->Readback(rb), "world: readback");
    auto px = [&](int cell) { return img + 4 * (((cell / kCols) * kCell + 8) * W + (cell % kCols) * kCell + 8); };
    auto near = [&](const uint8_t* p, int r, int g, int b) {
        return abs(p[0] - r) <= 2 && abs(p[1] - g) <= 2 && abs(p[2] - b) <= 2;
    };
    const uint8_t* p = px(0);
    c.Check(near(p, 255, 153, 89), "lit (3XLIGHTING): white x (ambient 0.1 + sun (1, .5, .25))", p[1], p[2]);
    const uint8_t* q = px(1);
    c.Check(q[1] > p[1] + 20 && q[2] > p[2] + 20, "lit_spec (3XLIGHTING_Spc): specular on top", q[1], q[2]);
    p = px(2); c.Check(near(p, 255, 0, 0), "lit_vc (3XLIGHTING_Vc): vertex colour red", p[1], p[2]);
    p = px(3); c.Check(near(p, 255, 0, 0), "land_lit (LANDAD): base x vc x (N.L sun + ambient)", p[0], p[1]);
    p = px(4); c.Check(near(p, 51, 102, 153), "sky (SKY): Color0 by the vertex colour's red weight", p[0], p[2]);
    p = px(5); c.Check(near(p, 255, 255, 255), "sky_clouds (SKY_CLOUDS): stage-0 texture over the gradient", p[0], p[2]);
    p = px(6); c.Check(near(p, 0, 0, 0), "depth_out (DEPTHMAP): z/w ~ 0 in screen space", p[0], p[2]);
    p = px(7); q = px(8);
    c.Check(near(p, 0, 0, 255) && near(q, 0, 0, 255), "depth (ZONLY, ZONLY_At): no colour written", p[2], q[2]);
    p = px(9); c.Check(near(p, 255, 255, 255), "nolight_tex (NOLIGHTING): texture x emissive", p[0], p[1]);
    p = px(10); c.Check(near(p, 255, 0, 0), "nolight_vc (NOLIGHTING_VC): vertex colour x emissive", p[1], p[2]);
    p = px(11); q = px(12);
    c.Check(near(p, 255, 255, 255) && near(q, 255, 255, 255), "tile (TILE) and the menu path", p[2], q[2]);
    p = px(13); c.Check(near(p, 0, 0, 255), "skinned: skipped", p[0], p[2]);
    int untouched = 0;
    for (int i = 14; i < kNumCells; i++) untouched += near(px(i), 0, 0, 255);
    c.Check(untouched == kNumCells - 14, "passes without a native family: not drawn", untouched);
    (void)be; (void)back;
    engine::BindPassGlobals(nullptr, nullptr);
    engine::BindSkyColors(nullptr);
    engine::SetNativeFamilies(routes0);
    *checks += c.checks;
    return c.fails;
}
