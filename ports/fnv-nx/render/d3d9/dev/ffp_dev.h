// ffp_dev.h: the fixed-function module of the device (D5b): device state -> the inputs of the two fixed-function state modules (render/d3d9/ffp), the program inputs / varyings / texture stages, the backend
// programs. Contract with the draw path: draw_ffp.h. Files: ffp_map.cpp (state -> module inputs), ffp_sets.cpp (input / stage / varying sets), ffp_dev.cpp (per-device cache), ffp_prog.cpp (programs).
//  F1 MAPPING: every field of NxFfpVsD3d9 / of the pixel module's inputs comes from ONE named device state (ffp_map.cpp lists them); the module functions are pure and total, so the blocks are a pure function of the
//     device state. No D3D9 enumeration is interpreted here except through a `switch` without default, or inside the modules' own total domain functions.
//  F2 CHANGE TRACKING: the module never uses a dirty group as a change log (draw_ffp.h): the vertex block is memoised by the VALUES of its inputs (NxFfpVsKey, compared bytewise), the pixel block is rebuilt per call (800
//     bytes, no derivation). The FFP-only groups the module consumes are cleared after the consumer ran (VS: LIGHTS XFORM MATERIAL TESS; PS: TEXFACTOR); no result depends on them.
#pragma once
#include "draw_core.h"
#include "../gen/d9impl.gen.h"
#include "state_rt.h"
#define NX_RS_COUNT NX_FFP_PS_RS_COUNT_   // ffp_state.h declares its own NX_RS_COUNT (= 210); the device's generated one (state.gen.h) is the same number: keep both, rename the module's
extern "C" {
#include "../ffp/ffp_state.h"
#include "../ffp/ffp_vs_state.h"
}
#undef NX_RS_COUNT

enum { NX_FFP_VS_KEY_RS = NX_VS_RS_COUNT };
// every input of nx_ffp_vs_state_from_d3d9 by value; zeroed before it is filled, all members are 4 bytes wide (no padding), so two keys are equal iff their bytes are
struct NxFfpVsKey {
    NxFfpVsDecl decl;                  // the bound declaration (nx_ffp_decl_from_elements)
    uint32_t rs[NX_VS_RS_COUNT];       // only the render states the module reads (ffp_map.cpp kVsRs); the others are 0
    uint32_t tci[8], ttff[8];          // D3DTSS_TEXCOORDINDEX / TEXTURETRANSFORMFLAGS of the 8 stages
    float view[16], proj[16], world0[16], tex[8][16], clip[6][4];
    float mDiffuse[4], mAmbient[4], mSpecular[4], mEmissive[4], mPower;
    uint32_t viewport[4];
    NxFfpD3dLight lights[NX_FFP_MAX_LIGHTS];   // the first NX_FFP_MAX_LIGHTS enabled lights (ascending light index)
    uint32_t nLights;                          // enabled lights, capped at NX_FFP_MAX_LIGHTS (the module caps the same way)
};

// ---- ffp_map.cpp
bool nx_ffp_vs_key(NxDeviceCore* d, NxFfpVsKey* k);                                   // false: no vertex declaration bound (the draw is invalid before it gets here)
void nx_ffp_vs_block(const NxFfpVsKey& k, NxFfpVsState* out);                         // nx_ffp_vs_state_from_d3d9 over the key
void nx_ffp_tex_types(NxDeviceCore* d, uint8_t out[8]);                               // NX_FFP_TEX_* of the texture bound at stage n (NONE: none, no backend image)
void nx_ffp_ps_block(NxDeviceCore* d, const uint8_t tex_types[8], NxFfpPsState* out); // nx_ffp_ps_state_from_d3d9 over the device's stage states
// ---- ffp_sets.cpp (pure)
// program inputs of the vertex stage: attribute set of the program (fixed locations of ffp.vert.glsl) intersected with what the declaration provides and the program reads for these states
uint32_t nx_ffp_vs_inputs(const NxFfpVsKey& k, NxFfpVsBind* out);                    // n_in, in_pair, in_loc; returns n_in
uint32_t nx_ffp_stage_mask(const uint32_t tss[8][33], const uint8_t tex_types[8]);    // PS tex_mask: stages the cascade reaches (before the first COLOROP DISABLE) whose texture type is not NONE
uint32_t nx_ffp_ps_pairs(uint32_t tex_mask, uint8_t out[31]);                         // varyings the pixel program reads: COLOR0, COLOR1, TEXCOORDn (n in the mask), FOG0; returns the count
// ---- ffp_dev.cpp: the per-device record (an IUnknown private-data entry of the device: it dies with the device)
struct NxFfpDev : IUnknown {
    long refs = 1;
    uint32_t vs[2] = {0, 0}, ps[2] = {0, 0};             // backend shader ids by flat shading (0 = not made)
    bool vs_ok = false; NxFfpVsKey key; NxFfpVsState vsb; NxFfpPsState psb;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void** pp) override { *pp = nullptr; return E_NOINTERFACE; }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() override { long r = --refs; if (!r) delete this; return r; }
    void* operator new(size_t n) noexcept NX_MUST_USE NX_USE_MAKE { return nxd3d_alloc(n); } void operator delete(void* p) { nxd3d_free(p); }   // made by nx_make_plain<NxFfpDev>()
    virtual ~NxFfpDev();                                          // destroys the backend programs (the pending stream is submitted first) and forgets them in the state table
};
NxFfpDev* nx_ffp_dev(NxDeviceCore* d);                    // the device's record, made at the first call; null = no memory. The pointer is valid as long as the device is.
// ---- ffp_prog.cpp
enum NxBackendKind : uint32_t { NX_BACKEND_DEKO3D = 1, NX_BACKEND_GL = 2 };   // NxGpuInfo.backend (gpu_abi.h)
bool nx_ffp_flat(NxDeviceCore* d, uint32_t* flat);        // the program variant: D3DRS_SHADEMODE FLAT (the front's flat_shade rule over the draw path's latch); false = no memory for the latch
uint32_t nx_ffp_make_program(NxFfpDev* f, uint32_t stage /* NxStage */, uint32_t flat);   // a backend shader id (>= 1) of the program, 0 = not made
