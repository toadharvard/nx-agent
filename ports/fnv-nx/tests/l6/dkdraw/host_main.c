/* Host harness of test_dkdraw.py: dk_draw.c and dk_state.c (the real product files) against a recording double of every dkCmdBuf* / dkMemBlock* call and of
 * the other backend files they call (dk_core, dk_shader, dk_sampler, dk_image, dk_vtx, dk_pass). Each scenario runs ops with exactly-sized argument arrays (ASan
 * sees an over-read), then compares the recorded log with the expected log, or checks the one error text. Prints "CHK name 0|1" lines; "# " lines are diagnostics. */
#define _GNU_SOURCE
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dk.h"

NxDk nxdk;
NxDkStream nxdk_s;
static NxFrameStats st;
static char L[16384], E[16384];
static int fail_flush, fail_vtx, total, bad;
static const DkCmdBuf CMD = (DkCmdBuf)(uintptr_t)0xC0DE, CMD2 = (DkCmdBuf)(uintptr_t)0xC0DF;
static DkCmdBuf want = (DkCmdBuf)(uintptr_t)0xC0DE;

static void vapp(char* b, const char* f, va_list ap) { size_t n = strlen(b); vsnprintf(b + n, 16384 - n, f, ap); strcat(b, "\n"); }
static void lg(const char* f, ...) { va_list ap; va_start(ap, f); vapp(L, f, ap); va_end(ap); }
static void ex(const char* f, ...) { va_list ap; va_start(ap, f); vapp(E, f, ap); va_end(ap); }
static void reset(void) { L[0] = E[0] = 0; nxdk.errCount = 0; nxdk.lastErr[0] = 0; }
static void chk(const char* name, int ok) { total++; if (!ok) bad++; printf("CHK %s %d\n", name, !!ok); if (!ok) printf("# errs %u '%s' log:\n%s", nxdk.errCount, nxdk.lastErr, L); }
#define CHK(name, cond) chk(name, !!(cond))
static void eqlog(const char* name) { int ok = !strcmp(L, E); chk(name, ok && !nxdk.errCount); if (!ok) printf("# got:\n%s# want:\n%s", L, E); }
static void err1(const char* name, const char* text) {   /* exactly one error with this text and nothing recorded */
    int ok = nxdk.errCount == 1 && !strcmp(nxdk.lastErr, text) && !L[0];
    chk(name, ok); if (!ok) printf("# errs %u '%s' log '%s' want '%s'\n", nxdk.errCount, nxdk.lastErr, L, text);
}

void nxchk_err(const char* fmt, ...) { va_list ap; va_start(ap, fmt); vsnprintf(nxdk.lastErr, sizeof nxdk.lastErr, fmt, ap); va_end(ap); nxdk.errCount++; }

/* ---- doubles of the other backend files ------------------------------------------------------------------------------------------------------ */
static NxDkMem M[4]; static DkShader SH[4]; static NxSamplerState SM[2]; static NxDkImage IM;
const NxDkMem* nxdk_mem(uint32_t id) { if (id < 1 || id > 3) return NULL; M[id].size = id == 1 ? 0x4000 : id == 2 ? 0x1000 : 0x100000; M[id].blk = (DkMemBlock)(uintptr_t)(0xB000 + id); return &M[id]; }
const DkShader* nxdk_shader(uint32_t id) { return id >= 1 && id <= 3 ? &SH[id] : NULL; }
const NxSamplerState* nxdk_smp(uint32_t id) { SM[1].filter = NXSMP_SRGB; return id == 1 ? &SM[0] : id == 2 ? &SM[1] : NULL; }
const NxDkImage* nxdk_img(uint32_t id) { return id >= 1 && id <= 5 ? &IM : NULL; }
uint32_t nxdk_img_view_id(uint32_t id, int srgb) { return id * 10 + (uint32_t)srgb; }
uint32_t nxdk_vtx_bind_layout(uint32_t l) { lg("layout %u", l); if (l == 99) { nxdk_err("layout 99"); return 1; } return 0; }
uint32_t nxdk_vtx_flush(void) { lg("vtxflush"); if (fail_vtx) { nxdk_err("vtx flush"); return 1; } return 0; }
void nxdk_pass_srgb(uint32_t on) { lg("srgb %u", on); }

/* ---- deko3d double --------------------------------------------------------------------------------------------------------------------------- */
static char* fmt(const char* f, ...) { static char r[8][512]; static int i; char* o = r[i++ & 7]; va_list ap; va_start(ap, f); vsnprintf(o, 512, f, ap); va_end(ap); return o; }
#define U(x) ((unsigned)(x))
static char* fr(const DkRasterizerState* s) { return fmt("en%u clamp%u rect%u pf%u pb%u cull%u ff%u pv%u smooth%u bias%u", U(s->rasterizerEnable), U(s->depthClampEnable),
    U(s->fillRectangleEnable), U(s->polygonModeFront), U(s->polygonModeBack), U(s->cullMode), U(s->frontFace), U(s->provokingVertex), U(s->polygonSmoothEnableMask), U(s->depthBiasEnableMask)); }
static char* fc(const DkColorState* s) { return fmt("bm%02x lop%u acmp%u", U(s->blendEnableMask), U(s->logicOp), U(s->alphaCompareOp)); }
static char* fb(const DkBlendState* s) { return fmt("c%u/%u/%u a%u/%u/%u", U(s->colorBlendOp), U(s->srcColorBlendFactor), U(s->dstColorBlendFactor), U(s->alphaBlendOp),
    U(s->srcAlphaBlendFactor), U(s->dstAlphaBlendFactor)); }
static char* fd(const DkDepthStencilState* s) { return fmt("dt%u dw%u st%u dc%u f%u%u%u%u b%u%u%u%u", U(s->depthTestEnable), U(s->depthWriteEnable), U(s->stencilTestEnable),
    U(s->depthCompareOp), U(s->stencilFrontFailOp), U(s->stencilFrontPassOp), U(s->stencilFrontDepthFailOp), U(s->stencilFrontCompareOp), U(s->stencilBackFailOp),
    U(s->stencilBackPassOp), U(s->stencilBackDepthFailOp), U(s->stencilBackCompareOp)); }
static char* fm(const DkMultisampleState* s) { return fmt("mode%u rmode%u a2c%u%u c2c%u%u cm%u loc%x,%x,%x,%x", U(s->mode), U(s->rasterizerMode), U(s->alphaToCoverageEnable),
    U(s->alphaToCoverageDither), U(s->coverageToColorEnable), U(s->coverageToColorOutput), U(s->coverageModulation), s->sampleLocations[0], s->sampleLocations[1],
    s->sampleLocations[2], s->sampleLocations[3]); }
#define CMDCHK(c) do { if ((c) != want) lg("BADCMD"); } while (0)
void dkCmdBufBindRasterizerState(DkCmdBuf c, DkRasterizerState const* s) { CMDCHK(c); lg("rast %s", fr(s)); }
void dkCmdBufBindColorState(DkCmdBuf c, DkColorState const* s) { CMDCHK(c); lg("color %s", fc(s)); }
void dkCmdBufBindColorWriteState(DkCmdBuf c, DkColorWriteState const* s) { CMDCHK(c); lg("cw %08x", s->masks); }
void dkCmdBufBindBlendStates(DkCmdBuf c, uint32_t first, DkBlendState const* s, uint32_t n) {
    CMDCHK(c); lg("blend first%u n%u", first, n); for (uint32_t i = 0; i < n; i++) lg(" %s", fb(&s[i])); }
void dkCmdBufBindDepthStencilState(DkCmdBuf c, DkDepthStencilState const* s) { CMDCHK(c); lg("ds %s", fd(s)); }
void dkCmdBufBindMultisampleState(DkCmdBuf c, DkMultisampleState const* s) { CMDCHK(c); lg("ms %s", fm(s)); }
void dkCmdBufSetStencil(DkCmdBuf c, DkFace face, uint8_t mask, uint8_t ref, uint8_t fmask) { CMDCHK(c); lg("stencil face%u mask%02x ref%02x fmask%02x", U(face), mask, ref, fmask); }
void dkCmdBufSetSampleMask(DkCmdBuf c, uint32_t m) { CMDCHK(c); lg("samplemask %x", m); }
void dkCmdBufSetScissors(DkCmdBuf c, uint32_t first, DkScissor const* s, uint32_t n) {
    CMDCHK(c); lg("scissor first%u n%u", first, n); for (uint32_t i = 0; i < n; i++) lg(" %u %u %u %u", s[i].x, s[i].y, s[i].width, s[i].height); }
void dkCmdBufBindShaders(DkCmdBuf c, uint32_t mask, DkShader const* const* s, uint32_t n) {
    CMDCHK(c); lg("shaders mask%u n%u", mask, n); for (uint32_t i = 0; i < n; i++) lg(" sh%d", (int)(s[i] - SH)); }
void dkCmdBufBindUniformBuffers(DkCmdBuf c, DkStage stage, uint32_t id, DkBufExtents const* b, uint32_t n) {
    CMDCHK(c); lg("ubo stage%u id%u n%u", U(stage), id, n); for (uint32_t i = 0; i < n; i++) lg(" addr%llx size%u", (unsigned long long)b[i].addr, b[i].size); }
void dkCmdBufBindIdxBuffer(DkCmdBuf c, DkIdxFormat f, DkGpuAddr a) { CMDCHK(c); lg("idx fmt%u addr%llx", U(f), (unsigned long long)a); }
void dkCmdBufBindTextures(DkCmdBuf c, DkStage stage, uint32_t first, DkResHandle const* h, uint32_t n) {
    CMDCHK(c); lg("tex stage%u first%u n%u", U(stage), first, n); for (uint32_t i = 0; i < n; i++) lg(" h%x", h[i]); }
void dkCmdBufDraw(DkCmdBuf c, DkPrimitive p, uint32_t n, uint32_t i, uint32_t fv, uint32_t fi) { CMDCHK(c); lg("draw p%u n%u i%u fv%u fi%u", U(p), n, i, fv, fi); }
void dkCmdBufDrawIndexed(DkCmdBuf c, DkPrimitive p, uint32_t n, uint32_t i, uint32_t fx, int32_t vo, uint32_t fi) {
    CMDCHK(c); lg("drawidx p%u n%u i%u fx%u vo%d fi%u", U(p), n, i, fx, vo, fi); }
void dkCmdBufSetViewports(DkCmdBuf c, uint32_t first, DkViewport const* v, uint32_t n) {
    CMDCHK(c); lg("viewport first%u n%u", first, n); for (uint32_t i = 0; i < n; i++) lg(" %.3f %.3f %.3f %.3f %.3f %.3f", v[i].x, v[i].y, v[i].width, v[i].height, v[i].near, v[i].far); }
void dkCmdBufSetBlendConst(DkCmdBuf c, float r, float g, float b, float a) { CMDCHK(c); lg("blendconst %.3f %.3f %.3f %.3f", r, g, b, a); }
void dkCmdBufSetDepthBias(DkCmdBuf c, float k, float cl, float s) { CMDCHK(c); lg("depthbias %.3f %.3f %.3f", k, cl, s); }
DkGpuAddr dkMemBlockGetGpuAddr(DkMemBlock b) { return (uint64_t)((uintptr_t)b - 0xB000) << 32; }
DkResult dkMemBlockFlushCpuCache(DkMemBlock b, uint32_t off, uint32_t size) { lg("flush blk%u %u %u", U((uintptr_t)b - 0xB000), off, size); return fail_flush ? DkResult_Fail : DkResult_Success; }

/* ---- scenario helpers ------------------------------------------------------------------------------------------------------------------------ */
typedef void (*Op)(const uint32_t*, uint32_t);
static int keep;
static void call(Op f, uint32_t argc, uint32_t n, const uint32_t* v) {   /* the argument array has exactly argc words */
    uint32_t* a = calloc(argc ? argc : 1, 4);
    memcpy(a, v, 4 * (n < argc ? n : argc));
    if (!keep) reset();
    f(a, argc); free(a);
}
#define OPN(f, argc, ...) do { const uint32_t _v[] = {__VA_ARGS__}; call(f, argc, sizeof _v / 4, _v); } while (0)
#define OP(f, ...) do { const uint32_t _v[] = {__VA_ARGS__}; call(f, sizeof _v / 4, sizeof _v / 4, _v); } while (0)
static void defstate(uint32_t id, uint32_t vs, uint32_t ps, uint32_t lay, const uint64_t w[4]) {
    uint32_t a[12] = {id, vs, ps, lay};
    for (int i = 0; i < 4; i++) { a[4 + 2 * i] = (uint32_t)w[i]; a[5 + 2 * i] = (uint32_t)(w[i] >> 32); }
    call(nxdk_op_define_state, 12, 12, a);
}
/* expected log of the state records of BindState / neutral, from the real nxdk_state / nxdk_dyn of the same words */
static void ex_rec(const uint64_t w[4], uint32_t samples, const char* scissor) {
    static const NxDynamicState z; NxRenderState rs; NxDkState s; NxDkDyn d; DkMultisampleState m;
    memcpy(rs.w, w, sizeof rs.w);
    if (nxdk_state(&rs, samples, &s) || nxdk_dyn(&rs, &z, samples, &d)) { ex("EXPECTED STATE REJECTED"); return; }
    dkMultisampleStateDefaults(&m); m.mode = m.rasterizerMode = s.derived.msMode;
    ex("srgb %u", s.derived.srgbWrite != 0); ex("rast %s", fr(&s.rast)); ex("color %s", fc(&s.color)); ex("cw %08x", s.write.masks); ex("blend first0 n4");
    for (int i = 0; i < 4; i++) ex(" %s", fb(&s.blend));
    ex("ds %s", fd(&s.ds)); ex("ms %s", fm(&m)); ex("stencil face%u mask%02x ref%02x fmask%02x", U(DkFace_FrontAndBack), d.stencilWriteMask, d.stencilRef, d.stencilReadMask);
    ex("samplemask %x", d.sampleMask); ex("scissor first0 n1"); ex(" %s", scissor);
}
static void pass(uint32_t on, uint32_t samples) { nxdk_s.inPass = on; nxdk_s.passSamples = samples; nxdk_s.passW = 640; nxdk_s.passH = 480; }
static void sc(int32_t x, int32_t y, int32_t w, int32_t h) { nxdk_s.scissor[0] = x; nxdk_s.scissor[1] = y; nxdk_s.scissor[2] = w; nxdk_s.scissor[3] = h; }
static char* last_line(void) { char* e = L + strlen(L); if (e > L) e--; while (e > L && e[-1] != '\n') e--; return e; }

static const uint64_t W1[4] = { 0xFFFFull << 32 | 1, 1ull << NXRS_D_TEST | 1ull << NXRS_D_WRITE | 1ull << NXRS_S_ENABLE | 0xF0ull << NXRS_S_READMASK | 0x0Full << NXRS_S_WRITEMASK |
                                0x12ull << NXRS_S_REF, 1ull << NXRS_R_SCISSOR | 1ull << NXRS_R_SRGB, 1ull << NXRS_M_MSAA | 0xA5A5ull << NXRS_M_SAMPLEMASK };
static const uint64_t W2[4] = { 0xFFFFull << 32, 1ull << NXRS_D_WRITE, 0, 0 };                         /* scissor test off, sRGB off, no MSAA */
static const uint64_t WC[4] = { 0xFFFFull << 32, 0, 1ull << (NXRS_R_CLIP + 2), 0 };
static const uint64_t WF[4] = { 0xFFFFull << 32, 0, 1ull << NXRS_R_FLAT, 0 };
static const uint64_t WP[4] = { 0xFFFFull << 32, 0, 1ull << NXRS_R_POINTSPRITE, 0 };
static const uint64_t WN[4] = { 0xFFFFull << 32, 1ull << NXRS_D_WRITE, 0, 1ull << NXRS_M_MSAA | 0xFFFFFFFFull << NXRS_M_SAMPLEMASK };   /* gl4_neutral_state */

static void t_define(void) {
    OPN(nxdk_op_define_state, 11, 7, 1, 2, 3); err1("define argc 11", "DefineState: 11 words, need 12"); CHK("define argc 11 defines nothing", !nxdk_s.st[7].defined);
    OPN(nxdk_op_define_state, 0, 0); err1("define argc 0", "DefineState: 0 words, need 12");
    OP(nxdk_op_define_state, 0, 1, 2, 3, 0, 0, 0, 0, 0, 0, 0, 0); err1("define id 0", "DefineState: id 0"); CHK("define id 0 defines nothing", !nxdk_s.st[0].defined);
    OP(nxdk_op_define_state, 4097, 1, 2, 3, 0, 0, 0, 0, 0, 0, 0, 0); err1("define id 4097", "DefineState: id 4097");
    OP(nxdk_op_define_state, 0xFFFFFFFF, 1, 2, 3, 0, 0, 0, 0, 0, 0, 0, 0); err1("define id 0xFFFFFFFF", "DefineState: id 4294967295");
    OP(nxdk_op_define_state, 5, 0x10000, 2, 3, 0, 0, 0, 0, 0, 0, 0, 0); err1("define vs 65536", "DefineState: shader or layout id above 65535 (65536 2 3)");
    OP(nxdk_op_define_state, 5, 1, 0x10000, 3, 0, 0, 0, 0, 0, 0, 0, 0); err1("define ps 65536", "DefineState: shader or layout id above 65535 (1 65536 3)");
    OP(nxdk_op_define_state, 5, 1, 2, 0x10000, 0, 0, 0, 0, 0, 0, 0, 0); err1("define layout 65536", "DefineState: shader or layout id above 65535 (1 2 65536)");
    CHK("define errors define nothing", !nxdk_s.st[5].defined);
    OP(nxdk_op_define_state, 4096, 65535, 65534, 65533, 1, 2, 3, 4, 5, 6, 7, 8); CHK("define id 4096 ok", !nxdk.errCount && !L[0]);
    const NxDkStateRec* r = &nxdk_s.st[4096];
    CHK("define stores ids", r->defined == 1 && r->vs == 65535 && r->ps == 65534 && r->layout == 65533);
    CHK("define words w0..w3 = lo | hi << 32", r->rs.w[0] == (1ull | 2ull << 32) && r->rs.w[1] == (3ull | 4ull << 32) && r->rs.w[2] == (5ull | 6ull << 32) && r->rs.w[3] == (7ull | 8ull << 32));
    OP(nxdk_op_define_state, 4096, 3, 2, 1, 0xFFFFFFFF, 0, 0, 0xFFFFFFFF, 0, 0, 0, 0x80000000);
    CHK("redefine replaces", !nxdk.errCount && r->vs == 3 && r->ps == 2 && r->layout == 1 && r->rs.w[0] == 0xFFFFFFFFull && r->rs.w[1] == 0xFFFFFFFF00000000ull && r->rs.w[2] == 0 &&
        r->rs.w[3] == 0x8000000000000000ull);
    defstate(1, 1, 2, 5, W1); defstate(2, 2, 3, 6, W2); defstate(3, 4, 2, 5, W2); defstate(4, 1, 9, 5, W2); defstate(5, 1, 2, 99, W2); defstate(6, 1, 2, 5, WC);
    defstate(7, 1, 2, 5, WF); defstate(8, 1, 2, 5, WP); defstate(9, 0, 2, 5, W2); defstate(10, 4, 9, 5, W2);
    CHK("define through helper", nxdk_s.st[1].defined && nxdk_s.st[1].rs.w[1] == W1[1] && nxdk_s.st[10].ps == 9);
}

static void t_bind_state(void) {
    OPN(nxdk_op_bind_state, 0); err1("bind argc 0", "BindState: 0 words, need 1");
    OP(nxdk_op_bind_state, 0); err1("bind id 0", "BindState: state 0 undefined");
    OP(nxdk_op_bind_state, 4097); err1("bind id 4097", "BindState: state 4097 undefined");
    OP(nxdk_op_bind_state, 11); err1("bind undefined id", "BindState: state 11 undefined");
    CHK("undefined ids count nothing", st.state_binds == 0 && st.block_binds == 0 && nxdk_s.boundState == 0);
    nxdk_s.scissor[0] = 10; nxdk_s.scissor[1] = 20; nxdk_s.scissor[2] = 30; nxdk_s.scissor[3] = 40; pass(0, 1);
    OP(nxdk_op_bind_state, 1);   /* outside a pass: single sample, the scissor is not clipped (D3) */
    ex("layout 5"); ex("shaders mask%u n2", DkStageFlag_Vertex | DkStageFlag_Fragment); ex(" sh1"); ex(" sh2"); ex_rec(W1, 1, "10 20 30 40"); eqlog("bind 1 outside a pass");
    CHK("bind stats and bound state", st.state_binds == 1 && st.block_binds == 1 && nxdk_s.boundState == 1 && nxdk_s.stencilRef == 0x12);
    CHK("bind literal facts", strstr(L, "srgb 1\n") && strstr(L, "stencil face3 mask0f ref12 fmaskf0\n") && strstr(L, "samplemask ffff\n") && strstr(L, "mode0 rmode0") && strstr(L, "blend first0 n4\n") && strstr(L, fmt("shaders mask%u n2\n sh1\n sh2\n", U(DkStageFlag_Vertex | DkStageFlag_Fragment))));
    pass(1, 4);   /* in a 4-sample pass: msaa on, the state's sample mask, the scissor clipped to the target */
    sc(600, 400, 100, 100); OP(nxdk_op_bind_state, 1);
    ex("layout 5"); ex("shaders mask%u n2", DkStageFlag_Vertex | DkStageFlag_Fragment); ex(" sh1"); ex(" sh2"); ex_rec(W1, 4, "600 400 40 80"); eqlog("bind 1 in a 4x pass, clipped scissor");
    CHK("bind 4x facts", strstr(L, "samplemask a5a5\n") && strstr(L, fmt("ms mode%u rmode%u", U(DkMsMode_4x), U(DkMsMode_4x))));
    sc(-5, -7, 20, 30); OP(nxdk_op_bind_state, 1); CHK("scissor with negative origin", !strcmp(last_line(), " 0 0 15 23\n"));
    sc(700, 10, 5, 5); OP(nxdk_op_bind_state, 1); CHK("scissor wholly outside the pass has zero width", !strcmp(last_line(), " 700 10 0 5\n"));
    sc(0x7FFFFFFF, 0, 0x7FFFFFFF, 1); OP(nxdk_op_bind_state, 1); CHK("scissor sum does not wrap", !strcmp(last_line(), " 2147483647 0 0 1\n"));
    sc(10, -7, 5, 3); OP(nxdk_op_bind_state, 1); CHK("scissor wholly above the pass has zero height", !strcmp(last_line(), " 10 0 5 0\n"));
    sc(0x7FFFFFFF, 0, 0x7FFFFFFF, 1); pass(0, 1); OP(nxdk_op_bind_state, 1); CHK("outside a pass the scissor is not clipped", !strcmp(last_line(), " 2147483647 0 2147483647 1\n"));
    pass(0, 4); sc(1, 2, 3, 4); OP(nxdk_op_bind_state, 1); CHK("outside a pass the target is single-sample", !nxdk.errCount && strstr(L, "samplemask ffff\n") && strstr(L, "mode0 rmode0"));
    pass(1, 4); sc(10, 20, 30, 40); OP(nxdk_op_bind_state, 2);
    ex("layout 6"); ex("shaders mask%u n2", DkStageFlag_Vertex | DkStageFlag_Fragment); ex(" sh2"); ex(" sh3"); ex_rec(W2, 4, "0 0 640 480"); eqlog("bind 2: scissor test off, whole pass");
    CHK("bind 2 facts", strstr(L, "srgb 0\n") && strstr(L, "samplemask 0\n") && strstr(L, fmt("ms mode%u rmode%u", U(DkMsMode_1x), U(DkMsMode_1x))));
    CHK("bind 2 stats", st.state_binds == 9 && st.block_binds == 9 && nxdk_s.boundState == 2);
    st.state_binds = st.block_binds = 0; nxdk_s.boundState = 1;
    OP(nxdk_op_bind_state, 3); err1("bind vs not live", "BindState: shader 4 is not live");
    OP(nxdk_op_bind_state, 4); err1("bind ps not live", "BindState: shader 9 is not live");
    OP(nxdk_op_bind_state, 9); err1("bind vs 0", "BindState: shader 0 is not live");
    OP(nxdk_op_bind_state, 10); err1("bind both missing names vs", "BindState: shader 4 is not live");
    CHK("shader errors: counted, not bound", st.state_binds == 4 && st.block_binds == 0 && nxdk_s.boundState == 1);
    st.state_binds = 0; OP(nxdk_op_bind_state, 5); CHK("layout failure is one error", nxdk.errCount == 1 && !strcmp(nxdk.lastErr, "layout 99") && !strcmp(L, "layout 99\n"));
    CHK("layout failure binds nothing", st.state_binds == 1 && st.block_binds == 0 && nxdk_s.boundState == 1);
    OP(nxdk_op_bind_state, 6); err1("bind clip planes", "BindState: derived property clipPlaneMask 4 has no rule in this backend");
    OP(nxdk_op_bind_state, 7); err1("bind flat shading", "BindState: derived property flatShade has no rule in this backend");
    OP(nxdk_op_bind_state, 8); err1("bind point sprites", "BindState: derived property pointSprite has no rule in this backend");
    pass(1, 3); OP(nxdk_op_bind_state, 1); err1("bind in a 3-sample pass", "BindState: render state rejected by nxdk_state, code 2");
    CHK("rejected binds leave the bound state", nxdk_s.boundState == 1 && st.block_binds == 0); pass(1, 4);
}

static void t_scissor_stencil(void) {
    OPN(nxdk_op_scissor, 3, 0); err1("scissor argc 3", "Scissor: 3 words, need 4");
    OP(nxdk_op_scissor, 1, 2, (uint32_t)-1, 5); err1("scissor negative width", "Scissor: size -1 x 5");
    OP(nxdk_op_scissor, 1, 2, 5, (uint32_t)-3); err1("scissor negative height", "Scissor: size 5 x -3");
    nxdk_s.boundState = 0; sc(0, 0, 0, 0); OP(nxdk_op_scissor, (uint32_t)-4, 7, 30, 40);
    CHK("scissor stored, nothing recorded without a state", !nxdk.errCount && !L[0] && nxdk_s.scissor[0] == -4 && nxdk_s.scissor[1] == 7 && nxdk_s.scissor[2] == 30 && nxdk_s.scissor[3] == 40);
    pass(1, 4); nxdk_s.boundState = 1; OP(nxdk_op_scissor, 600, 400, 100, 100); ex("scissor first0 n1"); ex(" 600 400 40 80"); eqlog("scissor with a scissor state");
    nxdk_s.boundState = 2; OP(nxdk_op_scissor, 600, 400, 100, 100); ex("scissor first0 n1"); ex(" 0 0 640 480"); eqlog("scissor with a state without scissor test");
    /* order: Scissor then BindState, and BindState then Scissor, end in the same scissor */
    nxdk_s.boundState = 0; sc(0, 0, 0, 0); OP(nxdk_op_scissor, 5, 6, 70, 80); OP(nxdk_op_bind_state, 1); char a1[64]; snprintf(a1, sizeof a1, "%s", last_line());
    nxdk_s.boundState = 0; sc(0, 0, 0, 0); OP(nxdk_op_bind_state, 1); keep = 1; OP(nxdk_op_scissor, 5, 6, 70, 80); keep = 0;
    CHK("scissor order: both ways end in the same rect", !strcmp(a1, " 5 6 70 80\n") && !strcmp(last_line(), a1) && strstr(L, "scissor first0 n1\n 5 6 70 80\n") != NULL);
    OPN(nxdk_op_stencil_ref, 0); err1("stencil_ref argc 0", "StencilRef: 0 words, need 1");
    nxdk_s.boundState = 0; OP(nxdk_op_stencil_ref, 3); err1("stencil_ref no state", "StencilRef: no state bound");
    nxdk_s.boundState = 1; OP(nxdk_op_stencil_ref, 0x1AB); CHK("stencil_ref masks to 8 bits and keeps the masks", !nxdk.errCount && !strcmp(L, "stencil face3 mask0f refab fmaskf0\n") && nxdk_s.stencilRef == 0xAB);
    OP(nxdk_op_stencil_ref, 0); CHK("stencil_ref 0", !strcmp(L, "stencil face3 mask0f ref00 fmaskf0\n") && nxdk_s.stencilRef == 0);
    pass(1, 3); OP(nxdk_op_stencil_ref, 1); err1("stencil_ref in a 3-sample pass", "StencilRef: render state rejected by nxdk_dyn, code 2"); pass(1, 4);
}

static void t_ubo_idx(void) {
    OPN(nxdk_op_bind_ubo, 4, 1); err1("ubo argc 4", "BindUbo: 4 words, need 5");
    OP(nxdk_op_bind_ubo, 1, 0, 0, 0, 16); err1("ubo block 0", "BindUbo: block 0");
    OP(nxdk_op_bind_ubo, 1, 0, 4, 0, 16); err1("ubo block 4", "BindUbo: block 4");
    OP(nxdk_op_bind_ubo, 4, 0, 1, 0, 16); err1("ubo stage mask 4", "BindUbo: stage mask 4 (bit 0 vertex, bit 1 fragment)");
    OP(nxdk_op_bind_ubo, 0x80000001u, 0, 1, 0, 16); err1("ubo stage mask high bit", "BindUbo: stage mask 2147483649 (bit 0 vertex, bit 1 fragment)");
    OP(nxdk_op_bind_ubo, 1, 16, 1, 0, 16); err1("ubo slot 16", "BindUbo: slot 16 offset 0 size 16 (slot < 16, offset a multiple of 256, size 1..65536)");
    OP(nxdk_op_bind_ubo, 1, 0, 1, 0, 0); err1("ubo size 0", "BindUbo: slot 0 offset 0 size 0 (slot < 16, offset a multiple of 256, size 1..65536)");
    OP(nxdk_op_bind_ubo, 1, 0, 1, 0, 0x10001); err1("ubo size 65537", "BindUbo: slot 0 offset 0 size 65537 (slot < 16, offset a multiple of 256, size 1..65536)");
    OP(nxdk_op_bind_ubo, 1, 0, 1, 0x80, 16); err1("ubo offset 128", "BindUbo: slot 0 offset 128 size 16 (slot < 16, offset a multiple of 256, size 1..65536)");
    OP(nxdk_op_bind_ubo, 1, 0, 1, 0x3F00, 0x200); err1("ubo range", "bind: range 16128+512 outside block 1");
    OP(nxdk_op_bind_ubo, 1, 0, 1, 0xFFFFFF00u, 0x10000); err1("ubo range past 32 bits", "bind: range 4294967040+65536 outside block 1");
    fail_flush = 1; OP(nxdk_op_bind_ubo, 1, 0, 1, 0, 16); CHK("ubo flush failure: one error, no bind", nxdk.errCount == 1 && !strcmp(nxdk.lastErr, "bind: flush of block 1 failed") && !strstr(L, "ubo")); fail_flush = 0;
    OP(nxdk_op_bind_ubo, 1, 3, 1, 256, 512); ex("flush blk1 256 512"); ex("ubo stage%u id3 n1", DkStage_Vertex); ex(" addr100000100 size512"); eqlog("ubo vertex");
    OP(nxdk_op_bind_ubo, 2, 15, 2, 0, 65536); ex("flush blk2 0 65536"); OP(nxdk_op_bind_ubo, 2, 15, 3, 0, 65536); ex("flush blk3 0 65536");
    ex("ubo stage%u id15 n1", DkStage_Fragment); ex(" addr300000000 size65536"); eqlog("ubo fragment, max slot, max size");
    OP(nxdk_op_bind_ubo, 3, 1, 3, 0x200, 100); ex("flush blk3 512 100"); ex("ubo stage%u id1 n1", DkStage_Vertex); ex(" addr300000200 size100");
    ex("ubo stage%u id1 n1", DkStage_Fragment); ex(" addr300000200 size100"); eqlog("ubo both stages: vertex, then fragment");
    OP(nxdk_op_bind_ubo, 0, 1, 1, 0, 16); ex("flush blk1 0 16"); eqlog("ubo empty stage set: only the flush");
    OPN(nxdk_op_bind_idx, 3, 1); err1("idx argc 3", "BindIdx: 3 words, need 4");
    OP(nxdk_op_bind_idx, 4, 0, 16, 0); err1("idx block 4", "BindIdx: block 4");
    OP(nxdk_op_bind_idx, 0, 0, 16, 0); err1("idx block 0", "BindIdx: block 0");
    OP(nxdk_op_bind_idx, 4, 0, 16, 2); err1("idx block precedes type", "BindIdx: block 4");
    OP(nxdk_op_bind_idx, 1, 0, 16, 2); err1("idx type 2", "BindIdx: type 2 (0 u16, 1 u32)");
    nxdk_s.idxBlock = 7; nxdk_s.idxOffset = 8; nxdk_s.idxType = 1;
    OP(nxdk_op_bind_idx, 1, 0x3000, 0x2000, 0); err1("idx range", "bind: range 12288+8192 outside block 1");
    CHK("idx errors keep the binding", nxdk_s.idxBlock == 7 && nxdk_s.idxOffset == 8 && nxdk_s.idxType == 1);
    OP(nxdk_op_bind_idx, 1, 0x3000, 0x1000, 0); ex("flush blk1 12288 4096"); ex("idx fmt%u addr100003000", DkIdxFormat_Uint16); eqlog("idx range ending exactly at the block end");
    OP(nxdk_op_bind_idx, 1, 0x100, 0x20, 0); ex("flush blk1 256 32"); ex("idx fmt%u addr100000100", DkIdxFormat_Uint16); eqlog("idx u16");
    CHK("idx stored", nxdk_s.idxBlock == 1 && nxdk_s.idxOffset == 0x100 && nxdk_s.idxType == 0);
    OP(nxdk_op_bind_idx, 2, 0x10, 0x40, 1); ex("flush blk2 16 64"); ex("idx fmt%u addr200000010", DkIdxFormat_Uint32); eqlog("idx u32");
    CHK("idx stored 2", nxdk_s.idxBlock == 2 && nxdk_s.idxOffset == 0x10 && nxdk_s.idxType == 1);
}

static void t_textures(void) {
    OPN(nxdk_op_bind_textures, 2, 0); err1("tex argc 2", "BindTextures: 2 words, need 3");
    OP(nxdk_op_bind_textures, 2, 0, 0); err1("tex stage 2", "BindTextures: stage 2");
    OPN(nxdk_op_bind_textures, 4, 0, 0, 2, 1 | 1 << 20); err1("tex argc below 3 + n", "BindTextures: 4 words, need 5");
    OPN(nxdk_op_bind_textures, 3, 0, 0, 0xFFFFFFFFu); err1("tex n 2^32-1 needs 2^32+2 words", "BindTextures: 3 words, need 4294967298");
    OP(nxdk_op_bind_textures, 0, 15, 2, 1 | 1 << 20, 1 | 1 << 20); err1("tex slots 15+2", "BindTextures: slots 15+2 (16 per stage)");
    OP(nxdk_op_bind_textures, 0, 0xFFFFFFFFu, 2, 1 | 1 << 20, 1 | 1 << 20); err1("tex slot sum does not wrap", "BindTextures: slots 4294967295+2 (16 per stage)");
    OP(nxdk_op_bind_textures, 0, 0, 1, 0 | 1 << 20); err1("tex image 0", "BindTextures: image 0");
    OP(nxdk_op_bind_textures, 0, 0, 1, 6 | 1 << 20); err1("tex image 6", "BindTextures: image 6");
    OP(nxdk_op_bind_textures, 0, 0, 1, 0xFFFFF | 1 << 20); err1("tex image 0xFFFFF", "BindTextures: image 1048575");
    OP(nxdk_op_bind_textures, 0, 0, 1, 1); err1("tex sampler 0", "BindTextures: sampler 0");
    OP(nxdk_op_bind_textures, 0, 0, 1, 1 | 3 << 20); err1("tex sampler 3", "BindTextures: sampler 3");
    OP(nxdk_op_bind_textures, 0, 0, 1, 1 | 0xFFFu << 20); err1("tex sampler 4095", "BindTextures: sampler 4095");
    OP(nxdk_op_bind_textures, 0, 0, 3, 1 | 1 << 20, 2 | 2 << 20, 9 | 1 << 20); err1("tex third handle bad: nothing recorded", "BindTextures: image 9");
    OP(nxdk_op_bind_textures, 1, 0, 2, 0 | 1 << 20, 1 | 1 << 20); err1("tex first handle bad: nothing recorded", "BindTextures: image 0");
    OP(nxdk_op_bind_textures, 0, 3, 2, 1 | 1 << 20, 2 | 2 << 20);
    ex("tex stage%u first3 n2", DkStage_Vertex); ex(" h%x", 10 | 1 << 20); ex(" h%x", 21 | 2 << 20); eqlog("tex vertex: the sRGB view follows the sampler");
    OP(nxdk_op_bind_textures, 1, 15, 1, 5 | 1 << 20); ex("tex stage%u first15 n1", DkStage_Fragment); ex(" h%x", 50 | 1 << 20); eqlog("tex fragment slot 15");
    OPN(nxdk_op_bind_textures, 3, 1, 4, 0); CHK("tex n = 0 records nothing", !nxdk.errCount && !L[0]);
    OP(nxdk_op_bind_textures, 0, 0, 16, 1 | 1 << 20, 1 | 1 << 20, 1 | 1 << 20, 1 | 1 << 20, 1 | 1 << 20, 1 | 1 << 20, 1 | 1 << 20, 1 | 1 << 20, 2 | 2 << 20, 2 | 2 << 20, 2 | 2 << 20,
       2 | 2 << 20, 2 | 2 << 20, 2 | 2 << 20, 2 | 2 << 20, 3 | 2 << 20);
    ex("tex stage%u first0 n16", DkStage_Vertex);
    for (int i = 0; i < 16; i++) ex(" h%x", i < 8 ? 10 | 1 << 20 : i < 15 ? 21 | 2 << 20 : 31 | 2 << 20);
    eqlog("tex 16 handles");
}

static void t_draw(void) {
    nxdk_s.boundState = 1; pass(1, 4); st.draws = 0; nxdk_s.frameDraws = 0; nxdk_s.drawsFrame = 77;
    OPN(nxdk_op_draw, 4, 0); err1("draw argc 4", "Draw: 4 words, need 5");
    nxdk_s.boundState = 0; OP(nxdk_op_draw, 0, 3, 1, 0, 0); err1("draw no state", "Draw: no state bound"); nxdk_s.boundState = 1;
    pass(0, 1); OP(nxdk_op_draw, 0, 3, 1, 0, 0); err1("draw no pass", "Draw: no open pass"); pass(1, 4);
    OP(nxdk_op_draw, 6, 3, 1, 0, 0); err1("draw primitive 6", "primitive 6");
    nxdk_s.boundState = 0; pass(0, 1); OP(nxdk_op_draw, 6, 3, 1, 0, 0); err1("draw: state before pass before primitive", "Draw: no state bound"); nxdk_s.boundState = 1; pass(1, 4);
    fail_vtx = 1; OP(nxdk_op_draw, 0, 3, 1, 0, 0); CHK("draw vtx flush failure: one error, no draw, no stats", nxdk.errCount == 1 && !strcmp(L, "vtxflush\n") && st.draws == 0 && nxdk_s.frameDraws == 0); fail_vtx = 0;
    static const DkPrimitive P[6] = { DkPrimitive_Triangles, DkPrimitive_TriangleStrip, DkPrimitive_Lines, DkPrimitive_LineStrip, DkPrimitive_Points, DkPrimitive_TriangleFan };
    for (uint32_t p = 0; p < 6; p++) {
        OP(nxdk_op_draw, p, 11, 12, 13, 14); ex("vtxflush"); ex("draw p%u n11 i12 fv13 fi14", U(P[p])); eqlog(fmt("draw prim %u", p));
    }
    CHK("draw stats", st.draws == 6 && nxdk_s.frameDraws == 6 && nxdk_s.drawsFrame == 77);
    CHK("draw prim values are distinct", P[0] != P[1] && P[1] != P[2] && P[2] != P[3] && P[3] != P[4] && P[4] != P[5] && P[0] != P[5]);
    st.draws = 0; nxdk_s.frameDraws = 0;
    OPN(nxdk_op_draw_indexed, 5, 0); err1("drawidx argc 5", "DrawIndexed: 5 words, need 6");
    nxdk_s.boundState = 0; OP(nxdk_op_draw_indexed, 0, 3, 1, 0, 0, 0); err1("drawidx no state", "DrawIndexed: no state bound"); nxdk_s.boundState = 1;
    pass(0, 1); OP(nxdk_op_draw_indexed, 0, 3, 1, 0, 0, 0); err1("drawidx no pass", "DrawIndexed: no open pass"); pass(1, 4);
    nxdk_s.idxBlock = 0; OP(nxdk_op_draw_indexed, 0, 3, 1, 0, 0, 0); err1("drawidx no index block", "DrawIndexed: index block 0");
    nxdk_s.idxBlock = 4; OP(nxdk_op_draw_indexed, 0, 3, 1, 0, 0, 0); err1("drawidx dead index block", "DrawIndexed: index block 4");
    nxdk_s.idxBlock = 1; nxdk_s.idxOffset = 0x100; nxdk_s.idxType = 0;
    OP(nxdk_op_draw_indexed, 6, 3, 1, 0, 0, 0); err1("drawidx primitive 6", "primitive 6");
    OP(nxdk_op_draw_indexed, 0, 0x2000, 1, 0x1F00, 0, 0); err1("drawidx range u16", "bind: range 16128+16384 outside block 1");
    nxdk_s.idxType = 1; OP(nxdk_op_draw_indexed, 0, 0xFFFFFFFFu, 1, 0xFFFFFFFFu, 0, 0); err1("drawidx range cannot wrap", "bind: range 17179869436+17179869180 outside block 1");
    nxdk_s.idxType = 0; fail_vtx = 1; OP(nxdk_op_draw_indexed, 0, 20, 3, 10, (uint32_t)-5, 7);
    CHK("drawidx vtx flush failure: one error, no bind, no draw", nxdk.errCount == 1 && !strcmp(L, "flush blk1 276 40\nvtxflush\n") && st.draws == 0); fail_vtx = 0;
    fail_flush = 1; OP(nxdk_op_draw_indexed, 0, 20, 3, 10, 0, 0); CHK("drawidx flush failure: one error, nothing else", nxdk.errCount == 1 && !strcmp(nxdk.lastErr, "bind: flush of block 1 failed") && !strstr(L, "draw") && !strstr(L, "vtx")); fail_flush = 0;
    OP(nxdk_op_draw_indexed, 0, 20, 3, 10, (uint32_t)-5, 7); ex("flush blk1 276 40"); ex("vtxflush"); ex("idx fmt%u addr100000100", DkIdxFormat_Uint16);
    ex("drawidx p%u n20 i3 fx10 vo-5 fi7", U(DkPrimitive_Triangles)); eqlog("drawidx u16: flush, vertex flush, index bind, draw");
    nxdk_s.idxType = 1; nxdk_s.idxBlock = 2; nxdk_s.idxOffset = 0x10; OP(nxdk_op_draw_indexed, 5, 20, 1, 10, 100, 0);
    ex("flush blk2 56 80"); ex("vtxflush"); ex("idx fmt%u addr200000010", DkIdxFormat_Uint32); ex("drawidx p%u n20 i1 fx10 vo100 fi0", U(DkPrimitive_TriangleFan)); eqlog("drawidx u32 on block 2");
    CHK("drawidx stats", st.draws == 2 && nxdk_s.frameDraws == 2);
    OP(nxdk_op_draw_indexed, 4, 0, 1, 0, 0, 0); CHK("drawidx count 0 flushes an empty range", !nxdk.errCount && strstr(L, "flush blk2 16 0\n") && strstr(L, "drawidx p") && st.draws == 3);
}

static void t_dyn(void) {
    OPN(nxdk_op_viewport, 5, 0); err1("viewport argc 5", "Viewport: 5 words, need 6");
    float v[6] = { 1.5f, 2.5f, 640.0f, 480.0f, 0.25f, 0.75f }; uint32_t u[6]; memcpy(u, v, 24);
    OP(nxdk_op_viewport, u[0], u[1], u[2], u[3], u[4], u[5]); ex("viewport first0 n1"); ex(" 1.500 2.500 640.000 480.000 0.250 0.750"); eqlog("viewport");
    OPN(nxdk_op_blend_const, 3, 0); err1("blend_const argc 3", "BlendConst: 3 words, need 4");
    float b[4] = { 0.1f, 0.2f, 0.3f, 0.4f }; uint32_t ub[4]; memcpy(ub, b, 16);
    OP(nxdk_op_blend_const, ub[0], ub[1], ub[2], ub[3]); ex("blendconst 0.100 0.200 0.300 0.400"); eqlog("blend_const");
    OPN(nxdk_op_depth_bias, 1, 0); err1("depth_bias argc 1", "DepthBias: 1 words, need 2");
    float d[3] = { 1.5f, 2.5f, 9.0f }; uint32_t ud[3]; memcpy(ud, d, 12);
    OPN(nxdk_op_depth_bias, 2, ud[0], ud[1]); ex("depthbias 1.500 0.000 2.500"); eqlog("depth_bias units, slope");
    OP(nxdk_op_depth_bias, ud[0], ud[1], ud[2]); ex("depthbias 1.500 0.000 2.500"); eqlog("depth_bias ignores the clamp word");
}

static void t_neutral(void) {
    nxdk_s.boundState = 5; pass(1, 4); want = CMD2; reset(); nxdk_draw_neutral(CMD2);
    ex_rec(WN, 4, "0 0 640 480"); eqlog("neutral in a 4x pass");
    CHK("neutral facts", strstr(L, "srgb 0\n") && strstr(L, "cw ffffffff\n") && strstr(L, "samplemask ffff\n") && strstr(L, fmt("ms mode%u rmode%u", U(DkMsMode_4x), U(DkMsMode_4x))) &&
        strstr(L, "stencil face3 mask00 ref00 fmask00\n") && strstr(L, "color bm00") && strstr(L, "ds dt0 dw0 st0"));
    CHK("neutral forces the next BindState", nxdk_s.boundState == 0);
    nxdk_s.boundState = 5; pass(0, 1); reset(); nxdk_draw_neutral(CMD2); ex_rec(WN, 1, "0 0 640 480"); eqlog("neutral outside a pass");
    nxdk_s.boundState = 1; pass(1, 3); reset(); nxdk_draw_neutral(CMD2);
    err1("neutral in a 3-sample pass", "draw_neutral: render state rejected by nxdk_state, code 2"); CHK("neutral error keeps the bound state", nxdk_s.boundState == 1);
    want = CMD; nxdk_s.boundState = 0; pass(1, 4); OP(nxdk_op_draw, 0, 3, 1, 0, 0); err1("draw after neutral needs a BindState", "Draw: no state bound");
}

int main(void) {
    nxdk_s.stats = &st; nxdk_s.cmd = CMD; pass(1, 4);
    t_define(); t_bind_state(); t_scissor_stencil(); t_ubo_idx(); t_textures(); t_draw(); t_dyn(); t_neutral();
    printf("TOTAL %d BAD %d\n", total, bad);
    return 0;
}
