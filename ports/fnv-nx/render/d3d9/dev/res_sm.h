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
    uint32_t pair_set[7];           // bit (usage * 16 + index) = the (usage, index) pair is a varying of the stage: vertex = a written output (declared with dcl in sm3, written in sm < 3: oPos POSITION0, oFog FOG0, oPts PSIZE0, oD# COLOR#, oT# TEXCOORD#),
                                    // pixel = a read input (sm3 dcl'd v#; sm < 3 the t# / v# registers used or dcl'd: TEXCOORD# / COLOR#). vPos / vFace and oC / oDepth are not pairs. The mapping of mojoshader's attribute / output lists (render/shaders/src/frontend.c)
};
// The resumable scanner behind nx_sm_check (res_smcheck.cpp): the same rules, fed in pieces. A scan is exactly one call of nx_sm_check on the concatenation of its feeds:
// nx_sm_scan_feed returns the first violation (sticky) as soon as the instruction holding it is complete, 0 while the stream is legal so far; nx_sm_scan_end additionally requires the closing END (UNTERMINATED otherwise).
// Words after END = NX_SM_E_LENGTH; the stream may not be longer than NX_SM_MAX_TOKENS before its END (the limit of nx_sm_bytes). stage_expect: 0 / 1 as nx_sm_check, -1 = the stage the version token names.
enum { NX_SM_MAX_TOKENS = 1 << 20 };
struct NxSmScan {
    NxSmIface ifc;                  // what the legal prefix declared (valid after nx_sm_scan_end returned 0)
    const void* M; uint32_t pos, skip, optok, len, npend, pend[16]; int stage_expect, err; uint8_t state;
};
void nx_sm_scan_begin(NxSmScan* s, int stage_expect);
int nx_sm_scan_feed(NxSmScan* s, const uint32_t* tokens, uint32_t n);
int nx_sm_scan_end(NxSmScan* s);
uint32_t nx_sm_bytes(const uint32_t* tokens);                                   // bytes version..END inclusive; 0 = no walkable END (unknown sm1 opcode, > 1M tokens)
int nx_sm_check(const uint32_t* tokens, uint32_t bytes, int stage_expect, NxSmIface* out);   // stage_expect: 0 vertex, 1 pixel; returns NxSmErr
uint32_t nx_sm_pairs(const NxSmIface* i, uint8_t* out);                       // the pair_set as FNVNX_DK_PAIR codes (usage * 16 + index), ascending; returns the count (<= 224)
uint32_t nx_sm_rules_covered(const char* what);                                 // introspection for the tests: number of table rows of a rule family
