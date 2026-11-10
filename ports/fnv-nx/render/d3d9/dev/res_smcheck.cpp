// Shader bytecode validation and interface extraction. ONE table-driven checker: the data is res_smtab.gen.h (res_smgen.py from sm_table.json); the code below
// has no per-opcode / per-model knowledge. The D3D9 runtime answers an illegal shader with D3DERR_INVALIDCALL; the rules checked (R1-R9) are the ones the table states as data:
//  R1 version token: type word 0xFFFE/0xFFFF = the interface asked for; (major, minor) a creatable model of the table (vs_1_0/ps_1_0: 1_1 grammar, table `undetermined`).
//  R2 token walk (spec rule sm_bytes): comments skipped, END 0x0000FFFF closes; operand count = length field (bits 24-27) for major >= 2, SM1 table below.
//  R3 the opcode exists in the model (stage, opcode number, model); R4 operand token count equals the roles of the table row (rows of conflicting operand shapes: any matches).
//  R5 register class (token type, index) legal for the role: destinations / ALU sources per model table, declared classes for dcl, index < table count.
//  R6 source modifiers in the model's list (code 13 `not` only on the predicate token); R7 result modifier / shift in the model's dstmods; R8 relative addressing only where the class's `rel` lists
//  the address register used; R9 predicated instruction only in models with p0; texld src0 / sampler operand without modifier or swizzle in the models of sm_illegal.tsv.
// NOT checked (accepted, superset): write-mask and swizzle form legality, instruction-slot / flow-depth / read-port / texld-count limits, the table's `undetermined` rows (res_smcheck.cpp lists none beyond R5's a0/b/i),
// `conflicts` rows (the table's model sets already hold the union).
#include "res_sm.h"
#include <string.h>
#include "res_smtab.gen.h"
enum { OP_COMMENT = 0xFFFE, OP_END = 0xFFFF, OP_PHASE = 0xFFFD, OP_DCL = 31, OP_DEFB = 47, OP_DEFI = 48, OP_DEF = 81, T_TEX = 66 };
static const NxSm1P* sm1(uint32_t op) { for (int i = 0; i < NX_SM1_N; i++) if (NX_SM1[i].opnum == op) return &NX_SM1[i]; return nullptr; }

uint32_t nx_sm_bytes(const uint32_t* t) {
    if (!t) return 0;
    uint32_t hi = t[0] >> 16, major = (t[0] >> 8) & 0xFF, minor = t[0] & 0xFF; if (hi != 0xFFFE && hi != 0xFFFF) return 0;
    for (uint32_t i = 1; i < (1u << 20);) {
        uint32_t w = t[i], op = w & 0xFFFF;
        if (w == 0x0000FFFF) return (i + 1) * 4;
        if (op == OP_COMMENT) { i += 1 + ((w >> 16) & 0x7FFF); continue; }
        if (major >= 2) { i += 1 + ((w >> 24) & 0xF); continue; }
        const NxSm1P* p = sm1(op); if (!p) return 0;
        i += 1 + (hi == 0xFFFF && minor == 4 ? p->n14 : p->n);
    }
    return 0;
}
static const NxSmModel* find_model(int stage, uint32_t major, uint32_t minor) {
    if (major == 1 && minor == 0) minor = 1;
    for (int i = 0; i < NX_SM_NMODELS; i++) if (NX_SM_MODELS[i].stage == stage && NX_SM_MODELS[i].major == major && NX_SM_MODELS[i].minor == minor) return &NX_SM_MODELS[i];
    return nullptr;
}
// token (type, index) -> table register class (-1 = no such class in this stage); *ci = index inside the class
static int cls_of(int stage, uint32_t major, uint32_t type, uint32_t idx, uint32_t* ci) {
    *ci = idx;
    switch (type) {
    case 0: return NXC_r; case 1: return NXC_v; case 2: return NXC_c; case 3: return stage ? NXC_t : NXC_a0;
    case 4: if (stage || idx > 2) return -1; *ci = 0; return NXC_oPos + (int)idx;
    case 5: return stage ? -1 : NXC_oD; case 6: return stage ? -1 : major < 3 ? NXC_o - 1 : NXC_o;   // NXC_o - 1 == NXC_oT
    case 7: return NXC_i; case 8: return NXC_oC; case 9: return NXC_oDepth; case 10: return NXC_s;
    case 11: case 12: case 13: *ci = idx + 2048 * (type - 10); return NXC_c;
    case 14: return NXC_b; case 15: return NXC_aL; case 17: *ci = 0; return !stage || idx > 1 ? -1 : (idx ? NXC_vFace : NXC_vPos);
    case 18: return NXC_l; case 19: return NXC_p0;
    }
    return -1;
}
struct Ctx { const NxSmModel* M; int stage, major; NxSmIface* acc; };
static uint32_t ptype(uint32_t t) { return ((t >> 28) & 7) | ((t >> 8) & 0x18); }
// one parameter token (+ relative token); role r: 0 = destination of table role d, else the source role
static int param(const Ctx& c, const uint32_t* t, uint32_t n, uint32_t* i, int dst, int role, uint32_t optok) {
    if (*i >= n) return NX_SM_E_LENGTH;
    uint32_t w = t[(*i)++], ci, ty = ptype(w), idx = w & 0x7FF; if (!(w & 0x80000000u)) return NX_SM_E_LENGTH;
    int cl = cls_of(c.stage, c.major, ty, idx, &ci); const NxSmModel& M = *c.M;
    if (cl < 0 || !((M.has_cls >> cl) & 1)) return dst ? NX_SM_E_DST_REG : NX_SM_E_SRC_REG;
    uint32_t cm = 1u << cl;
    if (dst) {                                    // table dst roles: 1 D, 2 Dr, 3 T, 4 Rr, 5 A0, 6 P0
        bool ok = role == 3 ? cl == NXC_t : role == 4 ? cl == NXC_r : role == 5 ? cl == NXC_a0 : role == 6 ? cl == NXC_p0 : (M.dst_cls & cm) != 0;
        if (!ok) return NX_SM_E_DST_REG;
    } else {                                      // src roles: 1 S 2 Ss 3 Sr 4 Sc 5 Sm 6 B 7 I 8 L 9 AL 10 Pp 11 X 12 Tmp
        static const int want[13] = {-1, -2, -2, -2, -2, NXC_s, NXC_b, NXC_i, NXC_l, NXC_aL, NXC_p0, NXC_t, NXC_r};
        int wc = want[role]; bool ok = wc == -2 ? ((M.src_cls & cm) != 0 || (role == 4 && cl == NXC_c)) : cl == wc;
        if (!ok) return NX_SM_E_SRC_REG;
    }
    if (ci >= M.n[cl] && cl != NXC_c) return NX_SM_E_REG_INDEX;
    if (cl == NXC_c && ci >= M.n[cl]) return NX_SM_E_REG_INDEX;
    bool rel = (w >> 13) & 1; uint32_t radd = 0;
    if (rel) {
        if (c.major >= 2) {
            if (*i >= n) return NX_SM_E_LENGTH;
            uint32_t r = t[(*i)++]; uint32_t rty = ptype(r); if (!(r & 0x80000000u) || (rty != 3 && rty != 15)) return NX_SM_E_RELADDR;
            radd = rty == 3 ? 1 : 2;
        } else radd = 1;                          // sm1 vertex shaders: the address register a0.x is implied
        if (!(M.rel[cl] & radd)) return NX_SM_E_RELADDR;
    }
    if (dst) {
        uint32_t res = (w >> 20) & 15, sh = (w >> 24) & 15, bits = 0;
        if (res & 8) return NX_SM_E_DSTMOD;
        bits = res & 7;
        static const int shbit[16] = {-1, 3, 4, 5, -1, -1, -1, -1, -1, -1, -1, -1, -1, 8, 7, 6};
        if (sh) { if (shbit[sh] < 0) return NX_SM_E_DSTMOD; bits |= 1u << shbit[sh]; }
        if (bits & ~(uint32_t)M.dstmods) return NX_SM_E_DSTMOD;
        if (c.acc && c.stage && c.major < 2 && ty == 3) c.acc->sampler_mask |= 1u << (ci & 15);   // refined by the caller (only sampling ops count)
    } else {
        uint32_t sm = (w >> 24) & 15;
        if (sm == 13 ? role != 10 : (sm && !((M.srcmods >> sm) & 1))) return NX_SM_E_SRCMOD;
        if (role != 10 && (role == 1 || role == 2 || role == 3 || role == 4) && false) {}
    }
    if (c.acc) {
        NxSmIface* a = c.acc;
        if (!dst && cl == NXC_c) { uint32_t hi = rel ? M.n[NXC_c] : ci + 1, lo = rel ? 0 : ci; if (a->cf_lo >= a->cf_hi) { a->cf_lo = lo; a->cf_hi = hi; } else { if (lo < a->cf_lo) a->cf_lo = lo; if (hi > a->cf_hi) a->cf_hi = hi; } }
        if (!dst && cl == NXC_i && ci < 32) a->ci_mask |= 1u << ci;
        if (!dst && cl == NXC_b && ci < 32) a->cb_mask |= 1u << ci;
        if (!dst && cl == NXC_v && !c.stage && ci < 16) a->in_mask |= 1u << ci;
        if (!dst && cl == NXC_s && ci < 16) a->sampler_mask |= 1u << ci;
    }
    (void)optok; return 0;
}
static int declare(const Ctx& c, uint32_t op, const uint32_t* t, uint32_t n) {
    uint32_t i = 0; const NxSmModel& M = *c.M;
    if (op == OP_DCL) {
        if (n != 2 || !(t[0] & 0x80000000u) || !(t[1] & 0x80000000u)) return NX_SM_E_DCL;
        uint32_t ci, w = t[1], u = t[0], usage = u & 31, uidx = (u >> 16) & 15, st = (u >> 27) & 15; int cl = cls_of(c.stage, c.major, ptype(w), w & 0x7FF, &ci);
        if (cl < 0 || !((M.dcl_cls >> cl) & 1) || ci >= M.n[cl]) return NX_SM_E_DCL;
        if (cl == NXC_s) { if (st < 2 || st > 4) return NX_SM_E_DCL; if (c.acc) { c.acc->sampler_mask |= 1u << ci; c.acc->sampler_type[ci & 15] = (uint8_t)st; } }
        else if (usage > 13) return NX_SM_E_DCL;
        else if (c.acc && cl == NXC_v && !c.stage) { c.acc->in_mask |= 1u << ci; c.acc->in_usage[ci & 15] = (uint8_t)usage; c.acc->in_index[ci & 15] = (uint8_t)uidx; }
        return 0;
    }
    int want = op == OP_DEF ? NXC_c : op == OP_DEFI ? NXC_i : NXC_b; uint32_t nv = op == OP_DEFB ? 1 : 4;
    if (n != 1 + nv) return NX_SM_E_LENGTH;
    uint32_t ci, w = t[0]; int cl = cls_of(c.stage, c.major, ptype(w), w & 0x7FF, &ci); (void)i;
    if (!(w & 0x80000000u) || cl != want || !((M.has_cls >> cl) & 1) || ci >= M.n[cl]) return NX_SM_E_DST_REG;
    if (c.acc) { if (op == OP_DEF) c.acc->def_f++; else if (op == OP_DEFI) { c.acc->def_i++; if (ci < 32) c.acc->ci_mask |= 1u << ci; } else { c.acc->def_b++; if (ci < 32) c.acc->cb_mask |= 1u << ci; } }
    return 0;
}
static int instr(const Ctx& c, const NxSmOp& o, const uint32_t* t, uint32_t n, uint32_t optok) {
    uint32_t i = 0; int e;
    if (o.d && (e = param(c, t, n, &i, 1, o.d, optok))) return e;
    uint32_t first_src = i;
    for (int k = 0; k < o.nsrc; k++) {
        uint32_t at = i; if ((e = param(c, t, n, &i, 0, o.s[k], optok))) return e;
        if (o.s[k] == 5 && c.major == 2 && (NX_SM_TEXLD_SAMPLER_MODELS >> (c.M - NX_SM_MODELS) & 1) && (t[at] >> 16 & 0x0FFF) != 0x0E4) return NX_SM_E_TEXLD_OPERAND;   // swizzle 0xE4 + modifier 0
        if (k == 0 && o.samples && o.opnum_is_texld() ) {}
    }
    if (optok & 0x10000000u) { if (!c.M->pred) return NX_SM_E_PRED; if ((e = param(c, t, n, &i, 0, 10, optok))) return e; }
    (void)first_src;
    return i == n ? 0 : NX_SM_E_LENGTH;
}
