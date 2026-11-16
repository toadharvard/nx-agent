// ffp_prog.cpp: nx_ffp_vs_program / nx_ffp_ps_program (draw_ffp.h) and the backend programs behind them.
//  P1 Programs: ONE vertex and ONE pixel program (render/d3d9/ffp/ffp.vert.glsl, ffp.frag.glsl), in two variants selected by D3DRS_SHADEMODE FLAT (locations 0 / 1 `flat`, NX_FFP_FLAT; both stages by the same rule), made
//     lazily at the first draw that needs (stage, variant), cached on the device record (ffp_dev.cpp R1), destroyed with it. A program id is NOT a function of any other state: everything else is uniform data.
//  P2 The backend's shaders of the fixed-function programs are made once, when the backend is created, from the rows of the one GLSL table of render/gl4shim (nx_gl4shim.h, stage rows FFP_VS/PS_SMOOTH/FLAT); the
//     front end names a program by the row's reserved shader id (NX_GL4SHIM_SHADER_*) and never sends GLSL or DKSH. The id is a function of (stage, variant) only; there is no backend switch, no call.
//  P4 The blocks handed out (ubo[i].data) are the record's: the vertex block (memoised by its inputs, ffp_dev.h F2), the pixel block, and the world palette = the device's own WORLDMATRIX(0..255) states
//     (256 consecutive D3DMATRIX = the std140 mat4[256] of the program, row-major bytes: ffp_vs_state.h).
#include "ffp_dev.h"
#include "nx_gl4shim.h"

namespace {
enum nx_gl4shim_shader_id row(uint32_t stage, uint32_t flat) {
    if (stage == NxStage_Vertex) return flat ? NX_GL4SHIM_SHADER_FFP_VS_FLAT : NX_GL4SHIM_SHADER_FFP_VS_SMOOTH;
    return flat ? NX_GL4SHIM_SHADER_FFP_PS_FLAT : NX_GL4SHIM_SHADER_FFP_PS_SMOOTH;
}
}

bool nx_ffp_flat(NxDeviceCore* d, uint32_t* flat) {
    NxDrawDev* dd = nx_draw_dev(d); if (!dd) return false;
    uint32_t r = dd->latch.flat;                                       // a value naming no shade mode keeps the previous one (front_rs.c flat_shade)
    switch (d->cur.rs[D3DRS_SHADEMODE]) {
    case D3DSHADE_FLAT: r = 1; break;
    case D3DSHADE_GOURAUD: r = 0; break;
    case D3DSHADE_PHONG: r = 0; break;
    }
    *flat = r; return true;
}

uint32_t nx_ffp_make_program(NxFfpDev*, uint32_t stage, uint32_t flat) {
    return (uint32_t)row(stage, flat);                                  // P2: the reserved backend shader id of the stage row
}

uint32_t nx_ffp_vs_program(NxDeviceCore* d, NxFfpVsBind* out) {
    memset((void*)out, 0, sizeof *out);
    NxFfpDev* f = nx_ffp_dev(d); if (!f) return 0;
    NxFfpVsKey k; if (!nx_ffp_vs_key(d, &k)) return 0;
    uint32_t flat; if (!nx_ffp_flat(d, &flat)) return 0;
    if (!f->vs[flat]) { uint32_t id = nx_ffp_make_program(f, NxStage_Vertex, flat); if (!id) return 0; f->vs[flat] = id; }
    if (!f->vs_ok || memcmp(&k, &f->key, sizeof k)) { f->vs_ok = false; f->key = k; nx_ffp_vs_block(k, &f->vsb); f->vs_ok = true; }   // F2
    nx_ffp_vs_inputs(k, out);
    out->n_ubo = 2;
    out->ubo[0] = {NX_FFP_VS_UBO_BINDING, sizeof f->vsb, &f->vsb};
    out->ubo[1] = {NX_FFP_VS_PALETTE_BINDING, NX_FFP_MAX_BLEND_MATRICES * 64u, &d->cur.xf[nx_dxf(D3DTS_WORLDMATRIX(0))]};   // P4
    d->dirty &= ~(NX_G_LIGHTS | NX_G_XFORM | NX_G_MATERIAL | NX_G_TESS);
    return f->vs[flat];
}

uint32_t nx_ffp_ps_program(NxDeviceCore* d, NxFfpPsBind* out) {
    memset((void*)out, 0, sizeof *out);
    NxFfpDev* f = nx_ffp_dev(d); if (!f) return 0;
    uint32_t flat; if (!nx_ffp_flat(d, &flat)) return 0;
    if (!f->ps[flat]) { uint32_t id = nx_ffp_make_program(f, NxStage_Fragment, flat); if (!id) return 0; f->ps[flat] = id; }
    uint8_t tt[8]; nx_ffp_tex_types(d, tt);
    nx_ffp_ps_block(d, tt, &f->psb);
    out->tex_mask = nx_ffp_stage_mask(d->cur.tss, tt); out->n_in = nx_ffp_ps_pairs(out->tex_mask, out->in_pair);
    out->n_ubo = 1; out->ubo[0] = {NX_FFP_UBO_BINDING, sizeof f->psb, &f->psb};
    d->dirty &= ~NX_G_TEXFACTOR;
    return f->ps[flat];
}
