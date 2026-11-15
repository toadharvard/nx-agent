/* fnv-nx L6: backend ABI v4.1 addition (lead's design): D3D9 shader creation by bytecode. The backend translates (shader cache keyed by shcache.h, else the translator)
 * synchronously and returns a shader id usable by the v3 NxOp_DefineState (vs / ps words) and released by NXGPU_FN_SHADER_DESTROY.
 *
 *   function NXGPU_FN_SHADER_CREATE_D3D = NXGPU_FN_COUNT4, argument = address of one NxShaderD3D -> shader id (>= 1), 0 = the backend could not make it (a backend defect: the D3D9
 *   device validated the bytecode, so the device never asks for a shader the D3D9 validator rejects; it answers a 0 with D3DERR_DRIVERINTERNALERROR and records nothing).
 *
 *   bytecode / size  the D3D9 token stream (version token .. END), `link_pairs` n_link_pairs bytes of FNVNX_DK_PAIR codes (usage * 16 + index) ascending, unique = the link set L of
 *                    render/shaders/include/fnvnx_dk_layout.h "Location rule" (fnvnx_shader_opts.link_pairs); n_link_pairs 0 = the stage alone (every pair of the draw is canonical).
 *   stage            0 vertex, 1 pixel (must match the bytecode's version token)
 *   variant          translator key flags (fnvnx_shaders.h): a subset of FNVNX_SHADER_KEY_FLAGS, passed to the translator as they are. FNVNX_SHADER_VS_COMPLETE
 *                    (fnvnx_dk_vs_needs_complete) is valid on stage 0 only; FNVNX_SHADER_FLAT_COLOR (D3DRS_SHADEMODE FLAT: the COLOR0/COLOR1 interpolants are `flat`,
 *                    first-vertex value; both stages of a draw carry the same bit). The header defines no bit of its own.
 *   key              SHA-256 of the bytecode (fnvnx_sha256, lib/sha256; 32 bytes), the cache key's code key
 *   Every address is an address of memory from nxd3d_gpu_alloc (as every address of every call).
 // ASM-629 (verify/ASSUMPTIONS.json)
 *   Implemented by gl4 (gl4_d3dshader.c): the same key (bytecode, stage, variant, L) returns the same id, once per create (each NXGPU_FN_SHADER_DESTROY takes one back; the last releases it).
 *   Reserved fields (pad, pad2) and a variant bit outside FNVNX_SHADER_KEY_FLAGS must be 0, a pixel stage has FNVNX_SHADER_VS_COMPLETE clear, size is a multiple of 4: else 0.
 * NXGPU_FN_COUNT4 is unchanged (the v4 table), NXGPU_VERSION4 stays 4. */
#ifndef FNVNX_NXGPU4_SHADER_H
#define FNVNX_NXGPU4_SHADER_H
#include <stddef.h>
#include "nxgpu4.h"

#ifdef __cplusplus
extern "C" {
#endif

/* The ABI 4.1 function (number NXGPU_FN_COUNT4, no NxGpuApi4 member): X(id suffix, name, argument count). */
#define NXGPU_FN_LIST_EXT(X) X(SHADER_CREATE_D3D, shader_create_d3d, 1)
enum { NXGPU_FN_SHADER_CREATE_D3D = NXGPU_FN_COUNT4 };
NX_ASSERT_((int)NXGPU_FN_SHADER_CREATE_D3D == (int)NXGPU_FN_COUNT4, "ABI 4.1 continues ABI 4");

/* The limit of one create call: bytes of bytecode. (The link set is bounded by FNVNX_DK_VARYING_SLOTS of fnvnx_dk_layout.h.) */
enum { NXGPU_SHADER_MAX_CODE = 1u << 20 };

typedef struct NxShaderD3D {
    uint64_t bytecode;
    uint32_t size;
    uint8_t stage, variant, pad[2];
    uint8_t key[32];
    uint64_t link_pairs;
    uint32_t n_link_pairs, pad2;
} NxShaderD3D;
NX_ASSERT_(sizeof(NxShaderD3D) == 64, "NxShaderD3D");
NX_ASSERT_(offsetof(NxShaderD3D, key) == 16 && offsetof(NxShaderD3D, link_pairs) == 48 && offsetof(NxShaderD3D, n_link_pairs) == 56, "NxShaderD3D layout");

#ifdef __cplusplus
}
#endif
#endif
