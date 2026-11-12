/* fnv-nx L6 GL 4.5 backend v4: replay of the command stream. Every op of NxOp_Count4 has one handler in the table
 * k_op (built from NXGL4_OP_LIST, completeness asserted in gl4_core.c). A malformed stream (an opcode outside the
 * table, an op overrunning the stream, an op whose arguments gl4_stream_check refuses: pass targets that disagree,
 * layouts, render-state bits GL 4.5 cannot express) makes the submit return NXGPU_BAD_CALL before anything executes;
 * an op that violates its contract at run time stops the replay and the submit returns NXGPU_BAD_CALL. */
#define _GNU_SOURCE
#include "gl4.h"
#include <string.h>
#include <time.h>

typedef struct { NxFrameStats* stats; int done; } Ctx;
typedef void (*OpFn)(const uint32_t* a, uint32_t argc, Ctx* c);
#define NEED(n, name) do { if ((uint64_t)argc < (uint64_t)(n)) { gl4_err(#name ": %u words, need %llu", argc, (unsigned long long)(n)); return; } } while (0)

static float asf(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

static void op_End(const uint32_t* a, uint32_t argc, Ctx* c) { (void)a; (void)argc; c->done = 1; }
static void op_DefineState(const uint32_t* a, uint32_t argc, Ctx* c) {
    (void)c; NEED(12, DefineState);
    if (!a[0] || a[0] > GL4_MAX_STATES) { gl4_err("DefineState: id %u", a[0]); return; }
    Gl4State* s = &g4.st[a[0]];
    for (int i = 0; i < 4; i++) s->rs.w[i] = a[4 + 2 * i] | (uint64_t)a[5 + 2 * i] << 32;
    s->vs = (uint16_t)a[1]; s->ps = (uint16_t)a[2]; s->layout = (uint16_t)a[3];
    s->prog = gl4_program_for(s->vs, s->ps); s->epoch = g4.progEpoch;
    s->defined = 1;
}
static void op_DefineLayout(const uint32_t* a, uint32_t argc, Ctx* c) { (void)c; gl4_op_define_layout(a, argc); }
static void op_DefineLayout4(const uint32_t* a, uint32_t argc, Ctx* c) { (void)c; gl4_op_define_layout4(a, argc); }
static void op_BindVtxN(const uint32_t* a, uint32_t argc, Ctx* c) { (void)c; gl4_op_bind_vtxn(a, argc); }
static void op_BeginPass4(const uint32_t* a, uint32_t argc, Ctx* c) { (void)c; gl4_op_begin_pass4(a, argc); }
static void op_QueryBegin(const uint32_t* a, uint32_t argc, Ctx* c) { (void)c; gl4_op_query_begin(a, argc); }
static void op_QueryEnd(const uint32_t* a, uint32_t argc, Ctx* c) { (void)c; gl4_op_query_end(a, argc); }
static void op_BeginPass(const uint32_t* a, uint32_t argc, Ctx* c) { (void)c; gl4_op_begin_pass(a, argc); }
static void op_EndPass(const uint32_t* a, uint32_t argc, Ctx* c) {   /* GL has no pass object: leave the target, no state */
    (void)a; (void)c; (void)argc;
    p_glBindFramebuffer(GL_FRAMEBUFFER, 0);
    g4.boundState = 0;
}
static void op_Barrier(const uint32_t* a, uint32_t argc, Ctx* c) {   /* GL orders render-to-texture -> sampling itself */
    (void)c; NEED(1, Barrier);
    if (a[0] != 0) gl4_err("Barrier: kind %u", a[0]);
}
static void op_BindState(const uint32_t* a, uint32_t argc, Ctx* c) {
    NEED(1, BindState);
    if (!a[0] || a[0] > GL4_MAX_STATES || !g4.st[a[0]].defined) { gl4_err("BindState: state %u undefined", a[0]); return; }
    Gl4State* s = &g4.st[a[0]];
    c->stats->state_binds++;
    if (s->epoch != g4.progEpoch) { s->prog = gl4_program_for(s->vs, s->ps); s->epoch = g4.progEpoch; }
    if (g4.boundState != a[0] || !g4.haveApplied) {
        p_glUseProgram(s->prog ? g4.prog[s->prog].prog : 0);
        gl4_bind_layout(s->layout);
        if (g4.idxBlock) p_glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g4.mem[g4.idxBlock].buf);
        g4.vtxDirty = 1;
    }
    gl4_apply_state(&s->rs, !g4.haveApplied);
    c->stats->block_binds++;
    g4.boundState = a[0];
}
static void op_BindUbo(const uint32_t* a, uint32_t argc, Ctx* c) {
    (void)c; NEED(5, BindUbo);
    if (!gl4_mem(a[2])) { gl4_err("BindUbo: block %u", a[2]); return; }
    gl4_upload_range(a[2], a[3], a[4]);
    if (a[0] & 1) p_glBindBufferRange(GL_UNIFORM_BUFFER, 0 * 16 + a[1], g4.mem[a[2]].buf, a[3], a[4]);
    if (a[0] & 2) p_glBindBufferRange(GL_UNIFORM_BUFFER, 1 * 16 + a[1], g4.mem[a[2]].buf, a[3], a[4]);
}
static void op_BindVtx(const uint32_t* a, uint32_t argc, Ctx* c) { (void)c; gl4_op_bind_vtx(a, argc); }
static void op_BindIdx(const uint32_t* a, uint32_t argc, Ctx* c) {
    (void)c; NEED(4, BindIdx);
    if (!gl4_mem(a[0])) { gl4_err("BindIdx: block %u", a[0]); return; }
    gl4_upload_range(a[0], a[1], a[2]);
    g4.idxBlock = a[0]; g4.idxOffset = a[1]; g4.idxType = a[3];
    p_glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g4.mem[a[0]].buf);
}
static void op_BindTextures(const uint32_t* a, uint32_t argc, Ctx* c) {
    (void)c; NEED(3, BindTextures);
    if (a[0] != NxStage_Vertex && a[0] != NxStage_Fragment) { gl4_err("BindTextures: stage %u", a[0]); return; }
    NEED(3 + a[2], BindTextures);
    for (uint32_t i = 0; i < a[2]; i++) {
        uint32_t h = a[3 + i], img = h & 0xFFFFF, smp = h >> 20;
        GLuint unit = a[0] * 16 + a[1] + i;
        const Gl4Image* im = gl4_img(img);
        if (img && !im) gl4_err("BindTextures: image %u", img);
        if (smp > g4.nSmp) gl4_err("BindTextures: sampler %u", smp);
        p_glBindTextureUnit(unit, im ? im->tex : 0);
        p_glBindSampler(unit, smp && smp <= g4.nSmp ? g4.smp[smp] : 0);
    }
}

static GLenum glprim(uint32_t p) {
    switch ((enum NxPrim)p) {
    case NxPrim_Triangles: return GL_TRIANGLES;
    case NxPrim_TriStrip: return GL_TRIANGLE_STRIP;
    case NxPrim_Lines: return GL_LINES;
    case NxPrim_LineStrip: return GL_LINE_STRIP;
    case NxPrim_Points: return GL_POINTS;
    case NxPrim_TriFan: return GL_TRIANGLE_FAN;
    }
    gl4_err("primitive %u", p);
    return GL_TRIANGLES;
}
static void op_Draw(const uint32_t* a, uint32_t argc, Ctx* c) {
    NEED(5, Draw);
    if (!g4.boundState) { gl4_err("Draw: no state bound"); return; }
    if (gl4_vtx_flush()) return;
    p_glDrawArraysInstancedBaseInstance(glprim(a[0]), (GLint)a[3], (GLsizei)a[1], (GLsizei)a[2], a[4]);
    c->stats->draws++; g4.frameDraws++;
}
static void op_DrawIndexed(const uint32_t* a, uint32_t argc, Ctx* c) {
    NEED(6, DrawIndexed);
    if (!g4.boundState) { gl4_err("DrawIndexed: no state bound"); return; }
    if (gl4_vtx_flush()) return;
    uint32_t isz = g4.idxType ? 4 : 2;
    p_glDrawElementsInstancedBaseVertexBaseInstance(glprim(a[0]), (GLsizei)a[1], g4.idxType ? GL_UNSIGNED_INT : GL_UNSIGNED_SHORT,
        (const void*)(uintptr_t)(g4.idxOffset + a[3] * isz), (GLsizei)a[2], (GLint)a[4], a[5]);
    c->stats->draws++; g4.frameDraws++;
}
static void op_Viewport(const uint32_t* a, uint32_t argc, Ctx* c) {
    (void)c; NEED(6, Viewport);
    p_glViewportIndexedf(0, asf(a[0]), asf(a[1]), asf(a[2]), asf(a[3]));
    p_glDepthRangef(asf(a[4]), asf(a[5]));
}
static void op_Scissor(const uint32_t* a, uint32_t argc, Ctx* c) {
    (void)c; NEED(4, Scissor);
    for (int q = 0; q < 4; q++) g4.scissor[q] = (GLint)a[q];
    p_glScissor(g4.scissor[0], g4.scissor[1], g4.scissor[2], g4.scissor[3]);
}
static void op_StencilRef(const uint32_t* a, uint32_t argc, Ctx* c) {
    (void)c; NEED(1, StencilRef);
    if (!g4.boundState) { gl4_err("StencilRef: no state bound"); return; }
    g4.stencilRef = a[0] & 0xFF;
    uint64_t w1 = g4.st[g4.boundState].rs.w[1];
    gl4_apply_stencil(w1, g4.stencilRef);
}
static void op_BlendConst(const uint32_t* a, uint32_t argc, Ctx* c) {
    (void)c; NEED(4, BlendConst);
    p_glBlendColor(asf(a[0]), asf(a[1]), asf(a[2]), asf(a[3]));
}
static void op_DepthBias(const uint32_t* a, uint32_t argc, Ctx* c) {
    (void)c; NEED(2, DepthBias);   /* units, slope, clamp (clamp: not a GL 4.5 core state; the device sends 0) */
    p_glPolygonOffset(asf(a[1]), asf(a[0]));
}
static void op_Clear(const uint32_t* a, uint32_t argc, Ctx* c) { (void)c; gl4_op_clear(a, argc); }
static void op_Marker(const uint32_t* a, uint32_t argc, Ctx* c) {   /* tags for captures: no effect on rendering (ABI) */
    (void)a; (void)c; NEED(3, Marker);
}
static void op_ImageUpdate(const uint32_t* a, uint32_t argc, Ctx* c) { (void)c; gl4_op_image_update(a, argc); }
static void op_Blit(const uint32_t* a, uint32_t argc, Ctx* c) { (void)c; gl4_op_blit(a, argc); }
static void op_GenMips(const uint32_t* a, uint32_t argc, Ctx* c) { (void)c; gl4_op_genmips(a, argc); }

#define OPENT(name) [NxOp_##name] = op_##name,
static const OpFn k_op[NxOp_Count4] = { NXGL4_OP_LIST(OPENT) };

/* An opcode outside the table (not an op of this build) or an op overrunning the stream makes the whole stream
 * malformed: it is refused before anything is executed. */
static int stream_ok(const uint32_t* w, const uint32_t* end) {
    while (w < end) {
        uint32_t op = w[0] & 0xFFFF, argc = w[0] >> 16;
        if (op >= NxOp_Count4 || !k_op[op]) { gl4_err("stream: opcode %u is not an op of this build", op); return 0; }
        if (w + 1 + argc > end) { gl4_err("stream: op %u overruns the stream", op); return 0; }
        if (op == NxOp_End) return 1;
        w += 1 + argc;
    }
    gl4_err("stream: no End op");
    return 0;
}
static int stream_valid(const uint32_t* w, const uint32_t* end) { return stream_ok(w, end) && gl4_stream_check(w, end); }

/* Executes until End or the first op that violated its contract (no later op runs after a violation). */
static void replay(const NxSubmit* sub, Ctx* ctx) {
    const uint32_t* w = (const uint32_t*)nxgl4_guest_ptr(sub->stream, (uint64_t)sub->words * 4);
    const uint32_t* end = w + sub->words;
    if (!stream_valid(w, end)) return;
    while (!ctx->done) {
        uint32_t op = w[0] & 0xFFFF, argc = w[0] >> 16, errs = g4.errCount;
        k_op[op](w + 1, argc, ctx);
        if (g4.errCount != errs) return;
        w += 1 + argc;
    }
}

uint64_t gl4_api_submit(uint64_t a) {
    const NxSubmit* s = (const NxSubmit*)nxgl4_guest_ptr(a, sizeof *s);
    if (!s) return NXGPU_BAD_CALL;
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    NxFrameStats st;
    memset(&st, 0, sizeof st);
    st.frame = s->frame; st.cmd_words = s->words;
    uint32_t errs = g4.errCount;
    if (g4.drawsFrame != (uint64_t)s->frame + 1) { g4.frameDraws = 0; g4.drawsFrame = (uint64_t)s->frame + 1; }
    Ctx ctx = {&st, 0};
    gl4_query_submit_begin();
    replay(s, &ctx);
    gl4_query_submit_end();
    p_glFinish();
    gl4_query_collect();
    clock_gettime(CLOCK_MONOTONIC, &t1);
    st.record_us = (uint32_t)((t1.tv_sec - t0.tv_sec) * 1000000 + (t1.tv_nsec - t0.tv_nsec) / 1000);
    GLenum e = p_glGetError();
    if (e) gl4_err("submit: GL error 0x%x", e);
    if (s->stats) memcpy(nxgl4_guest_ptr(s->stats, sizeof st), &st, sizeof st);
    return g4.errCount != errs ? NXGPU_BAD_CALL : 0;
}

uint64_t gl4_api_wait_frame(uint64_t f) { (void)f; return 0; }   /* submit is synchronous: glFinish before it returns */
