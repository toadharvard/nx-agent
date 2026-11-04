/* fnv-nx native renderer: a small C harness over NxGpuApi for tests (V6n's oracle comparisons, S6n's own tests).
 * LP64 host code (calls nxgpu_dispatch directly). It draws one full-screen triangle with a caller's pixel shader
 * and a caller's NxRenderState / NxDynamicState (e.g. straight from nx_d3d9_translate), clears rectangles, and
 * reads targets back as RGBA8, RGBA32F, depth or stencil.
 *
 *   NxHarness h; nxh_init(&h, api, 64, 64, NxFmt_RGBA16F, NxFmt_Z24S8);
 *   uint32_t ps = nxh_shader(&h, NxStage_Fragment, glsl, 0, 0);          // GLSL 4.60, deko3d flavour (GL backend)
 *   nxh_draw(&h, ps, &state, &dyn, ubo, sizeof ubo, &epi, handles, n);   // one frame, contents kept
 *   nxh_readback(&h, NxRb_RGBA32F, x, y, w, h, dst, size);
 * Shader interface: the vertex stage is built in (nxh_vertex_glsl) and passes, like the families' VS,
 *   location 0 vec2 uv ((0,0) top-left .. (1,1) bottom-right), location 1 vec4 colour (1), location 2 float fog (1);
 * the pixel shader gets the caller's UBO at fragment slot 1, NxEpilogue at slot 2 (when given), textures 0..n-1.
 * On deko3d the caller passes DKSH for both stages (nxh_set_vertex_dksh). */
#ifndef FNVNX_NX_HARNESS_H
#define FNVNX_NX_HARNESS_H
#include <stdint.h>
#include "nx/gpu_abi.h"
#ifdef __cplusplus
extern "C" {
#endif

typedef struct NxHarness {
    const NxGpuApi* api;
    uint32_t w, h, color, depth, colorFormat, depthFormat;
    uint32_t vs, mem, frame, nextState;
    uint8_t* memCpu;
    uint32_t memSize;
    uint32_t words[1024];
} NxHarness;

extern const char nxh_vertex_glsl[];

int nxh_init(NxHarness* h, const NxGpuApi* api, uint32_t w, uint32_t hgt, uint32_t colorFormat, uint32_t depthFormat);
int nxh_set_vertex_dksh(NxHarness* h, const void* dksh, uint32_t size);   /* deko3d: replaces the built-in VS */
uint32_t nxh_shader(NxHarness* h, uint32_t stage, const char* glsl, const void* dksh, uint32_t dkshSize);
uint32_t nxh_image(NxHarness* h, const NxImageDesc* d);
uint32_t nxh_sampler(NxHarness* h, const NxSamplerState* s);
/* One frame: (optional) clear, one full-screen triangle. state/dyn may be 0 (no blend, no depth, full viewport). */
int nxh_draw(NxHarness* h, uint32_t ps, const NxRenderState* state, const NxDynamicState* dyn, const void* ubo,
             uint32_t uboSize, const NxEpilogue* epi, const uint32_t* handles, uint32_t nHandles);
/* One frame that clears a rectangle (pixels, top-left origin) of the targets: flags 1 colour, 2 depth, 4 stencil. */
int nxh_clear(NxHarness* h, uint32_t flags, uint32_t rgba8, float depth, uint32_t stencil, uint32_t x, uint32_t y,
              uint32_t w, uint32_t hgt);
/* Reads the colour target (NxRb_RGBA8 / NxRb_RGBA32F) or the depth target (NxRb_Depth32F / NxRb_Stencil8). */
int nxh_readback(NxHarness* h, uint32_t format, uint32_t x, uint32_t y, uint32_t w, uint32_t hgt, void* dst, uint32_t size);

#ifdef __cplusplus
}
#endif
#endif
