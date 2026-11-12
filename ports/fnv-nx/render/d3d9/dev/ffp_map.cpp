// ffp_map.cpp: device state -> the inputs of the fixed-function state modules (ffp_dev.h F1). The table below is the COMPLETE list of what each module reads; nothing else of the device state enters a block.
//  vertex module (NxFfpVsD3d9)  decl = the bound declaration (nx_ffp_decl_from_elements: presence / component counts), rs = the 29 render states of kVsRs, texCoordIndex / texTransformFlags = D3DTSS_TEXCOORDINDEX /
//     TEXTURETRANSFORMFLAGS of stages 0-7, view / proj / world0 / texMatrix = D3DTS_VIEW, PROJECTION, WORLD(0), TEXTURE0-7, clipPlane = SetClipPlane 0-5, material = SetMaterial, viewport = SetViewport (X Y Width
//     Height), lights = the enabled lights in ascending light index (the device keeps no enable order: with at most NX_FFP_MAX_LIGHTS enabled lights the set is the same in every order).
//  pixel module  tss = the device's 8 x 33 stage states, rs = D3DRS_TEXTUREFACTOR and D3DRS_SPECULARENABLE, texType[n] = the dimension of the texture bound at stage n (nx_ffp_tex_types).
#include "ffp_dev.h"

#define NX_FFP_VS_RS(X) X(FOGENABLE) X(SPECULARENABLE) X(FOGTABLEMODE) X(FOGSTART) X(FOGEND) X(FOGDENSITY) X(RANGEFOGENABLE) X(CLIPPING) X(LIGHTING) X(AMBIENT) X(FOGVERTEXMODE) X(COLORVERTEX) \
    X(LOCALVIEWER) X(NORMALIZENORMALS) X(DIFFUSEMATERIALSOURCE) X(SPECULARMATERIALSOURCE) X(AMBIENTMATERIALSOURCE) X(EMISSIVEMATERIALSOURCE) X(VERTEXBLEND) X(CLIPPLANEENABLE) X(POINTSIZE) \
    X(POINTSIZE_MIN) X(POINTSCALEENABLE) X(POINTSCALE_A) X(POINTSCALE_B) X(POINTSCALE_C) X(POINTSIZE_MAX) X(INDEXEDVERTEXBLENDENABLE) X(TWEENFACTOR)
#define X(n) static_assert((uint32_t)NX_VS_RS_##n == (uint32_t)D3DRS_##n, "ffp_vs_state.h NX_VS_RS_" #n " is D3DRS_" #n);
NX_FFP_VS_RS(X)
#undef X
static const uint16_t kVsRs[] = {
#define X(n) NX_VS_RS_##n,
NX_FFP_VS_RS(X)
#undef X
};
#define NX_FFP_TSS(X) X(COLOROP) X(COLORARG1) X(COLORARG2) X(ALPHAOP) X(ALPHAARG1) X(ALPHAARG2) X(BUMPENVMAT00) X(BUMPENVMAT01) X(BUMPENVMAT10) X(BUMPENVMAT11) X(BUMPENVLSCALE) X(BUMPENVLOFFSET) \
    X(TEXTURETRANSFORMFLAGS) X(COLORARG0) X(ALPHAARG0) X(RESULTARG) X(CONSTANT)
#define X(n) static_assert((uint32_t)NX_TSS_##n == (uint32_t)D3DTSS_##n, "ffp_state.h NX_TSS_" #n " is D3DTSS_" #n);
NX_FFP_TSS(X)
#undef X
static_assert((uint32_t)NX_RS_TEXTUREFACTOR == (uint32_t)D3DRS_TEXTUREFACTOR && (uint32_t)NX_RS_SPECULARENABLE == (uint32_t)D3DRS_SPECULARENABLE, "ffp_state.h render state numbers");
static_assert((uint32_t)NX_TSS_COUNT == (uint32_t)NX_TSS_TYPES && (uint32_t)NX_FFP_STAGES == (uint32_t)NX_TSS_STAGES && (uint32_t)NX_FFP_PS_RS_COUNT_ == (uint32_t)NX_RS_COUNT && (uint32_t)NX_VS_RS_COUNT == (uint32_t)NX_RS_COUNT, "the modules' tables are the device's");
static_assert(sizeof(NxPlane) == 16 && sizeof(D3DCOLORVALUE) == 16 && sizeof(D3DMATRIX) == 64, "matrix / plane / colour layouts");

static void colour(float* o, const D3DCOLORVALUE& c) { o[0] = c.r; o[1] = c.g; o[2] = c.b; o[3] = c.a; }

bool nx_ffp_vs_key(NxDeviceCore* d, NxFfpVsKey* k) {
    const NxDeviceState& s = d->cur; memset((void*)k, 0, sizeof *k);
    NxObj* o = s.vdecl[0] ? nx_obj_of((IUnknown*)s.vdecl[0]) : nullptr; const NxResDecl* dc = o ? static_cast<NxResDecl*>(o) : nullptr;
    if (!dc || !dc->el) return false;
    nx_ffp_decl_from_elements(dc->el, &k->decl);
    for (uint16_t i : kVsRs) k->rs[i] = s.rs[i];
    for (uint32_t st = 0; st < 8; st++) { k->tci[st] = s.tss[st][D3DTSS_TEXCOORDINDEX]; k->ttff[st] = s.tss[st][D3DTSS_TEXTURETRANSFORMFLAGS]; }
    memcpy(k->view, &s.xf[nx_dxf(D3DTS_VIEW)], 64); memcpy(k->proj, &s.xf[nx_dxf(D3DTS_PROJECTION)], 64); memcpy(k->world0, &s.xf[nx_dxf(D3DTS_WORLDMATRIX(0))], 64);
    for (uint32_t i = 0; i < 8; i++) memcpy(k->tex[i], &s.xf[nx_dxf(D3DTS_TEXTURE0 + i)], 64);
    memcpy(k->clip, s.clip, sizeof k->clip);
    const D3DMATERIAL9& m = s.material[0];
    colour(k->mDiffuse, m.Diffuse); colour(k->mAmbient, m.Ambient); colour(k->mSpecular, m.Specular); colour(k->mEmissive, m.Emissive); k->mPower = m.Power;
    k->viewport[0] = s.viewport[0].X; k->viewport[1] = s.viewport[0].Y; k->viewport[2] = s.viewport[0].Width; k->viewport[3] = s.viewport[0].Height;
    uint32_t n = 0;
    for (uint32_t i = 0; i < d->lights.n && n < NX_FFP_MAX_LIGHTS; i++) {          // ascending light index (NxSparse is sorted)
        const NxLightEnt& e = d->lights.a[i]; if (!e.enabled) continue;
        const D3DLIGHT9& l = e.light; NxFfpD3dLight& o2 = k->lights[n++];
        o2.type = (uint32_t)l.Type; colour(o2.diffuse, l.Diffuse); colour(o2.specular, l.Specular); colour(o2.ambient, l.Ambient);
        o2.position[0] = l.Position.x; o2.position[1] = l.Position.y; o2.position[2] = l.Position.z;
        o2.direction[0] = l.Direction.x; o2.direction[1] = l.Direction.y; o2.direction[2] = l.Direction.z;
        o2.range = l.Range; o2.falloff = l.Falloff; o2.attenuation0 = l.Attenuation0; o2.attenuation1 = l.Attenuation1; o2.attenuation2 = l.Attenuation2; o2.theta = l.Theta; o2.phi = l.Phi;
    }
    k->nLights = n;
    return true;
}

void nx_ffp_vs_block(const NxFfpVsKey& k, NxFfpVsState* out) {
    NxFfpVsD3d9 in; memset((void*)&in, 0, sizeof in);
    in.decl = &k.decl; in.rs = k.rs; memcpy(in.texCoordIndex, k.tci, sizeof k.tci); memcpy(in.texTransformFlags, k.ttff, sizeof k.ttff);
    in.view = k.view; in.proj = k.proj; in.world0 = k.world0; in.texMatrix = k.tex; in.clipPlane = k.clip;
    memcpy(in.mDiffuse, k.mDiffuse, 16); memcpy(in.mAmbient, k.mAmbient, 16); memcpy(in.mSpecular, k.mSpecular, 16); memcpy(in.mEmissive, k.mEmissive, 16); in.mPower = k.mPower;
    memcpy(in.viewport, k.viewport, 16); in.lights = k.lights; in.nLights = k.nLights;
    nx_ffp_vs_state_from_d3d9(&in, out);
}

// the dimension of the texture a stage samples: the sampler slot n (stage n = sampler n, MS SetTextureStageState / SetTexture); a texture without a backend image (NxFmt_None) is not sampled (draw_tex.cpp T2: handle 0)
void nx_ffp_tex_types(NxDeviceCore* d, uint8_t out[8]) {
    for (uint32_t n = 0; n < 8; n++) {
        out[n] = NX_FFP_TEX_NONE;
        NxObj* o = d->cur.tex[n] ? nx_obj_of((IUnknown*)d->cur.tex[n]) : nullptr; NxImg* im = o ? nx_img_of(o) : nullptr;
        if (!im || im->ni.format == NxFmt_None) continue;
        switch ((decltype(NXK_TEXTURE))im->kind) {
        case NXK_TEXTURE: out[n] = NX_FFP_TEX_2D; break;
        case NXK_CUBE: out[n] = NX_FFP_TEX_CUBE; break;
        case NXK_VOLUME: out[n] = NX_FFP_TEX_VOLUME; break;
        case NXK_SURFACE: break;                                   // a surface is not a texture: no stage can hold one
        }
    }
}

void nx_ffp_ps_block(NxDeviceCore* d, const uint8_t tex_types[8], NxFfpPsState* out) { nx_ffp_ps_state_from_d3d9(d->cur.tss, d->cur.rs, tex_types, out); }
