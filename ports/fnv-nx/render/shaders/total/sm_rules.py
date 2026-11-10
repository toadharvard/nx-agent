"""sm_rules.py - curated SM1-3 language facts that are not machine-extractable from the Microsoft pages: per-opcode operand
shapes and the register tables, each with its `source`.  Model sets of opcodes come from the MS version tables
(sm_docs.version_table) and are cross-checked against wine/vkd3d by sm_table.py; nothing here selects by test result."""
MS = "MS:dx9-graphics-reference-asm-"          # learn.microsoft.com/en-us/windows/win32/direct3dhlsl/
V1, V2, V2X, V3 = "vs_1_1", "vs_2_0", "vs_2_x", "vs_3_0"
P11, P12, P13, P14, P20, P2X, P30 = "ps_1_1", "ps_1_2", "ps_1_3", "ps_1_4", "ps_2_0", "ps_2_x", "ps_3_0"
VS, PS, PS1, PS2, SM2 = [V1, V2, V2X, V3], [P11, P12, P13, P14, P20, P2X, P30], [P11, P12, P13, P14], [P20, P2X], [V2, V2X, P20, P2X]
ALL = VS + PS

# ---- operand roles: S src, D dst, Ss scalar-replicate src, Sc const-float src, Sm sampler, B bool, I int, Pp predicate,
# ---- L label, A0 address dst, P0 predicate dst, T ps_1_x texture dst, X ps_1_x texture src (t#), Rr ps_1_4 r# dst
# op: key -> dict(num=D3DSIO name, d=dst role|None, s=[roles], mk=dst mask rule, tm=asm template, ms=[(page stem, row label)], ...)
# mask rules: any | single | xy | xyz | xyz_or_all | x_y_xy | rg | none.  template keys {D} {S0} {S1} {S2} {S3}.
def O(num, d, s, tm, mk="any", **kw): return dict(num=num, d=d, s=s, tm=tm, mk=mk, **kw)
ALU = {k: O(k.upper(), "D", ["S"] * n, k + " {D}, " + ", ".join("{S%d}" % i for i in range(n)))
       for k, n in dict(mov=1, add=2, sub=2, mad=3, mul=2, dp3=2, dp4=2, min=2, max=2, slt=2, sge=2, frc=1, lrp=3, abs=1, crs=2, cmp=3, cnd=3,
                        dsx=1, dsy=1, nrm=1, lit=1, dst=2, dp2add=3).items()}
ALU["mov"]["alu"] = ALU["add"]["alu"] = True
ALU["cnd"]["fix"] = {"ps_1_1": {0: "r0.a"}, "ps_1_2": {0: "r0.a"}, "ps_1_3": {0: "r0.a"}}     # MS cnd---ps: versions 1_1 to 1_3, src0 must be r0.a
ALU["dp3"]["mk_ps1"] = ["", ".xyz"]                                                           # MS ps-registers-modifiers-write-mask: 1_0-1_3 dp3 only .rgb/.rgba
for k in ("rcp", "rsq", "exp", "log", "expp", "logp"): ALU[k] = O(k.upper(), "D", ["Ss"], k + " {D}, {S0}", mk="single")
ALU["pow"] = O("POW", "D", ["Ss", "Ss"], "pow {D}, {S0}, {S1}", mk="single")
ALU["crs"]["mk"] = "xyz"; ALU["frc"]["mk"] = "x_y_xy_vs1"; ALU["dp3"]["mk"] = "any"; ALU["dp2add"]["s"] = ["S", "S", "Ss"]
ALU["nop"] = O("NOP", None, [], "nop"); ALU["nop"]["alu"] = False
ALU["mova"] = O("MOVA", "A0", ["S"], "mova {D}, {S0}", mk="xy_any")
for k, n, mk in (("m4x4", 4, "xyz_or_all"), ("m4x3", 4, "xyz"), ("m3x4", 3, "xyz_or_all"), ("m3x3", 3, "xyz"), ("m3x2", 3, "xy")):
    ALU[k] = O(k.upper(), "D", ["S", "Sc"], k + " {D}, {S0}, {S1}", mk=mk, matrix=int(k[1]))
ALU["sgn"] = O("SGN", "D", ["S", "Tmp", "Tmp"], "sgn {D}, {S0}, {S1}, {S2}", s3=["S"], tm3="sgn {D}, {S0}")         # vs_3_0: sgn dst, src
ALU["sincos"] = O("SINCOS", "D", ["Ss", "Sc", "Sc"], "sincos {D}, {S0}, {S1}, {S2}", mk="x_y_xy", s3=["Ss"], tm3="sincos {D}, {S0}", dr=["r"])   # vs_3_0/ps_3_0: sincos dst, src
SETP = {"setp_" + c: O("SETP", "P0", ["S", "S"], "setp_%s {D}, {S0}, {S1}" % c, mk="any", cmp=c) for c in "gt lt ge le eq ne".split()}
CMPS = "gt lt ge le eq ne".split()
FLOW = {
 "if_bool": O("IF", None, ["B"], "if {S0}\n{BODY}\nendif", needs="defb"),
 "if_pred": O("IF", None, ["Pp"], "if {S0}\n{BODY}\nendif", needs="setp"),
 "else": O("ELSE", None, [], "if {B0}\n{BODY}\nelse\n{BODY2}\nendif", needs="defb"),
 "endif": O("ENDIF", None, [], "if {B0}\n{BODY}\nendif", needs="defb"),
 "loop": O("LOOP", None, ["AL", "I"], "loop aL, {S1}\n{BODY}\nendloop", needs="defi"),
 "endloop": O("ENDLOOP", None, [], "loop aL, {I0}\n{BODY}\nendloop", needs="defi"),
 "rep": O("REP", None, ["I"], "rep {S0}\n{BODY}\nendrep", needs="defi"),
 "endrep": O("ENDREP", None, [], "rep {I0}\n{BODY}\nendrep", needs="defi"),
 "break": O("BREAK", None, [], "rep {I0}\n{BODY}\nbreak\nendrep", needs="defi"),
 "breakp": O("BREAKP", None, ["Pp"], "rep {I0}\n{BODY}\nbreakp {S0}\nendrep", needs="defi+setp"),
 "call": O("CALL", None, ["L"], "call l0\n@E@\nret\nlabel l0\n{BODY}\nret"),
 "callnz_bool": O("CALLNZ", None, ["L", "B"], "callnz l0, {S1}\n@E@\nret\nlabel l0\n{BODY}\nret", needs="defb"),
 "callnz_pred": O("CALLNZ", None, ["L", "Pp"], "callnz l0, {S1}\n@E@\nret\nlabel l0\n{BODY}\nret", needs="setp"),
 "ret": O("RET", None, [], "call l0\n@E@\nret\nlabel l0\n{BODY}\nret"),
 "label": O("LABEL", None, ["L"], "call l{LBL}\n@E@\nret\nlabel l{LBL}\n{BODY}\nret"),
}
for c in CMPS:
    FLOW["if_" + c] = O("IFC", None, ["Sr", "Sr"], "if_%s {S0}, {S1}\n{BODY}\nendif" % c, cmp=c)
    FLOW["break_" + c] = O("BREAKC", None, ["Sr", "Sr"], "rep {I0}\n{BODY}\nbreak_%s {S0}, {S1}\nendrep" % c, needs="defi", cmp=c)
# texture-sample ops for sm2/3 (sampler s#, coordinate src); ps_1_x texture-addressing ops have fixed t# dst/src roles
TEX2 = {k: O("TEXLD", "Dr", ["S", "Sm"], "%s {D}, {S0}, {S1}" % k, mk="none", tex=True, ctrl=c) for k, c in (("texld", 0), ("texldp", 1), ("texldb", 2))}
TEX2["texldl"] = O("TEXLDL", "Dr", ["S", "Sm"], "texldl {D}, {S0}, {S1}", mk="none", tex=True)
TEX2["texldd"] = O("TEXLDD", "Dr", ["S", "Sm", "S", "S"], "texldd {D}, {S0}, {S1}, {S2}, {S3}", mk="none", tex=True)
TEX2["texkill"] = O("TEXKILL", None, ["S"], "texkill {S0}")
TEX1 = {  # ps_1_x: scaffold lines in tm; `st` = sampler type the instruction fixes (None = bound texture decides)
 "tex": O("TEX", "T", [], "tex {D}", tex=True, st=None), "texcoord": O("TEXCOORD", "T", [], "texcoord {D}"),
 "texkill1": O("TEXKILL", "T", [], "texkill {D}"),
 "texld14": O("TEXLD", "Rr", ["X"], "texld {D}, {S0}", tex=True, st=None), "texcrd": O("TEXCOORD", "Rr", ["X"], "texcrd {D}, {S0}"),
 "texbem": O("TEXBEM", "T", ["X"], "tex t0\ntexbem {D}, {S0}", tex=True, st="2d"), "texbeml": O("TEXBEML", "T", ["X"], "tex t0\ntexbeml {D}, {S0}", tex=True, st="2d"),
 "texreg2ar": O("TEXREG2AR", "T", ["X"], "tex t0\ntexreg2ar {D}, {S0}", tex=True, st="2d"),
 "texreg2gb": O("TEXREG2GB", "T", ["X"], "tex t0\ntexreg2gb {D}, {S0}", tex=True, st="2d"),
 "texreg2rgb": O("TEXREG2RGB", "T", ["X"], "tex t0\ntexreg2rgb {D}, {S0}", tex=True, st="2d"),
 "texdp3": O("TEXDP3", "T", ["X"], "tex t0\ntexdp3 {D}, {S0}"), "texdp3tex": O("TEXDP3TEX", "T", ["X"], "tex t0\ntexdp3tex {D}, {S0}", tex=True, st="1d"),
 "texm3x2pad": O("TEXM3x2PAD", "T", ["X"], "tex t0\ntexm3x2pad t1, t0\ntexm3x2tex t2, t0"),
 "texm3x2tex": O("TEXM3x2TEX", "T", ["X"], "tex t0\ntexm3x2pad t1, t0\ntexm3x2tex {D}, t0", tex=True, st="2d"),
 "texm3x2depth": O("TEXM3x2DEPTH", "T", ["X"], "tex t0\ntexm3x2pad t1, t0\ntexm3x2depth {D}, t0"),
 "texm3x3pad": O("TEXM3x3PAD", "T", ["X"], "tex t0\ntexm3x3pad t1, t0\ntexm3x3pad t2, t0\ntexm3x3tex t3, t0", tex=True, st="3d_or_cube"),
 "texm3x3tex": O("TEXM3x3TEX", "T", ["X"], "tex t0\ntexm3x3pad t1, t0\ntexm3x3pad t2, t0\ntexm3x3tex {D}, t0", tex=True, st="3d_or_cube"),
 "texm3x3": O("TEXM3x3", "T", ["X"], "tex t0\ntexm3x3pad t1, t0\ntexm3x3pad t2, t0\ntexm3x3 {D}, t0", st="3d_or_cube"),
 "texm3x3spec": O("TEXM3x3SPEC", "T", ["X", "Sc"], "tex t0\ntexm3x3pad t1, t0\ntexm3x3pad t2, t0\ntexm3x3spec {D}, t0, {S1}", tex=True, st="3d_or_cube"),
 "texm3x3vspec": O("TEXM3x3VSPEC", "T", ["X"], "tex t0\ntexm3x3pad t1, t0\ntexm3x3pad t2, t0\ntexm3x3vspec {D}, t0", tex=True, st="3d_or_cube"),
 "texdepth": O("TEXDEPTH", "Rr", [], "texdepth {D}"), "bem": O("BEM", "Rr", ["S", "S"], "texld r0, t0\ntexld r2, t1\nbem {D}, {S0}, {S1}", mk="rg"),
 "phase": O("PHASE", None, [], "texld r0, t0\nphase\nmov r0, r0"),
}
# MS instruction page -> (op key, row label) ; stage from the page suffix.  sm2/3 key is the asm mnemonic.
PAGES = {  # page stem (without ---vs/---ps) -> [(op key, row label in the version table)]
 **{k: [(k, k)] for k in ("abs add crs dp3 dp4 exp frc log lrp m3x2 m3x3 m3x4 m4x3 m4x4 mad max min mov mul nop nrm pow rcp rsq sub sincos "
                         "cmp cnd dp2add dsx dsy dst expp lit logp mova sge sgn slt").split()},
 "break": [("break", "break")], "break-comp": [("break_*", "break_comp")], "break-p": [("breakp", "breakp")], "breakp": [("breakp", "breakp")],
 "call": [("call", "call")], "callnz-bool": [("callnz_bool", "callnz bool")], "callnz-pred": [("callnz_pred", "callnz pred")],
 "else": [("else", "else")], "endif": [("endif", "endif")], "endloop": [("endloop", "endloop")], "endrep": [("endrep", "endrep")],
 "if-bool": [("if_bool", "if bool")], "if-comp": [("if_*", "if_comp")], "if-pred": [("if_pred", "if pred")], "label": [("label", "label")],
 "loop": [("loop", "loop")], "rep": [("rep", "rep")], "ret": [("ret", "ret")], "setp-comp": [("setp_*", "setp_comp")],
 "tex": [("tex", "tex")], "texcoord": [("texcoord", "texcoord")], "texcrd": [("texcrd", "texcrd")], "texld---ps-1-4": [("texld14", "texld")],
 "texld---ps-2-0": [("texld", "texld")], "texldb": [("texldb", "texldb")], "texldp": [("texldp", "texldp")], "texldl": [("texldl", "texldl")],
 "texkill": [("texkill", "texkill")], "bem": [("bem", "bem")], "phase": [("phase", "phase")],
 **{k: [(k, k)] for k in ("texbem texbeml texdepth texdp3 texdp3tex texm3x2depth texm3x2pad texm3x2tex texm3x3 texm3x3pad texm3x3spec "
                         "texm3x3tex texm3x3vspec texreg2ar texreg2gb texreg2rgb").split()},
}
# texldd has no parsable version table: MS ps-instructions-ps-2-x / ps-3-0 index lists it as ps_2_x+ (text), cross-checked by wine/vkd3d
MANUAL_MODELS = {("ps", "texldd"): ([P2X, P30], MS + "ps-instructions-ps-2-x (instruction list)")}
