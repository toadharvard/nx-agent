"""sm_legacy.py - resolves the 168 legal=? rows of the previous census (render/total/census/sm_ops.tsv) against sm_table facts.
status: legal | illegal | conflict | unreachable-doc.  Illegal rows carry the documented validator rule (-> sm_illegal.tsv)."""
import os, sys
S1 = "MS:dx9-graphics-reference-asm-"
def legacy_rows(cdir, models, ops):
    sys.path.insert(0, os.path.abspath(cdir))
    import sm_cases
    out, conf = [], []
    def R(m, k, f, st, src, rule=""): out.append(dict(model=m, kind=k, feature=f, status=st, source=src, rule=rule))
    inmodel = lambda st, key, m: m in ops.get("%s:%s" % (st, key), {}).get("models", [])
    for m, k, f, text, lg in sm_cases.cases():
        if lg != "?": continue
        st = m[:2]; base = f.split("@")[0]
        if k == "opcode" and f in ("lit", "dst", "expp", "logp", "sgn", "m4x4", "m4x3", "m3x4", "m3x3", "m3x2"):
            if inmodel(st, f, m): R(m, k, f, "legal", ops["%s:%s" % (st, f)]["source"])
            else: R(m, k, f, "illegal", S1 + "ps-instructions-ps-%s (no %s in the instruction set); vkd3d d3dbc.c ps_opcode_table agrees; wine decoder table unconstrained (silent)" % (m[5:], f), "CreatePixelShader: opcode %s not in the %s instruction set" % (f, m))
        elif f == "breakc": R(m, k, f, "legal", ops["vs:break_gt"]["source"] + " (breakc = break_comp)")
        elif f.startswith("call@label-used"): R(m, k, f, "legal", S1 + "vs-instructions-vs-3-0 call (aL rule only when the subroutine reads aL; this one does not)")
        elif f in ("callnz@p0", "if@p0"): R(m, k, f, "legal", ops["%s:%s" % (st, "callnz_pred" if f[0] == "c" else "if_pred")]["source"])
        elif f in ("if@temp-written-in-branch-only", "rep@temp-written-in-loop"):
            R(m, k, f, "unreachable-doc", "MS ps-registers-ps-1-x states the uninitialised-temp validator rule for ps_1_x only; sm2/3 pages are silent")
        elif k == "srcmod" and base in ("abs", "absneg") and "texld" not in f:
            R(m, k, f, "illegal", S1 + "ps/vs-registers-modifiers-source (abs: 3_0/3_sw only); wine asmparser.c check_abs_srcmod agrees", "source modifier %s not valid in %s" % (base, m))
        elif k == "srcmod" and base in ("bias", "biasneg", "sign", "signneg", "comp", "x2", "x2neg"):
            R(m, k, f, "illegal", S1 + "ps-registers-modifiers-source ('ps_2_0 and Above': only negate/abs); wine check_legacy_srcmod agrees", "legacy source modifier %s invalid in %s" % (base, m))
        elif k == "srcmod" and base == "not":
            R(m, k, f, "illegal", S1 + "if-bool---vs/ps: syntax 'if bool' has no NOT; '!' only on predicate register", "NOT modifier on b# invalid")
        elif f == "not@if.p0": R(m, k, f, "legal", S1 + "if-pred---ps/vs: 'if [!]pred.replicateSwizzle'")
        elif k == "srcmod" and f.endswith("@texld.coord"):
            if m == "ps_3_0": R(m, k, f, "legal", S1 + "texld---ps-2-0 ps_3_0: only src1 forbids modifiers; swizzle allowed on src0")
            else: R(m, k, f, "illegal", S1 + "texld---ps-2-0 (ps_2_0/ps_2_x: src0 'no modifier or swizzle')", "texld src0 modifier/swizzle invalid in %s" % m)
        elif f == "swizzle@texld.sampler-src":
            if m == "ps_3_0": R(m, k, f, "legal", S1 + "texld---ps-2-0 (ps_3_0: swizzle allowed on src1)")
            else: R(m, k, f, "illegal", S1 + "texld---ps-2-0 (ps_2_x: src1 no modifier or swizzle)", "texld sampler operand swizzle invalid in %s" % m)
        elif f == "pp@mov": R(m, k, f, "illegal", S1 + "vs-instructions-modifiers (vs: only _sat); wine check_ps_dstmod agrees", "_pp invalid in vertex shaders")
        elif f == "sat@mov":
            if m == "vs_3_0": R(m, k, f, "legal", S1 + "vs-instructions-modifiers (_sat: 3_0/3_sw)")
            else: R(m, k, f, "illegal", S1 + "vs-instructions-modifiers (_sat: 3_0/3_sw only)", "_sat invalid in %s (wine accepts: silent)" % m)
        elif f in ("shift_x8", "shift_d8"): R(m, k, f, "illegal", S1 + "ps-instructions-modifiers-ps-1-x (_x8/_d4/_d8: 1_4 only)", "result shift %s invalid in %s" % (f[6:], m))
        elif f == "centroid@dcl_texcoord": R(m, k, f, "legal", S1 + "ps-instructions-modifiers-ps-2-0 (_centroid: 2_0..3_0)")
        elif f == "c[aL+n]" and m == "ps_2_x": R(m, k, f, "illegal", S1 + "ps-registers-ps-2-x (c# RelAddr N); wine ps_2_x_reg_allowed CONST reladdr FALSE agrees", "relative addressing of c# invalid in ps_2_x")
        elif f == "c[a0.x+n]@aL-loop": R(m, k, f, "legal", S1 + "vs-registers-vs-2-0/2-x/3-0 (c# RelAddr a0/aL)")
        elif f == "o[a0.x]": R(m, k, f, "illegal", S1 + "vs-registers-vs-3-0 (o# RelAddr aL only)", "o# relative addressing only through aL")
        elif f == "v[a0.x]":
            R(m, k, f, "conflict", "MS vs-registers-vs-3-0 table: v# RelAddr 'a0/aL'; MS vs-3-0 page 'Indexing Registers': v# indexed 'using the loop counter register (aL)'")
            conf.append(dict(kind="legacy", id="vs_3_0:v[a0.x]", note="MS table says a0/aL, MS prose says aL only; wine allows any (reladdr flag)"))
        elif k == "usage":
            R(m, k, f, "legal", S1 + "dcl-usage---ps / dcl-usage-input-register---vs: usage may be any D3DDECLUSAGE value, index 0..15")
        else: R(m, k, f, "unreachable-doc", "no rule found for %s/%s" % (k, f))
    return dict(rows=out, conflicts=conf)
