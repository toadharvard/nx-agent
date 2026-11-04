// fnv-nx native renderer: N0 standalone scenes (stream S6n). The same code runs on the GL reference backend (host,
// n0/run_gl.cpp) and on deko3d (Switch guest, n0/guest_main.cpp). Each scene renders one frame into a 160x120
// RGBA8 target; the runner reads it back, compares it with tests/s6n/golden/<name>.png and evaluates the scene's
// own conformance checks (expected pixel values computed on the CPU from the D3D9 rules).
#ifndef FNVNX_N0_SCENES_H
#define FNVNX_N0_SCENES_H

#include "nx/front.h"

namespace n0 {

enum { W = 160, H = 120, kMaxChecks = 512 };

struct Check {
    const char* what;
    int16_t x, y;
    uint8_t rgba[4];
    uint8_t tol;             // max abs difference per channel
    uint8_t mask;            // channels compared (bit 0 R .. bit 3 A)
};

struct Checks {
    Check c[kMaxChecks];
    int n = 0;
    int expectStateBinds = -1, gotStateBinds = 0;   // BindState ops in the frame, checked when >= 0
    // whole-image expectation: pixels with imageMask != 0 must equal image (RGB, exact); used by the raster scenes
    bool hasImage = false;
    const char* imageWhat = nullptr;
    uint8_t image[W * H * 4];
    uint8_t imageMask[W * H];
    void Add(const char* what, int x, int y, uint8_t r, uint8_t g, uint8_t b, uint8_t a, int tol = 1, int mask = 15);
};

struct Ctx {
    nx::Backend* be = nullptr;
    nx::StateCache* sc = nullptr;
    nx::Frame* frame = nullptr;
    nx::StaticBuffer* vb = nullptr;
    uint16_t vsQuad = 0, vsTri = 0, psSolid = 0, psTex = 0, psText = 0;
    uint16_t layQuad = 0, layTri = 0, layTile = 0;
    uint16_t vsTile[3] = {0, 0, 0}, psTile[3] = {0, 0, 0};   // tile family: (-, scroll, vc) / (-, alphamap, vc)
    uint16_t vsLit = 0, psLit = 0, psLitSpec = 0, vsDepth = 0, psDepth = 0, psDepthOut = 0, vsLand = 0, psLand = 0,
             vsSky = 0, psSky = 0, psSkyClouds = 0, layN2 = 0;   // N2 families, one 36-byte vertex format
    uint16_t vsLitSkin = 0, vsDepthSkin = 0, layN2Skin = 0;   // N2 skinning: 44-byte vertices (+ weights, indices)
    uint32_t quadOff = 0;            // unit quad, triangle strip, in vb
    uint32_t rt = 0, depth = 0;      // 160x120 RGBA8 target, Z24S8
    uint32_t frameNo = 0;
    uint32_t stateBinds = 0, packets = 0;   // of the last frame
};

// Creates shaders (nx_shader_blobs), buffers and targets. Returns false with a reason in err.
bool Init(Ctx& c, nx::Backend* be, char* err, uint32_t errlen);

typedef void (*SceneFn)(Ctx& c, Checks& k);
struct Scene { const char* name; SceneFn fn; };
extern const Scene kScenes[];
extern const int kSceneCount;

// Runs a scene and reads the target back into rgba (W*H*4, row 0 = top). Returns false if readback failed.
bool Run(Ctx& c, const Scene& s, uint8_t* rgba, Checks& k);
// Evaluates the checks against rgba; writes one line per failure into log. Returns the failure count.
int Evaluate(const Checks& k, const uint8_t* rgba, char* log, uint32_t loglen);

} // namespace n0

#endif
