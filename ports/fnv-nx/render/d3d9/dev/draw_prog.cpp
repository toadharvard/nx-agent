// draw_prog.cpp: the program pair of a draw: backend shader ids of (vertex shader, pixel shader), the link set L, the VS variant bit, the vertex input signature.
// Rules (all from render/shaders/include/fnvnx_dk_layout.h and render/shaders/cache/shcache.h, restated here):
//  P1 vertex inputs: the declared inputs of the VS (v<n> with their dcl (usage, index)); vs_1_x has no dcl: v<n> = the usage of the Shader Model 1 register table (v0 POSITION, v1 BLENDWEIGHT, v2 BLENDINDICES,
//     v3 NORMAL, v4 PSIZE, v5 COLOR0, v6 COLOR1, v7-v14 TEXCOORD0-7, v15 POSITION1: mojoshader.c shader_model_1_input_usage, which the translator uses). Location of input k = fnvnx_dk_assign(ATTRIB) over the set.
//  P2 L = the varying link set of the pair: (VS outputs without POSITION0 / PSIZE0) U (PS inputs), empty when every pair is canonical (fnvnx_dk_varying_location >= 0), else the set ascending (shcache_link_set).
//  P3 the variant is the translator's key flags (fnvnx_shaders.h): FNVNX_SHADER_VS_COMPLETE iff fnvnx_dk_vs_needs_complete(VS written = declared outputs without POSITION0 / PSIZE0 plus FOG0, PS read = declared inputs plus FOG0 for ps < 3.0). A fixed-function VS is complete by construction
//     (draw_ffp.h): variant 0 and no pair of its own. A PS stage has variant 0.
//  P4 a backend shader id is made at the first draw that needs the (shader, variant, L) and cached on the shader object (NxShaderCore::progs); it is destroyed with the object (nxd3d_shader_ids_destroy).
//     A 0 from the backend = D3DERR_DRIVERINTERNALERROR (a backend defect, never a skipped draw); no backend memory for the bytecode copy = D3DERR_OUTOFVIDEOMEMORY (glue_stream.h exhaustion rules: the result of the call, nothing recorded).
#include "draw_core.h"
#include "../gen/d9impl.gen.h"
#include "ffp_dev.h"
#include "nx_pair.h"
#include <string.h>

bool nx_vs_sig(const NxSmIface& i, NxVsSig* out) {
    static const uint8_t sm1[16][2] = {{0, 0}, {1, 0}, {2, 0}, {3, 0}, {4, 0}, {10, 0}, {10, 1}, {5, 0}, {5, 1}, {5, 2}, {5, 3}, {5, 4}, {5, 5}, {5, 6}, {5, 7}, {0, 1}};
    out->n = 0;
    for (uint32_t k = 0; k < 16; k++) {
        if (!(i.in_mask >> k & 1)) continue;
        bool dcl = i.in_usage[k] != 0xFF; uint32_t u = dcl ? i.in_usage[k] : sm1[k][0], x = dcl ? i.in_index[k] : sm1[k][1];
        out->pair[out->n++] = FNVNX_DK_PAIR(u, x);
    }
    signed char loc[16]; if (fnvnx_dk_assign(FNVNX_DK_KIND_ATTRIB, out->pair, (int)out->n, loc)) return false;
    for (uint32_t k = 0; k < out->n; k++) out->loc[k] = (uint8_t)loc[k];
    return true;
}
static uint32_t shader_prog(NxShaderCore* s, uint32_t stage, uint32_t variant, const uint8_t* l, uint32_t nl, HRESULT* hr) {
    for (uint32_t i = 0; i < s->nprogs; i++) { const NxShaderProg& p = s->progs[i]; if (p.variant == variant && p.n_l == nl && !memcmp(p.l, l, nl)) return p.id; }
    if (s->nprogs == s->cprogs) {   // room for the entry first: a shader made by the backend is never left without its record
        uint32_t c = s->cprogs ? s->cprogs * 2 : 4; NxShaderProg* n = (NxShaderProg*)nxd3d_alloc(c * sizeof(NxShaderProg)); if (!n) { *hr = D3DERR_OUTOFVIDEOMEMORY; return 0; }
        if (s->nprogs) memcpy(n, s->progs, s->nprogs * sizeof(NxShaderProg));
        nxd3d_free(s->progs); s->progs = n; s->cprogs = c;
    }
    uint32_t cb = (s->nbytes + 3u) & ~3u, bytes = (cb + nl + 4095u) & ~4095u; uint8_t* m = (uint8_t*)nxd3d_gpu_alloc(bytes); if (!m) { *hr = D3DERR_OUTOFVIDEOMEMORY; return 0; } memset(m, 0, bytes);
    memcpy(m, s->code, s->nbytes); memcpy(m + cb, l, nl);
    NxShaderD3D* d = (NxShaderD3D*)nxs_scratch(sizeof *d); memset(d, 0, sizeof *d);
    d->bytecode = FNVNX_ADDR_(m); d->size = s->nbytes; d->stage = (uint8_t)stage; d->variant = (uint8_t)variant; memcpy(d->key, s->key, sizeof d->key); d->link_pairs = FNVNX_ADDR_(m + cb); d->n_link_pairs = nl;
    uint64_t a[1] = {FNVNX_ADDR_(d)}, r = nxd3d_gpu_call(NXGPU_FN_SHADER_CREATE_D3D, a, 1); nxd3d_gpu_free(m);
    if (!nxs_ok(r) || r == 0 || r >= (1u << 16)) { *hr = D3DERR_DRIVERINTERNALERROR; return 0; }
    NxShaderProg& p = s->progs[s->nprogs++]; memset(&p, 0, sizeof p); p.variant = (uint8_t)variant; p.n_l = (uint8_t)nl; memcpy(p.l, l, nl); p.id = (uint32_t)r; return p.id;
}
void nxd3d_shader_ids_destroy(NxShaderCore* s) {
    nxs_flush();                                                       // the pending stream may name the ids
    for (uint32_t i = 0; i < s->nprogs; i++) { uint64_t a[1] = {s->progs[i].id}; nxd3d_gpu_call(NXGPU_FN_SHADER_DESTROY, a, 1); nx_state_forget_shader(s->progs[i].id); }
}
static NxShaderCore* shader_of(IUnknown* o) { return static_cast<NxShaderCore*>(nx_obj_of(o)); }
HRESULT nx_prog_resolve(NxDeviceCore* d, NxProg* p) {
    const NxDeviceState& s = d->cur; memset((void*)p, 0, sizeof *p);
    NxShaderCore* vsc = s.vs[0] ? shader_of(s.vs[0]) : nullptr; NxShaderCore* psc = s.ps[0] ? shader_of(s.ps[0]) : nullptr;
    uint8_t vout[224], pin[224], l[FNVNX_DK_VARYING_SLOTS]; uint32_t nvo = 0, npi = 0, nl = 0;
    if (vsc) {
        p->vi = &vsc->iface; if (!nx_vs_sig(vsc->iface, &p->sig)) return D3DERR_DRIVERINTERNALERROR;
        nvo = vsc->iface.n_pairs; memcpy(vout, vsc->iface.pairs, nvo);
    } else {
        p->vs = nx_ffp_vs_program(d, &p->fv); if (!p->vs) return D3DERR_DRIVERINTERNALERROR;
        p->sig.n = p->fv.n_in > 16 ? 16 : p->fv.n_in; memcpy(p->sig.pair, p->fv.in_pair, p->sig.n); memcpy(p->sig.loc, p->fv.in_loc, p->sig.n);
    }
    if (psc) {
        p->pi = &psc->iface; p->ps_version = psc->iface.major << 8 | psc->iface.minor;
        npi = psc->iface.n_pairs; memcpy(pin, psc->iface.pairs, npi);
    } else {
        p->ps = nx_ffp_ps_program(d, &p->fp); if (!p->ps) return D3DERR_DRIVERINTERNALERROR;
        npi = p->fp.n_in > 31 ? 31 : p->fp.n_in; memcpy(pin, p->fp.in_pair, npi);
    }
    HRESULT hr = D3D_OK;
    // D3DRS_SHADEMODE FLAT (Wine 9.0 state_shademode): both translated stages take the same FNVNX_SHADER_FLAT_COLOR (the COLOR varyings are `flat`), from the one latch rule of the FFP
    // programs (nx_ffp_flat), so the two stages of a pair, and a translated stage against an FFP one, always agree on the qualifier.
    uint32_t flat = 0; if ((vsc || psc) && !nx_ffp_flat(d, &flat)) return D3DERR_OUTOFVIDEOMEMORY;
    uint32_t vvar, pvar;   // the pair's selection, one pure function (nx_pair_variant): the link set, the VS variant and the PS variant
    if (nx_pair_variant(vout, nvo, psc ? psc->iface.major : 3, pin, npi, flat, &vvar, &pvar, l, &nl)) return D3DERR_DRIVERINTERNALERROR;
    if (vsc) { p->vs = shader_prog(vsc, 0, vvar, l, nl, &hr); if (!p->vs) return hr; }
    if (psc) { p->ps = shader_prog(psc, 1, pvar, l, nl, &hr); if (!p->ps) return hr; }
    return D3D_OK;
}
