/* dk_msres.c: the multisample-resolve primitive of the deko3d backend (reference: gl4_blit.c gl4_resolve_tex, a nearest blit of the multisampled image).
 * One full-target triangle, 1:1 texels, that reads the 2DMS source with texelFetch on a sampler2DMS and writes the single-sample destination of the same
 * format, width and height. The programs are internal DKSH images (dk_msres_dksh.h, generated from dk_msres_*.glsl by build_msres_dksh.sh).
 * Modes: NXDK_MSRES_AVG (colour: mean of the n samples), NXDK_MSRES_S0_DEPTH (depth plane of sample 0), NXDK_MSRES_S0_STENCIL (stencil plane of sample 0).
 * It replaces the refusals X4 (multisampled depth / stencil, GL gives sample 0) and X5 (8 samples or a format without the 2D engine, GL gives the average).
 *
 * CONTRACT-REQUEST (dk.h, not edited here; a guarded local declaration below keeps this file self-contained):
 *   enum { NXDK_MSRES_AVG = 0, NXDK_MSRES_S0_DEPTH = 1, NXDK_MSRES_S0_STENCIL = 2 };
 *   uint32_t nxdk_ms_resolve(const NxDkImage* src, uint32_t layer, const NxDkImage* dst, uint32_t mode);   0 = recorded, 1 = one nxdk_err, nothing recorded
 *   uint32_t nxdk_msres_init(void); void nxdk_msres_shutdown(void);   called by dk_stream.c next to nxdk_scale_init / nxdk_scale_shutdown (nxdk_stream_init)
 *   Callers (dk_read.c, dk_blit.c, dk_present.c) create the single-sample destination with the nxdk_tmp_image helper that dk_blit.c already requests (same
 *   format, same size, freed after the SUBMIT wait) and then read / blit / present from it with the existing single-sample same-format paths.
 *   Fragment uniform-buffer slot 15 and texture slot 31 are reserved (as dk_scale.c); nxdk_draw_neutral makes the next BindState re-apply.
 * Semantics: src is a live 2D image with 2, 4 or 8 samples; dst is a live single-sample non-3D image of the same fmt / w / h; layer is the destination layer
 * (the cube face of a cube dst, 0 for a 2D dst; src has one layer). Refused with one nxdk_err inside an open pass (the target would replace the pass's, P8)
 * and outside a SUBMIT (no command buffer).
 * Decisions:
 *  - The sample count is a uniform, not a variant: the loop bound u.x is exact and one program serves 2 / 4 / 8, so no variant table can fall out of step.
 *  - Depth destination: the depth plane of a same-format depth image, written by gl_FragDepth (depth test Always, write on: Maxwell writes depth only with
 *    the test on). The result is bit exact (proof in dk_msres_fs_depth.glsl). Console-only assumptions: round-to-nearest sampler and ROP conversions.
 *  - Stencil destination: Maxwell has no shader stencil output. The stencil plane is cleared to 0 and eight draws set one bit each (draw b discards unless
 *    bit b of sample 0 is set; stencil Always / Replace, reference = write mask = 1 << b), so every one of the 256 values is reproduced.
 *  - No integer branch: dk_fmt.c lists 35 formats and every DKFMT_RENDER colour format is unorm, snorm or float (sRGB are views of unorm ones), so the
 *    sum of n samples in float scaled by the exact 1 / n is the average for every format that can be multisampled (X1: only renderable formats are).
 *    Formats with a view swizzle (BGR10A2, A4R4G4B4) are not DKFMT_RENDER, so they cannot be multisampled and need no branch either.
 * Recording: queries paused, destination bound, neutral state, viewport 0..1 and scissor = the target, the internal programs, a private descriptor set
 * (image 0 = the source view, sampler 0 = nearest / clamp) and the uniform ranges in one staging block, the draw(s), the global descriptor sets restored,
 * the neutral state recorded again (so the depth / stencil state set here never leaks), the queries resumed. Assumption (as dk_scale.c): the device is
 * OriginUpperLeft + YAxisPointsUp + DepthZeroToOne, so gl_FragCoord.xy is the image column / row. */
#include <string.h>
#include "dk.h"
#include "dk_fmt.h"
#include "dk_msres_dksh.h"

#ifndef NXDK_HAVE_MSRES   /* dk.h defines this once it carries the CONTRACT-REQUEST above */
enum { NXDK_MSRES_AVG = 0, NXDK_MSRES_S0_DEPTH = 1, NXDK_MSRES_S0_STENCIL = 2 };
uint32_t nxdk_ms_resolve(const NxDkImage* src, uint32_t layer, const NxDkImage* dst, uint32_t mode);
uint32_t nxdk_msres_init(void);
void nxdk_msres_shutdown(void);
#endif

enum { MR_UBO = 15, MR_TEX = 31, MR_RANGE = 256, MR_OFF_IMG = 2048, MR_OFF_SMP = 2080, MR_STAGE = 4096 };
#define MR_CTL_MAX 512u
typedef struct { const uint8_t* a; uint32_t ctl, code; } MrProg;
static const MrProg mr_prog[4] = { { mr_vs, MR_vs_CTL, MR_vs_CODE }, { mr_avg, MR_avg_CTL, MR_avg_CODE },
                                   { mr_depth, MR_depth_CTL, MR_depth_CODE }, { mr_stencil, MR_stencil_CTL, MR_stencil_CODE } };
_Static_assert(MR_vs_CTL <= MR_CTL_MAX && MR_avg_CTL <= MR_CTL_MAX && MR_depth_CTL <= MR_CTL_MAX && MR_stencil_CTL <= MR_CTL_MAX,
               "control sections fit the aligned copy");
_Static_assert(8 * MR_RANGE <= MR_OFF_IMG && MR_OFF_SMP + 32 <= MR_STAGE, "uniform ranges, then the two descriptors, inside the staging block");

static DkMemBlock mr_code;
static DkShader mr_sh[4];
static uint32_t mr_off(uint32_t i) {   /* code sections are 256-aligned */
    uint32_t o = 0;
    for (uint32_t k = 0; k < i; k++) o += (mr_prog[k].code + 255u) & ~255u;
    return o;
}

uint32_t nxdk_msres_init(void) {
    if (mr_code) { nxdk_err("msres: init: already initialised"); return 1; }
    uint32_t bytes = mr_off(3) + mr_prog[3].code;
    DkMemBlockMaker mk;
    dkMemBlockMakerDefaults(&mk, nxdk.dev, (bytes + DK_SHADER_CODE_UNUSABLE_SIZE + DK_MEMBLOCK_ALIGNMENT - 1) / DK_MEMBLOCK_ALIGNMENT * DK_MEMBLOCK_ALIGNMENT);
    mk.flags = DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached | DkMemBlockFlags_Code;
    mr_code = dkMemBlockCreate(&mk);
    if (!mr_code) { nxdk_err("msres: init: deko3d refused the code block of %u bytes", mk.size); return 1; }
    uint8_t* cpu = (uint8_t*)dkMemBlockGetCpuAddr(mr_code);
    for (uint32_t i = 0; i < 4; i++) {
        _Alignas(16) uint8_t ctl[MR_CTL_MAX];
        memcpy(ctl, mr_prog[i].a, mr_prog[i].ctl);
        memcpy(cpu + mr_off(i), mr_prog[i].a + mr_prog[i].ctl, mr_prog[i].code);
        DkShaderMaker sm;
        dkShaderMakerDefaults(&sm, mr_code, mr_off(i));
        sm.control = ctl;
        dkShaderInitialize(&mr_sh[i], &sm);
    }
    return 0;
}

void nxdk_msres_shutdown(void) {
    if (!mr_code) return;
    dkMemBlockDestroy(mr_code);
    mr_code = NULL;
    memset(mr_sh, 0, sizeof mr_sh);
}

#define MR_FAIL(...) do { nxdk_err(__VA_ARGS__); return 1; } while (0)

static uint32_t mr_check(const NxDkImage* s, uint32_t layer, const NxDkImage* d, uint32_t mode) {
    if (!mr_code) MR_FAIL("msres: not initialised");
    if (!nxdk_s.cmd) MR_FAIL("msres: outside a SUBMIT");
    if (nxdk_s.inPass) MR_FAIL("msres: inside an open pass");
    if (!s || !s->used || !d || !d->used) MR_FAIL("msres: %s image is not live", s && s->used ? "destination" : "source");
    if (mode > NXDK_MSRES_S0_STENCIL) MR_FAIL("msres: unknown mode %u", mode);
    if (s->type != NxImgType_2D || (s->samples != 2 && s->samples != 4 && s->samples != 8)) MR_FAIL("msres: source is not a 2D image of 2, 4 or 8 samples");
    if (d->samples != 1 || d->type == NxImgType_3D) MR_FAIL("msres: destination is not a single-sample 2D / cube image");
    if (d->fmt != s->fmt || d->w != s->w || d->h != s->h) MR_FAIL("msres: destination format / size differs from the source");
    if (layer >= d->layers) MR_FAIL("msres: layer %u of %u", layer, d->layers);
    const uint32_t f = dk_fmt(s->fmt).flags;
    if (mode == NXDK_MSRES_AVG && (f & (DKFMT_DEPTH | DKFMT_STENCIL))) MR_FAIL("msres: AVG of a depth / stencil format %u", s->fmt);
    if (mode == NXDK_MSRES_AVG && !(f & DKFMT_RENDER)) MR_FAIL("msres: format %u is not a render target format", s->fmt);
    if (mode == NXDK_MSRES_S0_DEPTH && !(f & DKFMT_DEPTH)) MR_FAIL("msres: S0_DEPTH of format %u without depth", s->fmt);
    if (mode == NXDK_MSRES_S0_STENCIL && !(f & DKFMT_STENCIL)) MR_FAIL("msres: S0_STENCIL of format %u without stencil", s->fmt);
    return 0;
}

uint32_t nxdk_ms_resolve(const NxDkImage* src, uint32_t layer, const NxDkImage* dst, uint32_t mode) {
    if (mr_check(src, layer, dst, mode)) return 1;
    DkMemBlock blk = nxdk_stage_alloc(MR_STAGE);                     /* refused: nxdk_stage_alloc has reported it, nothing is recorded */
    if (!blk) return 1;
    DkCmdBuf cmd = nxdk_s.cmd;
    uint8_t* cpu = (uint8_t*)dkMemBlockGetCpuAddr(blk);
    memset(cpu, 0, MR_STAGE);
    for (uint32_t b = 0; b < 8; b++) {                               /* range b: u = (samples, 1 << b, 0, 0), f = (1 / samples, 0, 0, 0); 1 / n is exact */
        const uint32_t u[4] = { src->samples, 1u << b, 0, 0 };
        const float f[4] = { 1.0f / (float)src->samples, 0.0f, 0.0f, 0.0f };
        memcpy(cpu + b * MR_RANGE, u, sizeof u);
        memcpy(cpu + b * MR_RANGE + 16, f, sizeof f);
    }
    DkImageView v, dv;
    dkImageViewDefaults(&v, &src->dk);                               /* raw (non-sRGB) texels, identity swizzle */
    v.type = DkImageType_2DMS;
    v.mipLevelOffset = 0;
    v.mipLevelCount = 1;
    if (mode != NXDK_MSRES_AVG) v.dsSource = mode == NXDK_MSRES_S0_DEPTH ? DkDsSource_Depth : DkDsSource_Stencil;
    DkSampler smp;
    dkSamplerDefaults(&smp);
    smp.minFilter = smp.magFilter = DkFilter_Nearest;
    smp.mipFilter = DkMipFilter_None;
    smp.wrapMode[0] = smp.wrapMode[1] = smp.wrapMode[2] = DkWrapMode_ClampToEdge;
    dkImageDescriptorInitialize((DkImageDescriptor*)(cpu + MR_OFF_IMG), &v, false, false);
    dkSamplerDescriptorInitialize((DkSamplerDescriptor*)(cpu + MR_OFF_SMP), &smp);
    DkGpuAddr g = dkMemBlockGetGpuAddr(blk);
    const DkViewport vp = { 0.0f, 0.0f, (float)dst->w, (float)dst->h, 0.0f, 1.0f };
    const DkScissor sc = { 0, 0, dst->w, dst->h };
    const DkShader* sh[2] = { &mr_sh[0], &mr_sh[1 + mode] };
    nxdk_query_pause();
    if (mode == NXDK_MSRES_AVG) {
        const NxDkSub t = { dst, 0, 0, layer, 0, 0, dst->w, dst->h };
        nxdk_target_bind(cmd, &t);                                   /* colour target + viewport + scissor */
    } else {
        dkImageViewDefaults(&dv, &dst->dk);
        dv.mipLevelOffset = 0;
        dv.mipLevelCount = 1;
        dv.layerOffset = layer;
        dv.type = DkImageType_2D;                                    /* one face of a cube is a 2D view */
        dkCmdBufBindRenderTargets(cmd, NULL, 0, &dv);                /* depth target only: nxdk_target_bind is colour-only */
        dkCmdBufSetViewports(cmd, 0, &vp, 1);
        dkCmdBufSetScissors(cmd, 0, &sc, 1);
    }
    nxdk_draw_neutral(cmd);
    if (mode != NXDK_MSRES_AVG) {
        DkDepthStencilState ds;
        dkDepthStencilStateDefaults(&ds);
        ds.depthTestEnable = mode == NXDK_MSRES_S0_DEPTH;            /* Always + write: a depth write needs the test on */
        ds.depthWriteEnable = mode == NXDK_MSRES_S0_DEPTH;
        ds.depthCompareOp = DkCompareOp_Always;
        ds.stencilTestEnable = mode == NXDK_MSRES_S0_STENCIL;        /* defaults: Always, pass op Replace */
        dkCmdBufBindDepthStencilState(cmd, &ds);
        if (mode == NXDK_MSRES_S0_STENCIL) dkCmdBufClearDepthStencil(cmd, false, 0.0f, 0xFF, 0);   /* bounded by the scissor = the whole target */
    }
    dkCmdBufBindShaders(cmd, DkStageFlag_Vertex | DkStageFlag_Fragment, sh, 2);
    dkCmdBufBindImageDescriptorSet(cmd, g + MR_OFF_IMG, 1);
    dkCmdBufBindSamplerDescriptorSet(cmd, g + MR_OFF_SMP, 1);
    dkCmdBufBarrier(cmd, DkBarrier_Full, DkInvalidateFlags_Descriptors | DkInvalidateFlags_Image);   /* the source was just rendered */
    dkCmdBufBindTexture(cmd, DkStage_Fragment, MR_TEX, dkMakeTextureHandle(0, 0));
    for (uint32_t b = 0; b < (mode == NXDK_MSRES_S0_STENCIL ? 8u : 1u); b++) {
        if (mode == NXDK_MSRES_S0_STENCIL) dkCmdBufSetStencil(cmd, DkFace_FrontAndBack, (uint8_t)(1u << b), (uint8_t)(1u << b), 0xFF);
        dkCmdBufBindUniformBuffer(cmd, DkStage_Fragment, MR_UBO, g + b * MR_RANGE, 32);
        dkCmdBufDraw(cmd, DkPrimitive_Triangles, 3, 1, 0, 0);
    }
    nxdk_bind_descriptors(cmd);
    if (mode != NXDK_MSRES_AVG) nxdk_draw_neutral(cmd);               /* neutral depth / stencil state again (draw state, mask, reference) */
    dkCmdBufBarrier(cmd, DkBarrier_Full, DkInvalidateFlags_Descriptors | DkInvalidateFlags_Image);
    nxdk_query_resume();
    return 0;
}
