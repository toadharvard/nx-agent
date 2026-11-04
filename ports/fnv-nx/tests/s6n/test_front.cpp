// Unit tests for render/native/front (front.cpp: StateCache, Encoder, SortPackets, Frame encoding, ParseDDS) and the
// GL shim (gl/nxgl.c nxgl_shim_glsl). Host LP64 with a recording backend; no GPU.
#include "nx/front.h"
#include "dynres.h"
#include "nx/skin.h"
#include "../../render/native/gl/nxgl.h"
#include <stdio.h>
#include <algorithm>
#include <math.h>
#include <string.h>
#include <stdlib.h>

static int g_fail, g_checks;
#define CHECK(c) do { g_checks++; if (!(c)) { g_fail++; printf("FAIL %s:%d: %s\n", __func__, __LINE__, #c); } } while (0)

struct RecBe final : nx::Backend {
    NxGpuInfo info;
    uint32_t mems = 0;
    uint32_t lastWords = 0;
    uint32_t stream[1 << 16];
    RecBe() { memset(&info, 0, sizeof info); info.magic = NXGPU_MAGIC; info.version = NXGPU_VERSION; info.frames_in_flight = 2; }
    uint32_t MemCreate(const NxMemBlockDesc&) override { return ++mems; }
    uint64_t MemGpuAddr(uint32_t) override { return 0; }
    uint32_t ImageCreate(const NxImageDesc&) override { return 1; }
    uint32_t SamplerCreate(const NxSamplerState&) override { return 1; }
    uint32_t ShaderCreate(const NxShaderDesc&) override { return 1; }
    void Submit(const NxSubmit& s) override {
        lastWords = s.words;
        memcpy(stream, (const void*)(uintptr_t)s.stream, s.words * 4);
    }
    void WaitFrame(uint32_t) override {}
    bool Readback(const NxReadback&) override { return true; }
    void Shutdown() override {}
    const NxGpuInfo& Info() const override { return info; }
    int Count(uint32_t op) const {
        int n = 0;
        for (uint32_t i = 0; i < lastWords; i += 1 + (stream[i] >> 16)) n += (stream[i] & 0xFFFF) == op;
        return n;
    }
};

static void test_state_cache() {
    static nx::StateCache sc;
    NxRenderState a = {{1, 2, 3, 0}}, b = {{1, 2, 4, 0}};
    uint16_t s1 = sc.State(1, 2, 1, a), s2 = sc.State(1, 2, 1, b), s3 = sc.State(1, 2, 1, a), s4 = sc.State(1, 3, 1, a);
    CHECK(s1 == 1 && s2 == 2 && s3 == s1 && s4 == 3);
    NxVtxLayout l; memset(&l, 0, sizeof l); l.count = 1; l.stride = 8;
    CHECK(sc.Layout(l) == 1 && sc.Layout(l) == 1);
    l.stride = 12; CHECK(sc.Layout(l) == 2);
    static uint32_t w[256];
    nx::Encoder e; e.Reset(w, 256);
    sc.FlushDefines(e);
    CHECK(e.size() == 2 * 11 + 3 * 13);            // 2 DefineLayout (op + id + 9), 3 DefineState (op + 12 args)
    CHECK((w[0] & 0xFFFF) == NxOp_DefineLayout && (w[0] >> 16) == 10);
    CHECK((w[22] & 0xFFFF) == NxOp_DefineState && (w[22] >> 16) == 12 && w[23] == 1 && w[27] == 1 && w[31] == 3);
    nx::Encoder e2; e2.Reset(w, 256); sc.FlushDefines(e2); CHECK(e2.size() == 0);   // only new entries
}

static void test_encoder_overflow() {
    uint32_t w[8];
    nx::Encoder e; e.Reset(w, 8);
    CHECK(e.Op(NxOp_Draw, 5) == w + 1 && !e.overflow());
    e.Op(NxOp_Draw, 5);                               // 6 + 6 + 1 (End) > 8
    CHECK(e.overflow() && e.size() == 6);
}

static void test_sort_stable() {
    static nx::Packet p[6];
    static uint32_t order[6], tmp[6];
    static uint64_t keys[6], kt[6];
    const uint64_t k[6] = {nx::SortKey(1, 0, 5, 0, 0), nx::SortKey(0, 1, 1, 0, 9), nx::SortKey(0, 0, 7, 0, 3),
                           nx::SortKey(0, 1, 1, 0, 2), nx::SortKey(0, 0, 7, 0, 3), nx::SortKey(0, 0, 2, 0, 0)};
    for (int i = 0; i < 6; i++) p[i].key = k[i];
    nx::SortPackets(p, 6, order, tmp, keys, kt);
    const uint32_t want[6] = {5, 2, 4, 3, 1, 0};     // pass, layer, state, depth; equal keys keep submission order
    for (int i = 0; i < 6; i++) CHECK(order[i] == want[i]);
}

// opaque draws (layer 0): coarse depth bucket first (front to back), state inside the bucket; other layers ignore it
static void test_sort_depth_buckets() {
    const uint16_t near = 0x1000, far = 0xF000;       // buckets 0 and 3 of 4
    CHECK(nx::SortKey(0, 0, 9, 0, near) < nx::SortKey(0, 0, 1, 0, far));      // nearer bucket first, any state
    CHECK(nx::SortKey(0, 0, 1, 0, near + 0x100) < nx::SortKey(0, 0, 9, 0, near));   // same bucket: state first
    CHECK(nx::SortKey(0, 1, 1, 0, far) < nx::SortKey(0, 1, 9, 0, near));      // layer 1: state before depth
    CHECK(nx::SortKey(0, 0, 0xFFF, 0xFFFF, 0xFFFF) < nx::SortKey(0, 1, 0, 0, 0));   // bucket never spills into the layer
    CHECK(nx::SortKey(0, 15, 0xFFF, 0xFFFF, 0xFFFF) < nx::SortKey(1, 0, 0, 0, 0));
}

static void test_frame_encoding() {
    static RecBe be;
    static nx::StateCache sc;
    static nx::Frame f;
    CHECK(f.Init(&be, 1 << 20, 64));
    f.Begin(0);
    nx::PassDesc pd; memset(&pd, 0, sizeof pd); pd.color = 1; pd.width = 64; pd.height = 32;
    uint8_t pass = f.AddPass(pd);
    NxRenderState r = {{0, 0, 0, 0}};
    uint16_t st = sc.State(1, 2, 0, r);
    nx::DynRec ref; nx::DefaultDyn(&ref, 64, 32); ref.stencilRef = 7;
    nx::DynRec bc; nx::DefaultDyn(&bc, 64, 32); bc.blendConstant[0] = 0.5f;
    for (int i = 0; i < 4; i++) {
        nx::Packet* p = f.Emit();
        p->key = nx::SortKey(pass, 0, st, 0, (uint16_t)i); p->state = st; p->count = 3; p->vtxBlock = 1;
        f.Constants(&p->uboOffset);
        p->dyn = i == 1 ? f.AddDyn(ref) : i == 2 ? f.AddDyn(bc) : 0;
    }
    f.Finish(sc, nullptr);
    CHECK(be.Count(NxOp_BeginPass) == 1 && be.Count(NxOp_EndPass) == 1 && be.Count(NxOp_Draw) == 4);
    CHECK(be.Count(NxOp_BindVtx) == 1);                // same vertex buffer: bound once
    CHECK(be.Count(NxOp_StencilRef) == 1);             // draw 1 overrides the ref
    CHECK(be.Count(NxOp_BindState) == 2);              // once + once to restore the state's ref after draw 1
    CHECK(be.Count(NxOp_BlendConst) == 2);             // draw 2 sets 0.5, draw 3 back to the pass default
    CHECK(be.Count(NxOp_Viewport) == 0 && be.Count(NxOp_Scissor) == 0);   // defaults are the pass's
    CHECK(be.Count(NxOp_DefineState) == 1);
}

static void test_dds() {
    static uint8_t f[128 + 64];
    memset(f, 0, sizeof f);
    memcpy(f, "DDS ", 4); f[4] = 124; f[12] = 4; f[16] = 4; f[28] = 1;      // 4x4, 1 mip
    f[80] = 0x41; f[88] = 32; f[92 + 2] = 0xFF; f[96 + 1] = 0xFF; f[100] = 0xFF; f[107] = 0xFF;   // A8R8G8B8 masks
    NxImageDesc d; uint32_t d3d = 0;
    CHECK(nx::ParseDDS(f, sizeof f, &d, &d3d) && d3d == 21 && d.format == NxFmt_BGRA8 && d.data_size == 64);
    CHECK(!nx::ParseDDS(f, 100, &d, &d3d));             // truncated
    f[80] = 4; memcpy(f + 84, "DXT1", 4);
    CHECK(nx::ParseDDS(f, 128 + 8, &d, &d3d) && d.format == NxFmt_BC1 && d.data_size == 8);
    memcpy(f + 84, "DXT9", 4);
    CHECK(!nx::ParseDDS(f, sizeof f, &d, &d3d));
}

static void test_glsl_shim() {
    const char* src = "#version 460\nlayout (std140, binding = 1) uniform Draw { vec4 c; };\n"
                      "layout(binding=3) uniform sampler2D t;\nvoid main() { gl_Position = c; }\n";
    char out[1024];
    size_t n = nxgl_shim_glsl(src, strlen(src), 1, out, sizeof out);
    CHECK(n && !strncmp(out, "#version 450 core\n", 18));
    CHECK(strstr(out, "binding = 17) uniform Draw_nxfs {") && strstr(out, "binding = 19) uniform sampler2D t"));
    CHECK(!strstr(out, "nx_main_"));                    // fragment stage: main untouched
    n = nxgl_shim_glsl(src, strlen(src), 0, out, sizeof out);
    CHECK(n && strstr(out, "binding = 1) uniform Draw_nxvs {") && strstr(out, "binding = 3) uniform sampler2D t"));
    CHECK(strstr(out, "void nx_main_() {") && strstr(out, "void main() { nx_main_(); gl_Position.y = -gl_Position.y; }"));
    CHECK(nxgl_shim_glsl(src, strlen(src), 0, out, 16) == 0);   // too small
}

static void test_dynres() {
    nx::DynResConfig cfg;   // 22.2 ms, 0.75..1.0 by 0.05, down after 3 frames over 95 %, up after 30 under 80 %
    nx::DynResController d(cfg);
    CHECK(d.scale() == 1.0f && d.Width(1280) == 1280 && d.Height(720) == 720);
    for (int i = 0; i < 2; i++) d.Update(22.0f);
    CHECK(d.scale() == 1.0f);                         // hysteresis: not yet
    d.Update(22.0f);
    CHECK(fabsf(d.scale() - 0.95f) < 1e-6f && d.Width(1280) == 1216 && d.Height(720) == 680);   // 684 -> 680 (x8)
    d.Update(30.0f);                                  // panic: two steps at once
    CHECK(fabsf(d.scale() - 0.85f) < 1e-6f);
    for (int i = 0; i < 100; i++) d.Update(40.0f);
    CHECK(fabsf(d.scale() - 0.75f) < 1e-6f && d.Height(720) == 536);  // clamped at minScale (540 -> 536, x8)
    int n = 0;
    while (d.scale() < 0.77f && n < 100) { d.Update(10.0f); n++; }
    CHECK(fabsf(d.scale() - 0.8f) < 1e-6f && n >= 30 && n <= 40);    // up after 30 smoothed frames under budget
    for (int i = 0; i < 1000; i++) d.Update(19.0f);   // in the dead band (80-95 %): stays
    CHECK(fabsf(d.scale() - 0.8f) < 1e-6f);
    for (int i = 0; i < 2000; i++) d.Update(5.0f);
    CHECK(d.scale() == 1.0f);                         // clamped at maxScale
}

static void test_skin_palette() {
    // bone world: rotate 90 degrees about z, scale 2, translate (1, 2, 3); bind (skin to bone): translate (-1, 0, 0)
    NxBoneXform w = {{0, -1, 0, 1, 0, 0, 0, 0, 1}, {1, 2, 3}, 2}, b = {{1, 0, 0, 0, 1, 0, 0, 0, 1}, {-1, 0, 0}, 1};
    float rows[3][4];
    const uint16_t bones[1] = {0};
    CHECK(nx_skin_palette(&w, &b, bones, 1, rows) == 1);
    // v = (2, 0, 0): skin -> bone (1, 0, 0) -> world 2 * R (1, 0, 0) + t = (0, 2, 0) + (1, 2, 3) = (1, 4, 3)
    float v[4] = {2, 0, 0, 1}, o[3];
    for (int r = 0; r < 3; r++) o[r] = rows[r][0] * v[0] + rows[r][1] * v[1] + rows[r][2] * v[2] + rows[r][3];
    CHECK(fabsf(o[0] - 1) < 1e-6f && fabsf(o[1] - 4) < 1e-6f && fabsf(o[2] - 3) < 1e-6f);
    uint16_t many[40] = {0};
    static float big[3 * 40][4];
    CHECK(nx_skin_palette(&w, &b, many, 40, big) == NX_SKIN_MAX_BONES);
}

static void test_ubo_ring_capacity() {
    // P6n: an 8 MiB ring at 3 frames in flight holds ~3,640 draws of 512 + 256 B; the high mix needs 3,710
    static RecBe be;
    be.info.frames_in_flight = 3;
    auto run = [&](nx::Frame& f, uint32_t draws) {
        f.Begin(0);
        uint32_t ok = 0;
        for (uint32_t i = 0; i < draws; i++) {
            nx::Packet* p = f.Emit();
            if (p && f.Constants(&p->uboOffset) && f.Epilogue(p)) ok++;
        }
        return ok;
    };
    static nx::Frame old, sized;
    CHECK(old.Init(&be, 8u << 20, 512));
    CHECK(run(old, 3710) < 3710 && old.droppedDraws() > 0);        // the bug, reproduced and counted
    CHECK(sized.Init(&be, 0, 512));                                 // sized for kMaxPackets per frame in flight
    CHECK(run(sized, nx::Frame::kMaxPackets) == nx::Frame::kMaxPackets && sized.droppedDraws() == 0);
    CHECK(nx::Frame::UboBytesFor(4000, 512, 3) >= 3u * 4000u * 768u);
    be.info.frames_in_flight = 2;
}

static void test_sort_property() {
    // stable and equal to std::stable_sort on random 64-bit keys with many ties (C65's property, 8-bit digits)
    static nx::Packet pk[4000];
    static uint32_t order[4000], tmp[4000], ref[4000];
    static uint64_t keys[4000], ktmp[4000];
    uint64_t rng = 12345;
    auto rnd = [&]() { rng = rng * 6364136223846793005ull + 1442695040888963407ull; return rng >> 11; };
    int bad = 0;
    for (int set = 0; set < 200; set++) {
        uint32_t n = 1 + (uint32_t)(rnd() % 4000);
        uint64_t mask = rnd() | rnd() << 32;   // which key bits vary in this set
        for (uint32_t i = 0; i < n; i++) { pk[i].key = (rnd() % 7 == 0 ? (rnd() | rnd() << 40) : (rnd() % 16) * 0x0101010101010101ull) & mask; ref[i] = i; }
        nx::SortPackets(pk, n, order, tmp, keys, ktmp);
        std::stable_sort(ref, ref + n, [&](uint32_t a, uint32_t b) { return pk[a].key < pk[b].key; });
        if (memcmp(order, ref, n * 4)) bad++;
        for (uint32_t i = 0; i < n; i++) if (keys[i] != pk[order[i]].key) { bad++; break; }
    }
    CHECK(bad == 0);
}

static void test_epilogue_binds() {
    // epilogue blocks: shared by SetEpilogue while equal, and bound only when the contents change
    static RecBe be;
    static nx::StateCache sc;
    static nx::Frame f;
    CHECK(f.Init(&be, 1 << 20, 64));
    f.Begin(0);
    nx::PassDesc pd; memset(&pd, 0, sizeof pd); pd.color = 1; pd.width = 64; pd.height = 32;
    uint8_t pass = f.AddPass(pd);
    NxRenderState r = {{0, 0, 0, 0}};
    uint16_t st = sc.State(1, 2, 0, r);
    NxEpilogue a, b;
    memset(&a, 0, sizeof a); memset(&b, 0, sizeof b); b.alphaRef = 0.5f; b.alphaTest = 1;
    const NxEpilogue* seq[6] = {&a, &a, &b, &b, &a, &a};
    uint32_t epis[6];
    for (int i = 0; i < 6; i++) {
        nx::Packet* p = f.Emit();
        p->key = nx::SortKey(pass, 0, st, 0, (uint16_t)i); p->state = st; p->count = 3; p->vtxBlock = 1;
        f.Constants(&p->uboOffset);
        if (i < 5) f.SetEpilogue(p, *seq[i]);
        else { NxEpilogue* e = f.Epilogue(p); *e = a; }   // a fresh block with the same contents as draw 4's
        epis[i] = p->epi;
    }
    CHECK(epis[0] == epis[1] && epis[2] == epis[3] && epis[0] != epis[2] && epis[4] != epis[0] && epis[5] != epis[4]);
    f.Finish(sc, nullptr);
    CHECK(be.Count(NxOp_BindUbo) == 6 + 3);   // 6 per-draw slices; epilogue a, b, a (draw 5's equal block: no bind)
}

int main() {
    test_epilogue_binds();
    test_sort_property();
    test_ubo_ring_capacity();
    test_skin_palette();
    test_dynres();
    test_state_cache(); test_encoder_overflow(); test_sort_stable(); test_sort_depth_buckets(); test_frame_encoding(); test_dds(); test_glsl_shim();
    printf("front: %d checks, %d failed\n", g_checks, g_fail);
    return g_fail != 0;
}
