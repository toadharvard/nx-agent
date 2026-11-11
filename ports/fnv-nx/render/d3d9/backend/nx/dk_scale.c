/* dk_scale.c: the point-scale primitive of the deko3d backend (reference: gl4_scale.c). One fullscreen triangle that fetches, for every texel of the
 * destination rect, exactly one source texel with an integer texelFetch: no filtering, no sRGB conversion, identity swizzle (raw texels), then the optional
 * gamma ramp. The programs are internal DKSH images (dk_scale_dksh.h, generated from dk_scale_vs.glsl / dk_scale_fs.glsl by build_scale_dksh.sh): they
 * need no guest shader, so no translator and no cache is involved.
 *
 * Rule (class-closing, proved in dk_scale_fs.glsl and checked exhaustively by tests/l6/dkscale): destination texel d (relative to the destination rect)
 * takes source texel floor((d + 0.5) * sw / dw) = ((2d + 1) * sw) / (2dw) per axis, integer division, relative to the source rect. Assumptions: the device
 * is created with OriginUpperLeft + YAxisPointsUp + DepthZeroToOne (dk_device), so gl_FragCoord.xy of the pixel is its image column / row; the rects
 * were validated here (inside the level, 1..NXDK_MAX_DIM) so every operand of the division stays below 2^30.
 * Ramp: per channel i = round(clamp(c, 0, 1) * 255), out = float(ramp[ch][i] >> 8) / 255, alpha unchanged. The 768 high bytes are copied to the uniforms
 * during the call (rule P1: the caller's memory is read before returning).
 * Recording: queries paused, destination bound, neutral state, viewport and scissor = the destination rect, the internal programs, a private descriptor
 * set (image 0 = the source view, sampler 0 = nearest / clamp) in one staging block, the uniforms in the same block, a 3-vertex draw, then the global
 * descriptor sets are restored and the queries resume.
 * CONTRACT-REQUEST: guest uniform-buffer slot 15 and texture slot 31 of the fragment stage are reserved for this primitive (a guest binding is not
 * restored by it; nxdk_draw_neutral makes the next BindState re-apply). S7: depth / stencil and multisampled images are refused with nxdk_err (the
 * point-scale rule for them is not defined yet). */
#include <string.h>
#include "dk.h"
#include "dk_fmt.h"
#include "dk_scale_dksh.h"

enum { SC_UBO = 15, SC_TEX = 31, SC_UBO_BYTES = 48 + 768, SC_OFF_IMG = 1024, SC_OFF_SMP = 1056, SC_STAGE = 4096 };
#define SC_CTL_MAX 512u
typedef struct { const uint8_t* a; uint32_t ctl, code, off; } ScProg;
static const ScProg sc_prog[4] = { { sc_vs, SC_vs_CTL, SC_vs_CODE, 0 }, { sc_fs0, SC_fs0_CTL, SC_fs0_CODE, 512 },
                                   { sc_fs1, SC_fs1_CTL, SC_fs1_CODE, 1792 }, { sc_fs2, SC_fs2_CTL, SC_fs2_CODE, 3072 } };
_Static_assert(SC_vs_CODE == 512 && SC_fs0_CODE == 1280 && SC_fs1_CODE == 1280 && SC_fs2_CODE == 1280,
               "code offsets of sc_prog follow the section sizes, 256-aligned");
_Static_assert(SC_vs_CTL <= 512 && SC_fs0_CTL <= 512 && SC_fs1_CTL <= 512 && SC_fs2_CTL <= 512, "control sections fit the aligned copy");
_Static_assert(SC_UBO_BYTES <= SC_OFF_IMG, "uniforms end before the descriptors");

static DkMemBlock sc_code;
static DkShader sc_sh[4];

uint32_t nxdk_scale_init(void) {
    if (sc_code) { nxdk_err("scale: init: already initialised"); return 1; }
    uint32_t bytes = sc_prog[3].off + sc_prog[3].code;
    DkMemBlockMaker mk;
    dkMemBlockMakerDefaults(&mk, nxdk.dev, (bytes + DK_SHADER_CODE_UNUSABLE_SIZE + DK_MEMBLOCK_ALIGNMENT - 1) / DK_MEMBLOCK_ALIGNMENT * DK_MEMBLOCK_ALIGNMENT);
    mk.flags = DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached | DkMemBlockFlags_Code;
    sc_code = dkMemBlockCreate(&mk);
    if (!sc_code) { nxdk_err("scale: init: deko3d refused the code block of %u bytes", mk.size); return 1; }
    uint8_t* cpu = (uint8_t*)dkMemBlockGetCpuAddr(sc_code);
    for (uint32_t i = 0; i < 4; i++) {
        _Alignas(16) uint8_t ctl[SC_CTL_MAX];
        memcpy(ctl, sc_prog[i].a, sc_prog[i].ctl);
        memcpy(cpu + sc_prog[i].off, sc_prog[i].a + sc_prog[i].ctl, sc_prog[i].code);
        DkShaderMaker sm;
        dkShaderMakerDefaults(&sm, sc_code, sc_prog[i].off);
        sm.control = ctl;
        dkShaderInitialize(&sc_sh[i], &sm);
    }
    return 0;
}

void nxdk_scale_shutdown(void) {
    if (!sc_code) return;
    dkMemBlockDestroy(sc_code);
    sc_code = NULL;
    memset(sc_sh, 0, sizeof sc_sh);
}

/* one subresource rect of an image that can be a source or a destination of the primitive; 0 = ok, 1 = nxdk_err'd */
static uint32_t sc_check(const char* who, const NxDkSub* s, uint32_t dst) {
    const NxDkImage* im = s->im;
    if (!im || !im->used) { nxdk_err("scale: %s image is not live", who); return 1; }
    if ((im->flags & NxImg_Depth) || (dk_fmt(im->fmt).flags & (DKFMT_DEPTH | DKFMT_STENCIL))) {
        nxdk_err("scale: %s is a depth / stencil image (S7)", who);
        return 1;
    }
    if (dst && !(dk_fmt(im->fmt).flags & DKFMT_RENDER)) { nxdk_err("scale: destination format %u is not a render target format", im->fmt); return 1; }
    if (im->samples > 1) { nxdk_err("scale: %s is multisampled (%u samples; S7)", who, im->samples); return 1; }
    if (s->level >= im->mips) { nxdk_err("scale: %s level %u of %u", who, s->level, im->mips); return 1; }
    if (s->layer >= (im->type == NxImgType_3D ? im->d : im->layers)) { nxdk_err("scale: %s layer %u out of range", who, s->layer); return 1; }
    uint64_t lw = im->w >> s->level, lh = im->h >> s->level;
    if (!lw) lw = 1;
    if (!lh) lh = 1;
    if (!s->w || !s->h || s->w > NXDK_MAX_DIM || s->h > NXDK_MAX_DIM || (uint64_t)s->x + s->w > lw || (uint64_t)s->y + s->h > lh) {
        nxdk_err("scale: %s rect %u,%u %ux%u is outside level %u (%llux%llu) or not in 1..%d", who, s->x, s->y, s->w, s->h, s->level,
                 (unsigned long long)lw, (unsigned long long)lh, NXDK_MAX_DIM);
        return 1;
    }
    return 0;
}

void nxdk_scale_point(DkCmdBuf cmd, const NxDkSub* s, const NxDkSub* d, const uint16_t* ramp) {
    if (!sc_code) { nxdk_err("scale: not initialised"); return; }
    if (!s || !d) { nxdk_err("scale: NULL %s", s ? "destination" : "source"); return; }
    if (sc_check("source", s, 0) || sc_check("destination", d, 1)) return;
    const NxDkImage* im = s->im;
    uint32_t kind = im->type == NxImgType_Cube ? 1 : im->type == NxImgType_3D ? 2 : 0;
    DkMemBlock blk = nxdk_stage_alloc(SC_STAGE);                     /* refused: nxdk_stage_alloc has reported it, nothing is recorded */
    if (!blk) return;
    uint8_t* cpu = (uint8_t*)dkMemBlockGetCpuAddr(blk);
    memset(cpu, 0, SC_STAGE);
    int32_t u[12] = { (int32_t)s->x, (int32_t)s->y, (int32_t)s->w, (int32_t)s->h, (int32_t)d->x, (int32_t)d->y, (int32_t)d->w, (int32_t)d->h,
                      ramp ? 1 : 0, kind ? (int32_t)s->layer : 0, 0, 0 };
    memcpy(cpu, u, sizeof u);
    if (ramp) for (uint32_t k = 0; k < 768; k++) cpu[48 + k] = (uint8_t)(ramp[k] >> 8);
    DkImageView v;
    dkImageViewDefaults(&v, &im->dk);
    v.mipLevelOffset = s->level;
    v.mipLevelCount = 1;
    v.type = kind == 0 ? DkImageType_2D : kind == 1 ? DkImageType_2DArray : DkImageType_3D;   /* the shader variant is chosen by the same kind */
    if (kind == 1) { v.layerOffset = 0; v.layerCount = im->layers; }
    DkSampler smp;
    dkSamplerDefaults(&smp);
    smp.minFilter = smp.magFilter = DkFilter_Nearest;
    smp.mipFilter = DkMipFilter_None;
    smp.wrapMode[0] = smp.wrapMode[1] = smp.wrapMode[2] = DkWrapMode_ClampToEdge;
    dkImageDescriptorInitialize((DkImageDescriptor*)(cpu + SC_OFF_IMG), &v, false, false);
    dkSamplerDescriptorInitialize((DkSamplerDescriptor*)(cpu + SC_OFF_SMP), &smp);
    DkGpuAddr g = dkMemBlockGetGpuAddr(blk);
    DkViewport vp = { (float)d->x, (float)d->y, (float)d->w, (float)d->h, 0.0f, 1.0f };
    DkScissor sc = { d->x, d->y, d->w, d->h };
    const DkShader* sh[2] = { &sc_sh[0], &sc_sh[1 + kind] };
    nxdk_query_pause();
    nxdk_target_bind(cmd, d);
    nxdk_draw_neutral(cmd, nxchk_lvl(d->im->w, d->level), nxchk_lvl(d->im->h, d->level));
    dkCmdBufSetViewports(cmd, 0, &vp, 1);
    dkCmdBufSetScissors(cmd, 0, &sc, 1);
    dkCmdBufBindShaders(cmd, DkStageFlag_Vertex | DkStageFlag_Fragment, sh, 2);
    dkCmdBufBindImageDescriptorSet(cmd, g + SC_OFF_IMG, 1);
    dkCmdBufBindSamplerDescriptorSet(cmd, g + SC_OFF_SMP, 1);
    dkCmdBufBarrier(cmd, DkBarrier_Full, DkInvalidateFlags_Descriptors);
    dkCmdBufBindTexture(cmd, DkStage_Fragment, SC_TEX, dkMakeTextureHandle(0, 0));
    dkCmdBufBindUniformBuffer(cmd, DkStage_Fragment, SC_UBO, g, SC_UBO_BYTES);
    dkCmdBufDraw(cmd, DkPrimitive_Triangles, 3, 1, 0, 0);
    nxdk_bind_descriptors(cmd);
    dkCmdBufBarrier(cmd, DkBarrier_Full, DkInvalidateFlags_Descriptors | DkInvalidateFlags_Image);
    nxdk_query_resume();
}
