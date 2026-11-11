// Shader bytecode validation and interface extraction. ONE table-driven checker: the data is res_smtab.gen.h (res_smgen.py from sm_table.json); the code below
// has no per-opcode / per-model knowledge. The D3D9 runtime answers an illegal shader with D3DERR_INVALIDCALL; the rules checked (R1-R9) are the ones the table states as data:
//  R1 version token: type word 0xFFFE/0xFFFF = the interface asked for; (major, minor) a creatable model of the table (vs_1_0/ps_1_0: 1_1 grammar, table `undetermined`).
//  R2 token walk (spec rule sm_bytes): comments skipped, END 0x0000FFFF closes; operand count = length field (bits 24-27) for major >= 2, SM1 table below.
//  R3 the opcode exists in the model (stage, opcode number, model); R4 operand token count equals the roles of the table row (rows of conflicting operand shapes: any matches).
//  R5 register class (token type, index) legal for the role: destinations / ALU sources per model table, declared classes for dcl, index < table count.
//  R6 source modifiers in the model's list (code 13 `not` only on the predicate token); R7 result modifier / shift in the model's dstmods; R8 relative addressing only where the class's `rel` lists
//  the address register used; R9 predicated instruction only in models with p0; texld src0 / sampler operand without modifier or swizzle in the models of sm_illegal.tsv.
// NOT checked (accepted, superset): write-mask and swizzle form legality, instruction-slot / flow-depth / read-port / texld-count limits, the table `undetermined` rows (R5 accepts a0, b#, i# as vertex ALU sources and writable registers as destinations),
// `conflicts` rows (the table's model sets already hold the union).
#include "res_sm.h"
#include <string.h>
#include "../gen/res_smtab.gen.h"
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
static void add_pair(NxSmIface* a, uint32_t usage, uint32_t idx) { uint32_t k = usage * 16 + idx; if (usage <= 13 && idx <= 15) a->pair_set[k >> 5] |= 1u << (k & 31); }
uint32_t nx_sm_pairs(const NxSmIface* a, uint8_t* out) { uint32_t n = 0; for (uint32_t k = 0; k < 224; k++) if (a->pair_set[k >> 5] >> (k & 31) & 1) out[n++] = (uint8_t)k; return n; }
struct Ctx { const NxSmModel* M; int stage, major; NxSmIface* acc; uint32_t mbit; };
static uint32_t ptype(uint32_t t) { return ((t >> 28) & 7) | ((t >> 8) & 0x18); }
static bool plain(uint32_t w) { return ((w >> 16) & 0xFF) == 0xE4 && ((w >> 24) & 15) == 0; }   // identity swizzle, no source modifier
// one parameter token (+ relative-address token). dst: table role 1 D 2 Dr 3 T 4 Rr 5 A0 6 P0; src: 1 S 2 Ss 3 Sr 4 Sc 5 Sm 6 B 7 I 8 L 9 AL 10 Pp 11 X 12 Tmp
static int param(const Ctx& c, const uint32_t* t, uint32_t n, uint32_t* i, int dst, int role) {
    if (*i >= n) return NX_SM_E_LENGTH;
    uint32_t w = t[(*i)++], ci, ty = ptype(w); if (!(w & 0x80000000u)) return NX_SM_E_LENGTH;
    int cl = cls_of(c.stage, c.major, ty, w & 0x7FF, &ci); const NxSmModel& M = *c.M; int bad = dst ? NX_SM_E_DST_REG : NX_SM_E_SRC_REG;
    if (cl < 0 || !((M.has_cls >> cl) & 1)) return bad;
    uint32_t cm = 1u << cl;
    if (dst) { if (!(role == 3 ? cl == NXC_t : role == 4 ? cl == NXC_r : role == 5 ? cl == NXC_a0 : role == 6 ? cl == NXC_p0 : (M.dst_cls & cm) != 0)) return bad; }
    else {
        static const int want[13] = {-1, -2, -2, -2, -2, NXC_s, NXC_b, NXC_i, NXC_l, NXC_aL, NXC_p0, NXC_t, NXC_r};
        if (!(role == 11 ? cl == NXC_t || (M.x_r && cl == NXC_r) : want[role] == -2 ? (M.src_cls & cm) != 0 || (role == 4 && cl == NXC_c) : cl == want[role])) return bad;
    }
    if (ci >= M.n[cl]) return NX_SM_E_REG_INDEX;
    bool rel = (w >> 13) & 1;
    if (rel) {
        uint32_t radd = 1;                        // sm1 vertex shaders: the address register a0.x is implied
        if (c.major >= 2) {
            if (*i >= n) return NX_SM_E_LENGTH;
            uint32_t r = t[(*i)++], rty = ptype(r); if (!(r & 0x80000000u) || (rty != 3 && rty != 15)) return NX_SM_E_RELADDR;
            radd = rty == 3 ? 1 : 2;
        }
        if (!(M.rel[cl] & radd)) return NX_SM_E_RELADDR;
    }
    if (dst) {
        static const int shbit[16] = {-1, 3, 4, 5, -1, -1, -1, -1, -1, -1, -1, -1, -1, 8, 7, 6};
        uint32_t res = (w >> 20) & 15, sh = (w >> 24) & 15, bits = res & 7;
        if ((res & 8) || (sh && shbit[sh] < 0)) return NX_SM_E_DSTMOD;
        if (sh) bits |= 1u << shbit[sh];
        if (bits & ~(uint32_t)M.dstmods) return NX_SM_E_DSTMOD;
    } else {
        uint32_t sm = (w >> 24) & 15;               // code 13 = `not`: the predicate token only
        if (sm == 13 ? role != 10 : (sm && !((M.srcmods >> sm) & 1))) return NX_SM_E_SRCMOD;
    }
    if (NxSmIface* a = c.acc) {
        if (!dst && cl == NXC_c) { uint32_t lo = rel ? 0 : ci, hi = rel ? M.n[NXC_c] : ci + 1; if (a->cf_lo >= a->cf_hi) { a->cf_lo = lo; a->cf_hi = hi; } else { if (lo < a->cf_lo) a->cf_lo = lo; if (hi > a->cf_hi) a->cf_hi = hi; } }
        if (!dst && cl == NXC_i && ci < 32) a->ci_mask |= 1u << ci;
        if (!dst && cl == NXC_b && ci < 32) a->cb_mask |= 1u << ci;
        if (!dst && cl == NXC_v && !c.stage && ci < 16) a->in_mask |= 1u << ci;
        if (!dst && cl == NXC_s && ci < 16) a->sampler_mask |= 1u << ci;
        if (!c.stage && dst) {   // vertex outputs of sm < 3 are the registers written (sm3: declared, see declare())
            if (cl == NXC_oPos) add_pair(a, 0, 0); else if (cl == NXC_oFog) add_pair(a, 11, 0); else if (cl == NXC_oPts) add_pair(a, 4, 0);
            else if (cl == NXC_oD) add_pair(a, 10, ci); else if (cl == NXC_oT) add_pair(a, 5, ci);
        }
        if (c.stage && c.major < 3) { if (cl == NXC_t) add_pair(a, 5, ci); else if (cl == NXC_v) add_pair(a, 10, ci); }   // pixel inputs of sm < 3: t# TEXCOORD#, v# COLOR#
    }
    return 0;
}
static int declare(const Ctx& c, uint32_t op, const uint32_t* t, uint32_t n) {
    const NxSmModel& M = *c.M; uint32_t ci;
    if (op == OP_DCL) {
        if (n != 2 || !(t[0] & 0x80000000u) || !(t[1] & 0x80000000u)) return NX_SM_E_DCL;
        uint32_t u = t[0], usage = u & 31, uidx = (u >> 16) & 15, st = (u >> 27) & 15; int cl = cls_of(c.stage, c.major, ptype(t[1]), t[1] & 0x7FF, &ci);
        if (cl < 0 || !((M.dcl_cls >> cl) & 1) || ci >= M.n[cl]) return NX_SM_E_DCL;
        if (cl == NXC_s) { if (st < 2 || st > 4) return NX_SM_E_DCL; if (c.acc) { c.acc->sampler_mask |= 1u << ci; c.acc->sampler_type[ci & 15] = (uint8_t)st; } }
        else if (usage > 13) return NX_SM_E_DCL;
        else if (c.acc && c.stage && cl == NXC_v && c.major >= 3) add_pair(c.acc, usage, uidx);
        else if (c.acc && c.stage && c.major < 3 && (cl == NXC_t || cl == NXC_v)) add_pair(c.acc, cl == NXC_t ? 5 : 10, ci);
        else if (c.acc && !c.stage && cl == NXC_o) add_pair(c.acc, usage, uidx);
        else if (c.acc && cl == NXC_v && !c.stage) { c.acc->in_mask |= 1u << ci; c.acc->in_usage[ci & 15] = (uint8_t)usage; c.acc->in_index[ci & 15] = (uint8_t)uidx; }
        return 0;
    }
    int want = op == OP_DEF ? NXC_c : op == OP_DEFI ? NXC_i : NXC_b;
    if (n != (op == OP_DEFB ? 2u : 5u)) return NX_SM_E_LENGTH;
    int cl = cls_of(c.stage, c.major, ptype(t[0]), t[0] & 0x7FF, &ci);
    if (!(t[0] & 0x80000000u) || cl != want || !((M.has_cls >> cl) & 1) || ci >= M.n[cl]) return NX_SM_E_DST_REG;
    if (NxSmIface* a = c.acc) { if (op == OP_DEF) a->def_f++; else if (op == OP_DEFI) { a->def_i++; if (ci < 32) a->ci_mask |= 1u << ci; } else { a->def_b++; if (ci < 32) a->cb_mask |= 1u << ci; } }
    return 0;
}
static int instr(const Ctx& c, const NxSmOp& o, const uint32_t* t, uint32_t n, uint32_t optok) {
    uint32_t i = 0, first_src; int e;
    if (o.d && (e = param(c, t, n, &i, 1, o.d))) return e;
    if (c.major >= 2 && (optok & 0x10000000u)) { if (!c.M->pred) return NX_SM_E_PRED; if ((e = param(c, t, n, &i, 0, 10))) return e; }   // the predicate token follows the destination (Wine shader_sm1_read_instruction)
    first_src = i;
    for (int k = 0; k < o.nsrc; k++) {
        uint32_t at = i; if ((e = param(c, t, n, &i, 0, o.s[k]))) return e;
        bool texld2 = o.opnum == T_TEX && c.stage && c.major == 2;   // sm_illegal.tsv: ps_2_0 / ps_2_x texld src0 and the sampler operand: no modifier, no swizzle
        if (texld2 && k == 0 && ((NX_SM_TEXLD_COORD_MODELS >> c.mbit) & 1) && !plain(t[at])) return NX_SM_E_TEXLD_OPERAND;
        if (texld2 && o.s[k] == 5 && ((NX_SM_TEXLD_SAMPLER_MODELS >> c.mbit) & 1) && !plain(t[at])) return NX_SM_E_TEXLD_OPERAND;
    }
    if (i != n) return NX_SM_E_LENGTH;
    if (NxSmIface* a = c.acc) if (o.samples && c.stage && c.major < 2) a->sampler_mask |= 1u << ((o.d == 3 ? t[0] : t[first_src]) & 15);   // ps_1_x: the texture stage = the t# register
    return 0;
}
// ---- the walk (R2) and the per-instruction rules (R3-R9) as ONE resumable scanner: nx_sm_check is "feed every token, then end", the shader validator of x1_validator.cpp feeds what D3DX hands it call by call.
// The scanner holds the version token's model, the words skipped inside a comment, and the instruction in progress (opcode token + operand words, at most 15: the length field is 4 bits, the sm1 table counts are <= 5);
// tokens are never stored, so the memory of a scan is constant. The verdict is a function of the token sequence alone, never of how it is cut into feeds.
static_assert(sizeof(((NxSmScan*)0)->pend) / sizeof(uint32_t) == 16, "an instruction of the 4-bit length field has at most 15 operand words");
static constexpr uint32_t sm1_max_operands() { uint32_t m = 0; for (int i = 0; i < NX_SM1_N; i++) { if (NX_SM1[i].n > m) m = NX_SM1[i].n; if (NX_SM1[i].n14 > m) m = NX_SM1[i].n14; } return m; }
static_assert(sm1_max_operands() <= 15, "an sm1 instruction of the table has more operand words than the scanner's buffer");
enum { SC_VERSION = 0, SC_BODY = 1, SC_OPERANDS = 2, SC_ENDED = 3 };
void nx_sm_scan_begin(NxSmScan* s, int stage_expect) {
    memset((void*)s, 0, sizeof *s); memset(s->ifc.in_usage, 0xFF, sizeof s->ifc.in_usage); s->stage_expect = stage_expect;
}
static int scan_version(NxSmScan* s, uint32_t v) {
    uint32_t hi = v >> 16; int stage = hi == 0xFFFE ? 0 : hi == 0xFFFF ? 1 : -1;
    if (stage < 0) return NX_SM_E_VERSION;
    if (s->stage_expect >= 0 && stage != s->stage_expect) return NX_SM_E_STAGE;
    const NxSmModel* M = find_model(stage, (v >> 8) & 0xFF, v & 0xFF); if (!M) return NX_SM_E_VERSION;
    s->M = M; s->ifc.stage = stage; s->ifc.major = M->major; s->ifc.minor = v & 0xFF; s->state = SC_BODY; return 0;
}
static int scan_instr(NxSmScan* s) {   // the instruction token s->optok with its s->len operand words s->pend: the rows of the table, the first that accepts wins (declare / instr)
    const NxSmModel* M = (const NxSmModel*)s->M; int stage = (int)s->ifc.stage; uint32_t op = s->optok & 0xFFFF, len = s->len;
    Ctx c = {M, stage, (int)M->major, &s->ifc, (uint32_t)(M - NX_SM_MODELS)};
    bool seen = false, any = false; int err = NX_SM_E_OP_MODEL, ok = 0;
    for (int r = 0; r < NX_SM_NOPS && !ok; r++) {
        const NxSmOp& o = NX_SM_OPS[r]; if (o.stage != stage || o.opnum != op) continue;
        seen = true; if (!((o.models >> c.mbit) & 1)) continue;
        int e = o.decl ? declare(c, op, s->pend, len) : instr(Ctx{M, stage, c.major, nullptr, c.mbit}, o, s->pend, len, s->optok);
        if (!e) { ok = 1; if (o.decl) break; Ctx cc = c; instr(cc, o, s->pend, len, s->optok); s->ifc.n_instr++; } else if (!any) { err = e; any = true; }
    }
    return ok ? 0 : seen ? err : NX_SM_E_OPCODE;
}
int nx_sm_scan_feed(NxSmScan* s, const uint32_t* tok, uint32_t n) {
    for (uint32_t k = 0; k < n && !s->err; k++, s->pos++) {
        uint32_t w = tok[k];
        if (s->state == SC_ENDED) { s->err = NX_SM_E_LENGTH; break; }                                   // words after END: the stream is exactly version .. END
        if (s->pos >= NX_SM_MAX_TOKENS) { s->err = NX_SM_E_UNTERMINATED; break; }                       // the walk's limit (nx_sm_bytes): no END within 2^20 tokens
        if (s->state == SC_VERSION) { s->err = scan_version(s, w); continue; }
        if (s->skip) { s->skip--; continue; }                                                            // inside a comment
        if (s->state == SC_OPERANDS) { s->pend[s->npend++] = w; if (s->npend < s->len) continue; s->state = SC_BODY; s->err = scan_instr(s); continue; }
        uint32_t op = w & 0xFFFF; const NxSmModel* M = (const NxSmModel*)s->M;
        if (w == 0x0000FFFF) { s->state = SC_ENDED; continue; }
        if (op == OP_COMMENT) { s->skip = (w >> 16) & 0x7FFF; continue; }
        if (M->major >= 2) s->len = (w >> 24) & 15;
        else { const NxSm1P* p = sm1(op); if (!p) { s->err = NX_SM_E_OPCODE; break; } s->len = s->ifc.stage && M->minor == 4 ? p->n14 : p->n; }
        s->optok = w; s->npend = 0;
        if (s->len) s->state = SC_OPERANDS; else s->err = scan_instr(s);
    }
    return s->err;
}
int nx_sm_scan_end(NxSmScan* s) { if (!s->err && s->state != SC_ENDED) s->err = NX_SM_E_UNTERMINATED; return s->err; }
int nx_sm_check(const uint32_t* tok, uint32_t bytes, int stage_expect, NxSmIface* out) {
    NxSmScan s; nx_sm_scan_begin(&s, stage_expect); uint32_t ntok = bytes / 4;
    int e = (bytes & 3) || ntok < 2 ? NX_SM_E_UNTERMINATED : nx_sm_scan_feed(&s, tok, ntok);
    if (!e) e = nx_sm_scan_end(&s);
    if (out) *out = s.ifc;
    return e;
}
uint32_t nx_sm_rules_covered(const char* what) {
    if (!strcmp(what, "models")) return NX_SM_NMODELS;
    if (!strcmp(what, "ops")) return NX_SM_NOPS;
    return 0;
}
