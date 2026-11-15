// draw_tex.cpp: samplers and texture bindings of a draw.
//  T1 which slots a program samples (data, not a decision from results): a pixel shader of sm 2 and later: the samplers it declares (NxSmIface::sampler_mask), sampler register s<n> reads sampler state / texture n at slot n;
//     a ps_1_x shader (stages sampled) and the fixed-function pixel stage (NxFfpPsBind::tex_mask): stage n's texture at the slot of its dimension (2D n, CUBE 8 + n, VOLUME 16 + n; fnvnx_dk_layout.h) with the type word
//     in the driver block; a vertex shader: the samplers it declares, s<i> = D3DVERTEXTEXTURESAMPLER<i> = sampler slot 17 + i at vertex slot i. D3DDMAPSAMPLER (slot 16) is read only by the tessellator (MS "displacement map
//     sampler"), no program of a non-patch draw reads it: it has no binding.
//  T2 handle = NX_TEX_HANDLE(image, sampler): image = the texture's backend image, + 1 when the sampler translation says sRGB (D3DSAMP_SRGBTEXTURE != 0 and the format has an sRGB view: NXSMP_SRGB; nxgpu4.h /
//     gpu_abi.h: "id + 1 is the same image seen through its sRGB view"); sampler = the backend sampler id of the NxSamplerState nx_d3d9_sampler returned (content-cached, 255 ids). A slot without a texture (or whose
//     texture has no backend image: NxFmt_None) has handle 0.
//  T3 shadow = a depth texture of a D3DFMT_D* enumerator (the FOURCC depth formats INTZ / DF16 / DF24 are plain reads: Nine nine_state shadow flag), managed_lod = the LOD of a MANAGED texture.
//  T4 sampler ids are redefined by rule when the 255 are used: the least recently used one that no sampler of this draw uses; NXGPU_FN_SAMPLER_UPDATE changes the id for the whole stream, so the pending stream is submitted
//     first (nxs_flush). This and the NxImg::sync uploads end the open pass: nx_tex_plan runs BEFORE the pass is ensured.
#include "draw_core.h"

namespace {
struct SampE { NxSamplerState s; uint32_t id, stamp; };
SampE g_samp[NX_MAX_SAMPLERS]; uint32_t g_ns = 0, g_sstamp = 0, g_sfloor = 0;
uint32_t sampler_id(const NxSamplerState& s) {
    for (uint32_t i = 0; i < g_ns; i++) if (!memcmp(&g_samp[i].s, &s, sizeof s)) { g_samp[i].stamp = ++g_sstamp; return g_samp[i].id; }
    NxSamplerState* m = (NxSamplerState*)nxs_scratch(sizeof s); *m = s; uint64_t a[1] = {FNVNX_ADDR_(m)};
    if (g_ns < NX_MAX_SAMPLERS - 1) { uint64_t r = nxd3d_gpu_call(NXGPU_FN_SAMPLER_CREATE, a, 1); if (!nxs_ok(r) || !r) return 0; g_samp[g_ns] = {s, (uint32_t)r, ++g_sstamp}; return g_samp[g_ns++].id; }
    uint32_t v = 0, best = ~0u; for (uint32_t i = 0; i < g_ns; i++) if (g_samp[i].stamp < g_sfloor && g_samp[i].stamp < best) { best = g_samp[i].stamp; v = i; }   // T4: one exists (<= 21 samplers per draw)
    nxs_flush(); m = (NxSamplerState*)nxs_scratch(sizeof s); *m = s; uint64_t b[2] = {g_samp[v].id, FNVNX_ADDR_(m)};
    nxd3d_gpu_call(NXGPU_FN_SAMPLER_UPDATE, b, 2); g_samp[v].s = s; g_samp[v].stamp = ++g_sstamp; return g_samp[v].id;
}
// the texture of sampler slot `slot` -> image, handle (0 = none); *dim = 0 2D / 1 cube / 2 volume
uint32_t handle_of(NxDeviceCore* d, uint32_t slot, uint32_t* dim, HRESULT* hr) {
    const NxDeviceState& s = d->cur; *dim = 0; NxObj* o = s.tex[slot] ? nx_obj_of((IUnknown*)s.tex[slot]) : nullptr; NxImg* im = o ? nx_img_of(o) : nullptr;
    if (!im) return 0;
    *dim = im->kind == NXK_CUBE ? 1 : im->kind == NXK_VOLUME ? 2 : 0; if (!im->sync(true)) { *hr = D3DERR_OUTOFVIDEOMEMORY; return 0; }
    if (!im->gpu) return 0;
    NxD3D9Texture tx; memset(&tx, 0, sizeof tx); tx.format = im->fmt; tx.cube = im->kind == NXK_CUBE; tx.shadow = (im->ni.flags & NXFMT_DEPTH) && im->fmt < 0x1000; tx.managed_lod = im->pool == D3DPOOL_MANAGED ? (uint8_t)im->lod : 0;   // T3
    NxSamplerState ss; nx_d3d9_sampler(s.samp[slot], &tx, &ss); uint32_t sid = sampler_id(ss); if (!sid) { *hr = D3DERR_DRIVERINTERNALERROR; return 0; }
    nxs_ref_image(im->gpu); return NX_TEX_HANDLE(im->gpu + ((ss.filter & NXSMP_SRGB) ? 1u : 0u), sid);                                          // T2
}
void put(NxTexPlan* p, uint32_t st, uint32_t slot, uint32_t h) { p->h[st][slot] = h; if (slot < p->lo[st]) p->lo[st] = slot; if (slot + 1 > p->hi[st]) p->hi[st] = slot + 1; }
}

HRESULT nx_tex_plan(NxDeviceCore* d, const NxProg& pr, NxTexPlan* plan) {
    HRESULT hr = D3D_OK; memset(plan, 0, sizeof *plan); plan->lo[0] = plan->lo[1] = 24; g_sfloor = ++g_sstamp;
    if (pr.pi && pr.pi->major >= 2) { for (uint32_t n = 0; n < 16; n++) if (pr.pi->sampler_mask >> n & 1) { uint32_t dm; put(plan, 1, n, handle_of(d, n, &dm, &hr)); } }                   // T1
    else {
        uint32_t mask = pr.pi ? pr.pi->sampler_mask : pr.fp.tex_mask;
        for (uint32_t n = 0; n < 8; n++) if (mask >> n & 1) { uint32_t dm, h = handle_of(d, n, &dm, &hr); plan->stype[n] = (uint8_t)dm; put(plan, 1, dm == 1 ? 8 + n : dm == 2 ? 16 + n : n, h); }
    }
    if (pr.vi) for (uint32_t i = 0; i < NXD_VS_SAMPLERS; i++) if (pr.vi->sampler_mask >> i & 1) { uint32_t dm; put(plan, 0, i, handle_of(d, NXD_SAMP_VS0 + i, &dm, &hr)); }
    return hr;
}
void nx_tex_flush(const NxTexPlan& plan, bool) {
    for (uint32_t st = 0; st < 2; st++) {
        uint32_t first = 24, last = 0;
        for (uint32_t k = plan.lo[st]; k < plan.hi[st]; k++) if (!g_nxb.tex_ok[st][k] || g_nxb.tex[st][k] != plan.h[st][k]) { if (k < first) first = k; last = k + 1; }
        if (first >= last) continue;
        uint32_t* w = nxs_op(NxOp_BindTextures, 3 + (last - first)); w[0] = st; w[1] = first; w[2] = last - first;
        for (uint32_t k = first; k < last; k++) { w[3 + k - first] = plan.h[st][k]; g_nxb.tex[st][k] = plan.h[st][k]; g_nxb.tex_ok[st][k] = 1; }
    }
}
