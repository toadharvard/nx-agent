/* fnv-nx L6 deko3d backend: NxRenderState / NxDynamicState / NxSamplerState -> deko3d state (see dk_state.h for the rule, errors and assumptions).
 * Every table is indexed by the Nx enum / field value and sized by _Static_assert; enumerator names are used, never numbers. */
#include <string.h>
#include "dk_state.h"
#include "../nxgpu4.h"

#define N(a) (sizeof(a) / sizeof((a)[0]))
#define BFS(n, d) [NxBf_##n] = DkBlendFactor_##d
static const DkCompareOp CMP[8] = {DkCompareOp_Never, DkCompareOp_Less, DkCompareOp_Equal, DkCompareOp_Lequal, DkCompareOp_Greater,
                                   DkCompareOp_NotEqual, DkCompareOp_Gequal, DkCompareOp_Always};
static const DkStencilOp SOP[8] = {DkStencilOp_Keep, DkStencilOp_Zero, DkStencilOp_Replace, DkStencilOp_Incr, DkStencilOp_Decr,
                                   DkStencilOp_Invert, DkStencilOp_IncrWrap, DkStencilOp_DecrWrap};
static const DkBlendOp BOP[NxBop_Max + 1] = {[NxBop_Add] = DkBlendOp_Add, [NxBop_Subtract] = DkBlendOp_Sub, [NxBop_RevSubtract] = DkBlendOp_RevSub,
                                             [NxBop_Min] = DkBlendOp_Min, [NxBop_Max] = DkBlendOp_Max};
static const DkBlendFactor BF[NxBf_Count] = {
    BFS(Zero, Zero), BFS(One, One), BFS(SrcColor, SrcColor), BFS(InvSrcColor, InvSrcColor), BFS(SrcAlpha, SrcAlpha), BFS(InvSrcAlpha, InvSrcAlpha),
    BFS(DstAlpha, DstAlpha), BFS(InvDstAlpha, InvDstAlpha), BFS(DstColor, DstColor), BFS(InvDstColor, InvDstColor),
    BFS(SrcAlphaSat, SrcAlphaSaturate), BFS(ConstColor, ConstColor), BFS(InvConstColor, InvConstColor), BFS(Src1Color, Src1Color),
    BFS(InvSrc1Color, InvSrc1Color)};
static const DkFace CULL[3] = {[NxCull_None] = DkFace_None, [NxCull_CW] = DkFace_Front, [NxCull_CCW] = DkFace_Back};
static const DkPolygonMode FILL[3] = {[NxFill_Solid] = DkPolygonMode_Fill, [NxFill_Wireframe] = DkPolygonMode_Line, [NxFill_Point] = DkPolygonMode_Point};
static const DkWrapMode WRAP[5] = {[NxWrap_Repeat] = DkWrapMode_Repeat, [NxWrap_Mirror] = DkWrapMode_MirroredRepeat, [NxWrap_Clamp] = DkWrapMode_ClampToEdge,
                                   [NxWrap_Border] = DkWrapMode_ClampToBorder, [NxWrap_MirrorOnce] = DkWrapMode_MirrorClampToEdge};
static const DkMipFilter MIP[3] = {[NxMip_None] = DkMipFilter_None, [NxMip_Nearest] = DkMipFilter_Nearest, [NxMip_Linear] = DkMipFilter_Linear};
_Static_assert(N(BF) == NxBf_Count && N(BOP) == 5 && N(CULL) == 3 && N(FILL) == 3 && N(WRAP) == 5 && N(MIP) == 3, "an ABI enum changed");
_Static_assert(NxCull_CCW == 2 && NxFill_Point == 2 && NxWrap_MirrorOnce == 4 && NxMip_Linear == 2 && NxBop_Max == 4 && NxBf_Count == 15, "an ABI enum changed");

#define F(w, shift, bits) NXRS_GET(w, shift, bits)
static int fail(int e) { return e; }   /* every error path precedes the first write: the entry memset already leaves *out all zero */
static int ms_mode(uint32_t samples, DkMsMode* m) {
    switch (samples) {
    case 1: *m = DkMsMode_1x; return 0;
    case 2: *m = DkMsMode_2x; return 0;
    case 4: *m = DkMsMode_4x; return 0;
    case 8: *m = DkMsMode_8x; return 0;
    }
    return 1;
}

int nxdk_state(const NxRenderState* rs, uint32_t samples, NxDkState* o) {
    uint64_t w0 = rs->w[0], w1 = rs->w[1], w2 = rs->w[2], w3 = rs->w[3];
    memset(o, 0, sizeof *o);
    uint32_t sf = F(w0, NXRS_B_SRC, 5), df = F(w0, NXRS_B_DST, 5), op = F(w0, NXRS_B_OP, 3), saf = F(w0, NXRS_B_SRCA, 5), daf = F(w0, NXRS_B_DSTA, 5), opa = F(w0, NXRS_B_OPA, 3);
    uint32_t cull = F(w2, NXRS_R_CULL, 2), fill = F(w2, NXRS_R_FILL, 2);
    int on = F(w0, NXRS_B_ENABLE, 1);
    if ((on && (sf >= NxBf_Count || df >= NxBf_Count || saf >= NxBf_Count || daf >= NxBf_Count || op > NxBop_Max || opa > NxBop_Max)) ||
        cull > NxCull_CCW || fill > NxFill_Point || (w3 >> 4 & 0x0FFFFFFF) != 0) return fail(NXDK_E_FIELD);
    if (ms_mode(samples, &o->derived.msMode)) return fail(NXDK_E_SAMPLES);
    if (!(w3 >> NXRS_M_MSAA & 1)) o->derived.msMode = DkMsMode_1x;
    /* rasterizer */
    o->rast.rasterizerEnable = 1;
    o->rast.depthClampEnable = w3 >> NXRS_M_NOCLIP & 1;
    o->rast.polygonModeFront = o->rast.polygonModeBack = FILL[fill];
    o->rast.cullMode = CULL[cull];
    o->rast.frontFace = DkFrontFace_CW;
    o->rast.provokingVertex = DkProvokingVertex_First;
    o->rast.polygonSmoothEnableMask = (w3 >> NXRS_M_AALINE & 1) ? DkPolygonFlag_Line : 0;
    o->rast.depthBiasEnableMask = F(w2, NXRS_R_BIAS, 1) ? DkPolygonFlag_All : 0;
    o->derived.scissorEnable = F(w2, NXRS_R_SCISSOR, 1); o->derived.srgbWrite = F(w2, NXRS_R_SRGB, 1); o->derived.flatShade = F(w2, NXRS_R_FLAT, 1);
    o->derived.clipPlaneMask = F(w2, NXRS_R_CLIP, 8); o->derived.pointSprite = F(w2, NXRS_R_POINTSPRITE, 1);
    /* colour: blend on RT 0-3, the write masks (Nx bit order = DkColorMask) */
    o->color.blendEnableMask = on ? 0x0F : 0;
    o->color.logicOp = DkLogicOp_Copy;
    o->color.alphaCompareOp = DkCompareOp_Always;
    o->write.masks = 0xFFFFFFFFu;
    for (uint32_t rt = 0; rt < 4; rt++) {
        uint32_t m = F(w0, NXRS_B_MASK(rt), 4), d = (m & 1 ? DkColorMask_R : 0) | (m & 2 ? DkColorMask_G : 0) | (m & 4 ? DkColorMask_B : 0) | (m & 8 ? DkColorMask_A : 0);
        o->write.masks = (o->write.masks & ~(0xFu << 4 * rt)) | d << 4 * rt;
    }
    /* blend (the fields are ignored when blending is off, as in GL; the patched deko3d, toolchain/deko3d/0001, writes FuncAlphaDst from dstAlphaBlendFactor) */
    o->blend.colorBlendOp = o->blend.alphaBlendOp = DkBlendOp_Add;
    o->blend.srcColorBlendFactor = o->blend.srcAlphaBlendFactor = DkBlendFactor_One;
    o->blend.dstColorBlendFactor = o->blend.dstAlphaBlendFactor = DkBlendFactor_Zero;
    if (on) {
        o->blend.colorBlendOp = BOP[op]; o->blend.alphaBlendOp = BOP[opa];
        o->blend.srcColorBlendFactor = BF[sf]; o->blend.dstColorBlendFactor = BF[df];
        o->blend.srcAlphaBlendFactor = BF[saf]; o->blend.dstAlphaBlendFactor = BF[daf];
    }
    /* depth / stencil: GL writes depth only while the depth test is on; Always / Keep when a test is off */
    int st = F(w1, NXRS_S_ENABLE, 1), dt = F(w1, NXRS_D_TEST, 1);
    o->ds.depthTestEnable = dt; o->ds.stencilTestEnable = st;
    o->ds.depthWriteEnable = dt && F(w1, NXRS_D_WRITE, 1);
    o->ds.depthCompareOp = dt ? CMP[F(w1, NXRS_D_FUNC, 3)] : DkCompareOp_Always;
    o->ds.stencilFrontCompareOp = st ? CMP[F(w1, NXRS_S_FUNC, 3)] : DkCompareOp_Always;
    o->ds.stencilFrontFailOp = st ? SOP[F(w1, NXRS_S_FAIL, 3)] : DkStencilOp_Keep;
    o->ds.stencilFrontDepthFailOp = st ? SOP[F(w1, NXRS_S_ZFAIL, 3)] : DkStencilOp_Keep;
    o->ds.stencilFrontPassOp = st ? SOP[F(w1, NXRS_S_PASS, 3)] : DkStencilOp_Keep;
    o->ds.stencilBackCompareOp = st ? CMP[F(w1, NXRS_S_BFUNC, 3)] : DkCompareOp_Always;
    o->ds.stencilBackFailOp = st ? SOP[F(w1, NXRS_S_BFAIL, 3)] : DkStencilOp_Keep;
    o->ds.stencilBackDepthFailOp = st ? SOP[F(w1, NXRS_S_BZFAIL, 3)] : DkStencilOp_Keep;
    o->ds.stencilBackPassOp = st ? SOP[F(w1, NXRS_S_BPASS, 3)] : DkStencilOp_Keep;
    return NXDK_OK;
}

int nxdk_dyn(const NxRenderState* rs, const NxDynamicState* d, uint32_t samples, NxDkDyn* o) {
    DkMsMode m;
    memset(o, 0, sizeof *o);
    if (ms_mode(samples, &m)) return fail(NXDK_E_SAMPLES);
    memcpy(o->blendConst, d->blendConstant, sizeof o->blendConst);
    o->stencilReadMask = F(rs->w[1], NXRS_S_READMASK, 8); o->stencilWriteMask = F(rs->w[1], NXRS_S_WRITEMASK, 8); o->stencilRef = F(rs->w[1], NXRS_S_REF, 8);
    o->biasConstant = d->depthBiasUnits; o->biasClamp = 0.0f; o->biasSlope = d->depthBiasSlope;
    o->pointSize = d->pointSize; o->lineWidth = 1.0f;
    o->sampleMask = samples > 1 ? (uint32_t)(rs->w[3] >> NXRS_M_SAMPLEMASK) & 0xFFFFu : 0xFFFFu;
    return NXDK_OK;
}

int nxdk_sampler(const NxSamplerState* s, DkSampler* o, uint32_t* srgbView) {
    uint32_t mip = s->filter >> NXSMP_MIP_SHIFT & 3, w[3] = {s->wrap & 7u, s->wrap >> 3 & 7u, s->wrap >> 6 & 7u};
    memset(o, 0, sizeof *o); *srgbView = 0;
    if (mip > NxMip_Linear || (s->filter & 0xC0) || (s->wrap >> 9) || s->pad[0] || s->pad[1] || w[0] > NxWrap_MirrorOnce || w[1] > NxWrap_MirrorOnce || w[2] > NxWrap_MirrorOnce)
        return fail(NXDK_E_FIELD);
    if (s->minLod > 15 || s->maxLod > 15 || !(s->lodBias >= -15.0f && s->lodBias <= 15.0f)) return fail(NXDK_E_RANGE);
    o->minFilter = (s->filter & NXSMP_MIN_LINEAR) ? DkFilter_Linear : DkFilter_Nearest;
    o->magFilter = (s->filter & NXSMP_MAG_LINEAR) ? DkFilter_Linear : DkFilter_Nearest;
    o->mipFilter = MIP[mip];
    for (int i = 0; i < 3; i++) o->wrapMode[i] = WRAP[w[i]];
    o->lodClampMin = (float)s->minLod; o->lodClampMax = (float)s->maxLod; o->lodBias = s->lodBias; o->lodSnap = 0.0f;
    o->compareEnable = (s->filter & NXSMP_SHADOW) != 0; o->compareOp = DkCompareOp_Lequal;
    for (int i = 0; i < 4; i++) o->borderColor[i].value_f = (s->border >> 8 * i & 255) / 255.0f;
    o->maxAnisotropy = s->maxAniso > 1 ? (float)s->maxAniso : 1.0f;
    o->reductionMode = DkSamplerReduction_WeightedAverage;
    *srgbView = (s->filter & NXSMP_SRGB) != 0;
    return NXDK_OK;
}
