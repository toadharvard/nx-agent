"""sm_rules2.py - curated register / modifier / declaration / flow-control / caps facts with sources (see sm_rules.py)."""
from sm_rules import *
R1, R2, R3 = MS + "vs-registers-vs-1-1", MS + "vs-registers-vs-2-0/2-x", MS + "vs-registers-vs-3-0"
Q1, Q2, Q3 = MS + "ps-registers-ps-1-x (Register Types)", MS + "ps-registers-ps-2-0/2-x", MS + "ps-registers-ps-3-0"
NT = lambda c: dict(min=12, max=32, cap=c)
# REG[model] = {regtype: dict(n=count|dict(cap), rw, rel=[...], dcl, src)}.  Counts/flags transcribed from the MS register tables.
def _r(n, rw="RW", rel=(), dcl=False, **kw): return dict(n=n, rw=rw, rel=list(rel), dcl=dcl, **kw)
_vs_out = {"oPos": _r(1, "W"), "oFog": _r(1, "W"), "oPts": _r(1, "W"), "oD": _r(2, "W"), "oT": _r(8, "W")}
REG = {
 V1: dict(v=_r(16, "R", dcl=True), r=_r(12), c=_r(dict(min=96, cap="D3DCAPS9.MaxVertexShaderConst"), "R", ["a0"]), a0=_r(1, ".x only"), **_vs_out),
 V2: dict(v=_r(16, "R", dcl=True), r=_r(12), c=_r(dict(min=256, cap="D3DCAPS9.MaxVertexShaderConst"), "R", ["a0", "aL"]), a0=_r(1), b=_r(16, "R"), i=_r(16, "R"),
          aL=_r(1, "R"), l=_r(16, "-"), **_vs_out),
 V2X: dict(v=_r(16, "R", dcl=True), r=_r(NT("D3DCAPS9.VS20Caps.NumTemps")), c=_r(dict(min=256, cap="D3DCAPS9.MaxVertexShaderConst"), "R", ["a0", "aL"]),
           a0=_r(1), b=_r(16, "R"), i=_r(16, "R"), aL=_r(1, "R"), p0=_r(1, gate="VS20Caps.Predication"), l=_r(16, "-"), **_vs_out),
 V3: dict(v=_r(16, "R", ["a0", "aL"], True), r=_r(32), c=_r(dict(min=256, cap="D3DCAPS9.MaxVertexShaderConst"), "R", ["a0", "aL"]), a0=_r(1), b=_r(16, "R"),
          i=_r(16, "R"), aL=_r(1, "R"), p0=_r(1), s=_r(4, "R", dcl=True), o=_r(12, "W", ["aL"], True), l=_r(2048, "-")),
 P11: dict(c=_r(8, "R"), r=_r(2), t=_r(4), v=_r(2, "R")),
 P12: dict(c=_r(8, "R"), r=_r(2), t=_r(4), v=_r(2, "R")),
 P13: dict(c=_r(8, "R"), r=_r(2), t=_r(4), v=_r(2, "R")),
 P14: dict(c=_r(8, "R"), r=_r(6), t=_r(6, "R"), v=_r(2, "R", phase2=True)),
 P20: dict(v=_r(2, "R", dcl=True), r=_r(12), c=_r(32, "R"), t=_r(8, "R", dcl=True), s=_r(16, "R", dcl=True), oC=_r(dict(min=1, max=4, cap="D3DCAPS9.NumSimultaneousRTs"), "W"),
           oDepth=_r(1, "W")),
 P2X: dict(v=_r(2, "R", dcl=True), r=_r(NT("D3DPSHADERCAPS2_0.NumTemps")), c=_r(32, "R"), t=_r(8, "R", dcl=True), s=_r(16, "R", dcl=True),
           i=_r(16, "R"), b=_r(16, "R"), p0=_r(1, gate="PS20Caps.Predication"), l=_r(16, "-"), oC=_r(dict(min=1, max=4, cap="D3DCAPS9.NumSimultaneousRTs"), "W"), oDepth=_r(1, "W")),
 P30: dict(v=_r(10, "R", ["aL"], True), r=_r(32), c=_r(224, "R"), i=_r(16, "R"), b=_r(16, "R"), p0=_r(1, "R"), s=_r(16, "R", dcl=True), vPos=_r(1, "R", dcl=True),
           vFace=_r(1, "R", dcl=True, cond_only=True), aL=_r(1, "R"), l=_r(2048, "-"), oC=_r(dict(min=1, max=4, cap="D3DCAPS9.NumSimultaneousRTs"), "W"), oDepth=_r(1, "W")),
}
REG_SRC = {V1: R1, V2: R2, V2X: R2, V3: R3, P11: Q1, P12: Q1, P13: Q1, P14: Q1, P20: Q2, P2X: Q2, P30: Q3}
# ps_2_0 page lists i#/b#/p0 but no ps_2_0 instruction can read them (flow control is ps_2_x+); wine ps_2_0_reg_allowed has none.
REG_NOTES = {(P20, "i"): "MS ps-registers page lists i# (same text as ps_2_x); not usable: no ps_2_0 instruction reads it",
             (P20, "b"): "same", (P20, "p0"): "same"}
# registers an ALU source may name (MS: b#/i#/p0 'only usable by a flow control instruction'; ps_1_4 t# only by texld/texcrd; vFace by compares)
SRC_OK = {**{m: ["r", "v", "c", "aL"] for m in (V2, V2X, V3)}, V1: ["r", "v", "c"], P11: ["r", "v", "c", "t"], P12: ["r", "v", "c", "t"], P13: ["r", "v", "c", "t"],
          P14: ["r", "v", "c"], P20: ["r", "v", "c", "t"], P2X: ["r", "v", "c", "t"], P30: ["r", "v", "c", "vPos", "aL"]}
DST_OK = {V1: ["r", "oPos", "oFog", "oPts", "oD", "oT"], V2: ["r", "oPos", "oFog", "oPts", "oD", "oT"], V2X: ["r", "oPos", "oFog", "oPts", "oD", "oT"],
          V3: ["r", "o"], P11: ["r", "t"], P12: ["r", "t"], P13: ["r", "t"], P14: ["r"], P20: ["r", "oC", "oDepth"], P2X: ["r", "oC", "oDepth"], P30: ["r", "oC", "oDepth"]}
# source modifiers by model.  neg combos: bias/bx2/x2 may be combined with neg (MS ps-registers-modifiers-source)
LEGACY = ["bias", "biasneg", "sign", "signneg", "comp"]
SRCMOD = {V1: ["neg"], V2: ["neg"], V2X: ["neg"], V3: ["neg", "abs", "absneg"], P11: ["neg"] + LEGACY, P12: ["neg"] + LEGACY, P13: ["neg"] + LEGACY,
          P14: ["neg"] + LEGACY + ["x2", "x2neg", "dz", "dw"], P20: ["neg"], P2X: ["neg"], P30: ["neg", "abs", "absneg"]}
SRCMOD_SRC = MS + "ps-registers-modifiers-source / vs-registers-modifiers-source (version tables); wine asmparser.c check_legacy_srcmod/check_abs_srcmod"
# result modifiers: sat (vs only 3_0), pp, centroid(dcl), shift (ps_1_x), per MS ps-instructions-modifiers-ps-1-x / -ps-2-0, vs-instructions-modifiers
DSTMOD = {V1: [], V2: [], V2X: [], V3: ["sat"], P11: ["sat", "x2", "x4", "d2"], P12: ["sat", "x2", "x4", "d2"], P13: ["sat", "x2", "x4", "d2"],
          P14: ["sat", "x2", "x4", "x8", "d2", "d4", "d8"], P20: ["sat", "pp", "centroid"], P2X: ["sat", "pp", "centroid"], P30: ["sat", "pp", "centroid"]}
SAT_EXCEPT_VS = ["frc", "sincos", "texldl"]            # MS vs-instructions-modifiers
# swizzle forms per model (MS ps-registers-modifiers-source-register-swizzling; vs: any swizzle for all models)
SWZ = {**{m: "arbitrary" for m in VS}, P11: "ps1", P12: "ps1", P13: "ps1", P14: "ps14", P20: "ps20", P2X: "arbitrary", P30: "arbitrary"}
SWZ_FORMS = {"ps1": ["", ".w", ".a"], "ps14": ["", ".x", ".y", ".z", ".w"], "ps20": ["", ".x", ".y", ".z", ".w", ".yzxw", ".zxyw", ".wzyx"], "arbitrary": None}
# dcl: D3DDECLUSAGE x index 0..15 ; vs input (all models), vs_3_0 output, ps_3_0 input
USAGES = "position blendweight blendindices normal psize texcoord tangent binormal tessfactor positiont color fog depth sample".split()
DCL_SRC = MS + "dcl-usage-input-register---vs, dcl-usage---ps (D3DDECLUSAGE values, usage_index 0..15)"
SAMPLER_TYPES = {P20: ["2d", "cube", "volume"], P2X: ["2d", "cube", "volume"], P30: ["2d", "cube", "volume"], V3: ["2d", "cube", "volume"]}
# flow-control nesting maxima (None = bounded only by static flow count).  MS vs/ps-instructions-flow-control 'Nesting Depth' tables
FLOW_LIM = {V1: None, V2: dict(static=16, dynamic=0, loop_rep=1, call=1, labels=16, cap=None), V2X: dict(static=16, dynamic=24, loop_rep=4, call=4, labels=16, cap="VS20Caps"),
            V3: dict(static=24, dynamic=24, loop_rep=4, call=4, labels=2048), P2X: dict(static=24, dynamic=24, loop_rep=4, call=4, labels=16, cap="PS20Caps"),
            P30: dict(static=24, dynamic=24, loop_rep=4, call=4, labels=2048)}
# caps gates: named cap -> features it enables (vs_2_x / ps_2_x).  The ENV table decides the values; the universe uses ENV_DEFAULT.
CAPS = {"VS20Caps.Predication": dict(models=[V2X], feats=["setp", "if_pred", "callnz_pred", "breakp", "pred_prefix", "p0"], src=MS + "vs-2-x (Predication)"),
        "VS20Caps.DynamicFlowControlDepth": dict(models=[V2X], feats=["if_cmp", "break", "break_cmp"], src=MS + "vs-2-x (Dynamic Flow Control)"),
        "VS20Caps.StaticFlowControlDepth": dict(models=[V2X], feats=["loop_rep_nest", "call_nest"], src=MS + "vs-2-x"),
        "VS20Caps.NumTemps": dict(models=[V2X], feats=["r_index>=12"], src=MS + "vs-2-x (section on the number of temp registers)"),
        "PS20Caps.Predication": dict(models=[P2X], feats=["setp", "if_pred", "callnz_pred", "breakp", "pred_prefix", "p0"], src=MS + "ps-2-x (Predication)"),
        "PS20Caps.DynamicFlowControlDepth": dict(models=[P2X], feats=["if_cmp", "break", "break_cmp", "if_bool"], src=MS + "ps-2-x"),
        "PS20Caps.StaticFlowControlDepth": dict(models=[P2X], feats=["rep", "call", "callnz_bool", "label", "ret", "else", "endif"], src=MS + "ps-2-x"),
        "PS20Caps.NumTemps": dict(models=[P2X], feats=["r_index>=12"], src=MS + "ps-2-x"),
        "PS20Caps.ArbitrarySwizzle": dict(models=[P2X], feats=["swizzle_arbitrary"], src=MS + "ps-2-x (Arbitrary Swizzle)"),
        "PS20Caps.GradientInstructions": dict(models=[P2X], feats=["dsx", "dsy", "texldd"], src=MS + "ps-2-x (Gradient Instructions)"),
        "PS20Caps.NoDependentReadLimit": dict(models=[P2X, P20], feats=[], validator_only=True, src=MS + "ps-2-x; texld---ps-2-0 (dependent read order)"),
        "PS20Caps.NoTexInstructionLimit": dict(models=[P2X, P20], feats=[], validator_only=True, src=MS + "ps-2-x"),
        "MaxPixelShaderInstructionSlots": dict(models=PS, feats=[], validator_only=True, src=MS + "ps-differences (Instruction Slots)"),
        "MaxVertexShaderConst": dict(models=VS, feats=["c_index"], src=MS + "vs-registers-vs-*")}
ENV_DEFAULT = {"temps": 32, "predication": True, "dynamic_flow": True, "static_depth": 4, "arbitrary_swizzle": True, "gradient": True,
               "vs_const": 256, "ps_const": {P11: 8, P12: 8, P13: 8, P14: 8, P20: 32, P2X: 32, P30: 224}, "mrt": 4,
               "note": "superset ENV: the ENV table may narrow it; the universe is generated for these values (fnvnx_dk_layout.h: c[256])"}
INSTR_LIMITS = "instruction slot / executed-instruction / read-port / dependent-read limits are validator rules, not translator features"
