/* fnv-nx L6 deko3d backend: render passes and Clear (ops BeginPass, BeginPass4, EndPass, Clear; the sRGB-write rebind; the internal target bind).
 * Reference: gl4_pass.c (same acceptance, same messages where the check is neutral: nxgpu4_check.c).
 *
 * Rules
 *  P1 A pass is up to 4 colour subresources (image, level, layer) and an optional depth-stencil one, all of one size and sample count
 *     (nxchk_pass_check). The colour image that equals the checker's swapImage is nxdk.front (its NxDkSub.id is 0, it has no sRGB view).
 *  P2 A subresource is bound as the DkImageView of its image: mipLevelOffset = level, mipLevelCount 1, layerOffset = layer, and a cube as
 *     the non-layered type 2D (one face). deko3d refuses layerOffset on a 3D view (fromImageView, DK_DEBUG_BAD_INPUT) and has no other
 *     single-slice view of a 3D image, so a 3D image is a rejected target (open rule, one nxdk_err). A colour format without DKFMT_RENDER
 *     is rejected too (the image has no UsageRender: GL reports the same as an incomplete framebuffer).
 *  P3 sRGB: the colour views are the sRGB ones (format DkFmt.srgb) exactly when nxdk_img_view_id(id, 1) != id and passSrgb is set; a pass
 *     starts with passSrgb 0 (GL: FRAMEBUFFER_SRGB off at begin). nxdk_pass_srgb changes it and rebinds; an equal value records nothing.
 *  P4 deko3d clears are bounded by the scissor of viewport 0 (GL: scissor test). The load-clear of BeginPass sets the whole target
 *     first (GL: test off); Clear sets the op rect, and afterwards the scissor the bound state implies: the last Scissor op when the bound
 *     state enables the scissor test (NXRS_R_SCISSOR), else the whole pass (GL: apply_state). Clears use all four channels.
 *  P5 A buffer the pass does not have clears nothing (GL 4.5 17.4.3): depth needs NxClear_Depth, stencil needs NxClear_Stencil and the
 *     DKFMT_STENCIL format (both: one dkCmdBufClearDepthStencil), as in gl4_pass.c. Clear needs an open pass (no render target otherwise).
 *  P6 Pass start (GL begin_end): boundState 0, viewport (0.5, 0.5, w, h, 0, 1), scissor (0, 0, w, h) (also nxdk_s.scissor), blend constant
 *     (1,1,1,1), depth bias 0. EndPass: inPass 0, boundState 0 (S6). The reference has no resolve (a multisampled image is sampled as such).
 *  P7 Clear with NxClear_Srgb colours through the sRGB views (GL: FRAMEBUFFER_SRGB on), then rebinds the pass's own choice. Assumption:
 *     deko3d encodes a clear colour written through an sRGB-format view (console-verifiable).
 *  P8 nxdk_target_bind (internal draws) is refused inside an open pass: it would replace the pass's bindings and nothing restores them.
 * Assumptions: native code is tested (ASan/UBSan), not proven; the target and the scissor of viewport 0 act on clears as the P4 comment says. */
#include "dk.h"
#include <string.h>

static float asf(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

static void view(const NxDkSub* t, uint32_t srgb, DkImageView* v) {
    dkImageViewDefaults(v, &t->im->dk);
    v->mipLevelOffset = t->level; v->mipLevelCount = 1; v->layerOffset = t->layer;
    if (t->im->type == NxImgType_Cube) v->type = DkImageType_2D;
    if (srgb && nxdk_img_view_id(t->id, 1) != t->id) v->format = dk_fmt(t->im->fmt).srgb;
}

static void bind(DkCmdBuf cmd, const NxDkSub* c, uint32_t n, const NxDkSub* d, uint32_t srgb) {
    DkImageView cv[4], dv;
    const DkImageView* cp[4] = { NULL, NULL, NULL, NULL };
    for (uint32_t i = 0; i < n; i++) { view(&c[i], srgb, &cv[i]); cp[i] = &cv[i]; }
    if (d && d->im) view(d, 0, &dv);
    dkCmdBufBindRenderTargets(cmd, cp, n, d && d->im ? &dv : NULL);
}

static int usable(const NxDkSub* s, uint32_t i, int depth) {
    if (s->im->type == NxImgType_3D) { nxdk_err("pass: target %u is a 3D image, deko3d has no single-slice view of one", i); return 0; }
    if (!depth && !(dk_fmt(s->im->fmt).flags & DKFMT_RENDER)) {
        nxdk_err("pass: target %u: format %u is not a render format of deko3d", i, s->im->fmt);
        return 0;
    }
    return 1;
}

static void resolve(uint32_t image, uint32_t sub, NxDkSub* s) {
    const int sw = image == nxdk_chk_env()->swapImage;
    s->im = sw ? &nxdk.front : nxdk_img(image);   /* the check just proved the image live */
    s->id = sw ? 0 : image; s->level = sub & 0xFFFF; s->layer = sub >> 16; s->x = 0; s->y = 0;
    s->w = nxchk_lvl(s->im->w, s->level); s->h = nxchk_lvl(s->im->h, s->level);
}

/* checks p, then opens the pass (targets recorded, bound without sRGB views); 1 = rejected with nothing recorded. *w, *h: the target size */
static int open_pass(const NxChkPass* p, uint32_t* w, uint32_t* h) {
    NxDkSub c[4], d;
    uint32_t ns = 0;
    memset(c, 0, sizeof c); memset(&d, 0, sizeof d);
    if (nxchk_pass_check(nxdk_chk_env(), p, w, h, &ns)) return 1;
    for (uint32_t i = 0; i < p->nColor; i++) { resolve(p->c[i].image, p->c[i].sub, &c[i]); if (!usable(&c[i], i, 0)) return 1; }
    if (p->d.image) { resolve(p->d.image, p->d.sub, &d); if (!usable(&d, p->nColor, 1)) return 1; }
    memcpy(nxdk_s.passColor, c, sizeof c); nxdk_s.passDepth = d;
    nxdk_s.passColors = p->nColor; nxdk_s.passSamples = ns; nxdk_s.passSrgb = 0; nxdk_s.inPass = 1;
    bind(nxdk_s.cmd, c, p->nColor, &d, 0);
    return 0;
}

static void clear_color(uint32_t rgba8) {
    for (uint32_t i = 0; i < nxdk_s.passColors; i++)
        dkCmdBufClearColorFloat(nxdk_s.cmd, i, DkColorMask_RGBA, (rgba8 & 255) / 255.0f, (rgba8 >> 8 & 255) / 255.0f,
                                (rgba8 >> 16 & 255) / 255.0f, (rgba8 >> 24) / 255.0f);
}

static void clear_ds(uint32_t flags, uint32_t zbits, uint32_t stencil) {
    const NxDkSub* d = &nxdk_s.passDepth;
    if (!d->im) return;
    const int z = (flags & NxClear_Depth) != 0, s = (flags & NxClear_Stencil) && (dk_fmt(d->im->fmt).flags & DKFMT_STENCIL);
    if (z || s) dkCmdBufClearDepthStencil(nxdk_s.cmd, z, asf(zbits), s ? 0xFF : 0, (uint8_t)(stencil & 0xFF));
}

static void scissor(uint32_t x, uint32_t y, uint32_t w, uint32_t h) { const DkScissor s = { x, y, w, h }; dkCmdBufSetScissors(nxdk_s.cmd, 0, &s, 1); }

static void begin_end(uint32_t w, uint32_t h) {
    const DkViewport vp = { 0.5f, 0.5f, (float)w, (float)h, 0.0f, 1.0f };
    nxdk_s.passW = w; nxdk_s.passH = h; nxdk_s.boundState = 0;
    dkCmdBufSetViewports(nxdk_s.cmd, 0, &vp, 1);
    scissor(0, 0, w, h);
    nxdk_s.scissor[0] = 0; nxdk_s.scissor[1] = 0; nxdk_s.scissor[2] = (int32_t)w; nxdk_s.scissor[3] = (int32_t)h;   /* CONTRACT-REQUEST: dk_draw.c owns it */
    dkCmdBufSetBlendConst(nxdk_s.cmd, 1.0f, 1.0f, 1.0f, 1.0f);
    dkCmdBufSetDepthBias(nxdk_s.cmd, 0.0f, 0.0f, 0.0f);
}

/* NxOp_BeginPass (v3): colorImage, depthImage, clearFlags, clear RGBA8, clear depth, clear stencil, width, height */
void nxdk_op_begin_pass(const uint32_t* a, uint32_t argc) {
    NxChkPass p;
    uint32_t w, h;
    if (argc < 8) { nxdk_err("BeginPass: %u words", argc); return; }
    memset(&p, 0, sizeof p);
    p.nColor = 1; p.c[0].image = a[0] ? a[0] : nxdk_chk_env()->swapImage; p.d.image = a[1];
    if (open_pass(&p, &w, &h)) return;
    if (a[2] & (NxClear_Color | NxClear_Depth | NxClear_Stencil)) {
        scissor(0, 0, w, h);
        if (a[2] & NxClear_Color) clear_color(a[3]);
        clear_ds(a[2], a[4], a[5]);
    }
    begin_end(a[6], a[7]);
}

/* NxOp_BeginPass4: colorCount (0-4), (image, level | layer << 16) per colour target, depth (image, level | layer << 16) */
void nxdk_op_begin_pass4(const uint32_t* a, uint32_t argc) {
    NxChkPass p;
    uint32_t w, h;
    if (nxchk_pass_parse4(nxdk_chk_env(), a, argc, &p) || open_pass(&p, &w, &h)) return;
    begin_end(w, h);
}

void nxdk_op_end_pass(const uint32_t* a, uint32_t argc) { (void)a; (void)argc; nxdk_s.inPass = 0; nxdk_s.boundState = 0; }

/* NxOp_Clear: flags, RGBA8, depth, stencil, x, y, w, h: a rectangle of the pass's targets, outside the bound state */
void nxdk_op_clear(const uint32_t* a, uint32_t argc) {
    if (argc < 8) { nxdk_err("Clear: %u words", argc); return; }
    if (!nxdk_s.inPass) { nxdk_err("Clear: no open pass"); return; }
    const uint32_t srgb = (a[0] & NxClear_Srgb) != 0;
    scissor(a[4], a[5], a[6], a[7]);
    if (a[0] & NxClear_Color) {
        if (srgb != nxdk_s.passSrgb) bind(nxdk_s.cmd, nxdk_s.passColor, nxdk_s.passColors, &nxdk_s.passDepth, srgb);
        clear_color(a[1]);
        if (srgb != nxdk_s.passSrgb) bind(nxdk_s.cmd, nxdk_s.passColor, nxdk_s.passColors, &nxdk_s.passDepth, nxdk_s.passSrgb);
    }
    clear_ds(a[0], a[2], a[3]);
    if (nxdk_s.boundState && NXRS_GET(nxdk_s.st[nxdk_s.boundState].rs.w[2], NXRS_R_SCISSOR, 1))
        scissor((uint32_t)nxdk_s.scissor[0], (uint32_t)nxdk_s.scissor[1], (uint32_t)nxdk_s.scissor[2], (uint32_t)nxdk_s.scissor[3]);
    else scissor(0, 0, nxdk_s.passW, nxdk_s.passH);
}

void nxdk_pass_srgb(uint32_t on) {
    on = on != 0;
    if (!nxdk_s.inPass) { nxdk_err("pass_srgb: no open pass"); return; }
    if (on == nxdk_s.passSrgb) return;
    nxdk_s.passSrgb = on;
    bind(nxdk_s.cmd, nxdk_s.passColor, nxdk_s.passColors, &nxdk_s.passDepth, on);
}

void nxdk_target_bind(DkCmdBuf cmd, const NxDkSub* dst) {
    if (nxdk_s.inPass) { nxdk_err("target_bind: inside an open pass"); return; }
    if (!usable(dst, 0, 0)) return;
    const uint32_t w = nxchk_lvl(dst->im->w, dst->level), h = nxchk_lvl(dst->im->h, dst->level);
    const DkViewport vp = { 0.0f, 0.0f, (float)w, (float)h, 0.0f, 1.0f };
    const DkScissor sc = { 0, 0, w, h };
    bind(cmd, dst, 1, NULL, 0);
    dkCmdBufSetViewports(cmd, 0, &vp, 1);
    dkCmdBufSetScissors(cmd, 0, &sc, 1);
}
