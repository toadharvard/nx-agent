/* fnv-nx L6 deko3d backend: state, binding and draw ops of the stream (contract: dk.h "The stream", S1-S7; reference: gl4_stream.c, gl4_state.c).
 * Every handler checks argc with the reference's text, records dkCmdBuf* calls into nxdk_s.cmd, updates nxdk_s. A violated contract is ONE nxdk_err + return.
 *  D1 BindState converts and checks everything first (nothing is recorded on error), then records: the layout (dk_vtx.c), shaders, the sRGB-write rebind,
 *     rasterizer, colour, colour-write, blend state of targets 0-3, depth-stencil, multisample (mode = rasterizerMode = NxDkDerived.msMode), the stencil
 *     (FrontAndBack: write mask, reference, read mask), the sample mask and the scissor. It records the whole state on every call: nothing is cached.
 *  D2 Dynamic values with an op of their own (blend constant, depth bias, viewport) are not touched by BindState, as in the reference; nxdk_dyn is
 *     evaluated with a zero NxDynamicState and only its stencil and sample-mask parts are used. Outside a pass the target is single-sample.
 *  D3 deko3d has no scissor-test switch: the rect in force is nxdk_s.scissor clipped to [0, pass size) when the bound state enables the scissor test,
 *     else the whole pass rect. Pixels outside the target do not exist, so the clip changes nothing visible. BindState and Scissor both record it.
 *  D4 Derived properties without a rule here (S7): clipPlaneMask != 0, flatShade, pointSprite. Each is rejected by name; none is applied or dropped.
 *  D5 A texture slot takes an image and a sampler that exist: image 0 and sampler 0 (GL's "no texture / no sampler object") have no deko3d meaning: rejected.
 *  D6 DrawIndexed binds its index buffer itself (block, offset, type of the last BindIdx) after checking that the indices it reads lie inside the block,
 *     so a draw never depends on a binding recorded in an earlier command buffer; BindIdx binds too (the reference binds there).
 *  Y  Viewport values pass through: the device is created with OriginUpperLeft + YAxisPointsUp (dk_core.c): window y grows downwards from row 0 and
 *     clip +y is the top, which is the NxViewport convention (ndc (-1,+1) -> (x, y)). Console-verified only, as is the front face (dk_state.h A1). */
#include <string.h>
#include "dk.h"
#include "dk_state.h"

#define NEED(n, name) do { if ((uint64_t)argc < (uint64_t)(n)) { nxdk_err(#name ": %u words, need %llu", argc, (unsigned long long)(n)); return; } } while (0)

typedef struct Conv { NxDkState s; NxDkDyn d; } Conv;

static float asf(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static uint32_t samples_now(void) { return nxdk_s.inPass ? nxdk_s.passSamples : 1; }

/* the reference's NxPrim switch: the table is indexed by NxPrim, anything else is an error */
static int prim_of(uint32_t p, DkPrimitive* out) {
    static const DkPrimitive T[] = { DkPrimitive_Triangles, DkPrimitive_TriangleStrip, DkPrimitive_Lines, DkPrimitive_LineStrip,
                                     DkPrimitive_Points, DkPrimitive_TriangleFan };
    _Static_assert(NxPrim_Triangles == 0 && NxPrim_TriStrip == 1 && NxPrim_Lines == 2 && NxPrim_LineStrip == 3 && NxPrim_Points == 4 &&
                   NxPrim_TriFan == 5, "NxPrim");
    if (p >= sizeof T / sizeof T[0]) { nxdk_err("primitive %u", p); return 1; }
    *out = T[p];
    return 0;
}

/* S5: range check against NxDkMem.size, then flush; 0 = ok, 1 = nxdk_err'd (one message) */
static int flush(uint32_t block, uint64_t off, uint64_t size) {
    const NxDkMem* m = nxdk_mem(block);
    if (!m || off + size > m->size) { nxdk_err("bind: range %llu+%llu outside block %u", (unsigned long long)off, (unsigned long long)size, block); return 1; }
    if (dkMemBlockFlushCpuCache(m->blk, (uint32_t)off, (uint32_t)size) != DkResult_Success) { nxdk_err("bind: flush of block %u failed", block); return 1; }
    return 0;
}

/* D3 */
static void set_scissor(DkCmdBuf cmd, uint32_t enabled) {
    int64_t x0 = 0, y0 = 0, x1 = nxdk_s.passW, y1 = nxdk_s.passH;
    if (enabled) {
        x0 = nxdk_s.scissor[0] > 0 ? nxdk_s.scissor[0] : 0; y0 = nxdk_s.scissor[1] > 0 ? nxdk_s.scissor[1] : 0;
        x1 = (int64_t)nxdk_s.scissor[0] + nxdk_s.scissor[2]; y1 = (int64_t)nxdk_s.scissor[1] + nxdk_s.scissor[3];
        if (nxdk_s.inPass) { x1 = x1 < nxdk_s.passW ? x1 : nxdk_s.passW; y1 = y1 < nxdk_s.passH ? y1 : nxdk_s.passH; }
    }
    const DkScissor s = { (uint32_t)x0, (uint32_t)y0, x1 > x0 ? (uint32_t)(x1 - x0) : 0, y1 > y0 ? (uint32_t)(y1 - y0) : 0 };
    dkCmdBufSetScissors(cmd, 0, &s, 1);
}

static void set_stencil(DkCmdBuf cmd, const NxDkDyn* d) {
    nxdk_s.stencilRef = d->stencilRef;
    dkCmdBufSetStencil(cmd, DkFace_FrontAndBack, d->stencilWriteMask, d->stencilRef, d->stencilReadMask);
}

/* D2, D4: converts and checks, records nothing. who = the op's name for the messages. */
static int convert(const char* who, const NxRenderState* rs, uint32_t samples, Conv* c) {
    static const NxDynamicState zero;
    int e = nxdk_state(rs, samples, &c->s);
    if (!e) e = nxdk_dyn(rs, &zero, samples, &c->d);
    if (e) { nxdk_err("%s: render state rejected by nxdk_state, code %d", who, e); return 1; }
    const NxDkDerived* v = &c->s.derived;
    if (v->clipPlaneMask) { nxdk_err("%s: derived property clipPlaneMask %u has no rule in this backend", who, v->clipPlaneMask); return 1; }
    if (v->flatShade) { nxdk_err("%s: derived property flatShade has no rule in this backend", who); return 1; }
    if (v->pointSprite) { nxdk_err("%s: derived property pointSprite has no rule in this backend", who); return 1; }
    return 0;
}

/* D1: the part of a state that BindState and the neutral state have in common: everything except the sRGB-write rebind (it needs an open pass) and the
 * scissor (the pass rect, or the caller's target rect for the neutral state) */
static void record_core(DkCmdBuf cmd, const Conv* c) {
    DkBlendState bl[4] = { c->s.blend, c->s.blend, c->s.blend, c->s.blend };
    DkMultisampleState ms;
    dkMultisampleStateDefaults(&ms);
    ms.mode = ms.rasterizerMode = c->s.derived.msMode;
    dkCmdBufBindRasterizerState(cmd, &c->s.rast);
    dkCmdBufBindColorState(cmd, &c->s.color);
    dkCmdBufBindColorWriteState(cmd, &c->s.write);
    dkCmdBufBindBlendStates(cmd, 0, bl, 4);
    dkCmdBufBindDepthStencilState(cmd, &c->s.ds);
    dkCmdBufBindMultisampleState(cmd, &ms);
    set_stencil(cmd, &c->d);
    dkCmdBufSetSampleMask(cmd, c->d.sampleMask);
}

static void record(DkCmdBuf cmd, const Conv* c) {   /* BindState: sRGB rebind of the open pass first, the pass scissor last */
    nxdk_pass_srgb(c->s.derived.srgbWrite != 0);
    record_core(cmd, c);
    set_scissor(cmd, c->s.derived.scissorEnable);
}

void nxdk_op_define_state(const uint32_t* a, uint32_t argc) {
    NEED(12, DefineState);
    if (!a[0] || a[0] > NXDK_MAX_STATES) { nxdk_err("DefineState: id %u", a[0]); return; }
    if ((a[1] | a[2] | a[3]) > 0xFFFFu) { nxdk_err("DefineState: shader or layout id above 65535 (%u %u %u)", a[1], a[2], a[3]); return; }
    NxDkStateRec* s = &nxdk_s.st[a[0]];
    for (int i = 0; i < 4; i++) s->rs.w[i] = a[4 + 2 * i] | (uint64_t)a[5 + 2 * i] << 32;
    s->vs = (uint16_t)a[1]; s->ps = (uint16_t)a[2]; s->layout = (uint16_t)a[3];
    s->defined = 1;
}

void nxdk_op_bind_state(const uint32_t* a, uint32_t argc) {
    NEED(1, BindState);
    if (!a[0] || a[0] > NXDK_MAX_STATES || !nxdk_s.st[a[0]].defined) { nxdk_err("BindState: state %u undefined", a[0]); return; }
    const NxDkStateRec* s = &nxdk_s.st[a[0]];
    const DkShader* sh[2] = { nxdk_shader(s->vs), nxdk_shader(s->ps) };
    Conv c;
    nxdk_s.stats->state_binds++;
    if (!sh[0] || !sh[1]) { nxdk_err("BindState: shader %u is not live", sh[0] ? s->ps : s->vs); return; }
    if (convert("BindState", &s->rs, samples_now(), &c)) return;
    if (nxdk_vtx_bind_layout(s->layout)) return;
    dkCmdBufBindShaders(nxdk_s.cmd, DkStageFlag_Vertex | DkStageFlag_Fragment, sh, 2);
    record(nxdk_s.cmd, &c);
    nxdk_s.stats->block_binds++;
    nxdk_s.boundState = a[0];
}

void nxdk_op_bind_ubo(const uint32_t* a, uint32_t argc) {
    NEED(5, BindUbo);
    const NxDkMem* m = nxdk_mem(a[2]);
    if (!m) { nxdk_err("BindUbo: block %u", a[2]); return; }
    if (a[0] & ~3u) { nxdk_err("BindUbo: stage mask %u (bit 0 vertex, bit 1 fragment)", a[0]); return; }
    if (a[1] >= 16 || !a[4] || a[4] > DK_UNIFORM_BUF_MAX_SIZE || a[3] % DK_UNIFORM_BUF_ALIGNMENT) {
        nxdk_err("BindUbo: slot %u offset %u size %u (slot < 16, offset a multiple of %u, size 1..%u)", a[1], a[3], a[4], DK_UNIFORM_BUF_ALIGNMENT,
                 DK_UNIFORM_BUF_MAX_SIZE);
        return;
    }
    if (flush(a[2], a[3], a[4])) return;
    const DkGpuAddr addr = dkMemBlockGetGpuAddr(m->blk) + a[3];
    if (a[0] & 1) dkCmdBufBindUniformBuffer(nxdk_s.cmd, DkStage_Vertex, a[1], addr, a[4]);
    if (a[0] & 2) dkCmdBufBindUniformBuffer(nxdk_s.cmd, DkStage_Fragment, a[1], addr, a[4]);
}

void nxdk_op_bind_idx(const uint32_t* a, uint32_t argc) {
    NEED(4, BindIdx);
    const NxDkMem* m = nxdk_mem(a[0]);
    if (!m) { nxdk_err("BindIdx: block %u", a[0]); return; }
    if (a[3] > 1) { nxdk_err("BindIdx: type %u (0 u16, 1 u32)", a[3]); return; }
    if (flush(a[0], a[1], a[2])) return;
    nxdk_s.idxBlock = a[0]; nxdk_s.idxOffset = a[1]; nxdk_s.idxType = a[3];
    dkCmdBufBindIdxBuffer(nxdk_s.cmd, a[3] ? DkIdxFormat_Uint32 : DkIdxFormat_Uint16, dkMemBlockGetGpuAddr(m->blk) + a[1]);
}

void nxdk_op_bind_textures(const uint32_t* a, uint32_t argc) {
    DkResHandle h[16];
    NEED(3, BindTextures);
    if (a[0] != NxStage_Vertex && a[0] != NxStage_Fragment) { nxdk_err("BindTextures: stage %u", a[0]); return; }
    NEED(3 + (uint64_t)a[2], BindTextures);
    if ((uint64_t)a[1] + a[2] > 16) { nxdk_err("BindTextures: slots %u+%u (16 per stage)", a[1], a[2]); return; }
    for (uint32_t i = 0; i < a[2]; i++) {
        const uint32_t img = a[3 + i] & 0xFFFFF, smp = a[3 + i] >> 20;
        const NxSamplerState* ss = nxdk_smp(smp);
        if (!img || !nxdk_img(img)) { nxdk_err("BindTextures: image %u", img); return; }
        if (!ss) { nxdk_err("BindTextures: sampler %u", smp); return; }
        h[i] = dkMakeTextureHandle(nxdk_img_view_id(img, (ss->filter & NXSMP_SRGB) != 0), smp);
    }
    if (a[2]) dkCmdBufBindTextures(nxdk_s.cmd, a[0] == NxStage_Vertex ? DkStage_Vertex : DkStage_Fragment, a[1], h, a[2]);
}

void nxdk_op_draw(const uint32_t* a, uint32_t argc) {
    DkPrimitive p;
    NEED(5, Draw);
    if (!nxdk_s.boundState) { nxdk_err("Draw: no state bound"); return; }
    if (!nxdk_s.inPass) { nxdk_err("Draw: no open pass"); return; }
    if (prim_of(a[0], &p) || nxdk_vtx_flush()) return;
    dkCmdBufDraw(nxdk_s.cmd, p, a[1], a[2], a[3], a[4]);
    nxdk_s.stats->draws++; nxdk_s.frameDraws++;
}

void nxdk_op_draw_indexed(const uint32_t* a, uint32_t argc) {
    DkPrimitive p;
    NEED(6, DrawIndexed);
    if (!nxdk_s.boundState) { nxdk_err("DrawIndexed: no state bound"); return; }
    if (!nxdk_s.inPass) { nxdk_err("DrawIndexed: no open pass"); return; }
    const NxDkMem* m = nxdk_mem(nxdk_s.idxBlock);
    if (!m) { nxdk_err("DrawIndexed: index block %u", nxdk_s.idxBlock); return; }
    const uint32_t isz = nxdk_s.idxType ? 4 : 2;
    if (prim_of(a[0], &p) || flush(nxdk_s.idxBlock, (uint64_t)nxdk_s.idxOffset + (uint64_t)a[3] * isz, (uint64_t)a[1] * isz) || nxdk_vtx_flush()) return;
    dkCmdBufBindIdxBuffer(nxdk_s.cmd, nxdk_s.idxType ? DkIdxFormat_Uint32 : DkIdxFormat_Uint16, dkMemBlockGetGpuAddr(m->blk) + nxdk_s.idxOffset);
    dkCmdBufDrawIndexed(nxdk_s.cmd, p, a[1], a[2], a[3], (int32_t)a[4], a[5]);
    nxdk_s.stats->draws++; nxdk_s.frameDraws++;
}

void nxdk_op_viewport(const uint32_t* a, uint32_t argc) {
    NEED(6, Viewport);
    const DkViewport v = { asf(a[0]), asf(a[1]), asf(a[2]), asf(a[3]), asf(a[4]), asf(a[5]) };
    dkCmdBufSetViewports(nxdk_s.cmd, 0, &v, 1);
}

void nxdk_op_scissor(const uint32_t* a, uint32_t argc) {
    NEED(4, Scissor);
    if ((int32_t)a[2] < 0 || (int32_t)a[3] < 0) { nxdk_err("Scissor: size %d x %d", (int32_t)a[2], (int32_t)a[3]); return; }
    for (int q = 0; q < 4; q++) nxdk_s.scissor[q] = (int32_t)a[q];
    if (nxdk_s.boundState) set_scissor(nxdk_s.cmd, NXRS_GET(nxdk_s.st[nxdk_s.boundState].rs.w[2], NXRS_R_SCISSOR, 1));
}

void nxdk_op_stencil_ref(const uint32_t* a, uint32_t argc) {
    static const NxDynamicState zero;
    NxDkDyn d;
    NEED(1, StencilRef);
    if (!nxdk_s.boundState) { nxdk_err("StencilRef: no state bound"); return; }
    const int e = nxdk_dyn(&nxdk_s.st[nxdk_s.boundState].rs, &zero, samples_now(), &d);
    if (e) { nxdk_err("StencilRef: render state rejected by nxdk_dyn, code %d", e); return; }
    d.stencilRef = a[0] & 0xFF;
    set_stencil(nxdk_s.cmd, &d);
}

void nxdk_op_blend_const(const uint32_t* a, uint32_t argc) {
    NEED(4, BlendConst);
    dkCmdBufSetBlendConst(nxdk_s.cmd, asf(a[0]), asf(a[1]), asf(a[2]), asf(a[3]));
}

void nxdk_op_depth_bias(const uint32_t* a, uint32_t argc) {
    NEED(2, DepthBias);   /* units, slope, [clamp]: the reference ignores the clamp word (the device sends 0); deko3d's clamp 0 = none, as in nxdk_dyn */
    dkCmdBufSetDepthBias(nxdk_s.cmd, asf(a[0]), 0.0f, asf(a[1]));
}

/* gl4_neutral_state: all channels written, no blend, no depth or stencil test, cull none, solid, no bias, no scissor test, linear write, full sample mask,
 * multisample on: the zero state with write masks 15, depth mask on (as GL; nxdk_state writes depth only with the test on), MSAA on, sample mask all ones.
 * Internal draws (scale, resolve) run OUTSIDE any pass onto a target the caller bound with nxdk_target_bind: single-sample views without sRGB (dk_pass.c,
 * bind(..., srgb = 0)) and scissor (0,0,w,h) of the level. So the state is converted for 1 sample (no pass state is read), nxdk_pass_srgb is not called
 * (the bound views already are the non-sRGB ones that the state's srgb = 0 asks for) and the scissor is the caller's w x h. Inside a pass the target
 * would be the pass's: one error, nothing recorded (as nxdk_target_bind, P8). */
void nxdk_draw_neutral(DkCmdBuf cmd, uint32_t w, uint32_t h) {
    const NxRenderState rs = { { 0xFFFFull << 32, 1ull << NXRS_D_WRITE, 0, 1ull << NXRS_M_MSAA | 0xFFFFFFFFull << NXRS_M_SAMPLEMASK } };
    Conv c;
    if (nxdk_s.inPass) { nxdk_err("draw_neutral: inside an open pass"); return; }
    if (convert("draw_neutral", &rs, 1, &c)) return;
    record_core(cmd, &c);
    const DkScissor sc = { 0, 0, w, h };
    dkCmdBufSetScissors(cmd, 0, &sc, 1);
    nxdk_s.boundState = 0;
}
