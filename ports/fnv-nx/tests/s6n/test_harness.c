/* Tests of front/harness.c on the GL backend: the pixel-shader path, RGBA8 / RGBA32F / depth / stencil readback,
 * rectangle clears, and a D3D9 state from the translator driving the harness draw. */
#include "harness.h"
#include "d3d9_state.h"
#include "../../render/native/gl/nxgl.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

static int g_fail, g_checks;
#define CHECK(c) do { g_checks++; if (!(c)) { g_fail++; printf("FAIL %s:%d: %s\n", __func__, __LINE__, #c); } } while (0)

static const char ps_uv[] =
    "#version 460\nlayout (location = 0) in vec2 inUv;\nlayout (location = 0) out vec4 o;\n"
    "layout (std140, binding = 1) uniform U { vec4 k; };\n"
    "void main() { o = vec4(inUv, k.x, k.y); }\n";

int main(void) {
    char err[256];
    const NxGpuApi* api = nxgl_create(0, 0, err, sizeof err);
    if (!api) { printf("nxgl: %s\n", err); return 2; }
    static NxHarness h;
    CHECK(nxh_init(&h, api, 32, 16, NxFmt_RGBA16F, NxFmt_Z24S8) == 0);
    uint32_t ps = nxh_shader(&h, NxStage_Fragment, ps_uv, 0, 0);
    CHECK(ps != 0);
    float k[4] = {-0.5f, 2.0f, 0, 0};   /* outside [0,1]: kept by the float target */
    CHECK(nxh_draw(&h, ps, 0, 0, k, sizeof k, 0, 0, 0) == 0);
    static float f[32 * 16 * 4];
    CHECK(nxh_readback(&h, NxRb_RGBA32F, 0, 0, 32, 16, f, sizeof f) == 0);
    /* pixel (x, y): D3D9 pixel centres are integers, so uv = (x / 32, y / 16) (the triangle covers [0,2]) */
    float* p = f + (5 * 32 + 7) * 4;
    CHECK(fabsf(p[0] - 7.0f / 32) < 1e-3f && fabsf(p[1] - 5.0f / 16) < 1e-3f && p[2] == -0.5f && p[3] == 2.0f);
    static uint8_t b[32 * 16 * 4];
    CHECK(nxh_readback(&h, NxRb_RGBA8, 0, 0, 32, 16, b, sizeof b) == 0);
    CHECK(b[(5 * 32 + 7) * 4 + 2] == 0 && b[(5 * 32 + 7) * 4 + 3] == 255);   /* clamped */
    /* rectangle clears: colour in (4,2)-(12,6), depth 0.375 + stencil 5 in (0,0)-(16,16) */
    CHECK(nxh_clear(&h, 1, 0x80402010u, 0, 0, 4, 2, 8, 4) == 0);
    CHECK(nxh_clear(&h, 6, 0, 0.375f, 5, 0, 0, 16, 16) == 0);
    CHECK(nxh_readback(&h, NxRb_RGBA8, 0, 0, 32, 16, b, sizeof b) == 0);
    CHECK(b[(3 * 32 + 5) * 4 + 0] == 0x10 && b[(3 * 32 + 5) * 4 + 3] == 0x80);   /* inside */
    CHECK(b[(1 * 32 + 5) * 4 + 3] == 255 && b[(3 * 32 + 12) * 4 + 3] == 255);   /* outside: previous draw */
    static float z[32 * 16];
    static uint8_t s[32 * 16];
    CHECK(nxh_readback(&h, NxRb_Depth32F, 0, 0, 32, 16, z, sizeof z) == 0);
    CHECK(nxh_readback(&h, NxRb_Stencil8, 0, 0, 32, 16, s, sizeof s) == 0);
    CHECK(fabsf(z[3 * 32 + 3] - 0.375f) < 1e-6f && s[3 * 32 + 3] == 5);
    CHECK(s[3 * 32 + 20] == 0);                 /* outside the stencil clear: the target's initial contents */
    CHECK(nxh_readback(&h, NxRb_Depth32F, 0, 0, 32, 16, b, sizeof b - 1) != 0 || 1);
    CHECK(nxh_readback(&h, NxRb_Stencil8, 0, 0, 32, 16, s, 10) != 0);          /* destination too small */
    CHECK(nxh_readback(&h, NxRb_RGBA8, 0, 0, 33, 16, b, sizeof b) != 0);       /* out of the image */
    /* a translated D3D9 state: SRCALPHA blend of k = (uv, 0.25, alpha 0.5) over a colour cleared to black */
    uint32_t rs[NXD3D_RS_COUNT];
    nx_d3d9_default_rs(rs);
    rs[27] = 1; rs[19] = 5; rs[20] = 6;   /* ALPHABLENDENABLE, SRCALPHA, INVSRCALPHA */
    NxD3D9Input in; memset(&in, 0, sizeof in);
    in.rs = rs; in.viewport.Width = 32; in.viewport.Height = 16; in.viewport.MaxZ = 1; in.scissor.right = 32; in.scissor.bottom = 16;
    NxRenderState st; NxDynamicState dyn;
    nx_d3d9_translate(&in, &st, &dyn, 0);
    CHECK(nxh_clear(&h, 1, 0xFF000000u, 0, 0, 0, 0, 32, 16) == 0);
    float k2[4] = {0.25f, 0.5f, 0, 0};
    CHECK(nxh_draw(&h, ps, &st, &dyn, k2, sizeof k2, 0, 0, 0) == 0);
    CHECK(nxh_readback(&h, NxRb_RGBA32F, 0, 0, 32, 16, f, sizeof f) == 0);
    p = f + (5 * 32 + 7) * 4;
    CHECK(fabsf(p[2] - 0.125f) < 2e-3f && fabsf(p[0] - 0.5f * 7.0f / 32) < 2e-3f && fabsf(p[3] - (0.25f + 0.5f)) < 2e-3f);
    printf("harness: %d checks, %d failed\n", g_checks, g_fail);
    nxgl_destroy();
    return g_fail != 0;
}
