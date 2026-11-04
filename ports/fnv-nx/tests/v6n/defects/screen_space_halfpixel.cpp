// V6n DEFECT screen_space_halfpixel (tests/v6n/DEFECTS.md).
// Reference: the engine's screen-space camera (decomp @fnv 0x00e6cf40, the function that builds the view, screen
// and projection matrices and the viewport from one port rect: Do_SetScreenSpaceCameraData's body in D3D9 terms).
//   view: _11 1, _22 -1, _33 1, _41 -0.5, _42 0.5, _43 1, _44 1
//   proj: _11 2, _22 2, _41 -1/W, _42 1/H, _33 1/9999, _43 -1/9999, _44 1        (row vectors, D3D9)
// i.e. clip = (2x - 1 - 1/W, 1 - 2y + 1/H, z/9999, 1): Gamebryo's D3D9 half-pixel correction (geometry at screen x
// lands on window x*W - 0.5, so texel centres meet D3D9 pixel centres) and z scaled into the 1..9999 range.
// Tested: nx::ScreenSpaceProjection (render/native/n1/renderer.cpp; N1 sets view = identity in screen space).
// Window positions under D3D9 viewport rules must agree within 1/256 px; depth within 1e-6.
#include <cstdio>
#include <cmath>
#include "renderer.h"

static void mul(const float* v, const float* m, float* o) {   // row vector * row-major 4x4
    for (int c = 0; c < 4; c++) o[c] = v[0] * m[c] + v[1] * m[4 + c] + v[2] * m[8 + c] + v[3] * m[12 + c];
}
int main() {
    const float W = 1280, H = 720;
    float V[16] = {1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 1, 0, -0.5f, 0.5f, 1, 1};
    float P[16] = {2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1.0f / 9999.0f, 0, -1.0f / W, 1.0f / H, -1.0f / 9999.0f, 1};
    float S[16]; nx::ScreenSpaceProjection(S);
    const float pts[][3] = {{0, 0, 0}, {1, 1, 0}, {0.5f, 0.5f, 0.5f}, {0.25f, 0.75f, 1.0f}, {10.0f / W, 20.0f / H, 0}};
    int bad = 0;
    for (auto& p : pts) {
        float v[4] = {p[0], p[1], p[2], 1}, a[4], b[4], r[4];
        mul(v, V, a); mul(a, P, r);          // engine
        mul(v, S, b);                        // S6n (view = identity)
        float rx = (r[0] / r[3] + 1) / 2 * W, ry = (1 - r[1] / r[3]) / 2 * H, rz = r[2] / r[3];
        float sx = (b[0] / b[3] + 1) / 2 * W, sy = (1 - b[1] / b[3]) / 2 * H, sz = b[2] / b[3];
        int ok = std::fabs(rx - sx) <= 1.0f / 256 && std::fabs(ry - sy) <= 1.0f / 256 && std::fabs(rz - sz) <= 1e-6f;
        bad += !ok;
        printf("screen (%.4f, %.4f, %.2f): engine window (%.3f, %.3f) z %.7f | S6n (%.3f, %.3f) z %.7f  %s\n",
               p[0], p[1], p[2], rx, ry, rz, sx, sy, sz, ok ? "ok" : "DIFFERS");
    }
    printf("screen_space_halfpixel: %s\n", bad ? "FAIL" : "PASS");
    return bad ? 1 : 0;
}
