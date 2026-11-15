// draw_exec.cpp: one draw. Order (the order IS the rule: everything that may submit the stream or end the pass runs before the pass is ensured, everything that writes ops after nxs_reserve):
//  1 program pair (backend shader ids; a 0 = D3DERR_DRIVERINTERNALERROR, nothing recorded)  2 vertex layout (a draw that reads a stream without data has no vertex data: D3D_OK, nothing recorded)
//  3 textures + samplers (image uploads, sampler ids)  4 buffer mirrors (hazard rule)  5 pass targets, pass  6 nxs_reserve: the ops of the draw land in one segment and one pass epoch  7 flush by the rules of
//  draw_state.cpp S1: state, constant blocks, textures, streams, indices; the draw op  8 nx_img_gpu_wrote of every bound target, the dirty groups consumed are cleared.
//  Instances: indexed draws draw `freq[0] & 0x7fffff` instances, non-indexed draws one (draw_prim.cpp). Occlusion queries are the glue's business.
#include "draw_core.h"

namespace {
const uint64_t FFP_ONLY = NX_G_LIGHT | NX_G_LIGHTS | NX_G_XFORM | NX_G_MATERIAL | NX_G_TEXFACTOR | NX_G_WRAP | NX_G_TESS | NX_G_PALETTE | NX_G_MISC;   // cleared by the fixed-function module (draw_ffp.h)
void reset_tracking(NxDeviceCore* d) {
    NxBound& b = g_nxb; b.owner = d; b.epoch = nxs_pass_epoch(); b.state_ok = false; memset(b.vtx_ok, 0, sizeof b.vtx_ok); b.idx_ok = 0; memset(b.tex_ok, 0, sizeof b.tex_ok);
    for (auto& st : b.ubo) for (auto& c : st) c.ok = false;
    b.vp_ok = b.sc_ok = b.blend_ok = b.bias_ok = 0;
}
}
HRESULT nx_draw_exec(NxDeviceCore* d, const NxDrawReq& r) {
    NxDeviceState& s = d->cur; NxResDecl* decl = static_cast<NxResDecl*>(nx_obj_of((IUnknown*)s.vdecl[0]));
    if (!nxs_reserve()) return D3DERR_OUTOFVIDEOMEMORY;                                                                                              // E1: the ring (scratch, segments)
    if (!nx_draw_dev(d)) return D3DERR_OUTOFVIDEOMEMORY;
    NxProg pr; HRESULT hr = nx_prog_resolve(d, &pr); if (hr != D3D_OK) return hr;                                                                  // 1
    uint32_t bound = 0;
    for (uint32_t i = 0; i < NX4_MAX_STREAMS; i++) {
        NxBufCore* vb = s.stream[i].vb ? static_cast<NxBufCore*>(nx_obj_of((IUnknown*)s.stream[i].vb)) : nullptr;
        if (r.up ? i == 0 : vb && s.stream[i].offset < vb->length) bound |= 1u << i;
    }
    NxLayout lay; if (!nx_layout_build(decl, pr.sig, s.freq, bound, &lay)) return D3D_OK;                                                           // 2
    bool mem = nx_ubo_reserve(0, 0, 4096) && nx_ubo_reserve(0, 1, 512) && nx_ubo_reserve(0, 2, FNVNX_DK_VS_DRIVER_SIZE) && nx_ubo_reserve(1, 0, 4096) && nx_ubo_reserve(1, 1, 512) && nx_ubo_reserve(1, 2, FNVNX_DK_PS_DRIVER_SIZE);
    for (uint32_t i = 0; mem && i < pr.fv.n_ubo && !pr.vi; i++) mem = nx_ubo_reserve(0, pr.fv.ubo[i].slot, pr.fv.ubo[i].bytes);
    for (uint32_t i = 0; mem && i < pr.fp.n_ubo && !pr.pi; i++) mem = nx_ubo_reserve(1, pr.fp.ubo[i].slot, pr.fp.ubo[i].bytes);
    if (!mem) return D3DERR_OUTOFVIDEOMEMORY;
    NxTexPlan tp; if ((hr = nx_tex_plan(d, pr, &tp)) != D3D_OK) return hr;                                                                          // 3
    NxBindPlan bp; if ((hr = nx_bind_plan(d, r, lay, &bp)) != D3D_OK) return hr;                                                                     // 4
    NxPassSet ps; if ((hr = nx_pass_resolve(d, &ps)) != D3D_OK) { nx_bind_done(&bp); return hr; }                                                   // 5
    nx_pass_ensure(d, ps);
    uint32_t payload = 16384; for (uint32_t i = 0; i < pr.fv.n_ubo && !pr.vi; i++) payload += pr.fv.ubo[i].bytes + 256; for (uint32_t i = 0; i < pr.fp.n_ubo && !pr.pi; i++) payload += pr.fp.ubo[i].bytes + 256;
    if (r.up) { if (!bp.big_mem[0]) payload += r.up->vbytes + 256; if (r.indexed && !bp.big_mem[1]) payload += r.up->ibytes + 256; }
    if (!nxs_room(4096, payload)) { nx_bind_done(&bp); return D3DERR_OUTOFVIDEOMEMORY; }                                                                                                                      // 6
    NxBound& b = g_nxb; if (b.pass_ok && nxs_pass_open() && nxs_pass_epoch() != b.pass_epoch) b.pass_epoch = nxs_pass_epoch();   // a split of the pass by the reserve continues OUR pass
    bool full = b.owner != d || b.epoch != nxs_pass_epoch(); if (full) reset_tracking(d);
    uint64_t groups = full ? ~0ull : d->dirty;
    nx_state_flush(d, r, pr, lay, ps, groups); nx_const_flush(d, pr, ps, full, groups); nx_tex_flush(tp, full); nx_bind_flush(r, lay, bp, full);       // 7
    uint32_t vc = nx_prim_vertices(r.prim, r.count);
    if (r.indexed) { uint32_t* w = nxs_op(NxOp_DrawIndexed, 6); w[0] = nx_prim_nx(r.prim); w[1] = vc; w[2] = nx_instances(s); w[3] = r.first; w[4] = (uint32_t)r.base; w[5] = 0; }
    else { uint32_t* w = nxs_op(NxOp_Draw, 5); w[0] = nx_prim_nx(r.prim); w[1] = vc; w[2] = 1; w[3] = r.first; w[4] = 0; }
    for (uint32_t i = 0; i < ps.nc; i++) if (ps.csc[i]) nx_img_gpu_wrote(ps.csc[i]->img, ps.csc[i]->level, ps.csc[i]->face);                          // 8
    if (ps.dsc) nx_img_gpu_wrote(ps.dsc->img, ps.dsc->level, ps.dsc->face);
    nx_bind_done(&bp); d->dirty &= FFP_ONLY; return D3D_OK;
}
