/* fnv-nx L6: backend ABI v4.1 addition (lead's design): D3D9 shader creation by bytecode. The backend translates (shader cache keyed by shcache.h, else the translator)
 * synchronously and returns a shader id usable by the v3 NxOp_DefineState (vs / ps words) and released by NXGPU_FN_SHADER_DESTROY.
 *
 *   function NXGPU_FN_SHADER_CREATE_D3D = NXGPU_FN_COUNT4, argument = address of one NxShaderD3D -> shader id (>= 1), 0 = the backend could not make it (a backend defect: the D3D9
 *   device validated the bytecode, so the device never asks for a shader the D3D9 validator rejects; it answers a 0 with D3DERR_DRIVERINTERNALERROR and records nothing).
 *
 *   bytecode / size  the D3D9 token stream (version token .. END), `link_pairs` n_link_pairs bytes of FNVNX_DK_PAIR codes (usage * 16 + index) ascending, unique = the link set L of
 *                    render/shaders/include/fnvnx_dk_layout.h "Location rule" (fnvnx_shader_opts.link_pairs); n_link_pairs 0 = the stage alone (every pair of the draw is canonical).
 *   stage            0 vertex, 1 pixel (must match the bytecode's version token)
 *   variant          bit 0: vertex shader complete (FNVNX_SHADER_VS_COMPLETE; fnvnx_dk_vs_needs_complete); bit 1 (either stage): D3DRS_SHADEMODE FLAT
 *                    (FNVNX_SHADER_FLAT_COLOR: the COLOR0/COLOR1 interpolants are `flat`, first-vertex value; both stages of a draw carry the same bit); all other bits 0
 *   hash             fnvnx_shader_hash(bytecode) (FNV-1a 64), the cache key's code hash
 *   Every address is an address of memory from nxd3d_gpu_alloc (as every address of every call).
 *   Implemented by gl4 (gl4_d3dshader.c): the same key (bytecode, stage, variant, L) returns the same id, once per create (each NXGPU_FN_SHADER_DESTROY takes one back; the last releases it).
 *   Reserved fields (pad, pad2) and the variant bits above bit 1 must be 0, a pixel stage has bit 0 clear, size is a multiple of 4: else 0.
 * NXGPU_FN_COUNT4 is unchanged (the v4 table), NXGPU_VERSION4 stays 4. */
#ifndef FNVNX_NXGPU4_SHADER_H
#define FNVNX_NXGPU4_SHADER_H
#include "nxgpu4.h"

#ifdef __cplusplus
extern "C" {
#endif

enum { NXGPU_FN_SHADER_CREATE_D3D = NXGPU_FN_COUNT4 };

typedef struct NxShaderD3D {
    uint64_t bytecode;
    uint32_t size;
    uint8_t stage, variant, pad[2];
    uint64_t hash;
    uint64_t link_pairs;
    uint32_t n_link_pairs, pad2;
} NxShaderD3D;
NX_ASSERT_(sizeof(NxShaderD3D) == 40, "NxShaderD3D");

#ifdef __cplusplus
}
#endif
#endif
