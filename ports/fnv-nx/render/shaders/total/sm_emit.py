"""sm_emit.py - turns table facts (sm_table.json) into assembler text for one test shader: a valid shell for the model, operand
spelling per register type, and the setup (dcl/def/loop/setp) each register type needs according to the table's `dcl`/`rel`/`rw` attributes."""
import json, os, re
HERE = os.path.dirname(os.path.abspath(__file__))
T = json.load(open(os.path.join(HERE, "sm_table.json")))
isvs = lambda m: m[0] == "v"
ps1 = lambda m: m[0] == "p" and m[3] == "1"
OPS, MODELS = T["opcodes"], T["models"]
FILL = "mov r0, c0"

def shell(m, body, decls=(), pre=(), epi=True):
    """full valid shader around body.  def/defi/defb are hoisted before the first instruction; @E@ marks the output write"""
    v, hoist, keep = isvs(m), [], []
    for ln in list(pre) + body.split("\n"):
        (hoist if ln.split(" ")[0] in ("def", "defi", "defb") else keep).append(ln)
    hoist = list(dict.fromkeys(hoist))
    L = [m] + (["dcl_position v0"] if v else []) + (["dcl_position o0"] if m == "vs_3_0" else []) + list(dict.fromkeys(decls)) + hoist
    if v or m[3] != "1": L += ["mov r0, c0", "mov r1, c1", "mov r2, c2", "mov r3, c3"]
    ep = {"vs_3_0": "mov o0, r0"}.get(m, ("mov oPos, r0" if v else "mov oC0, r0") if (v or m[3] != "1") else "")
    text = "\n".join(keep); had = "@E@" in text
    L.append(text.replace("@E@", ep))
    if m[0] == "p" and m[3] == "1" and not any(re.match(r"\+?\s*\w+ r0\b", x) for x in text.split("\n")): L.append("mov r0, c0")
    if epi and not had and ep: L.append(ep)
    return "\n".join(x for x in L if x) + "\n"

def can_src(m, i):
    return (["v0", "r1", "r2"] if isvs(m) else ["v0", "c0", "v1"] if m[3] == "1" else ["r1", "r2", "r3"])[i % 3]

def reg_names(m, which):
    """register types the table allows for ALU sources / destinations in model m"""
    mm = MODELS[m]; return [r for r in mm["src_regs" if which == "s" else "dst_regs"] if r in mm["regs"]]

def count(m, rt):
    n = MODELS[m]["regs"][rt]["n"]
    return n if isinstance(n, int) else n.get("max", n["min"])

def operand(m, rt, idx, role="S"):
    """-> (text, pre_lines, decl_lines, wrap) for register type rt index idx in model m"""
    spec = MODELS[m]["regs"].get(rt, {}); pre, dc, wrap, v = [], [], None, isvs(m)
    if rt == "r":
        if idx >= 4 or ps1(m): pre.append("mov r%d, c0" % idx)
        return "r%d" % idx, pre, dc, wrap
    if rt == "v":
        if spec.get("dcl") and not (v and idx == 0): dc.append(("dcl_texcoord%d v%d" % (idx % 16, idx)) if (v or m == "ps_3_0") else "dcl v%d" % idx)
        return "v%d" % idx, pre, dc, wrap
    if rt == "t":
        if ps1(m): pre.append("tex t%d" % idx) if m != "ps_1_4" else None
        elif spec.get("dcl"): dc.append("dcl t%d" % idx)
        return "t%d" % idx, pre, dc, wrap
    if rt == "c": return "c%d" % idx, pre, dc, wrap
    if rt == "vPos": return "vPos", pre, ["dcl vPos.xy"], wrap
    if rt == "vFace": return "vFace", pre, ["dcl vFace"], wrap
    if rt == "aL": return "aL", pre, dc, "loop"
    if rt == "a0": return "a0.x", ["mov a0.x, c0.x" if m == "vs_1_1" else "mova a0, c0"], dc, wrap
    if rt == "o": dc.append("dcl_texcoord%d o%d" % (idx, idx)) if idx else None; return "o%d" % idx, pre, dc, wrap
    if rt in ("oD", "oT", "oC"): return "%s%d" % (rt, idx), pre, dc, wrap
    if rt in ("oPos", "oFog", "oPts", "oDepth"): return rt, pre, dc, wrap
    if rt == "s": return "s%d" % idx, pre, dc, wrap
    if rt == "b": return "b%d" % idx, ["defb b%d, true" % idx], dc, wrap
    if rt == "i": return "i%d" % idx, ["defi i%d, 4, 0, 1, 0" % idx], dc, wrap
    if rt == "p0": return "p0.x", ["setp_gt p0, r1, r2"], dc, wrap
    raise KeyError(rt)

MASK = {"single": ".x", "xy": ".xy", "xyz": ".xyz", "xyz_or_all": "", "x_y_xy": ".xy", "x_y_xy_vs1": ".xy", "rg": ".xy", "xy_any": ".x"}
SCAL = {"Ss": ".x", "Sr": ".x"}

def shape_s(e, m): return e["s3"] if "s3" in e and m.endswith("3_0") else e["s"]

class Build:
    """one test shader under construction"""
    def __init__(s, m): s.m, s.pre, s.decl, s.wrap, s.ctab = m, [], [], None, None
    def use(s, r):
        t, p, d, w = r; s.pre += p; s.decl += d; s.wrap = w or s.wrap; return t

def op_text(m, e, o=None, sw=None, mask=None, dmod="", srcmod=None, b=None):
    """asm text of op `key` (e.g. 'vs:add') in model m.  o: {'D': (rt, idx), 0: (rt, idx) ...} operand overrides; srcmod: {slot: fmt};
    sw: {slot: swizzle}; mask: dst mask text; dmod: instruction suffix (_sat/_pp/_x2...).  -> (body, pre, decls, wrap)"""
    e = OPS[e] if isinstance(e, str) else e; o = o or {}; srcmod = srcmod or {}; sw = sw or {}; b = b or Build(m); key = e.get("key", "")
    roles = shape_s(e, m); tm = e["tm3"] if "tm3" in e and m.endswith("3_0") else e["tm"]; rep = {}
    d = e.get("d")
    if d in ("D", "Dr", "T", "Rr", "A0", "P0"):
        if "D" in o: dt = b.use(operand(m, *o["D"]))
        else:
            dt = {"A0": "a0", "P0": "p0"}.get(d) or ("r0" if d in ("D", "Dr", "Rr") else "t1")
        rep["D"] = dt + (mask if mask is not None else (MASK.get(e.get("mk"), "") if d in ("D", "A0", "Rr", "P0") else ""))
    for i, r in enumerate(roles):
        if i in o: t = b.use(operand(m, *o[i], role=r))
        elif r in ("S", "Tmp", "X", "Sr", "Ss"): t = can_src(m, i) if r != "X" else "t0"
        elif r == "Sc": t = "c%d" % (4 + i)
        elif r == "Sm": t = b.use(operand(m, "s", 0)); b.decl.append("dcl_%s s0" % (o.get("stype") or "2d")) if not ps1(m) else None
        elif r == "B": t = b.use(operand(m, "b", 0))
        elif r == "I": t = b.use(operand(m, "i", 0))
        elif r == "Pp": t = b.use(operand(m, "p0", 0))
        elif r == "AL": t = "aL"
        elif r == "L": t = "l0"
        else: t = can_src(m, i)
        fx = e.get("fix", {}).get(m, {}).get(str(i))
        if fx: t = fx; b.pre.append("mov r0, c0")
        z = sw.get(i, ".x" if r in SCAL else "")
        if "." not in t and t != "aL": t += z                 # aL takes no swizzle (wine check_loop_swizzle)
        if i in srcmod: t = srcmod[i].format(t)
        rep["S%d" % i] = t
    for k, v in (("I0", "i0"), ("B0", "b0"), ("LBL", "0"), ("BODY", FILL), ("BODY2", FILL)): rep.setdefault(k, v)
    txt = tm
    for k, v in rep.items(): txt = txt.replace("{%s}" % k, v)
    nd = e.get("needs", "")
    if "defi" in nd: b.pre.append("defi i0, 4, 0, 1, 0")
    if "defb" in nd: b.pre.append("defb b0, true")
    if "setp" in nd: b.pre.append("setp_gt p0, r1, r2")
    if dmod:                                                     # suffix on the first mnemonic
        first, _, rest = txt.partition(" ")
        txt = first + dmod + (" " + rest if rest else "")
    return txt, b

def finish(m, txt, b, extra_decl=()):
    if b.wrap == "loop": txt = "defi i0, 4, 0, 1, 0\nloop aL, i0\n" + txt + "\nendloop"
    return shell(m, txt, decls=list(b.decl) + list(extra_decl), pre=b.pre)
