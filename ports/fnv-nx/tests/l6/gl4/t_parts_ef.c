/* Part e: ids (reuse only after destroy), destroy of mem / shaders / images, the tables (version 3 while B2b is
 ), a malformed stream executes nothing. Part f: the v3 ops still work (draws, UBO, vertex / index buffers,
 * textures with mips, rect and sRGB clears, depth / stencil readback). */
#include "t_common.h"

static const char k_vs[] = "#version 460\nvoid main() { vec2 p[3] = vec2[3](vec2(-1,-1), vec2(1,-1), vec2(-1,1)); gl_Position = vec4(p[gl_VertexID], 0, 1); }\n";
static const char k_vs_in[] = "#version 460\nlayout (location = 0) in vec2 p;\nvoid main() { gl_Position = vec4(p, 0, 1); }\n";
static const char k_fs[] = "#version 460\nlayout (location = 0) out vec4 o;\nlayout (std140, binding = 1) uniform U { vec4 k; };\nvoid main() { o = k; }\n";
static const char k_fs_tex[] = "#version 460\nlayout (location = 0) out vec4 o;\nlayout (binding = 0) uniform sampler2D t;\n"
                               "void main() { o = texelFetch(t, ivec2(gl_FragCoord.xy) / 8, 0); }\n";

static uint32_t shader(uint32_t stage, const char* src) {
    NxShaderDesc d = {stage, 0, 0, (uint64_t)(uintptr_t)src, (uint32_t)strlen(src), 0};
    return (uint32_t)t_ext(NXGPU_FN_SHADER_CREATE, 1, (uint64_t)(uintptr_t)&d);
}
static void state_words(Stream* s, uint32_t id, uint32_t vs, uint32_t ps, uint32_t layout) {
    t_op(s, NxOp_DefineState, 12, id, vs, ps, layout, 0, 0xF, 0, 0, 0, 0, 0, 0);   /* w0: all four colour write masks */
}
static int readback(uint32_t img, uint32_t fmt, uint32_t w, uint32_t h, void* dst, size_t size) {
    NxReadback rd = {img, 0, 0, w, h, fmt, (uint64_t)(uintptr_t)dst, (uint32_t)size, 0};
    return t_ext(NXGPU_FN_READBACK, 1, (uint64_t)(uintptr_t)&rd) == 0;
}
static const uint8_t* px(const uint8_t* b, int w, int x, int y) { return b + (y * w + x) * 4; }
static int is_rgba(const uint8_t* p, int r, int g, int b, int a) { return p[0] == r && p[1] == g && p[2] == b && p[3] == a; }

static void part_e(void) {
    uint32_t ids[24], n = 0;
    for (int i = 0; i < 20; i++) ids[n++] = t_image(NxImgType_2D, i & 1 ? NxFmt_R8 : NxFmt_RGBA8, 4, 4, 1, 1, 1);
    int ok = 1;
    for (uint32_t i = 0; i < n; i++) {
        ok &= ids[i] >= 3;   /* the swapchain image owns ids 1 and 2 (RGBA8 + its sRGB view) */
        uint32_t span_i = (i & 1) ? 1 : 2;   /* RGBA8 (even i) reserves id and id + 1 */
        for (uint32_t j = i + 1; j < n; j++) {
            uint32_t span_j = (j & 1) ? 1 : 2;
            ok &= ids[i] + span_i <= ids[j] || ids[j] + span_j <= ids[i];
        }
    }
    CHECK(ok);
    /* destroy an R8 and an RGBA8: ids are reused only now, never while live */
    uint32_t dr8 = ids[3], drgba = ids[4];
    CHECK(t_ext(NXGPU_FN_IMAGE_DESTROY, 1, (uint64_t)dr8) == 0 && t_ext(NXGPU_FN_IMAGE_DESTROY, 1, (uint64_t)drgba) == 0);
    uint32_t nr8 = t_image(NxImgType_2D, NxFmt_R8, 4, 4, 1, 1, 1), nrgba = t_image(NxImgType_2D, NxFmt_RGBA8, 4, 4, 1, 1, 1);
    CHECK(nr8 == dr8 || nr8 == drgba || nr8 == drgba + 1);
    CHECK(nrgba == drgba || nrgba > ids[19]);
    for (uint32_t i = 0; i < n; i++) CHECK(i == 3 || i == 4 || (nr8 != ids[i] && nrgba != ids[i]));
    /* dead / foreign ids are refused */
    uint32_t box[6] = {0, 0, 0, 4, 4, 1};
    uint8_t buf[64];
    uint32_t live_view = ids[0] + 1;   /* ids[0] is a live RGBA8: its view id is not an image */
    CHECK(t_ext(NXGPU_FN_IMAGE_DESTROY, 1, (uint64_t)live_view) == NXGPU_BAD_CALL);
    CHECK(t_ext(NXGPU_FN_IMAGE_DESTROY, 1, (uint64_t)ids[0]) == 0);   /* ...while the image itself is */
    CHECK(t_ext(NXGPU_FN_IMAGE_DESTROY, 1, (uint64_t)ids[0]) == NXGPU_BAD_CALL);   /* double destroy */
    CHECK(t_ext(NXGPU_FN_IMAGE_DESTROY, 1, (uint64_t)1) == NXGPU_BAD_CALL);                  /* the swapchain image */
    CHECK(t_ext(NXGPU_FN_IMAGE_DESTROY, 1, (uint64_t)0) == NXGPU_BAD_CALL);
    CHECK(t_ext(NXGPU_FN_IMAGE_DESTROY, 1, (uint64_t)99999) == NXGPU_BAD_CALL);
    CHECK(t_read(4000, 0, 0, box, buf, sizeof buf, 16) == NXGPU_BAD_CALL);
    /* the same id, reused with a different image, must not draw into the old framebuffer: clear colours differ */
    uint32_t a = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 1, 1);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_op(s, NxOp_BeginPass, 8, a, 0, 1, 0xFF0000FFu, 0, 0, 8, 8); t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    CHECK(t_ext(NXGPU_FN_IMAGE_DESTROY, 1, (uint64_t)a) == 0);
    uint32_t b = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 1, 1);
    CHECK(b == a);
    t_op(s, NxOp_BeginPass, 8, b, 0, 1, 0xFF00FF00u, 0, 0, 8, 8); t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    static uint8_t rb[8 * 8 * 4];
    CHECK(readback(b, NxRb_RGBA8, 8, 8, rb, sizeof rb) && is_rgba(px(rb, 8, 3, 3), 0, 255, 0, 255));
    /* churn: 3000 create / destroy cycles never run out of ids */
    int churn = 1;
    for (int i = 0; i < 3000; i++) { uint32_t t = t_image(NxImgType_2D, NxFmt_RGBA8, 2, 2, 1, 1, 1); churn &= t != 0 && t_ext(NXGPU_FN_IMAGE_DESTROY, 1, (uint64_t)t) == 0; }
    CHECK(churn);
    /* memory blocks and shaders: reuse after destroy only */
    static uint8_t blk[4096] __attribute__((aligned(4096)));
    NxMemBlockDesc md = {(uint64_t)(uintptr_t)blk, 4096, NxMem_Generic};
    uint32_t m1 = (uint32_t)t_ext(NXGPU_FN_MEM_CREATE, 1, (uint64_t)(uintptr_t)&md), m2 = (uint32_t)t_ext(NXGPU_FN_MEM_CREATE, 1, (uint64_t)(uintptr_t)&md);
    CHECK(m1 && m2 && m1 != m2 && m1 != g_mem && m2 != g_mem);
    CHECK(t_ext(NXGPU_FN_MEM_DESTROY, 1, (uint64_t)m1) == 0 && t_ext(NXGPU_FN_MEM_DESTROY, 1, (uint64_t)m1) == NXGPU_BAD_CALL);
    CHECK(t_ext(NXGPU_FN_MEM_CREATE, 1, (uint64_t)(uintptr_t)&md) == m1);
    uint32_t v1 = shader(NxStage_Vertex, k_vs), v2 = shader(NxStage_Vertex, k_vs);
    CHECK(v1 && v2 && v1 != v2);
    CHECK(t_ext(NXGPU_FN_SHADER_DESTROY, 1, (uint64_t)v1) == 0 && t_ext(NXGPU_FN_SHADER_DESTROY, 1, (uint64_t)v1) == NXGPU_BAD_CALL);
    CHECK(shader(NxStage_Vertex, k_vs) == v1);
    /* tables: version 4; v4 functions with arguments that name nothing are bad calls */
    NxGpuInfo info;
    CHECK(t_ext(NXGPU_FN_INFO, 1, (uint64_t)(uintptr_t)&info) == 0 && info.magic == NXGPU_MAGIC && info.backend == 2 && info.version == NXGPU_VERSION4);
    CHECK(t_ext(NXGPU_FN_SAMPLER_UPDATE, 2, (uint64_t)1, (uint64_t)0) == NXGPU_BAD_CALL);
    CHECK(t_ext(NXGPU_FN_QUERY_RESULT, 3, (uint64_t)1, (uint64_t)0, (uint64_t)0) == NXGPU_BAD_CALL);
    CHECK(t_ext(NXGPU_FN_PRESENT, 1, (uint64_t)0) == NXGPU_BAD_CALL);
    CHECK(t_ext(NXGPU_FN_COUNT4, 1, (uint64_t)0) == NXGPU_BAD_CALL);
    /* a stream with a malformed v4 op (BeginPass4 without its target words) is refused as a whole: nothing before it executes */
    t_op(s, NxOp_BeginPass, 8, b, 0, 1, 0xFFFF0000u, 0, 0, 8, 8);
    t_op(s, NxOp_BeginPass4, 1, 0);
    CHECK(t_submit(s) == NXGPU_BAD_CALL);
    CHECK(readback(b, NxRb_RGBA8, 8, 8, rb, sizeof rb) && is_rgba(px(rb, 8, 3, 3), 0, 255, 0, 255));
    free(s);
}

static void part_f(void) {
    uint32_t vs = shader(NxStage_Vertex, k_vs), vsin = shader(NxStage_Vertex, k_vs_in), fs = shader(NxStage_Fragment, k_fs);
    uint32_t fst = shader(NxStage_Fragment, k_fs_tex);
    CHECK(vs && vsin && fs && fst);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    float red[4] = {1, 0, 0, 1};
    memcpy(g_scratch, red, 16);
    static uint8_t rb[16 * 16 * 4];
    /* triangle (lower-left half) into the swapchain image, colour from a fragment UBO */
    state_words(s, 1, vs, fs, 0);
    t_op(s, NxOp_BeginPass, 8, 0, 0, 1, 0xFF000000u, 0, 0, 16, 16);
    t_op(s, NxOp_BindUbo, 5, 2, 1, g_mem, 0, 16);
    t_op(s, NxOp_BindState, 1, 1);
    t_op(s, NxOp_Draw, 5, 0, 3, 1, 0, 0);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0 && g_stats.draws == 1 && g_stats.state_binds == 1);
    CHECK(readback(0, NxRb_RGBA8, 16, 16, rb, sizeof rb));
    CHECK(is_rgba(px(rb, 16, 1, 14), 255, 0, 0, 255) && is_rgba(px(rb, 16, 1, 6), 255, 0, 0, 255) && is_rgba(px(rb, 16, 14, 1), 0, 0, 0, 255));
    /* vertex layout + vertex buffer + index buffer: the same triangle, drawn indexed, in green */
    float verts[6] = {-1, -1, 1, -1, -1, 1}, green[4] = {0, 1, 0, 1};
    uint16_t idx[3] = {0, 1, 2};
    memcpy(g_scratch, green, 16); memcpy(g_scratch + 4096, verts, sizeof verts); memcpy(g_scratch + 8192, idx, sizeof idx);
    NxVtxLayout lay;
    memset(&lay, 0, sizeof lay);
    lay.count = 1; lay.stride = 8; lay.attr[0].type = NxAttr_Float32; lay.attr[0].components = 2;
    uint32_t lw[9]; memcpy(lw, &lay, sizeof lay);
    t_op(s, NxOp_DefineLayout, 10, 1, lw[0], lw[1], lw[2], lw[3], lw[4], lw[5], lw[6], lw[7], lw[8]);
    state_words(s, 2, vsin, fs, 1);
    t_op(s, NxOp_BeginPass, 8, 0, 0, 1, 0xFF000000u, 0, 0, 16, 16);
    t_op(s, NxOp_BindUbo, 5, 2, 1, g_mem, 0, 16);
    t_op(s, NxOp_BindVtx, 3, g_mem, 4096, sizeof verts);
    t_op(s, NxOp_BindIdx, 4, g_mem, 8192, sizeof idx, 0);
    t_op(s, NxOp_BindState, 1, 2);
    t_op(s, NxOp_DrawIndexed, 6, 0, 3, 1, 0, 0, 0);
    CHECK(t_submit(s) == 0);
    CHECK(readback(0, NxRb_RGBA8, 16, 16, rb, sizeof rb));
    CHECK(is_rgba(px(rb, 16, 1, 14), 0, 255, 0, 255) && is_rgba(px(rb, 16, 14, 1), 0, 0, 0, 255));
    /* v3 image_create with two mips of data, sampled: 2x2 texels magnified 8x; level 1 read back */
    uint8_t tex[2 * 2 * 4 + 4] = {255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255, 255, 255, 255, 10, 20, 30, 40};
    NxImageDesc d;
    memset(&d, 0, sizeof d);
    d.width = 2; d.height = 2; d.format = NxFmt_RGBA8; d.mips = 2; d.data = (uint64_t)(uintptr_t)tex; d.data_size = sizeof tex;
    uint32_t timg = (uint32_t)t_ext(NXGPU_FN_IMAGE_CREATE, 1, (uint64_t)(uintptr_t)&d);
    CHECK(timg != 0);
    uint8_t l1[4];
    uint32_t b1[6] = {0, 0, 0, 1, 1, 1};
    CHECK(t_read(timg, 1, 0, b1, l1, 4, 4) == 0 && !memcmp(l1, tex + 16, 4));
    state_words(s, 3, vs, fst, 0);
    t_op(s, NxOp_BeginPass, 8, 0, 0, 1, 0xFF000000u, 0, 0, 16, 16);
    t_op(s, NxOp_BindTextures, 4, NxStage_Fragment, 0, 1, NX_TEX_HANDLE(timg, 0));
    t_op(s, NxOp_BindState, 1, 3);
    t_op(s, NxOp_Draw, 5, 0, 3, 1, 0, 0);
    CHECK(t_submit(s) == 0);
    CHECK(readback(0, NxRb_RGBA8, 16, 16, rb, sizeof rb));
    CHECK(is_rgba(px(rb, 16, 1, 6), 255, 0, 0, 255) && is_rgba(px(rb, 16, 1, 12), 0, 0, 255, 255) && is_rgba(px(rb, 16, 14, 1), 0, 0, 0, 255));   /* texels (0,0), (0,1); lower-left half only */
    /* rect clears: plain clear then an sRGB clear of the left half: 0x80 -> 188 */
    NxImageDesc rt;
    memset(&rt, 0, sizeof rt);
    rt.width = 8; rt.height = 8; rt.format = NxFmt_RGBA8; rt.flags = NxImg_RenderTarget;
    uint32_t cimg = (uint32_t)t_ext(NXGPU_FN_IMAGE_CREATE, 1, (uint64_t)(uintptr_t)&rt);
    memset(&rt, 0, sizeof rt);
    rt.width = 8; rt.height = 8; rt.format = NxFmt_Z24S8; rt.flags = NxImg_Depth;
    uint32_t zimg = (uint32_t)t_ext(NXGPU_FN_IMAGE_CREATE, 1, (uint64_t)(uintptr_t)&rt);
    float z = 0.375f; uint32_t zb; memcpy(&zb, &z, 4);
    t_op(s, NxOp_BeginPass, 8, cimg, zimg, 0, 0, 0, 0, 8, 8);
    t_op(s, NxOp_Clear, 8, NxClear_Color, 0xFF808080u, 0, 0, 0, 0, 8, 8);
    t_op(s, NxOp_Clear, 8, NxClear_Color | NxClear_Srgb, 0xFF808080u, 0, 0, 0, 0, 4, 8);
    t_op(s, NxOp_Clear, 8, NxClear_Depth | NxClear_Stencil, 0, zb, 5, 0, 0, 8, 8);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    static uint8_t c8[8 * 8 * 4];
    CHECK(readback(cimg, NxRb_RGBA8, 8, 8, c8, sizeof c8));
    CHECK(c8[(2 * 8 + 1) * 4] == 188 && c8[(2 * 8 + 1) * 4 + 3] == 255 && c8[(2 * 8 + 6) * 4] == 128);
    float zf[64]; uint8_t st8[64];
    CHECK(readback(zimg, NxRb_Depth32F, 8, 8, zf, sizeof zf) && readback(zimg, NxRb_Stencil8, 8, 8, st8, sizeof st8));
    CHECK(fabsf(zf[27] - 0.375f) < 1e-6f && st8[27] == 5);
    CHECK(!readback(cimg, NxRb_Depth32F, 8, 8, zf, sizeof zf));   /* colour image, depth readback: refused */
    free(s);
}

void part_ef(char which) { if (which == 'e') part_e(); else part_f(); }
