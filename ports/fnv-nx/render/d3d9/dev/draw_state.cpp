// draw_state.cpp: the passes of draws and the state flush: viewport, scissor, dynamics (blend constant, depth bias), the render-state words (front: nx_d3d9_translate), layout / state ids, BindState.
// Rules:
//  S1 the one global stream keeps what it has bound (g_nxb); everything is unbound after a pass begins (BeginPass4) and when another device draws, so `full` = the pass epoch changed or the device changed: every
//     binding is emitted again. Otherwise only what differs from g_nxb is emitted (dirty groups gate the recomputation of the large blocks, the comparison decides the ops: nothing when clean).
//  S2 render targets (NULL-format rule of copy_util.h: a target without storage has no attachment): colour targets 0..h (h = the last target with storage) in order; a target without storage BELOW h is an attachment
//     of a scratch RGBA8 image of the pass size (a pass has no holes: nxgpu4.h BeginPass4; nobody can read a NULL target); no colour target with storage and a depth target = a depth-only pass; no target of any kind =
//     one scratch colour target. Targets must agree in size and sample count (backend rule): a depth surface larger than the colour targets is NOT representable (reported, nxs_errors() counts the submit).
//  S3 colour write masks of NxRenderState ANDed with nx_d3d9_rt_rule(format).mask per target; the NxRenderState word 3 bits (nxgpu4.h NXRS_M_*): MSAA = MULTISAMPLEANTIALIAS != 0, AALINE = ANTIALIASEDLINEENABLE != 0, LASTPIXEL =
//     LASTPIXEL != 0, NOCLIP = CLIPPING == 0, bits 32.. = MULTISAMPLEMASK (the front leaves word 3 at 0).
//  S4 half pixel: the viewport already carries it (nx/state.h NxViewport x = X + 0.5), so VS posfix.xy = 0 and posfix.z = 1 (draw_const.cpp).
#include "draw_core.h"

NxBound g_nxb;
void nx_bind_shutdown();   // draw_bind.cpp
void nx_draw_shutdown() {
    for (auto& st : g_nxb.ubo) for (auto& c : st) nxd3d_free(c.data);
    nx_bind_shutdown(); memset(&g_nxb, 0, sizeof g_nxb);
}
NxDrawDev* nx_draw_dev(NxDeviceCore* d) { if (!d->draw) d->draw = nx_make_plain<NxDrawDev>(); return static_cast<NxDrawDev*>(d->draw); }   // null = no memory (nx_draw_exec asks first)

namespace {
struct Scratch { uint32_t w, h, s, image, stamp; };
Scratch g_scr[8]; uint32_t g_scr_stamp;
uint32_t scratch_image(uint32_t w, uint32_t h, uint32_t samples) {   // 0 = the backend has no image
    uint32_t v = 0, best = ~0u; ++g_scr_stamp;
    for (uint32_t i = 0; i < 8; i++) {
        if (g_scr[i].image && g_scr[i].w == w && g_scr[i].h == h && g_scr[i].s == samples) { g_scr[i].stamp = g_scr_stamp; return g_scr[i].image; }
        if (!g_scr[i].image) { v = i; best = 0; } else if (best && g_scr[i].stamp < best) { best = g_scr[i].stamp; v = i; }
    }
    if (g_scr[v].image) nxd3d_image_destroy(nullptr, g_scr[v].image);
    NxImageDesc4 d; memset(&d, 0, sizeof d); d.width = (uint16_t)w; d.height = (uint16_t)h; d.depth = 1; d.type = NxImgType_2D; d.format = NxFmt_RGBA8; d.mips = 1; d.samples = (uint8_t)samples; d.flags = NxImg_RenderTarget;
    g_scr[v] = {w, h, samples, nxd3d_image_create(nullptr, &d), g_scr_stamp}; return g_scr[v].image;
}
bool real(NxSurfCore* c) { return c && c->img->gpu && !nx_img_null(c->img); }
bool same(const NxTarget& a, const NxTarget& b) { return a.image == b.image && a.level == b.level && a.layer == b.layer; }
}

HRESULT nx_pass_resolve(NxDeviceCore* d, NxPassSet* ps) {
    const NxDeviceState& s = d->cur; memset((void*)ps, 0, sizeof *ps); NxSurfCore* c[4]; int hi = -1; uint32_t w = 0, h = 0, smp = 1;
    for (uint32_t i = 0; i < 4; i++) { c[i] = nx_surf_core(s.rt[i]); if (real(c[i])) hi = (int)i; if (c[i] && !w) { const NxSub* sb = nx_surf_sub(c[i]); w = sb->w; h = sb->h; smp = c[i]->img->samples ? c[i]->img->samples : 1; } }
    NxSurfCore* ds = nx_surf_core(s.ds[0]);
    if (real(ds)) { ps->dep = nx_target(ds); ps->has_dep = true; ps->dsc = ds; ps->depth_fmt = ds->img->fmt; if (!w) { const NxSub* sb = nx_surf_sub(ds); w = sb->w; h = sb->h; smp = ds->img->samples ? ds->img->samples : 1; } }
    ps->nc = hi >= 0 ? (uint32_t)hi + 1 : ps->has_dep ? 0 : 1;                                             // S2
    for (uint32_t i = 0; i < ps->nc; i++) {
        if (real(c[i])) { ps->col[i] = nx_target(c[i]); ps->csc[i] = c[i]; }
        else { ps->col[i].image = scratch_image(w, h, smp); ps->col[i].level = ps->col[i].layer = 0; if (!ps->col[i].image) return D3DERR_OUTOFVIDEOMEMORY; }
    }
    return D3D_OK;
}
void nx_pass_ensure(NxDeviceCore*, const NxPassSet& ps) {
    NxBound& b = g_nxb; bool ok = b.pass_ok && nxs_pass_open() && nxs_pass_epoch() == b.pass_epoch && b.nc == ps.nc && b.has_dep == ps.has_dep && (!ps.has_dep || same(b.dep, ps.dep));
    for (uint32_t i = 0; ok && i < ps.nc; i++) ok = same(b.col[i], ps.col[i]);
    if (ok) return;
    bool began = nxs_pass_begin(ps.nc, ps.col, ps.has_dep ? &ps.dep : nullptr); (void)began;   // the ring is reserved (nx_draw_exec): cannot be false
    b.pass_ok = true; b.pass_epoch = nxs_pass_epoch(); b.nc = ps.nc; b.has_dep = ps.has_dep; b.dep = ps.dep; for (uint32_t i = 0; i < ps.nc; i++) b.col[i] = ps.col[i];
}
static void emit_vp(const NxViewport& v) { uint32_t* w = nxs_op(NxOp_Viewport, 6); memcpy(w, &v, sizeof v); g_nxb.vp = v; g_nxb.vp_ok = 1; }

void nx_state_flush(NxDeviceCore* d, const NxDrawReq&, const NxProg& pr, const NxLayout& lay, const NxPassSet& ps, uint64_t groups) {
    NxBound& b = g_nxb; const NxDeviceState& s = d->cur; NxDrawDev* dd = nx_draw_dev(d); (void)groups;
    NxD3D9Input in; memset(&in, 0, sizeof in); in.rs = s.rs; in.ps_version = pr.ps_version; in.depth_format = ps.depth_fmt; in.latch = &dd->latch;
    memcpy(&in.viewport, &s.viewport[0], sizeof in.viewport); in.scissor.left = s.scissor[0].left; in.scissor.top = s.scissor[0].top; in.scissor.right = s.scissor[0].right; in.scissor.bottom = s.scissor[0].bottom;
    NxRenderState st; NxDynamicState dyn; uint32_t sb; nx_d3d9_translate(&in, &st, &dyn, &sb);
    const uint32_t* rs = s.rs;
    // S5 D3DRS_LASTPIXEL: Wine 9.0 wined3d_device_apply_stateblock logs "Last Pixel Drawing Disabled, not handled yet" once (static bool warned) when the state is FALSE and does nothing else
    //    (no GL state in the state table: lines are GL's diamond-exit lines for both values); same here, one line per process (g_nxb lives until nx_draw_shutdown).
    if (!rs[D3DRS_LASTPIXEL] && !b.fx_lastpixel) { b.fx_lastpixel = true; fnvnx_wine_fixme(NXW_wined3d_device_apply_stateblock); }
    st.w[3] = (rs[D3DRS_MULTISAMPLEANTIALIAS] != 0 ? 1ull << NXRS_M_MSAA : 0) | (rs[D3DRS_ANTIALIASEDLINEENABLE] != 0 ? 1ull << NXRS_M_AALINE : 0) | (rs[D3DRS_LASTPIXEL] != 0 ? 1ull << NXRS_M_LASTPIXEL : 0)   // S3
              | (rs[D3DRS_CLIPPING] == 0 ? 1ull << NXRS_M_NOCLIP : 0) | (uint64_t)rs[D3DRS_MULTISAMPLEMASK] << NXRS_M_SAMPLEMASK;
    for (uint32_t i = 0; i < ps.nc; i++) if (ps.csc[i]) { uint8_t m; nx_d3d9_rt_rule(ps.csc[i]->img->fmt, &m); st.w[0] &= ~(0xFull << NXRS_B_MASK(i)) | ((uint64_t)m << NXRS_B_MASK(i)); }
    if (!b.vp_ok || memcmp(&b.vp, &dyn.viewport, sizeof b.vp)) emit_vp(dyn.viewport);
    if (!b.sc_ok || memcmp(&b.sc, &dyn.scissor, sizeof b.sc)) { uint32_t* w = nxs_op(NxOp_Scissor, 4); memcpy(w, &dyn.scissor, sizeof dyn.scissor); b.sc = dyn.scissor; b.sc_ok = 1; }
    if (!b.blend_ok || memcmp(b.blend, dyn.blendConstant, 16)) { uint32_t* w = nxs_op(NxOp_BlendConst, 4); memcpy(w, dyn.blendConstant, 16); memcpy(b.blend, dyn.blendConstant, 16); b.blend_ok = 1; }
    float bias[2] = {dyn.depthBiasUnits, dyn.depthBiasSlope};
    if (!b.bias_ok || memcmp(b.bias, bias, 8)) { uint32_t* w = nxs_op(NxOp_DepthBias, 3); memcpy(w, bias, 8); w[2] = 0; memcpy(b.bias, bias, 8); b.bias_ok = 1; }
    uint32_t ls, ss, lid = nx_layout_id(lay.v, &ls), sid = nx_state_id(pr.vs, pr.ps, lid, ls, st, &ss);
    if (!b.state_ok || b.state_id != sid || b.state_serial != ss) { nxs_op(NxOp_BindState, 1)[0] = sid; b.state_id = sid; b.state_serial = ss; b.state_ok = true; }
}
