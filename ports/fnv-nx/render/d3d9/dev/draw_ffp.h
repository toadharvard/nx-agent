// draw_ffp.h: the contract between the draw path (draw_*.cpp) and the fixed-function module (D5b, ffp programs of render/d3d9/ffp). The two functions are DECLARED here and defined by D5b only: they stay
// undefined symbols until then (no skip path, no body in the draw path). The draw path calls nx_ffp_vs_program when no vertex shader is bound and nx_ffp_ps_program when no pixel shader is bound, at every draw.
//  - The module reads the device state it needs (d->cur, d->lights, d->pals, ...) and returns what the backend needs. `out` is overwritten completely. The returned memory (ubo[i].data) is owned by the
//    module and valid until its next call; the draw path copies it into the stream before returning.
//  - Return value: the backend shader id of the stage's program (>= 1), made through NXGPU_FN_SHADER_CREATE_D3D or the module's own cache; 0 = the backend refused (the draw returns D3DERR_DRIVERINTERNALERROR).
//  - The draw path binds the two driver blocks (UBO 2 of fnvnx_dk_layout.h: VS 64 bytes, PS 352 bytes, same contents as for a shader pair) for the fixed-function stages too; the module's own blocks (ffp_vs_state.h:
//    UBO 4 + palette UBO 5, ffp_state.h: UBO 3) travel in `ubo` and are bound to the stage's slot.
//  - Change tracking: the draw path CLEARS the dirty groups it consumed (NxDeviceCore::dirty) except the groups only fixed function reads (NX_G_LIGHT, LIGHTS, XFORM, MATERIAL, TEXFACTOR, WRAP, TESS, PALETTE, MISC):
//    those stay set until the module clears them. The module must NOT use any other group as a change log (compare its inputs instead).
//  - Varyings: the fixed-function vertex program writes EVERY canonical varying slot (it is a "complete" vertex stage by construction) and has no non-canonical varying, so it pairs with any pixel stage; the
//    pixel program reads the pairs listed in `in_pair`. A user vertex shader paired with the fixed-function pixel program is completed by the draw path from that list.
//  - Samplers of the fixed-function pixel program: stage n's 2D texture at slot n, CUBE at 8 + n, VOLUME at 16 + n (fnvnx_dk_layout.h ps_1_x rule); the draw path binds the stage's texture at the slot of its
//    dimension for the stages in `tex_mask`.
#pragma once
#include "state_rt.h"

struct NxFfpUbo { uint32_t slot, bytes; const void* data; };   // uniform block `slot` of the stage = `bytes` bytes of `data`
enum { NX_FFP_MAX_UBO = 3 };
struct NxFfpVsBind {
    uint32_t n_in;                    // vertex inputs the program reads (<= 16)
    uint8_t in_pair[16], in_loc[16];  // FNVNX_DK_PAIR (usage * 16 + index) of input k and its attribute location (the program's reflection; fnvnx_dk_assign over the set)
    uint32_t n_ubo; NxFfpUbo ubo[NX_FFP_MAX_UBO];
};
struct NxFfpPsBind {
    uint32_t n_in; uint8_t in_pair[31];   // varyings the program reads, FNVNX_DK_PAIR (COLOR0, COLOR1, TEXCOORD0-7, FOG0, ...), unique
    uint32_t tex_mask;                    // bit n: texture stage n may be sampled
    uint32_t n_ubo; NxFfpUbo ubo[NX_FFP_MAX_UBO];
};
uint32_t nx_ffp_vs_program(NxDeviceCore* d, NxFfpVsBind* out);
uint32_t nx_ffp_ps_program(NxDeviceCore* d, NxFfpPsBind* out);
