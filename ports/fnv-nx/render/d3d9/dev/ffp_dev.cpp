// ffp_dev.cpp: the per-device record of the fixed-function module (ffp_dev.h): the backend programs made so far, the memo of the vertex block, the blocks themselves (the memory draw_ffp.h hands out).
//  R1 The record is an IUnknown private-data entry of the device under a private GUID (the pattern of the FVF declaration cache, res_fvf.cpp): it is made at the first fixed-function draw, found by the next ones, and
//     released with the device's private data, which runs ~NxFfpDev.
//  R2 ~NxFfpDev submits the pending stream (it may name the ids), destroys every program id the record made (NXGPU_FN_SHADER_DESTROY) and removes the states that name them from the draw path's state table
//     (nx_state_forget_shader: ids are reused by the backend), exactly as nxd3d_shader_ids_destroy does for the shaders of the application.
#include "ffp_dev.h"

namespace { const GUID kRec = {0x7d1f9a52, 0x3b86, 0x4e0c, {0x91, 0x4a, 0x0c, 0x5e, 0x6b, 0xd2, 0x38, 0xa1}}; }

NxFfpDev* nx_ffp_dev(NxDeviceCore* d) {
    NxFfpDev* c = nullptr; DWORD sz = sizeof c;
    if (d->getPrivateData(kRec, &c, &sz) == D3D_OK) { c->Release(); return c; }   // the device's entry keeps it alive
    c = nx_make_plain<NxFfpDev>(); if (!c) return nullptr;
    HRESULT hr = d->setPrivateData(kRec, &c, sizeof c, D3DSPD_IUNKNOWN | NX_SPD_NATIVE);   // AddRefs it (2 on success)
    c->Release();                                                                          // our creation reference: on success the entry holds the record, on failure this deletes it
    return FAILED(hr) ? nullptr : c;
}

NxFfpDev::~NxFfpDev() {
    bool any = false; for (uint32_t f = 0; f < 2; f++) any |= vs[f] != 0 || ps[f] != 0;
    if (!any) return;
    nxs_flush();                                                       // R2
    for (uint32_t f = 0; f < 2; f++)
        for (uint32_t i = 0; i < 2; i++) { uint32_t id = i ? ps[f] : vs[f]; if (id) { uint64_t a[1] = {id}; nxd3d_gpu_call(NXGPU_FN_SHADER_DESTROY, a, 1); nx_state_forget_shader(id); } }
}
