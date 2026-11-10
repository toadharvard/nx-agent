// Shader bytecode: token walk (spec rule sm_bytes), table-driven validator (tables generated from render/shaders/total/sm_table.json by res_smgen.py)
// and the parsed interface the draw path needs. Pure C++ (no libc beyond string.h), -m32 freestanding-safe.
#pragma once
#include <stdint.h>
enum NxSmErr {
    NX_SM_OK = 0, NX_SM_E_UNTERMINATED, NX_SM_E_VERSION, NX_SM_E_STAGE, NX_SM_E_OPCODE, NX_SM_E_OP_MODEL, NX_SM_E_LENGTH, NX_SM_E_DST_REG, NX_SM_E_SRC_REG,
    NX_SM_E_REG_INDEX, NX_SM_E_SRCMOD, NX_SM_E_DSTMOD, NX_SM_E_RELADDR, NX_SM_E_PRED, NX_SM_E_DCL, NX_SM_E_TEXLD_OPERAND
};
struct NxSmIface {
    uint32_t stage;                 // 0 vertex, 1 pixel
    uint32_t major, minor;
    uint32_t n_instr;               // instruction tokens (declarations and comments excluded)
    uint32_t in_mask;               // vertex: bit n = input register v<n> declared (dcl) or read
    uint8_t in_usage[16], in_index[16];   // dcl_usage of v<n> (D3DDECLUSAGE, index); 0xFF = no dcl (shader model < 2: the register-to-usage mapping is not in the bytecode)
    uint32_t sampler_mask;          // bit n = sampler s<n> declared (sm2+) / texture stage n sampled (ps_1_x: tex*, texld)
    uint8_t sampler_type[16];       // D3DSAMPLER_TEXTURE_TYPE of dcl (2 = 2D, 3 = cube, 4 = volume); 0 = the bound texture's (ps_1_x)
    uint32_t cf_lo, cf_hi;          // float constants c<lo>..c<hi-1> possibly READ (a relatively addressed read covers [base, register limit)); lo >= hi = none
    uint32_t ci_mask, cb_mask;      // integer / boolean constant registers read (i<n>, b<n>) or defined
    uint32_t def_f, def_i, def_b;   // number of def / defi / defb in the shader
};
uint32_t nx_sm_bytes(const uint32_t* tokens);                                   // bytes version..END inclusive; 0 = no walkable END (unknown sm1 opcode, > 1M tokens)
int nx_sm_check(const uint32_t* tokens, uint32_t bytes, int stage_expect, NxSmIface* out);   // stage_expect: 0 vertex, 1 pixel; returns NxSmErr
uint32_t nx_sm_rules_covered(const char* what);                                 // introspection for the tests: number of table rows of a rule family
