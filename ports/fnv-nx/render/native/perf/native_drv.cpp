// fnv-nx P6n: the native consumer of a .nxd3d stream (perf/nxd3d.h). Replays the stream draw by draw through the
// native path of render/native/n1 - nx::StateTracker (every engine D3D9 state set), nx::Renderer::Render (state
// resolve via the d3d9_state translator, StateCache, packet build, UBO/epilogue/dyn records) and, at the end of
// the frame, Frame::Finish (radix sort, encoding with redundant-bind elimination) - into a null backend: the
// backend's recording (host/nxgpu.c + deko3d) is NOT counted here.
//
// Builds as the ILP32 guest (perf/build_guest.sh; run under perf/emu_count.py, which counts AArch64 instructions
// between marks) and as an LP64 host program (perf/build_host.sh; run under callgrind, marks = callgrind dumps).
//
//   guest: sdmc:/perf/args.txt = "<stream> <dry> <frames in flight>"
//   host:  native_drv <stream> <dry 0|1> [capture.nxcap|-] [results|-] [frames in flight, default 3 = NXGPU_FRAMES]
//   dry = 1 decodes the stream without calling the renderer (the harness's own cost, subtracted by the tools).
// Marks: frame f -> 4f + 1 at FrameBegin, 4f + 2 before Do_EndFrame (packet build done), 4f + 3 after it.
#include "renderer.h"
#include "capture.h"
#include "nxd3d.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(__ILP32__)
#include "fnvnx/hostapi.h"
#define PERF_EXT_MODULE 0x50u
static void Mark(uint32_t id) { fnvnx_hcall4(fnvnx_host->ext_call, PERF_EXT_MODULE, id, 0, 0); }
#elif defined(PERF_CALLGRIND)
#include <valgrind/callgrind.h>
static void Mark(uint32_t id) {
    char n[32]; snprintf(n, sizeof n, "mark%u", id);
    CALLGRIND_DUMP_STATS_AT(n);
}
#else
static void Mark(uint32_t) {}
#endif

namespace {
// A backend that only hands out ids (frames_in_flight 3 = nxgpu.c's NXGPU_FRAMES).
struct NullBackend final : nx::Backend {
    NxGpuInfo info;
    uint32_t mem = 0, img = 0, smp = 0, sh = 0, submits = 0, words = 0;
    NullBackend() { memset(&info, 0, sizeof info); info.magic = NXGPU_MAGIC; info.version = NXGPU_VERSION; info.frames_in_flight = 3; }
    uint32_t MemCreate(const NxMemBlockDesc&) override { return ++mem; }
    uint64_t MemGpuAddr(uint32_t) override { return 0; }
    uint32_t ImageCreate(const NxImageDesc& d) override { img += nx_format_has_srgb(d.format) ? 2 : 1; return img - (nx_format_has_srgb(d.format) ? 1 : 0); }
    uint32_t SamplerCreate(const NxSamplerState&) override { return ++smp; }
    uint32_t ShaderCreate(const NxShaderDesc&) override { return ++sh; }
    void Submit(const NxSubmit& s) override { submits++; words = s.words; }
    void WaitFrame(uint32_t) override {}
    bool Readback(const NxReadback&) override { return false; }
    void Shutdown() override {}
    const NxGpuInfo& Info() const override { return info; }
};

enum { kMaxRes = 4096 };
struct Geo { uint32_t vtxOff, vtxSize, idxOff, idxSize; };
struct Tgt { uint32_t color, depth, w, h; };

uint32_t g_texImage[kMaxRes], g_texFmt[kMaxRes];
Geo g_geo[kMaxRes];
Tgt g_tgt[64];
float g_consts[256 * 4];

uint8_t* Load(const char* path, uint32_t* size) {
    FILE* f = fopen(path, "rb");
    if (!f) return nullptr;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t* d = (uint8_t*)malloc((size_t)n);
    if (!d || fread(d, 1, (size_t)n, f) != (size_t)n) { fclose(f); return nullptr; }
    fclose(f);
    *size = (uint32_t)n;
    return d;
}
} // namespace

static int Run(const char* stream, int dry, const char* capPath, FILE* out, uint32_t fif) {
    uint32_t size = 0;
    uint8_t* file = Load(stream, &size);
    if (!file || size < 8 || memcmp(file, "NXD3D\0\0\1", 8)) { fprintf(out, "error: cannot read %s\n", stream); return 2; }
    const uint32_t* w = (const uint32_t*)(file + 8);
    const uint32_t* end = (const uint32_t*)(file + (size & ~3u));
    static NullBackend null;
    null.info.frames_in_flight = fif ? fif : 3;
    nx::Backend* be = &null;
    static nx::CaptureBackend cap;
    if (capPath) {
        FILE* cf = fopen(capPath, "wb");
        if (!cf || !cap.Open(&null, cf)) { fprintf(out, "error: capture %s\n", capPath); return 2; }
        be = &cap;
    }
    static nx::Renderer r;
    NxImageDesc bd; memset(&bd, 0, sizeof bd); bd.width = 1280; bd.height = 720; bd.format = NxFmt_RGBA8; bd.flags = NxImg_RenderTarget;
    uint32_t back = be->ImageCreate(bd);
    NxImageDesc dd = bd; dd.format = NxFmt_Z24S8; dd.flags = NxImg_Depth;
    uint32_t depth = be->ImageCreate(dd);
    if (!r.Init(be, 1280, 720, back, depth)) { fprintf(out, "error: renderer init\n"); return 2; }
    static nx::StaticBuffer geo;
    if (!geo.Init(be, 4u << 20)) { fprintf(out, "error: static buffer\n"); return 2; }
    NxVtxLayout lay; memset(&lay, 0, sizeof lay);
    lay.count = 2; lay.stride = 20;
    lay.attr[0].offset = 0; lay.attr[0].type = NxAttr_Float32; lay.attr[0].components = 3;
    lay.attr[1].offset = 12; lay.attr[1].type = NxAttr_Float32; lay.attr[1].components = 2;
    uint16_t layout = r.cache().Layout(lay);
    for (int i = 0; i < 256 * 4; i++) g_consts[i] = (float)(i & 15) * 0.25f;
    g_tgt[0] = {back, depth, 1280, 720};

    nx::StateTracker& st = r.state();
    uint32_t curTarget = ~0u, vs = 1, ps = 2, curGeo = 0, uboBytes = 0, draws = 0, failed = 0, frames = 0;
    uint32_t lastWords = 0, lastBinds = 0, lastStates = 0, lastPackets = 0;
    bool groupOpen = false;
    while (w < end) {
        uint32_t op = w[0] & 0xFFFF, argc = w[0] >> 16;
        const uint32_t* a = w + 1;
        w += 1 + argc;
        if (op == D3E_End) break;
        switch (op) {
        case D3E_ResTexture: {
            if (a[0] >= kMaxRes) break;
            NxFormatInfo fi = nx_d3d9_format(a[1]);
            NxImageDesc d; memset(&d, 0, sizeof d);
            d.width = (uint16_t)a[2]; d.height = (uint16_t)a[3]; d.format = fi.format; d.mips = (uint8_t)a[4];
            g_texImage[a[0]] = be->ImageCreate(d); g_texFmt[a[0]] = a[1];
            break;
        }
        case D3E_ResShader: break;   // shader ids are used as the pair ids directly (shaders exist in the backend)
        case D3E_ResGeom: {
            if (a[0] >= kMaxRes) break;
            Geo& g = g_geo[a[0]];
            g.vtxSize = a[1] * a[3]; g.idxSize = a[2] * 2;
            geo.Alloc(g.vtxSize, 16, &g.vtxOff); geo.Alloc(g.idxSize, 16, &g.idxOff);
            break;
        }
        case D3E_ResTarget: {
            if (a[0] == 0 || a[0] >= 64) break;
            NxFormatInfo fi = nx_d3d9_format(a[3]);
            NxImageDesc d; memset(&d, 0, sizeof d);
            d.width = (uint16_t)a[1]; d.height = (uint16_t)a[2]; d.format = fi.format; d.flags = NxImg_RenderTarget;
            Tgt& t = g_tgt[a[0]];
            t.color = be->ImageCreate(d); t.w = a[1]; t.h = a[2];
            if (a[4]) { d.format = NxFmt_Z24S8; d.flags = NxImg_Depth; t.depth = be->ImageCreate(d); }
            r.RegisterTargetGroup((const void*)(uintptr_t)(0x1000 + a[0]), t.color, t.depth, t.w, t.h);
            break;
        }
        case D3E_FrameBegin:
            Mark(4 * frames + 1);
            if (!dry) r.Do_BeginFrame();
            curTarget = ~0u; groupOpen = false;
            break;
        case D3E_FrameEnd:
            if (!dry && groupOpen) r.Do_EndUsingRenderTargetGroup();
            Mark(4 * frames + 2);
            if (!dry) r.Do_EndFrame();
            Mark(4 * frames + 3);
            lastWords = r.frame().encodedWords(); lastBinds = r.frame().stateBindsEncoded();
            lastStates = r.cache().states(); lastPackets = r.frame().packets();
            frames++;
            break;
        case D3E_Target:
            if (a[0] != curTarget && !dry) {
                if (groupOpen) r.Do_EndUsingRenderTargetGroup();
                uint32_t c = a[1], mode = (c & 1 ? nx::Renderer::CLEAR_BACKBUFFER : 0) | (c & 2 ? nx::Renderer::CLEAR_ZBUFFER : 0) |
                                         (c & 4 ? nx::Renderer::CLEAR_STENCIL : 0);
                groupOpen = r.Do_BeginUsingRenderTargetGroup(a[0] ? (const void*)(uintptr_t)(0x1000 + a[0]) : nullptr, mode);
            }
            curTarget = a[0];
            break;
        case D3E_PassTag: if (!dry) r.SetEnginePass((uint16_t)a[0], (uint16_t)a[1]); break;
        case D3E_RS: if (!dry) st.SetRenderState(a[0], a[1]); break;
        case D3E_Samp: if (!dry) st.SetSamplerState(a[0], a[1], a[2]); break;
        case D3E_Tex:
            if (!dry && a[1] < kMaxRes) {
                NxD3D9Texture k; memset(&k, 0, sizeof k); k.format = g_texFmt[a[1]];
                st.SetTexture(a[0], g_texImage[a[1]], k);
            }
            break;
        case D3E_Shaders: vs = a[0]; ps = a[1]; break;
        case D3E_Geom: curGeo = a[0] < kMaxRes ? a[0] : 0; break;
        case D3E_Consts: uboBytes = (a[0] + a[1]) * 16; break;
        case D3E_Draw: {
            if (dry) { draws++; break; }
            const Geo& g = g_geo[curGeo];
            nx::Renderer::Draw d;
            memset(&d, 0, sizeof d);
            d.vs = (uint16_t)vs; d.ps = (uint16_t)ps; d.layout = layout;
            d.prim = a[0] == 5 ? NxPrim_TriStrip : NxPrim_Triangles;   // D3DPT_TRIANGLESTRIP 5, TRIANGLELIST 4
            d.layer = (uint8_t)a[6]; d.depthKey = (uint16_t)a[7];
            d.vtxBlock = geo.block(); d.vtxOffset = g.vtxOff; d.vtxSize = g.vtxSize;
            d.idxBlock = geo.block(); d.idxOffset = g.idxOff; d.idxSize = g.idxSize; d.idxType = 0;
            d.first = a[3]; d.count = a[0] == 5 ? a[4] + 2 : a[4] * 3; d.baseVertex = (int32_t)a[1];
            d.textures = a[5]; d.ubo = g_consts; d.uboSize = uboBytes;
            if (r.Render(d)) draws++; else failed++;
            break;
        }
        case D3E_Mark: Mark(a[0]); break;
        default: break;
        }
    }
    fprintf(out, "stream %s dry %d frames %u draws %u failed %u | last frame: packets %u words %u state_binds %u states %u "
                 "samplers %u\n", stream, dry, (unsigned)frames, (unsigned)draws, (unsigned)failed, (unsigned)lastPackets, (unsigned)lastWords,
                 (unsigned)lastBinds, (unsigned)lastStates, (unsigned)null.smp);
    if (capPath) cap.Close();
    return failed ? 1 : 0;
}

#if defined(__ILP32__)
#include <sys/stat.h>
int main() {
    static char args[512];
    FILE* a = fopen("sdmc:/perf/args.txt", "r");
    if (!a || !fgets(args, sizeof args, a)) return 3;
    fclose(a);
    char stream[256] = {0}; int dry = 0; unsigned fif = 3;
    if (sscanf(args, "%255s %d %u", stream, &dry, &fif) < 1) return 3;
    FILE* o = fopen("sdmc:/perf/result.txt", "w");
    if (!o) return 3;
    int rc = Run(stream, dry, nullptr, o, fif);
    fclose(o);
    return rc;
}
#else
int main(int argc, char** argv) {
    if (argc < 3) { fprintf(stderr, "usage: native_drv <stream.nxd3d> <dry 0|1> [capture.nxcap|-] [results|-] [frames in flight]\n"); return 2; }
    FILE* o = argc > 4 && strcmp(argv[4], "-") ? fopen(argv[4], "w") : stdout;
    const char* cap = argc > 3 && strcmp(argv[3], "-") ? argv[3] : nullptr;
    return Run(argv[1], atoi(argv[2]), cap, o ? o : stdout, argc > 5 ? (uint32_t)atoi(argv[5]) : 3);
}
#endif
