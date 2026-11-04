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
    uint32_t shader;    // the engine shader every geometry points at (BSShader -> NiD3DPass -> programs, maps)
};

// ---- a synthetic translated program pair (our own GLSL in the S6b layout contract, not a game program) --------------
const char kTestVs[] =
    "#version 450 core\n"
    "layout(std140, binding = 2) uniform vs_driver { vec4 vs_drv_posfix; vec4 vs_drv_reserved[3]; };\n"
    "layout(std140, binding = 0) uniform vs_cbuf_f { vec4 vs_uniforms_vec4[256]; };\n"
    "layout(location = 0) in vec4 vs_v0;\n"
    "layout(location = 4) in vec4 vs_v1;\n"
    "layout(location = 0) out highp vec4 io_10_0;\n"
    "void main()\n{\n"
    "\tgl_Position = vec4(dot(vs_uniforms_vec4[0], vs_v0), dot(vs_uniforms_vec4[1], vs_v0), dot(vs_uniforms_vec4[2], vs_v0), dot(vs_uniforms_vec4[3], vs_v0));\n"
    "\tio_10_0 = vs_v1 * vs_uniforms_vec4[4];\n"
    "\tgl_Position.xy += vs_drv_posfix.xy * gl_Position.w;\n"
    "\tgl_Position.y *= vs_drv_posfix.z;\n}\n";
// reads a varying the VS does not write (location 3): the GL path pads the vertex program for the pair
const char kTestPs[] =
    "#version 450 core\n"
    "layout(std140, binding = 2) uniform ps_driver { vec4 ps_drv_fogcolor; vec4 ps_drv_fog; ivec4 ps_drv_alpha; vec4 ps_drv_alpharef; vec4 ps_drv_texbem[16]; };\n"
    "layout(std140, binding = 0) uniform ps_cbuf_f { vec4 ps_uniforms_vec4[256]; };\n"
    "layout(std140, binding = 1) uniform ps_cbuf_ib { ivec4 ps_i[16]; uint ps_b[16]; };\n"
    "layout(location = 0) in highp vec4 io_10_0;\n"
    "layout(location = 3) in highp vec4 io_5_1;\n"
    "layout(location = 0) out highp vec4 _gl_FragData_0;\n"
    "void main()\n{\n"
    "\tvec4 c = io_10_0 * ps_uniforms_vec4[0] + ps_uniforms_vec4[1] + io_5_1;\n"
    "\tif (ps_b[0] != 0u) c.b += 0.25;\n"
    "\t_gl_FragData_0 = c;\n}\n";
const uint32_t kPsBytecode[2] = {0xFFFF0200u, 0x0000FFFFu};   // ps_2_0 + END: the hooked CreatePixelShader hashes it

uint8_t g_nxsp[32 + 6 * 104 + 3 * 1024] __attribute__((aligned(256)));
// the NXSP container of NX1's shaders.nxsp (translated.h): two "translated" rows (no DKSH: the GL reference only),
// their "translated-glsl" rows and two "translated-name" rows
uint32_t BuildNxsp() {
    memset(g_nxsp, 0, sizeof g_nxsp);
    const uint64_t h[2] = {0x1111111111111111ull, TranslatedStore::Hash(kPsBytecode, sizeof kPsBytecode)};
    const char* src[2] = {kTestVs, kTestPs};
    const char* names[2] = {"TESTVS.vso", "TESTPS.pso"};
    const uint32_t n = 6, idx = 32, data = (idx + n * 104 + 255) & ~255u;
    uint32_t o = data, row = 0;
    auto put = [&](const char* name, const char* fam, const char* var, uint32_t stage, const void* blob, uint32_t size) {
        uint8_t* e = g_nxsp + idx + 104 * row++;
        strncpy((char*)e, name, 47); strncpy((char*)e + 48, fam, 23); memcpy(e + 72, var, strlen(var) < 16 ? strlen(var) : 16);
        const uint32_t w[4] = {stage, size ? o : data, size, size ? TranslatedStore::Crc32(blob, size) : 0};
        memcpy(e + 88, w, 16);
        if (size) { memcpy(g_nxsp + o, blob, size); o += (size + 255) & ~255u; }
    };
    char hex[2][17];
    for (int s = 0; s < 2; s++) snprintf(hex[s], 17, "%08x%08x", (unsigned)(h[s] >> 32), (unsigned)h[s]);
    put(hex[0], "translated", "vs_2_0/0011", 0, nullptr, 0);
    put(hex[1], "translated", "ps_2_0/0000", 1, nullptr, 0);
    for (int s = 0; s < 2; s++) put(hex[s], "translated-glsl", "", (uint32_t)s, src[s], (uint32_t)strlen(src[s]) + 1);
    for (int s = 0; s < 2; s++) put(names[s], "translated-name", hex[s], 0, nullptr, 0);
    const uint32_t head[5] = {0x5053584Eu, 1, n, idx, data};   // "NXSP"
    memcpy(g_nxsp, head, 20);
    return o;
}

// a stand-in D3D9 device: every method returns S_OK; CreatePixelShader (106) hands out a token object
int32_t DevOk() { return 0; }
uint32_t g_psObject;
int32_t DevCreatePs(void*, const uint32_t*, void** out) { *out = &g_psObject; return 0; }
struct FakeDevice { const uintptr_t* vtbl; };

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
    // the engine shader of the translated passes: BSShader +0x5C NiD3DPass {+0x34 PS constant map, +0x44 PS program,
    // +0x48 VS constant map, +0x5C VS program}; VS program found by name (+0x0C), PS program by the D3D shader the
    // hooked device created (held at +0x10); VS map: WorldViewProjTranspose (c0-c3, predefined) and a data entry c4
    const uint32_t sh = A.Alloc(0x70), pass = A.Alloc(0x70), vsObj = A.Alloc(0x80), psObj = A.Alloc(0x80);
    w.shader = sh;
    A.P32(sh + 0x5C, A.G(pass));
    A.P32(pass + 0x5C, A.G(vsObj)); A.P32(pass + 0x44, A.G(psObj));
    const uint32_t vsName = A.Alloc(16);
    memcpy(A.a + vsName, "TESTVS.vso", 11);
    A.P32(vsObj + 0x0C, A.G(vsName));
    A.P32(psObj + 0x10, engine::GuestAddress(&g_psObject));
    auto map = [&](int n, const char* const* keys, const uint32_t* regs, const uint32_t* counts, const float (*data)[4]) {
        const uint32_t m = A.Alloc(0x40), arr = A.Alloc(4 * n);
        A.P32(m + 0x10, A.G(arr)); A.P16(m + 0x14, (uint16_t)n); A.P16(m + 0x16, (uint16_t)n);
        for (int i = 0; i < n; i++) {
            const uint32_t e = A.Alloc(0x38), k = A.Alloc(32);
            memcpy(A.a + k, keys[i], strlen(keys[i]) + 1);
            A.P32(e + 0x0C, A.G(k)); A.P32(e + 0x1C, regs[i]); A.P32(e + 0x20, counts[i]);
            if (data[i][3] >= 0) { const uint32_t d = A.Alloc(16); A.PF(d, data[i], 4); A.P32(e + 0x28, 16); A.P32(e + 0x30, A.G(d)); }
            A.P32(arr + 4 * i, A.G(e));
        }
        return m;
    };
    static const char* const vk[2] = {"WorldViewProjTranspose", "Color"};
    static const uint32_t vr[2] = {0, 4}, vc[2] = {4, 1};
    static const float vd[2][4] = {{0, 0, 0, -1}, {0.5f, 0.5f, 0.5f, 1}};
    static const char* const pk[1] = {"Tint"};
    static const uint32_t pr[1] = {0}, pc[1] = {1};
    static const float pdd[1][4] = {{1, 1, 1, 1}};
    A.P32(pass + 0x48, A.G(map(2, vk, vr, vc, vd)));
    A.P32(pass + 0x34, A.G(map(1, pk, pr, pc, pdd)));
    for (int i = 0; i < kNumCells; i++) A.P32(w.geo[i] + 0xC0, A.G(sh));
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
    snprintf(b + n, sizeof b - n, "), runtime compiles %u\n", (unsigned)s.runtimeCompiles);
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
    engine::NiDX9Renderer* eng = (engine::NiDX9Renderer*)&r;
    engine::ImageSpaceManager* ism = (engine::ImageSpaceManager*)(w.ar.a + w.imgMgr);
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
    // without a translated store: the translated route has nothing to draw with
    const DrawStats sNo = frame();
    Report(c, sNo, "world frame (default routes, no translated store)");
    c.Check(sNo.Native() == 2 && sNo.skipped[Skip_NoFamily] == (uint32_t)(kNumCells - 3), "world: no store -> skipped",
            (int)sNo.skipped[Skip_NoFamily]);
    // the translated path: the synthetic NXSP, a stand-in device hooked for program creation and constants
    static TranslatedStore store;
    c.Check(store.Load(g_nxsp, BuildNxsp()) && store.count() == 2 && store.FindName("TESTVS.vso") >= 0,
            "translated: NXSP load");
    static uintptr_t devTable[120];
    for (int i = 0; i < 120; i++) devTable[i] = (uintptr_t)&DevOk;
    devTable[106] = (uintptr_t)&DevCreatePs;
    static FakeDevice dev = {devTable};
    c.Check(engine::HookD3DDevice(&dev) && dev.vtbl != devTable, "translated: device hooked");
    typedef int32_t (*CreateFn)(void*, const uint32_t*, void**);
    typedef int32_t (*ConstFn)(void*, uint32_t, const void*, uint32_t);
    void* psh = nullptr;
    c.Check(((CreateFn)dev.vtbl[106])(&dev, kPsBytecode, &psh) == 0 && psh == &g_psObject, "translated: CreatePixelShader through the hook");
    const float tint[4] = {0, 0.25f, 0, 0};
    const uint32_t bTrue = 1;
    ((ConstFn)dev.vtbl[109])(&dev, 1, tint, 1);   // SetPixelShaderConstantF(1)
    ((ConstFn)dev.vtbl[113])(&dev, 0, &bTrue, 1);  // SetPixelShaderConstantB(0)
    c.Check(engine::DeviceConstants().psc[1][1] == 0.25f && engine::DeviceConstants().psb[0] == 1,
            "translated: the device's register files recorded");
    r.SetTranslatedStore(&store);
    const DrawStats s0 = frame();
    Report(c, s0, "world frame (default routes)");
    c.Check(s0.engineDraws == (uint32_t)(kNumCells + kEffects) &&
            s0.Native() + s0.translated + s0.misrendered + s0.Skipped() == s0.engineDraws, "world: every draw counted once");
    c.Check(s0.Native() == 2 && s0.translated == (uint32_t)(kNumCells - 3) && s0.misrendered == 0 &&
            s0.translatedPartial == 0 && s0.Skipped() == 1u + kEffects, "world (default): tile native, every other pass translated",
            (int)s0.translated, (int)s0.Skipped());
    {
        static uint8_t img0[W * H * 4];
        NxReadback rb0;
        memset(&rb0, 0, sizeof rb0);
        rb0.image = back; rb0.w = W; rb0.h = H; rb0.dst = FNVNX_ADDR_(img0); rb0.dst_size = sizeof img0;
        c.Check(be->Readback(rb0), "translated: readback");
        auto px0 = [&](int cell) { return img0 + 4 * (((cell / kCols) * kCell + 8) * W + (cell % kCols) * kCell + 8); };
        // VS: WVP x position, colour = vertex colour (white without) x c4 (0.5); PS: x c0 (1) + c1 (0, .25, 0) + the
        // padded varying (0), b0 -> blue + .25
        int good = 0;
        for (int i = 0; i < kNumCells; i++) {
            const PassInfo pi = RoutePass(kCells[i].pass);
            if (pi.route == Route_Tile || pi.route == Route_Legacy || (kCells[i].flags & Q_SKIN)) continue;
            const uint8_t* p = px0(i);
            const bool vc = (kCells[i].flags & Q_VC) != 0;
            const int er = 128, eg = vc ? 64 : 191, eb = vc ? 64 : 191;
            good += abs(p[0] - er) <= 2 && abs(p[1] - eg) <= 2 && abs(p[2] - eb) <= 2;
        }
        c.Check(good == kNumCells - 3, "translated: every translated cell = vertex colour x c4 x c0 + c1 (+ b0)", good);
        c.Check(r.translatedShaderLoads() == 2, "translated: one VS (padded for the pair) + one PS loaded", (int)r.translatedShaderLoads());
    }
    // the tile family against the translated route in the same frame: tile cells white
    {
        static uint8_t img1[W * H * 4];
        NxReadback rb1;
        memset(&rb1, 0, sizeof rb1);
        rb1.image = back; rb1.w = W; rb1.h = H; rb1.dst = FNVNX_ADDR_(img1); rb1.dst_size = sizeof img1;
        c.Check(be->Readback(rb1), "world: readback");
        const uint8_t* t0 = img1 + 4 * ((1 * kCell + 8) * W + 3 * kCell + 8);   // cell 11 (TILE), row 1 col 3
        const uint8_t* t1 = img1 + 4 * ((1 * kCell + 8) * W + 4 * kCell + 8);   // cell 12 (no pass)
        const uint8_t* sk = img1 + 4 * ((1 * kCell + 8) * W + 5 * kCell + 8);   // cell 13 (skinned)
        c.Check(t0[0] == 255 && t0[1] == 255 && t0[2] == 255 && t1[0] == 255 && t1[2] == 255,
                "world: tile cells drawn by the tile family", t0[1], t1[1]);
        c.Check(sk[0] == 0 && sk[2] == 255, "world: the skinned cell is skipped", sk[0], sk[2]);
    }
    (void)be; (void)back;
    engine::BindPassGlobals(nullptr, nullptr);
    r.SetTranslatedStore(nullptr);
    *checks += c.checks;
    return c.fails;
}
