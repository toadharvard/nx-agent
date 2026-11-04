/* fnv-nx native renderer: test harness over NxGpuApi. See harness.h. */
#include "harness.h"
#include <malloc.h>
#include <string.h>

const char nxh_vertex_glsl[] =
    "#version 460\n"
    "layout (location = 0) out vec2 outUv;\n"
    "layout (location = 1) out vec4 outColor;\n"
    "layout (location = 2) out float outFog;\n"
    "void main() {\n"
    "    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));\n"   /* (0,0) (2,0) (0,2) */
    "    outUv = p;\n"
    "    gl_Position = vec4(p.x * 2.0 - 1.0, 1.0 - p.y * 2.0, 0.5, 1.0);\n"
    "    outColor = vec4(1.0);\n"
    "    outFog = 1.0;\n"
    "}\n";

static uint64_t call(const NxHarness* h, uint32_t fn, uint64_t a) { return nxgpu_dispatch(h->api, fn, &a, 1); }
static uint32_t fbits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }

uint32_t nxh_shader(NxHarness* h, uint32_t stage, const char* glsl, const void* dksh, uint32_t dkshSize) {
    NxShaderDesc d;
    memset(&d, 0, sizeof d);
    d.stage = stage; d.glsl = (uint64_t)(uintptr_t)glsl; d.glsl_size = glsl ? (uint32_t)strlen(glsl) : 0;
    d.dksh = (uint64_t)(uintptr_t)dksh; d.dksh_size = dkshSize;
    return (uint32_t)call(h, NXGPU_FN_SHADER_CREATE, (uint64_t)(uintptr_t)&d);
}
uint32_t nxh_image(NxHarness* h, const NxImageDesc* d) { return (uint32_t)call(h, NXGPU_FN_IMAGE_CREATE, (uint64_t)(uintptr_t)d); }
uint32_t nxh_sampler(NxHarness* h, const NxSamplerState* s) {
    return (uint32_t)call(h, NXGPU_FN_SAMPLER_CREATE, (uint64_t)(uintptr_t)s);
}

int nxh_init(NxHarness* h, const NxGpuApi* api, uint32_t w, uint32_t hgt, uint32_t colorFormat, uint32_t depthFormat) {
    memset(h, 0, sizeof *h);
    h->api = api; h->w = w; h->h = hgt; h->colorFormat = colorFormat; h->depthFormat = depthFormat;
    NxImageDesc d;
    memset(&d, 0, sizeof d);
    d.width = (uint16_t)w; d.height = (uint16_t)hgt; d.format = (uint8_t)colorFormat; d.flags = NxImg_RenderTarget;
    h->color = nxh_image(h, &d);
    if (depthFormat) { d.format = (uint8_t)depthFormat; d.flags = NxImg_RenderTarget | NxImg_Depth; h->depth = nxh_image(h, &d); }
    h->memSize = 64 * 1024;
    h->memCpu = (uint8_t*)memalign(0x1000, h->memSize);
    if (!h->memCpu) return -1;
    memset(h->memCpu, 0, h->memSize);
    NxMemBlockDesc m = {(uint64_t)(uintptr_t)h->memCpu, h->memSize, NxMem_Generic};
    h->mem = (uint32_t)call(h, NXGPU_FN_MEM_CREATE, (uint64_t)(uintptr_t)&m);
    h->vs = nxh_shader(h, NxStage_Vertex, nxh_vertex_glsl, 0, 0);
    return h->color && h->mem && (!depthFormat || h->depth) ? 0 : -1;
}

int nxh_set_vertex_dksh(NxHarness* h, const void* dksh, uint32_t size) {
    h->vs = nxh_shader(h, NxStage_Vertex, nxh_vertex_glsl, dksh, size);
    return h->vs ? 0 : -1;
}

static uint32_t* op(NxHarness* h, uint32_t* n, uint32_t code, uint32_t argc) {
    if (*n + 1 + argc + 1 > sizeof h->words / 4) return 0;
    h->words[*n] = code | argc << 16;
    uint32_t* a = h->words + *n + 1;
    *n += 1 + argc;
    return a;
}

static int submit(NxHarness* h, uint32_t n) {
    op(h, &n, NxOp_End, 0);
    NxSubmit s;
    memset(&s, 0, sizeof s);
    s.stream = (uint64_t)(uintptr_t)h->words; s.words = n; s.frame = h->frame;
    call(h, NXGPU_FN_SUBMIT, (uint64_t)(uintptr_t)&s);
    call(h, NXGPU_FN_WAIT_FRAME, h->frame);
    h->frame++;
    return 0;
}

static void begin(NxHarness* h, uint32_t* n) {
    uint32_t* a = op(h, n, NxOp_BeginPass, 8);
    a[0] = h->color; a[1] = h->depth; a[2] = 0; a[3] = 0; a[4] = fbits(1.0f); a[5] = 0; a[6] = h->w; a[7] = h->h;
}

int nxh_clear(NxHarness* h, uint32_t flags, uint32_t rgba8, float depth, uint32_t stencil, uint32_t x, uint32_t y,
              uint32_t w, uint32_t hgt) {
    uint32_t n = 0;
    begin(h, &n);
    uint32_t* a = op(h, &n, NxOp_Clear, 8);
    a[0] = flags; a[1] = rgba8; a[2] = fbits(depth); a[3] = stencil; a[4] = x; a[5] = y; a[6] = w; a[7] = hgt;
    op(h, &n, NxOp_EndPass, 0);
    return submit(h, n);
}

int nxh_draw(NxHarness* h, uint32_t ps, const NxRenderState* state, const NxDynamicState* dyn, const void* ubo,
             uint32_t uboSize, const NxEpilogue* epi, const uint32_t* handles, uint32_t nHandles) {
    if (uboSize > 16384 || nHandles > 16) return -1;
    NxRenderState none;
    memset(&none, 0, sizeof none);
    none.w[0] = 0xFFFFull << 32;   /* all colour writes */
    if (!state) state = &none;
    uint32_t id = ++h->nextState, n = 0, *a;
    a = op(h, &n, NxOp_DefineState, 12);
    a[0] = id; a[1] = h->vs; a[2] = ps; a[3] = 0;
    for (int i = 0; i < 4; i++) { a[4 + 2 * i] = (uint32_t)state->w[i]; a[5 + 2 * i] = (uint32_t)(state->w[i] >> 32); }
    begin(h, &n);
    a = op(h, &n, NxOp_BindState, 1); a[0] = id;
    if (dyn) {
        a = op(h, &n, NxOp_Viewport, 6); memcpy(a, &dyn->viewport, 24);
        a = op(h, &n, NxOp_Scissor, 4); memcpy(a, &dyn->scissor, 16);
        a = op(h, &n, NxOp_BlendConst, 4); memcpy(a, dyn->blendConstant, 16);
        a = op(h, &n, NxOp_DepthBias, 3); a[0] = fbits(dyn->depthBiasUnits); a[1] = fbits(dyn->depthBiasSlope); a[2] = 0;
    }
    if (ubo && uboSize) {
        memcpy(h->memCpu, ubo, uboSize);
        a = op(h, &n, NxOp_BindUbo, 5); a[0] = 3; a[1] = 1; a[2] = h->mem; a[3] = 0; a[4] = (uboSize + 255) & ~255u;
    }
    if (epi) {
        memcpy(h->memCpu + 16384, epi, sizeof *epi);
        a = op(h, &n, NxOp_BindUbo, 5); a[0] = 2; a[1] = 2; a[2] = h->mem; a[3] = 16384; a[4] = 256;
    }
    if (nHandles) {
        a = op(h, &n, NxOp_BindTextures, 3 + nHandles);
        a[0] = NxStage_Fragment; a[1] = 0; a[2] = nHandles; memcpy(a + 3, handles, nHandles * 4);
    }
    a = op(h, &n, NxOp_Draw, 5); a[0] = NxPrim_Triangles; a[1] = 3; a[2] = 1; a[3] = 0; a[4] = 0;
    op(h, &n, NxOp_EndPass, 0);
    return submit(h, n);
}

int nxh_readback(NxHarness* h, uint32_t format, uint32_t x, uint32_t y, uint32_t w, uint32_t hgt, void* dst, uint32_t size) {
    NxReadback r;
    memset(&r, 0, sizeof r);
    r.image = format == NxRb_Depth32F || format == NxRb_Stencil8 ? h->depth : h->color;
    r.x = x; r.y = y; r.w = w; r.h = hgt; r.format = format; r.dst = (uint64_t)(uintptr_t)dst; r.dst_size = size;
    return call(h, NXGPU_FN_READBACK, (uint64_t)(uintptr_t)&r) == 0 ? 0 : -1;
}
