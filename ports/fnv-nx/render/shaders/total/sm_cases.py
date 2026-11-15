"""sm_cases.py - the SM1-3 universe U: yields (model, kind, feature, asm_text, legal) rows ('y' or '?').
Legality is the D3D9 SDK instruction/register tables as remembered by the author; every pair that is not certain is '?'."""
MODELS = ["vs_1_1", "vs_2_0", "vs_2_x", "vs_3_0", "ps_1_1", "ps_1_2", "ps_1_3", "ps_1_4", "ps_2_0", "ps_2_x", "ps_3_0"]
def isvs(m): return m[0] == "v"
def ver(m): return (int(m[3]), m[5])          # ("1","1") ...
def ge(m, v): return (m[3] + m[5].replace("x", "1")) >= v and m[:2] == m[:2]

VS11 = "mov add sub mad mul rcp rsq dp3 dp4 min max slt sge exp log lit dst frc m4x4 m4x3 m3x4 m3x3 m3x2 expp logp nop def dcl".split()
VS20 = VS11 + "abs crs lrp nrm pow sincos sgn mova call callnz loop endloop rep endrep if else endif label ret defb defi".split()
VS2X = VS20 + "break break_gt breakp setp_gt if_gt".split()
VS30 = VS2X + ["texldl"]
PS11 = "add sub mad mul lrp mov dp3 cnd nop tex texcoord texkill texbem texbeml texreg2ar texreg2gb texm3x2pad texm3x2tex texm3x3pad texm3x3tex texm3x3spec texm3x3vspec def".split()
PS12 = PS11 + "cmp dp4 texreg2rgb texdp3 texdp3tex texm3x3".split()
PS13 = PS12 + ["texm3x2depth"]
PS14 = "add sub mad mul lrp mov dp3 dp4 cmp cnd nop def phase texcrd texld texkill texdepth bem".split()
PS20 = "abs add cmp crs dp2add dp3 dp4 exp frc log lrp mad max min mov mul nop nrm pow rcp rsq sincos sub texkill texld texldp texldb dcl def".split()
PS2X = PS20 + "dsx dsy texldd setp_gt breakp break break_gt call callnz else endif endloop endrep if if_gt label loop rep ret defb defi".split()
PS30 = PS2X + ["texldl"]
OPS = {"vs_1_1": VS11, "vs_2_0": VS20, "vs_2_x": VS2X, "vs_3_0": VS30, "ps_1_1": PS11, "ps_1_2": PS12, "ps_1_3": PS13,
       "ps_1_4": PS14, "ps_2_0": PS20, "ps_2_x": PS2X, "ps_3_0": PS30}
# ops MojoShader's table allows but the SDK tables list for the other stage / lower models only: included, legal=?
EXTRA = {"ps_2_0": "lit dst expp logp sgn m4x4 m4x3 m3x4 m3x3 m3x2".split(), "ps_2_x": "lit dst expp logp sgn m4x4 m4x3 m3x4 m3x3 m3x2".split(),
         "ps_3_0": "lit dst expp logp sgn m4x4 m4x3 m3x4 m3x3 m3x2".split(), "vs_2_x": ["breakc"]}
SC = {"rcp": 1, "rsq": 1, "exp": 1, "log": 1, "expp": 1, "logp": 1}
NS = dict(mov=1, add=2, sub=2, mad=3, mul=2, rcp=1, rsq=1, dp3=2, dp4=2, min=2, max=2, slt=2, sge=2, exp=1, log=1, lit=1, dst=2, frc=1,
          expp=1, logp=1, abs=1, crs=2, lrp=3, nrm=1, pow=2, sgn=3, dp2add=3, cmp=3, cnd=3, dsx=1, dsy=1, mova=1, nop=0)
DSTM = dict(frc=".xy", m4x4="", m4x3=".xyz", m3x4="", m3x3=".xyz", m3x2=".xy", crs=".xyz", sincos=".xy", mova=".xy", dp3="", lit="")

def shell(m, body, pre="", epi=True, decls=""):
    """full valid shader around body; @E@ in body marks where the output write goes (default: end of body)"""
    v = isvs(m)
    hoist, keep = [], []
    for ln in (pre + "\n" + body).splitlines():          # def/defi/defb must precede every instruction
        (hoist if ln.split(" ")[0] in ("def", "defi", "defb") else keep).append(ln)
    L = [m]
    if v: L.append("dcl_position v0")
    if m == "vs_3_0": L.append("dcl_position o0")
    if decls: L.append(decls)
    L += hoist
    if v or m[3] != "1": L += ["mov r0, c0", "mov r1, c1", "mov r2, c2", "mov r3, c3"]
    ep = {"vs_3_0": "mov o0, r0"}.get(m, ("mov oPos, r0" if v else "mov oC0, r0") if (v or m[3] != "1") else "")
    text = "\n".join(keep)
    had = "@E@" in text
    text = text.replace("@E@", ep)
    L.append(text)
    if epi and not had and ep: L.append(ep)
    return "\n".join(x for x in L if x) + "\n"

def srcs(m, n):
    if isvs(m): return ["v0", "r1", "r2"][:n]
    if m[3] == "1": return ["v0", "c0", "v1"][:n]
    return ["r1", "r2", "r3"][:n]

def opbody(m, op):
    """minimal instruction text for op on model m, or (body, pre, decls)"""
    p, pre, dc = [], "", ""
    s = srcs(m, 3); ps1 = (not isvs(m)) and m[3] == "1"; sm3 = m[3] == "3"
    if op in NS and op not in ("nop",):
        a = list(s[:NS[op]])
        if op in SC and not ps1: a = [x + ".x" for x in a]
        if op == "pow": a = [s[0] + ".x", s[1] + ".x"]
        if op == "dp2add": a[2] += ".x"
        if op == "cnd": a[0] = "r0.a"
        if op == "sgn": a = [s[0], "r1", "r2"] if not isvs(m) else ["v0", "r1", "r2"]
        d = "a0" + DSTM["mova"] if op == "mova" else "r0" + DSTM.get(op, "")
        if op == "mova": return f"mova a0.xy, {a[0]}", "", ""
        return f"{op} {d}, " + ", ".join(a), "", ""
    if op == "nop": return "nop\nmov r0, c0" if ps1 else "nop", "", ""
    if op in ("m4x4", "m4x3", "m3x4", "m3x3", "m3x2"): return f"{op} r0{DSTM[op]}, {s[0]}, c4", "", ""
    if op == "sincos":
        return ("sincos r0.xy, r1.x" if sm3 else "sincos r0.xy, r1.x, c0, c1"), "", ""
    if op in ("def",): return "def c0, 1.0, 2.0, 3.0, 4.0\nmov r0, c0", "", ""
    if op == "dcl": return "mov r0, v0", "", ("" if isvs(m) else ("dcl v0" if m[3] == "2" else "dcl_color0 v0"))
    if op == "defi": return "defi i0, 4, 0, 1, 0\nrep i0\nadd r0, r0, c0\nendrep", "", ""
    if op == "defb": return "defb b0, true\nif b0\nmov r0, c0\nendif", "", ""
    if op == "call": return "call l0\n@E@\nret\nlabel l0\nmov r0, c0\nret", "", ""
    if op == "callnz": return "defb b0, true\ncallnz l0, b0\n@E@\nret\nlabel l0\nmov r0, c0\nret", "", ""
    if op in ("label", "ret"): return "call l0\n@E@\nret\nlabel l0\nmov r0, c0\nret", "", ""
    if op in ("loop", "endloop"): return "defi i0, 4, 0, 1, 0\nloop aL, i0\nadd r0, r0, c0\nendloop", "", ""
    if op in ("rep", "endrep"): return "defi i0, 4, 0, 1, 0\nrep i0\nadd r0, r0, c0\nendrep", "", ""
    if op in ("if", "else", "endif"): return "defb b0, true\nif b0\nmov r0, c0\nelse\nmov r0, c1\nendif", "", ""
    if op == "if_gt": return "if_gt r1.x, r2.x\nmov r0, c0\nelse\nmov r0, c1\nendif", "", ""
    if op == "breakc": op = "break_gt"
    if op in ("break", "break_gt", "breakp"):
        c = {"break": "break", "break_gt": "break_gt r1.x, r2.x", "breakp": "breakp p0.x"}[op]
        pr = "setp_gt p0, r1, r2\n" if op == "breakp" else ""
        return pr + f"defi i0, 4, 0, 1, 0\nrep i0\nadd r0, r0, c0\n{c}\nendrep", "", ""
    if op == "setp_gt": return "setp_gt p0, r1, r2\n(p0) mov r0, c0", "", ""
    if op in ("texld", "texldp", "texldb") and not ps1:
        return f"{op} r0, r1, s0", "", "dcl_2d s0"
    if op == "texldl": return "texldl r0, r1, s0", "", "dcl_2d s0"
    if op == "texldd": return "texldd r0, r1, s0, r2, r3", "", "dcl_2d s0"
    if op == "texkill": return ("texkill t0\nmov r0, c0" if ps1 else "texkill r1"), "", ""
    if op == "tex": return "tex t0\nmov r0, t0", "", ""
    if op == "texcoord": return "texcoord t0\nmov r0, t0", "", ""
    if op == "texcrd": return "texcrd r0, t0", "", ""
    if op == "texld" and ps1: return "texld r0, t0", "", ""
    if op == "phase": return "texld r0, t0\nphase\nmov r0, r0", "", ""
    if op == "texdepth": return "texdepth r5\nmov r0, c0", "", ""
    if op == "bem": return "texld r0, t0\ntexld r2, t1\nbem r1.xy, r0, r2\nmov r0, r1", "", ""
    T = {"texbem": "tex t0\ntexbem t1, t0", "texbeml": "tex t0\ntexbeml t1, t0", "texreg2ar": "tex t0\ntexreg2ar t1, t0",
         "texreg2gb": "tex t0\ntexreg2gb t1, t0", "texreg2rgb": "tex t0\ntexreg2rgb t1, t0", "texdp3": "tex t0\ntexdp3 t1, t0",
         "texdp3tex": "tex t0\ntexdp3tex t1, t0", "texm3x2pad": "tex t0\ntexm3x2pad t1, t0\ntexm3x2tex t2, t0",
         "texm3x2tex": "tex t0\ntexm3x2pad t1, t0\ntexm3x2tex t2, t0",
         "texm3x2depth": "tex t0\ntexm3x2pad t1, t0\ntexm3x2depth t2, t0",
         "texm3x3pad": "tex t0\ntexm3x3pad t1, t0\ntexm3x3pad t2, t0\ntexm3x3tex t3, t0",
         "texm3x3tex": "tex t0\ntexm3x3pad t1, t0\ntexm3x3pad t2, t0\ntexm3x3tex t3, t0",
         "texm3x3": "// ctab: s3 sampler 3 1 object samplercube 1 1\ntex t0\ntexm3x3pad t1, t0\ntexm3x3pad t2, t0\ntexm3x3 t3, t0",
         "texm3x3spec": "tex t0\ntexm3x3pad t1, t0\ntexm3x3pad t2, t0\ntexm3x3spec t3, t0, c0",
         "texm3x3vspec": "tex t0\ntexm3x3pad t1, t0\ntexm3x3pad t2, t0\ntexm3x3vspec t3, t0"}
    if op in T: return T[op] + "\nmov r0, t3" if "t3" in T[op] else T[op] + "\nmov r0, t2" if "t2" in T[op] else T[op] + "\nmov r0, t1", "", ""
    raise KeyError(op)

def cases():
    C = []
    add = lambda m, k, f, t, lg="y": C.append((m, k, f, t, lg))
    for m in MODELS:
        v = isvs(m); ps1 = (not v) and m[3] == "1"
        for op in OPS[m] + EXTRA.get(m, []):
            b, pre, dc = opbody(m, op)
            add(m, "opcode", op, shell(m, b, pre, decls=dc), "?" if op in EXTRA.get(m, []) else "y")
        # source modifiers
        mods = {"neg": "-{}"}
        if (not ps1) or True: mods["neg"] = "-{}"
        if not (v and m[3] == "1"):
            mods.update({"abs": "{}_abs", "absneg": "-{}_abs", "not": None})
        if ps1 or not v:
            mods.update({"bias": "{}_bias", "biasneg": "-{}_bias", "sign": "{}_bx2", "signneg": "-{}_bx2", "comp": "1-{}",
                         "x2": "{}_x2", "x2neg": "-{}_x2"})
        if m == "ps_1_4": mods.update({"dz": "{}_dz", "dw": "{}_dw"})
        for k, f in mods.items():
            lg = "y"
            if k in ("abs", "absneg") and m[3] == "2": lg = "?"
            if k in ("bias", "biasneg", "sign", "signneg", "comp", "x2", "x2neg") and m[3] != "1": lg = "?"
            if k == "not": lg = "?"
            if k == "not":
                if m[3] == "1" or m in ("vs_1_1",): continue
                body = "defb b0, true\nif !b0\nmov r0, c0\nendif"
                if m in ("vs_2_0", "ps_2_0"):
                    if m == "ps_2_0": continue
                add(m, "srcmod", "not", shell(m, body), lg); continue
            if dz_dw(k):
                add(m, "srcmod", k, shell(m, "texld r0, " + f.format("t0") if k == "dz" else "texcrd r0, " + f.format("t0")), lg); continue
            src = srcs(m, 3)
            add(m, "srcmod", k + "@mov", shell(m, "mov r0, " + f.format(src[0])), lg)
            add(m, "srcmod", k + "@mad.s2", shell(m, "mad r0, " + f.format(src[2]).join([src[0] + ", " + src[1] + ", ", ""])), lg)
        # result modifiers
        if not v or m[3] != "1":
            for k in ("sat", "pp"):
                if k == "pp" and ps1: continue
                if m == "vs_1_1": continue
                lg = "y" if not v else "?"
                if k == "pp" and m == "vs_3_0": lg = "?"
                s0 = srcs(m, 1)[0]
                add(m, "dstmod", k + "@mov", shell(m, f"mov_{k} r0, {s0}"), lg)
                if not v and m[3] != "1": add(m, "dstmod", k + "@texld", shell(m, f"texld_{k} r0, r1, s0", decls="dcl_2d s0"), lg)
                if not v and m[3] != "1": add(m, "dstmod", k + "@add", shell(m, f"add_{k} r0, r1, r2"), lg)
        if not v and m[3] == "2": add(m, "dstmod", "centroid@dcl_texcoord", shell(m, "mov r0, t0", decls="dcl_centroid t0"), "?")
        if m == "ps_3_0": add(m, "dstmod", "centroid@dcl_texcoord", shell(m, "mov r0, v0", decls="dcl_texcoord0_centroid v0"), "y")
        if ps1:
            for sh in ("x2", "x4", "x8", "d2", "d4", "d8"):
                lg = "y" if (m == "ps_1_4" or sh not in ("x8", "d8")) else "?"
                add(m, "dstmod", "shift_" + sh, shell(m, f"mul_{sh} r0, v0, c0"), lg)
        # dcl usages
        US = "position blendweight blendindices normal psize texcoord tangent binormal tessfactor positiont color fog depth sample".split()
        if v:
            for i, u in enumerate(US):
                add(m, "usage", f"{i}:{u}0@input", shell(m, "mov r0, v1", decls=f"dcl_{u}0 v1"))
            if m[3] == "3":
                for i, u in enumerate(US):
                    add(m, "usage", f"{i}:{u}0@output", shell(m, "mov o1, r0", decls=f"dcl_{u}0 o1"),
                        "y" if u in ("position", "psize", "color", "fog", "texcoord", "tessfactor", "depth", "sample") else "?")
                add(m, "usage", "texcoord15@v15", shell(m, "mov r0, v15", decls="dcl_texcoord15 v15"))
                add(m, "usage", "texcoord11@o11", shell(m, "mov o11, r0", decls="dcl_texcoord11 o11"))
        elif m[3] == "2":    # ps_2_x: semantics are not encoded (usage token is 0), only the register and flags
            add(m, "usage", "dcl_t0", shell(m, "mov r0, t0", decls="dcl t0"))
            add(m, "usage", "dcl_t7", shell(m, "mov r0, t7", decls="dcl t7"))
            add(m, "usage", "dcl_v0", shell(m, "mov r0, v0", decls="dcl v0"))
            add(m, "usage", "dcl_v1", shell(m, "mov r0, v1", decls="dcl v1"))
        elif m == "ps_3_0":
            for i, u in enumerate(US):
                add(m, "usage", f"{i}:{u}0@input", shell(m, "mov r0, v1", decls=f"dcl_{u}0 v1"),
                    "y" if u in ("color", "texcoord", "fog") else "?")
            add(m, "usage", "texcoord9@v9", shell(m, "mov r0, v9", decls="dcl_texcoord9 v9"))
        # samplers
        if m[3] != "1" or m == "vs_3_0" or v and m == "vs_3_0":
            if (not v and m[3] != "1") or m == "vs_3_0":
                for ty in ("2d", "cube", "volume"):
                    ops_ = (["texld", "texldp", "texldb"] if not v else []) + (["texldl"] if m in ("ps_3_0", "vs_3_0") else []) + \
                           (["texldd"] if m in ("ps_2_x", "ps_3_0") else [])
                    for o in ops_:
                        tx = f"{o} r0, r1, s0, r2, r3" if o == "texldd" else f"{o} r0, r1, s0"
                        add(m, "sampler", f"{ty}:{o}", shell(m, tx, decls=f"dcl_{ty} s0"))
                    add(m, "sampler", f"{ty}:s15", shell(m, "texld r0, r1, s15" if not v else "texldl r0, r1, s15", decls=f"dcl_{ty} s15"))
        # register types
        R = lambda f, t, d="", lg="y", pre="": add(m, "regtype", f, shell(m, t, pre, decls=d), lg)
        if v:
            R("v", "mov r0, v0"); R("c", "mov r0, c5"); R("r", "mov r0, c0\nadd r0, r0, r0")
            if m == "vs_1_1": R("a0", "mov a0.x, c0.x\nmov r0, c[a0.x + 4]")
            else: R("a0", "mova a0.x, c0\nmov r0, c[a0.x + 4]")
            if m != "vs_3_0":
                for o, t in (("oFog", "oFog, c0.x"), ("oPts", "oPts, c0.x"), ("oD0", "oD0, c0"), ("oD1", "oD1, c0"), ("oT0", "oT0, c0"),
                             ("oT7", "oT7, c0")): R(o, "mov " + t + "\nmov oPos, c1", epi=False) if False else add(m, "regtype", o, shell(m, "mov " + t), "y")
            else:
                for n in (1, 11): add(m, "regtype", f"o{n}", shell(m, f"mov o{n}, r0", decls=f"dcl_texcoord{n} o{n}"))
            if m[3] != "1":
                R("b0", "defb b0, true\nif b0\nmov r0, c0\nendif"); R("i0", "defi i0, 4, 0, 1, 0\nrep i0\nadd r0, r0, c0\nendrep")
                R("aL", "defi i0, 4, 0, 1, 0\nloop aL, i0\nmov r0, c[aL + 2]\nendloop")
                R("l0", "call l0\n@E@\nret\nlabel l0\nmov r0, c0\nret")
                R("b0:no-defb", "if b0\nmov r0, c0\nendif")
                R("i0:no-defi", "rep i0\nadd r0, r0, c0\nendrep")
            if m in ("vs_2_x", "vs_3_0"):
                R("p0", "setp_gt p0, r1, r2\n(p0) mov r0, c0"); R("p0:neg", "setp_gt p0, r1, r2\n(!p0) mov r0, c0")
                R("p0:swz", "setp_gt p0, r1, r2\n(p0.w) mov r0, c0")
            if m == "vs_3_0": R("s0", "texldl r0, r1, s0", "dcl_2d s0")
            R("def", "def c9, 1.0, 2.0, 3.0, 4.0\nmov r0, c9")
        else:
            if ps1:
                R("v", "mov r0, v0"); R("c", "mov r0, c0"); R("r", "mov r1, c0\nmov r0, r1")
                if m != "ps_1_4": R("t", "tex t0\nmov r0, t0")
                else: R("t", "texld r0, t0"); R("r_as_texdst", "texld r2, t0\nmov r0, r2")
                R("def", "def c3, 1.0, 2.0, 3.0, 4.0\nmov r0, c3")
            else:
                R("r", "mov r0, r1"); R("c", "mov r0, c0"); R("s", "texld r0, r1, s0", "dcl_2d s0")
                R("v", "mov r0, v0", "dcl v0" if m[3] == "2" else "dcl_color0 v0")
                R("t", "mov r0, t0", "dcl t0") if m[3] == "2" else None
                R("oC0", "mov oC0, r0", ), R("oC1", "mov oC0, r0\nmov oC1, r0"), R("oC2", "mov oC0, r0\nmov oC2, r0")
                R("oC3", "mov oC0, r0\nmov oC3, r0")
                add(m, "regtype", "oC0-3", shell(m, "mov oC0, r0\nmov oC1, r0\nmov oC2, r0\nmov oC3, r0"))
                add(m, "regtype", "oDepth", shell(m, "mov oDepth, r0.x"))
                R("def", "def c9, 1.0, 2.0, 3.0, 4.0\nmov r0, c9")
                if m in ("ps_2_x", "ps_3_0"):
                    R("b0", "defb b0, true\nif b0\nmov r0, c0\nendif"); R("i0", "defi i0, 4, 0, 1, 0\nrep i0\nadd r0, r0, c0\nendrep")
                    R("aL", "defi i0, 4, 0, 1, 0\nloop aL, i0\nmov r0, c[aL + 2]\nendloop" if m == "ps_3_0" else "defi i0, 4, 0, 1, 0\nloop aL, i0\nadd r0, r0, c0\nendloop")
                    R("l0", "call l0\n@E@\nret\nlabel l0\nmov r0, c0\nret")
                    R("p0", "setp_gt p0, r1, r2\n(p0) mov r0, c0"); R("p0:neg", "setp_gt p0, r1, r2\n(!p0) mov r0, c0")
                    R("p0:swz", "setp_gt p0, r1, r2\n(p0.w) mov r0, c0")
                if m == "ps_3_0":
                    R("vPos", "mov r0, vPos", "dcl vPos.xy"); R("vFace", "mov r0, vFace", "dcl vFace")
                    R("v9", "mov r0, v9", "dcl_texcoord9 v9")
        extra_rows(m, add); more_rows(m, add)
        # relative addressing
        pa = lambda f, t, d="", lg="y": add(m, "addr", f, shell(m, t, decls=d), lg)
        if v:
            pa("c[a0.x+n]", ("mov a0.x, c0.x" if m == "vs_1_1" else "mova a0.x, c0") + "\nmov r0, c[a0.x + 3]")
            pa("c[a0.x]", ("mov a0.x, c0.x" if m == "vs_1_1" else "mova a0.x, c0") + "\nadd r0, c[a0.x], r1")
            if m[3] != "1":
                pa("c[aL+n]", "defi i0, 4, 0, 1, 0\nloop aL, i0\nmov r0, c[aL + 3]\nendloop")
                pa("c[a0.x+n]@aL-loop", "mova a0.x, c0\ndefi i0, 4, 0, 1, 0\nloop aL, i0\nmov r0, c[a0.x + 3]\nendloop", "", "?")
            if m == "vs_3_0":
                pa("v[aL]", "defi i0, 4, 0, 1, 0\nloop aL, i0\nmov r0, v[aL]\nendloop", "dcl_texcoord1 v1")
                pa("v[aL+n]", "defi i0, 4, 0, 1, 0\nloop aL, i0\nmov r0, v[aL + 1]\nendloop", "dcl_texcoord1 v1\ndcl_texcoord2 v2", "y")
                pa("v[a0.x]", "mova a0.x, c0\nmov r0, v[a0.x]", "dcl_texcoord1 v1", "?")
                pa("o[aL]", "defi i0, 4, 0, 1, 0\nloop aL, i0\nmov o[aL + 1], r0\nendloop", "dcl_texcoord1 o1\ndcl_texcoord2 o2")
                pa("o[a0.x]", "mova a0.x, c0\nmov o[a0.x + 1], r0", "dcl_texcoord1 o1\ndcl_texcoord2 o2", "?")
        if v and m[3] != "1" or m == "vs_1_1":
            mv = "mov a0.x, c0.x" if m == "vs_1_1" else "mova a0.x, c0"
            pa("c[a0.x+n]@ctab-covering", "// ctab: Arr float 8 4 matrix_rows float 4 4\n" + mv + "\nmov r0, c[a0.x + 8]")
            pa("c[a0.x+n]@ctab-other-regs", "// ctab: Other float 0 1 vector float 1 4\n" + mv + "\nmov r0, c[a0.x + 8]")
            pa("c[a0.x+n]@ctab-straddle", "// ctab: A float 8 2 vector float 1 4\n// ctab: B float 10 2 vector float 1 4\n" + mv + "\nmov r0, c[a0.x + 8]")
        if m in ("vs_2_0", "vs_2_x", "vs_3_0", "ps_3_0"):
            pa("c[aL+n]@ctab-other-regs", "// ctab: Other float 0 1 vector float 1 4\ndefi i0, 4, 0, 1, 0\nloop aL, i0\nmov r0, c[aL + 8]\nendloop")
        if m in ("ps_2_x", "ps_3_0"):
            pa("c[aL+n]", "defi i0, 4, 0, 1, 0\nloop aL, i0\nmov r0, c[aL + 3]\nendloop", "", "y" if m == "ps_3_0" else "?")
            if m == "ps_3_0":
                pa("v[aL]", "defi i0, 4, 0, 1, 0\nloop aL, i0\nmov r0, v[aL]\nendloop", "dcl_color0 v0\ndcl_color1 v1")
    return C

def extra_rows(m, add):
    """comparison-control variants, max register indices, vFace forms"""
    v = isvs(m); ps1 = (not v) and m[3] == "1"; sm2x = m in ("vs_2_x", "vs_3_0", "ps_2_x", "ps_3_0")
    if sm2x:
        for cc in ("gt", "lt", "ge", "le", "eq", "ne"):
            add(m, "opcode", f"if_{cc}", shell(m, f"if_{cc} r1.x, r2.x\nmov r0, c0\nendif"))
            add(m, "opcode", f"break_{cc}", shell(m, f"defi i0, 4, 0, 1, 0\nrep i0\nadd r0, r0, c0\nbreak_{cc} r1.x, r2.x\nendrep"))
            add(m, "opcode", f"setp_{cc}", shell(m, f"setp_{cc} p0, r1, r2\n(p0) mov r0, c0"))
        add(m, "opcode", "callnz@p0", shell(m, "setp_gt p0, r1, r2\ncallnz l0, p0.x\n@E@\nret\nlabel l0\nmov r0, c0\nret"), "?")
        add(m, "opcode", "if@p0", shell(m, "setp_gt p0, r1, r2\nif p0.x\nmov r0, c0\nendif"), "?")
        add(m, "srcmod", "not@if.p0", shell(m, "setp_gt p0, r1, r2\nif !p0.x\nmov r0, c0\nendif"), "?")
    mx = {"vs_1_1": ("c95", "r11", "v15"), "vs_2_0": ("c255", "r11", "v15"), "vs_2_x": ("c255", "r11", "v15"), "vs_3_0": ("c255", "r31", "v15"),
          "ps_1_1": ("c7", "r1", "v1"), "ps_1_2": ("c7", "r1", "v1"), "ps_1_3": ("c7", "r1", "v1"), "ps_1_4": ("c7", "r5", "v1"),
          "ps_2_0": ("c31", "r11", "v1"), "ps_2_x": ("c31", "r11", "v1"), "ps_3_0": ("c223", "r31", "v9")}[m]
    c, r, vv = mx
    add(m, "regtype", "c_max=" + c, shell(m, f"mov r0, {c}"))
    if not (ps1 and m != "ps_1_4"): add(m, "regtype", "r_max=" + r, shell(m, f"mov {r}, c0\nmov r0, {r}"))
    if v or ps1 or m == "ps_2_0" or m == "ps_2_x": pass
    if v: add(m, "regtype", "v_max=" + vv, shell(m, f"mov r0, {vv}", decls=f"dcl_texcoord1 {vv}"))
    elif m == "ps_3_0": add(m, "regtype", "v_max=" + vv, shell(m, f"mov r0, {vv}", decls=f"dcl_texcoord1 {vv}"))
    if not ps1 and m[3] != "1" or m in ("vs_2_0", "vs_2_x", "vs_3_0"):
        if m not in ("ps_2_0",):
            add(m, "regtype", "b15", shell(m, "defb b15, true\nif b15\nmov r0, c0\nendif"))
            add(m, "regtype", "i15", shell(m, "defi i15, 4, 0, 1, 0\nrep i15\nadd r0, r0, c0\nendrep"))
    if m == "ps_3_0":
        add(m, "regtype", "vFace.x", shell(m, "mov r0, vFace.x", decls="dcl vFace"))
        add(m, "regtype", "vFace@cmp", shell(m, "cmp r0, vFace, r1, r2", decls="dcl vFace"))
        add(m, "regtype", "vPos.xy", shell(m, "mov r0.xy, vPos.xy", decls="dcl vPos.xy"))

def more_rows(m, add):
    v = isvs(m); sm2p = m[3] != "1"
    if not v and sm2p:
        lg = "y" if m == "ps_3_0" else "?"
        add(m, "srcmod", "neg@texld.coord", shell(m, "texld r0, -r1, s0", decls="dcl_2d s0"), lg)
        add(m, "srcmod", "abs@texld.coord", shell(m, "texld r0, r1_abs, s0", decls="dcl_2d s0"), "?")
        add(m, "srcmod", "swizzle@texld.coord", shell(m, "texld r0, r1.yxzw, s0", decls="dcl_2d s0"), lg)
        add(m, "srcmod", "swizzle@texld.sampler-src", shell(m, "texld r0, r1, s0.xyzw", decls="dcl_2d s0"), "?")
    if m in ("vs_2_0", "vs_2_x", "vs_3_0", "ps_2_x", "ps_3_0"):
        add(m, "opcode", "if@temp-written-in-branch-only", shell(m, "defb b0, true\nif b0\nmov r5, c0\nendif\nmov r0, r5"), "?")
        add(m, "opcode", "rep@temp-written-in-loop", shell(m, "defi i0, 4, 0, 1, 0\nrep i0\nmov r5, c0\nendrep\nmov r0, r5"), "?")
        add(m, "opcode", "call@label-used-in-and-out-of-loop", shell(m, "defi i0, 4, 0, 1, 0\ncall l0\nloop aL, i0\ncall l0\nendloop\n@E@\nret\nlabel l0\nadd r0, r0, c0\nret"), "?")
    if m in ("ps_1_1", "ps_1_2", "ps_1_3"):
        add(m, "opcode", "tex@ctab-cube-sampler", shell(m, "// ctab: s0 sampler 0 1 object samplercube 1 1\ntex t0\nmov r0, t0"), "y")
        add(m, "opcode", "tex@ctab-volume-sampler", shell(m, "// ctab: s0 sampler 0 1 object sampler3d 1 1\ntex t0\nmov r0, t0"), "y")
    if m == "ps_1_4":
        add(m, "opcode", "texld@ctab-cube-sampler", shell(m, "// ctab: s0 sampler 0 1 object samplercube 1 1\ntexld r0, t0\n"), "y")
        add(m, "opcode", "texld@ctab-volume-sampler", shell(m, "// ctab: s0 sampler 0 1 object sampler3d 1 1\ntexld r0, t0\n"), "y")

def dz_dw(k): return k in ("dz", "dw")
