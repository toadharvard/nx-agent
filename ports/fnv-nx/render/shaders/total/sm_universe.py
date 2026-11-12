"""sm_universe.py - generates the universe rows from sm_table.json ALONE.  Each row = one orthogonal fact:
(model, kind, feature, asm text, ctab).  Families: opcode x model; opcode x slot x register type; register index extremes (both ends);
source modifiers; result modifiers; write masks; swizzles; predication.  sm_universe2.py adds declarations, samplers, flow control,
relative addressing, ps_1_x specials.  `rows()` is deterministic."""
import itertools, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sm_emit import *
import sm_universe2

SM = {"neg": "-{}", "abs": "{}_abs", "absneg": "-{}_abs", "bias": "{}_bias", "biasneg": "-{}_bias", "sign": "{}_bx2", "signneg": "-{}_bx2",
      "comp": "1-{}", "x2": "{}_x2", "x2neg": "-{}_x2", "dz": "{}_dz", "dw": "{}_dw"}
SUB15 = [("." + "".join(c for c, k in zip("xyzw", bits) if k)) for bits in itertools.product([0, 1], repeat=4) if any(bits)]
MASKSET = {"any": SUB15, "xy_any": SUB15, "single": [".x", ".y", ".z", ".w"], "xy": [".xy"], "xyz": [".xyz"], "xyz_or_all": [".xyz", ""],
           "x_y_xy": [".x", ".y", ".xy"], "x_y_xy_vs1": [".x", ".y", ".xy"], "rg": [".xy"]}
PS1_MASK = {"ps_1_1": ["", ".xyz", ".w"], "ps_1_2": ["", ".xyz", ".w"], "ps_1_3": ["", ".xyz", ".w"], "ps_1_4": SUB15 + [""]}
OUT_MASK = {"oPos": [""], "oFog": ["", ".x"], "oPts": ["", ".x"], "oT": ["", ".x", ".xy", ".xyz"], "oDepth": [""], "oD": SUB15 + [""], "o": SUB15 + [""],
            "oC": SUB15 + [""], "t": SUB15 + [""], "r": SUB15 + [""]}
FIRST = {"r": 1, "v": 1, "c": 1, "t": 0, "vPos": 0, "aL": 0, "oD": 0, "oT": 0, "o": 1, "oC": 0, "oPos": 0, "oFog": 0, "oPts": 0, "oDepth": 0}
CONFLICT_ROWS = {("ps_2_0", "defb"), ("ps_1_1", "texreg2gb")}      # table.conflicts: models where MS and vkd3d disagree (universe = union)

def tag(m, f):
    return "conflict" if (m, f.split("@")[0]) in CONFLICT_ROWS else ""

def ops_for(m):
    """(feature name, entry) of every non-declaration op legal in m, with the ps_1_x texkill form substituted"""
    out = []
    for k, e in sorted(OPS.items()):
        if k[:2] != m[:2] or m not in e["models"] or e.get("decl"): continue
        e = dict(e, key=k)
        if ps1(m) and k == "ps:texkill": e.update(d="T", s=[], tm="texkill {D}")
        out.append((k[3:], e))
    return out

def alu(e): return e.get("d") == "D"
def srcs_idx(m, e):
    roles = shape_s(e, m)
    return [i for i, r in enumerate(roles) if r in ("S", "Ss", "Sr", "X")]

class U:
    def __init__(s): s.rows, s.seen = [], set()
    def add(s, m, kind, feat, text, ctab=None, tg=""):
        if (m, kind, feat) in s.seen: return
        s.seen.add((m, kind, feat)); s.rows.append(dict(model=m, kind=kind, feature=feat, asm=text, ctab=ctab, tag=tg))
    def op(s, m, kind, feat, e, tg="", **kw):
        txt, b = op_text(m, e, **kw); s.add(m, kind, feat, finish(m, txt, b), b.ctab, tg)

def family_ops(u, m):
    for name, e in ops_for(m):
        if e.get("tex") and ps1(m): continue                       # ps_1_x texture-addressing ops: universe2 (sampler types)
        u.op(m, "opcode", name, e, tg=tag(m, name))
        if "s3" in e and m.endswith("3_0") and name == "sgn":     # MS sgn page: 3 operands in every version; vkd3d d3dbc.c: 1 source in vs_3_0
            u.op(m, "opcode", name + "@3src", dict(e, s3=e["s"], tm3=e["tm"]), tg="conflict")
        if not alu(e) and e.get("d") not in ("Dr",): continue
        roles = shape_s(e, m)
        for i in srcs_idx(m, e):                                    # operand cross: register type per source slot
            for rt in reg_names(m, "s"):
                if rt == can_src(m, i).rstrip("0123456789") or (rt == "aL" and ps1(m)): continue
                if e.get("matrix") and i == 1: continue
                if rt == "aL" and roles[i] in ("Ss", "Sr"): continue          # scalar ops need a replicate swizzle, aL takes none (wine check_loop_swizzle)
                u.op(m, "operand", "%s@src%d=%s" % (name, i, rt), e, o={i: (rt, FIRST.get(rt, 0))})
        if alu(e):
            for rt in e.get("dr") or reg_names(m, "d"):
                if rt == "r": continue
                if e.get("matrix") and rt in ("oFog", "oPts", "oDepth"): continue      # 2/3/4-wide result into a scalar sink
                ok = [x for x in MASKSET.get(e["mk"], [""]) if x in OUT_MASK.get(rt, SUB15)]         # mask rule of the op must be admitted by the sink's mask rule
                if not ok: continue
                cm = MASK.get(e["mk"], "")
                u.op(m, "operand", "%s@dst=%s" % (name, rt), e, o={"D": (rt, FIRST.get(rt, 0))}, mask=(cm if cm in ok else ok[0]) if e["mk"] not in ("any", "none") else ("" if rt in ("oPos", "oFog", "oPts", "oDepth") else None))

def family_index(u, m):
    """both ends of every register type's index range, read (mov r0, X) and written (mov X, c0)"""
    mov = OPS[m[:2] + ":mov"]
    for rt, spec in sorted(MODELS[m]["regs"].items()):
        n = count(m, rt) if rt not in ("l",) else 0
        if not n: continue
        top = {"c": ENV_C(m)}.get(rt, n)
        for idx in sorted({0, top - 1, (spec["n"]["min"] - 1) if isinstance(spec["n"], dict) else 0}):
            if idx < 0 or (idx == 0 and rt in ("oPos", "oFog", "oPts", "oDepth") and False): continue
            if rt in MODELS[m]["src_regs"] and rt not in ("aL", "vPos"):
                u.op(m, "regidx", "src:%s%d" % (rt, idx), mov, o={1 - 1: (rt, idx)})
            if rt in MODELS[m]["dst_regs"]:
                u.op(m, "regidx", "dst:%s%d" % (rt, idx), mov, o={"D": (rt, idx)}, mask="" if rt in ("oFog", "oPts", "oDepth", "oPos") else None)

def ENV_C(m):
    e = T["env_default"]; return e["vs_const"] if m[0] == "v" else e["ps_const"][m]

def family_srcmod(u, m):
    mods = MODELS[m]["srcmods"]
    for name, e in ops_for(m):
        if not alu(e) and not (e.get("tex") and m == "ps_3_0" and e.get("d") == "Dr"): continue
        if e.get("tex") and ps1(m): continue
        idx = srcs_idx(m, e)
        if ps1(m): idx = [i for i in idx if not can_src(m, i).startswith("c")]
        if e.get("matrix"): idx = [0]
        if e.get("tex"): idx = [0]
        for md in mods:
            if md in ("dz", "dw"): continue
            for i in sorted({idx[0], idx[-1]}) if idx else []:
                u.op(m, "srcmod", "%s@%s.s%d" % (md, name, i), e, srcmod={i: SM[md]})

def family_dstmod(u, m):
    for name, e in ops_for(m):
        if not alu(e) and not e.get("tex"): continue
        if ps1(m) and e.get("tex"): continue
        for md in MODELS[m]["dstmods"]:
            if md == "centroid" or (md == "sat" and m[0] == "v" and name in T["sat_except_vs"]): continue
            if md in ("sat", "pp") and e.get("tex") and m[0] == "v": continue
            u.op(m, "dstmod", "%s@%s" % (md, name), e, dmod="_" + md)

def family_mask(u, m):
    mov = OPS[m[:2] + ":mov"]
    for rt in MODELS[m]["dst_regs"]:
        if rt not in MODELS[m]["regs"]: continue
        for mk in (PS1_MASK[m] if ps1(m) and rt in ("r", "t") else OUT_MASK.get(rt, SUB15)):
            idx = {"o": 1, "oT": 0}.get(rt, 0) if rt != "r" else 0
            tg = "conflict" if (rt in ("oFog", "oPts") and mk == ".x") else ""
            u.op(m, "writemask", "mov@%s%d%s" % (rt, idx, mk or ".all"), mov, o={"D": (rt, idx)}, mask=mk, tg=tg)
    for name, e in ops_for(m):
        if not alu(e) or (e["mk"] in ("any", "none") and not (ps1(m) and e.get("mk_ps1"))): continue
        mks = MASKSET.get(e["mk"], []) + ([".xyz"] if e["mk"] == "x_y_xy" else []) + ([""] if e["mk"] in ("single", "x_y_xy_vs1") else [])
        if ps1(m) and e.get("mk_ps1"): mks = e["mk_ps1"]
        elif m[0] == "p" and e["mk"] != "x_y_xy": continue         # the restricted-mask rules are stated on the vs masking page (sincos also on its ps page)
        for mk in mks:
            if ps1(m) and mk not in PS1_MASK[m] and e["mk"] not in ("rg",): continue
            u.op(m, "writemask", "%s@%s" % (name, mk or ".all"), e, mask=mk, tg="conflict" if (e["mk"] == "x_y_xy" and mk == ".xyz") else ("undet" if mk == "" else ""))

def family_swizzle(u, m):
    form = T["swizzle_forms"].get(MODELS[m]["swizzle"])
    mov = OPS[m[:2] + ":mov"]
    if form is None:
        form = ["." + "".join(p) for n in range(1, 5) for p in itertools.product("xyzw", repeat=n) if n == 4 or p == tuple(p[0] * n)] + [".xy", ".yz", ".xyz", ".wzy"]
    for z in form:
        mk = ".w" if (ps1(m) and z in (".z", ".b")) else None
        if z in (".w", ".a") and ps1(m): mk = None
        u.op(m, "swizzle", "mov@" + (z or ".none"), mov, sw={0: z}, mask=mk)
    for name, e in ops_for(m):
        roles = shape_s(e, m)
        for i, r in enumerate(roles):
            if r in ("Ss", "Sr"):
                for z in (".x", ".y", ".z", ".w"): u.op(m, "swizzle", "%s@s%d%s" % (name, i, z), e, sw={i: z})
        if m in ("ps_3_0",) and e.get("tex") and e.get("d") == "Dr":
            for z in (".yxzw", ".wzyx"): u.op(m, "swizzle", "%s@coord%s" % (name, z), e, sw={0: z})

def family_pred(u, m):
    if "p0" not in MODELS[m]["regs"]: return
    for name, e in ops_for(m):
        if not alu(e) or name in ("nop",): continue
        for pf in ("(p0)", "(!p0)", "(p0.w)", "(!p0.y)"):
            txt, b = op_text(m, e); b.pre.append("setp_gt p0, r1, r2")
            u.add(m, "pred", "%s@%s" % (name, pf), finish(m, "\n".join(pf + " " + l if l.split(" ")[0] == name else l for l in txt.split("\n")), b))

def rows():
    u = U()
    for m in MODELS:
        for fam in (family_ops, family_index, family_srcmod, family_dstmod, family_mask, family_swizzle, family_pred):
            fam(u, m)
        sm_universe2.families(u, m)
    base = list(u.rows)
    for alias, b in (("vs_1_0", "vs_1_1"), ("ps_1_0", "ps_1_1")):        # undetermined (table.excluded_models): version token 1.0 with the 1_1 grammar
        for r in base:
            if r["model"] == b and r["kind"] == "opcode":
                u.add(alias, "opcode", r["feature"], alias + r["asm"][len(b):], None, "undet")
    for r in u.rows:                                                   # ps_1_x type-dependent texture ops without a CTAB: translator input missing
        if r["kind"] == "ps1tex" and r["feature"].endswith("@ctab=none"):
            e = OPS["ps:" + r["feature"].split("@")[0]]
            r["needs_input"] = "sampler_type" if e.get("st") in (None, "3d_or_cube") else ""
    return u.rows

if __name__ == "__main__":
    import collections
    r = rows(); print(len(r)); print(collections.Counter(x["kind"] for x in r))
