// V6n DEFECT camera_port_rounding (tests/v6n/DEFECTS.md).
// Reference: the engine's Do_SetCameraData (decomp @fnv 0x00e6c780) builds the D3D9 viewport from the port rect by
// truncation:  X = (unsigned long)(W * left), Y = (unsigned long)(H * (1 - top)),
//              Width = (unsigned long)(W * (right - left)), Height = (unsigned long)(H * (top - bottom)).
// (Its view and projection agree with nx::CameraView / nx::CameraProjection; checked here too.)
// Tested: nx::Renderer::Do_SetCameraData (render/native/n1/renderer.cpp), which rounds (+0.5f).
#include <cstdio>
#include <cstring>
#include <cmath>
#include "renderer.h"
#include "../gl/nxgl.h"

int main() {
    char err[256];
    const NxGpuApi* api = nxgl_create(0, 0, err, sizeof err);
    if (!api) { fprintf(stderr, "nxgl: %s\n", err); return 2; }
    nx::Backend* be = nx::GpuBackend(api);
    const uint32_t W = 1280, H = 720;
    NxImageDesc rt; memset(&rt, 0, sizeof rt);
    rt.width = W; rt.height = H; rt.format = NxFmt_RGBA8; rt.flags = NxImg_RenderTarget;
    uint32_t back = be->ImageCreate(rt);
    static nx::Renderer r;
    if (!r.Init(be, W, H, back, 0)) { fprintf(stderr, "init\n"); return 2; }
    nx::NiRectF ports[5];   // fields set by name (NiRectF is left, right, top, bottom)
    const float lrtb[5][4] = {{0, 1, 1, 0}, {0.3333f, 0.6667f, 0.9f, 0.1f}, {0.25f, 0.75f, 0.75f, 0.25f},
                              {0.0004f, 0.9996f, 0.9996f, 0.0004f}, {0.1f, 0.55f, 0.7f, 0.05f}};
    for (int i = 0; i < 5; i++) { ports[i].left = lrtb[i][0]; ports[i].right = lrtb[i][1]; ports[i].top = lrtb[i][2]; ports[i].bottom = lrtb[i][3]; }
    nx::NiPoint3f loc = {1, 2, 3}, dir = {0, 0, 1}, up = {0, 1, 0}, right = {1, 0, 0};
    nx::NiFrustumF fr = {-0.5f, 0.5f, 0.4f, -0.4f, 1.0f, 1000.0f, false};
    int bad = 0;
    for (const auto& p : ports) {
        r.Do_SetCameraData(loc, dir, up, right, fr, p);
        const NxD3D9Viewport& v = r.viewport();
        unsigned long ex = (unsigned long)(W * p.left), ey = (unsigned long)(H * (1.0f - p.top));
        unsigned long ew = (unsigned long)(W * (p.right - p.left)), eh = (unsigned long)(H * (p.top - p.bottom));
        int ok = v.X == ex && v.Y == ey && v.Width == ew && v.Height == eh;
        bad += !ok;
        printf("port l %.4f t %.4f r %.4f b %.4f: engine viewport %lu %lu %lu %lu | S6n %u %u %u %u  %s\n", p.left, p.top,
               p.right, p.bottom, ex, ey, ew, eh, v.X, v.Y, v.Width, v.Height, ok ? "ok" : "DIFFERS");
    }
    // projection (right-handed branch of 0x00e6c780): proj[0][0] 2/w, [2][0] -(r+l)/w, [1][1] 2/h, [2][1] -(t+b)/h,
    // [2][2] f/(f-n), [3][2] -f*n/(f-n), [2][3] 1
    const float* m = r.proj();
    float w = fr.right - fr.left, h = fr.top - fr.bottom, dr = fr.farZ - fr.nearZ;
    float e[16] = {2 / w, 0, 0, 0, 0, 2 / h, 0, 0, -(fr.right + fr.left) / w, -(fr.top + fr.bottom) / h, fr.farZ / dr, 1,
                   0, 0, -fr.farZ * fr.nearZ / dr, 0};
    int pbad = 0;
    for (int i = 0; i < 16; i++) pbad += std::fabs(m[i] - e[i]) > 1e-5f * (1 + std::fabs(e[i]));
    printf("projection vs 0x00e6c780: %s\n", pbad ? "DIFFERS" : "ok");
    printf("camera_port_rounding: %s\n", bad || pbad ? "FAIL" : "PASS");
    return bad || pbad ? 1 : 0;
}
