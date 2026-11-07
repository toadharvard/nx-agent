// fnv-nx native renderer: nx_render, the host entry point that renders captures and scenes with the native renderer
// on the GL reference backend to PNG (docs/RENDER_NATIVE.md §8). Used by S16-compare for reference comparisons.
//
//   nx_render nxcap <file.nxcap> -o <dir> [--per-draw] [--until-draw N] [--frame F]
//   nx_render d3d9  <file.nxd9>  -o <dir> [--shaders <dir>] [--native <dir>] [--per-draw] [--raw] [--until-draw N]
//                [--frame F] [--families all|none] [--draw-log <file>] [--force-visible]
//   nx_render scene <name|all>   -o <dir> [--per-draw] [--until-draw N]
//
// Output names (stable): f<frame %05u>.png per present (nxcap: SUBM with present; d3d9: PRES; scene: f00000.png
// = <name>.png), rb<index %04u>.png per nxcap RDBK record, f<frame>_d<draw %05u>.png after each draw with
// --per-draw (the colour target of that draw's pass, draw = index in the frame; scene mode: <name>_d<draw>.png). --until-draw N stops drawing after
// draw N of each selected frame (the frame image then shows draws 0..N). --frame F limits per-draw images and
// --until-draw to frame F. summary.txt holds the counters.
// Exit codes: 0 success; 1 runtime failure (malformed input, backend error); 2 usage or I/O error;
// 3 rendered, but some draws were skipped (unsupported D3D9 features; first reason in summary.txt).
#include "replay.h"
#include "d3d9_exec.h"
#include "png.h"
#include "../gl/nxgl.h"
#include "../n0/scenes.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>
#include <sys/stat.h>
#include <errno.h>

namespace {
const char* g_out = nullptr;
bool g_perDraw = false;
long g_until = -1, g_onlyFrame = -1;
uint32_t g_frame = 0;
std::vector<uint8_t> g_px(4096u * 4096u * 4u);
uint32_t g_images = 0;
const char* g_scene = nullptr;    // scene mode: per-draw names are <scene>_d<draw>.png

bool Png(const char* name, uint32_t image) {
    uint32_t w, h;
    if (nxgl_read_rgba8(image, &w, &h, g_px.data(), g_px.size())) return false;
    char p[2048];
    snprintf(p, sizeof p, "%s/%s", g_out, name);
    if (!nxpng::Write(p, g_px.data(), w, h)) { fprintf(stderr, "nx_render: cannot write %s\n", p); exit(2); }
    g_images++;
    return true;
}
bool g_raw = false;              // --raw: also f<frame>_d<draw>.f32 (u32 w, h, then w x h x RGBA float)
std::vector<float> g_fpx;
void Raw(const char* name, uint32_t image) {
    uint32_t w, h;
    if (g_fpx.empty()) g_fpx.resize(4096u * 4096u * 4u);
    if (nxgl_read_rgba32f(image, &w, &h, g_fpx.data(), g_fpx.size() * sizeof(float))) return;
    char p[2048];
    snprintf(p, sizeof p, "%s/%s", g_out, name);
    FILE* f = fopen(p, "wb");
    if (!f) { fprintf(stderr, "nx_render: cannot write %s\n", p); exit(2); }
    uint32_t hd[2] = {w, h};
    bool ok = fwrite(hd, 4, 2, f) == 2 && fwrite(g_fpx.data(), 16, (size_t)w * h, f) == (size_t)w * h;
    if (fclose(f) || !ok) { fprintf(stderr, "nx_render: cannot write %s\n", p); exit(2); }
}
bool FrameSelected() { return g_onlyFrame < 0 || (long)g_frame == g_onlyFrame; }
int DrawHook(void*, uint32_t draw, uint32_t color) {
    if (!FrameSelected()) return 0;
    if (g_perDraw) {
        char n[256];
        if (g_scene) snprintf(n, sizeof n, "%s_d%05u.png", g_scene, draw);
        else snprintf(n, sizeof n, "f%05u_d%05u.png", g_frame, draw);
        Png(n, color);
        if (g_raw && !g_scene) { snprintf(n, sizeof n, "f%05u_d%05u.f32", g_frame, draw); Raw(n, color); }
    }
    return g_until >= 0 && (long)draw >= g_until;
}
void SubmitHook(void*, uint32_t frame, uint32_t present, int after) {
    if (!after) { g_frame = frame; nxgl_set_draw_hook(DrawHook, nullptr); return; }
    if (present) { char n[64]; snprintf(n, sizeof n, "f%05u.png", frame); Png(n, 0); }
}
void OnReadback(void*, uint32_t index, const NxReadback* r, const uint8_t* px) {
    std::vector<uint8_t> rgba((size_t)r->w * r->h * 4);
    for (size_t i = 0; i < (size_t)r->w * r->h; i++) {
        uint8_t* o = &rgba[4 * i];
        if (r->format == NxRb_RGBA8) memcpy(o, px + 4 * i, 4);
        else if (r->format == NxRb_RGBA32F) for (int c = 0; c < 4; c++) { float f; memcpy(&f, px + 16 * i + 4 * c, 4); f = f < 0 ? 0 : f > 1 ? 1 : f; o[c] = (uint8_t)(f * 255 + 0.5f); }
        else if (r->format == NxRb_Depth32F) { float f; memcpy(&f, px + 4 * i, 4); o[0] = o[1] = o[2] = (uint8_t)(f * 255 + 0.5f); o[3] = 255; }
        else { o[0] = o[1] = o[2] = px[i]; o[3] = 255; }
    }
    char p[2048];
    snprintf(p, sizeof p, "%s/rb%04u.png", g_out, index);
    if (!nxpng::Write(p, rgba.data(), r->w, r->h)) { fprintf(stderr, "nx_render: cannot write %s\n", p); exit(2); }
    g_images++;
}
void OnPresent(void*, uint32_t frame, uint32_t image) { char n[64]; snprintf(n, sizeof n, "f%05u.png", frame); Png(n, image); }

bool ReadFile(const char* path, std::vector<uint8_t>& d) {
    FILE* f = fopen(path, "rb");
    if (!f) return false;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    d.resize(n > 0 ? (size_t)n : 0);
    bool ok = fread(d.data(), 1, d.size(), f) == d.size();
    fclose(f);
    return ok;
}
void Summary(const char* text) {
    char p[2048];
    snprintf(p, sizeof p, "%s/summary.txt", g_out);
    FILE* f = fopen(p, "w");
    if (f) { fputs(text, f); fclose(f); }
    fputs(text, stdout);
}
int Usage() {
    fprintf(stderr, "usage: nx_render nxcap <file.nxcap> | d3d9 <file.nxd9> | scene <name|all>  -o <dir> "
                    "[--shaders <dir>] [--native <dir>] [--per-draw] [--raw] [--until-draw N] [--frame F] [--families all|none]\n"
                    "  [--draw-log <file>] [--force-visible]\n");
    return 2;
}
} // namespace

int main(int argc, char** argv) {
    if (argc < 3) return Usage();
    const char* mode = argv[1]; const char* in = argv[2]; const char* shaders = nullptr; const char* nativeDir = nullptr;
    for (int i = 3; i < argc; i++) {
        if (!strcmp(argv[i], "-o") && i + 1 < argc) g_out = argv[++i];
        else if (!strcmp(argv[i], "--shaders") && i + 1 < argc) shaders = argv[++i];
        else if (!strcmp(argv[i], "--native") && i + 1 < argc) nativeDir = argv[++i];
        else if (!strcmp(argv[i], "--per-draw")) g_perDraw = true;
        else if (!strcmp(argv[i], "--until-draw") && i + 1 < argc) g_until = atol(argv[++i]);
        else if (!strcmp(argv[i], "--frame") && i + 1 < argc) g_onlyFrame = atol(argv[++i]);
        else if (!strcmp(argv[i], "--raw")) g_raw = true;
        else if (!strcmp(argv[i], "--force-visible")) nx_d3d9_set_force_visible(1);
        else if (!strcmp(argv[i], "--families") && i + 1 < argc) {
            // d3d9: hand-written d9 families on (all) or off (none, default: every program translated)
            const char* v = argv[++i];
            if (!strcmp(v, "none")) nx_d3d9_set_families(0);
            else if (!strcmp(v, "all")) nx_d3d9_set_families(~0u);
            else return Usage();
        }
        else if (!strcmp(argv[i], "--draw-log") && i + 1 < argc) {
            FILE* f = fopen(argv[++i], "w");
            if (!f) { perror(argv[i]); return 2; }
            nx_d3d9_set_draw_log(f);
        }
        else return Usage();
    }
    if (!g_out) return Usage();
    if (mkdir(g_out, 0755) && errno != EEXIST) { perror(g_out); return 2; }
    char err[512];
    const NxGpuApi* api = nxgl_create(0, 0, err, sizeof err);
    if (!api) { fprintf(stderr, "nx_render: nxgl: %s\n", err); return 1; }
    char sum[1024];
    int rc = 0;
    if (!strcmp(mode, "nxcap")) {
        std::vector<uint8_t> d;
        if (!ReadFile(in, d)) { perror(in); return 2; }
        nx_replay_set_submit_hook(SubmitHook, nullptr);
        NxReplayStats st;
        if (nx_replay(api, d.data(), d.size(), &st, OnReadback, nullptr, err, sizeof err)) { fprintf(stderr, "nx_render: %s\n", err); return 1; }
        snprintf(sum, sizeof sum, "nx_render nxcap: frames %u, readbacks %u (%u differ from the capture), images %u\n",
                 st.frames, st.readbacks, st.readbackMismatches, g_images);
    } else if (!strcmp(mode, "d3d9")) {
        std::vector<uint8_t> d;
        if (!ReadFile(in, d)) { perror(in); return 2; }
        // the executor submits one stream per frame; the draw hook needs the frame number before each submit
        struct H { static void P(void* u, uint32_t f, uint32_t img) { OnPresent(u, f, img); g_frame = f + 1; nxgl_set_draw_hook(DrawHook, nullptr); } };
        g_frame = 0; nxgl_set_draw_hook(DrawHook, nullptr);
        NxD9Stats st;
        nx_d3d9_native_dir(nativeDir);
        if (nx_d3d9_run(api, d.data(), d.size(), shaders, H::P, nullptr, &st, err, sizeof err)) { fprintf(stderr, "nx_render: %s\n", err); return 1; }
        snprintf(sum, sizeof sum, "nx_render d3d9: frames %u, draws %u, skipped %u, clears %u, programs %u, textures %u, images %u%s%s\n",
                 st.frames, st.draws, st.skipped, st.clears, st.shaders, st.textures, g_images,
                 st.skipped ? "; first skip: " : "", st.firstSkip);
        if (st.flushes) {          // frames submitted in parts (the per-frame arena or command buffer was full)
            size_t l = strlen(sum);
            if (l && sum[l - 1] == '\n') sum[--l] = 0;
            snprintf(sum + l, sizeof sum - l, "; mid-frame flushes %u\n", st.flushes);
        }
        uint32_t fam = 0;
        for (int f = 1; f < 8; f++) fam += st.familyDraws[f];
        if (nativeDir || fam) {    // native path: shaders and draws that ran our hand-written GLSL instead of the translation
            size_t l = strlen(sum);
            if (l && sum[l - 1] == '\n') sum[--l] = 0;
            snprintf(sum + l, sizeof sum - l, "; native shaders %u, native draws %u (d9 families %u)\n", st.nativeShaders, st.nativeDraws, fam);
        }
        rc = st.skipped ? 3 : 0;
    } else if (!strcmp(mode, "scene")) {
        nx::Backend* be = nx::GpuBackend(api);
        static n0::Ctx c;
        if (!n0::Init(c, be, err, sizeof err)) { fprintf(stderr, "nx_render: %s\n", err); return 1; }
        static uint8_t rgba[n0::W * n0::H * 4];
        static n0::Checks k;
        int n = 0;
        for (int i = 0; i < n0::kSceneCount; i++) {
            if (strcmp(in, "all") && strcmp(in, n0::kScenes[i].name)) continue;
            g_frame = 0; g_scene = n0::kScenes[i].name; nxgl_set_draw_hook(DrawHook, nullptr);
            k = n0::Checks();
            if (!n0::Run(c, n0::kScenes[i], rgba, k)) { fprintf(stderr, "nx_render: scene %s failed\n", n0::kScenes[i].name); return 1; }
            char p[2048];
            snprintf(p, sizeof p, "%s/%s.png", g_out, n0::kScenes[i].name);
            if (!nxpng::Write(p, rgba, n0::W, n0::H)) { fprintf(stderr, "nx_render: cannot write %s\n", p); return 2; }
            n++; g_images++;
        }
        if (!n) { fprintf(stderr, "nx_render: no scene %s\n", in); return 2; }
        snprintf(sum, sizeof sum, "nx_render scene: %d scenes, images %u\n", n, g_images);
    } else {
        return Usage();
    }
    Summary(sum);
    nxgl_destroy();
    return rc;
}
