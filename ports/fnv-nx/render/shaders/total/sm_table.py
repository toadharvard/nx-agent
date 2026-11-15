#!/usr/bin/env python3
"""sm_table.py - generate sm_table.json (the machine-readable SM1-3 language definition), sm_illegal.tsv, sm_legacy.tsv and fnvnx_sm_rows.h.
  python3 -I sm_table.py --docs DIR --wine DIR --recipe build.json --out DIR   (recipe step of this root: the reference trees are its externals)
Opcode model sets: MS version tables (per instruction page); cross-checked against wine wined3d shader_sm1.c (the runtime's own SM1-3 parser: set, operand counts);
registers: MS register tables vs wine d3dcompiler asmparser tables.  Disagreements -> `conflicts`; facts nobody states -> `undetermined`."""
import re, argparse, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sm_docs, sm_xref
from sm_rules import *
from sm_rules2 import *
from sm_legacy import legacy_rows

S1 = MS
MOD_ALL = set(ALL)
def stage(m): return m[:2]

def op_defs():
    d = {}
    for dd in (ALU, FLOW, TEX2, TEX1, SETP): d.update(dd)
    return d

def single_mask_ops(cache):
    """the mnemonics MS's vs masking page says require a single write mask: the sentence 'Certain instructions require the destination register
    to use a single write mask: exp, expp, log, logp, pow, rcp, and rsq.' (read from the page, not listed here)"""
    t = sm_docs.get("dx9-graphics-reference-asm-vs-registers-modifiers-masking", cache)
    m = re.search(r"require the destination register to use a single write mask:([^.]*)\.", t)
    if not m: raise SystemExit("the masking page no longer holds the single write mask sentence")
    return sorted(w for w in re.findall(r"[a-z0-9_]+", m.group(1)) if w != "and")

def build(a):
    D, ops = op_defs(), {}
    enum = {k.upper(): v for k, v in sm_xref.wine_ops(a.wine)[1].items()}; enum["TEXLD"] = enum["TEX"]   # TEX and TEXLD share token 0x42
    wine, _ = sm_xref.wine_ops(a.wine)
    cache = a.docs
    def put(st, key, num, models, src, shape):
        e = dict(shape); e.update(num=num, opnum=enum[num.upper()] if num.upper() in enum else None, models=[m for m in ALL if m in models and stage(m) == st],
                 source=src)
        ops["%s:%s" % (st, key)] = e
    for stem, variants in PAGES.items():
        for st in ("vs", "ps"):
            page = stem if "---" in stem else "%s---%s" % (stem, st)
            if "---" in stem and not page.endswith("ps-1-4") and not page.endswith("ps-2-0"): continue
            if "---" in stem and st == "vs": continue
            try: vt = sm_docs.version_table(page, cache)
            except Exception: vt = None
            if not vt: continue
            nvt = {l.lower().replace(" ", "_"): v for l, v in vt.items()}
            for key, label in variants:
                label = label.lower().replace(" ", "_")
                if label not in nvt: continue
                vt = nvt
                keys = [key.replace("*", c) for c in CMPS] if key.endswith("*") else [key]
                for k in keys:
                    shp = dict(D[k]); put(st, k, shp["num"], set(vt[label]) & MOD_ALL, "%s%s (instruction page '%s', version table)" % (MS, "instructions/" + page, label), shp)
    # decl instructions: not operand-shaped; one op per MS page
    for st, page, key, num in (("vs", "dcl-usage-input-register---vs", "dcl_usage", "DCL"), ("ps", "dcl-usage---ps", "dcl_usage", "DCL"),
                               ("ps", "dcl---ps", "dcl", "DCL"), ("vs", "dcl-samplertype---vs", "dcl_samplertype", "DCL"), ("ps", "dcl-samplertype---ps", "dcl_samplertype", "DCL"),
                               ("vs", "def---vs", "def", "DEF"), ("ps", "def---ps", "def", "DEF"), ("vs", "defb---vs", "defb", "DEFB"), ("ps", "defb---ps", "defb", "DEFB"),
                               ("vs", "defi---vs", "defi", "DEFI"), ("ps", "defi---ps", "defi", "DEFI")):
        vt = sm_docs.version_table(page, cache)
        put(st, key, num, set(next(iter(vt.values()))) & MOD_ALL, MS + "instructions/" + page + " (version table)", dict(d=None, s=[], decl=True, tm=key))
    for (st, key), (mods, src) in MANUAL_MODELS.items(): put(st, key, D[key]["num"], set(mods), src, dict(D[key]))
    # ---- (stage, opcode number): MS per-model lists against wined3d's decoder table (dlls/wined3d/shader_sm1.c, the runtime's own SM1-3 parser) ----
    # CLAUDE.md "Where the Microsoft page is narrower": MS lists a model -> legal (MS states it); MS silent (a model its list omits, or no page) and the
    # runtime parser accepts (an entry whose version bounds admit the model; no bound = every model: decoding is the acceptance, nothing else on the path
    # rejects the opcode) -> legal, the union; the parser rejects (explicit bounds exclude the model) and MS is silent -> illegal, citing both.
    WINE = "wine-9.0:dlls/wined3d/shader_sm1.c"
    RULE = "CLAUDE.md: Where the Microsoft page is narrower (Microsoft silent or narrower, Wine 9.0 accepts: the universe is the union)"
    conflicts, xr, resolved = [], {}, []
    def wcite(st, n): return ["%s:%s_opcode_table:%d" % (WINE, st, ln) for ln in sm_xref.LINES.get((st, n), [])]
    def wrec(st, n, what): return dict(file="dlls/wined3d/shader_sm1.c", line=(sm_xref.LINES.get((st, n)) or [0])[0], what=what)
    def page_of(e): return e["source"].split(" (")[0].replace(MS, "desktop-src/direct3dhlsl/dx9-graphics-reference-asm-") + ".md"
    by = {}
    for k, e in ops.items():
        if e.get("opnum") is not None: by.setdefault((k[:2], e["opnum"]), []).append((k, e))
    for (st, n), ents in sorted(by.items()):
        ms = set().union(*[set(e["models"]) for _, e in ents]); w = set(sm_xref.models_of(wine.get((st, n), []), st)); uni = ms | w
        xr[(st, n)] = dict(ms=sorted(ms, key=ALL.index), wine=sorted(w, key=ALL.index), status="agree" if uni == ms else "union")
        if uni != ms:
            add = sorted(uni - ms, key=ALL.index); k0, e0 = ents[0]
            conflicts.append(dict(kind="opcode_models", id="%s:%d" % (st, n), decision="union", rule=RULE, ms=dict(page=page_of(e0), section="instruction page version table (lists %s)" % ", ".join(sorted(ms, key=ALL.index))),
                                  wine=wrec(st, n, "%s_opcode_table entry accepts %s as well" % (st, ", ".join(add))), cite=wcite(st, n), models_added=add,
                                  note="MS per-model list is silent on %s; Wine 9.0's decoder accepts them; universe uses the UNION" % ", ".join(add)))
            for k, e in ents: e["models"] = sorted(set(e["models"]) | {m for m in add}, key=ALL.index); e["models_wine_added"] = add
    for k, e in ops.items():
        if e.get("opnum") is not None: e["xref"] = xr[(k[:2], e["opnum"])]["status"]
    # opcodes the decoder lists for a stage but MS has no page for: the sibling stage's page gives the shape (operands); MS is silent on the stage
    for (st, n), w in sorted(wine.items()):
        if (st, n) not in by and n < 0xFFFC:
            wm = sm_xref.models_of(w, st)
            sib = [(k, e) for k, e in list(ops.items()) if e.get("opnum") == n and k[:2] != st]
            if not (wm and sib): continue
            for k, e in sib:
                ne = dict(e); ne.update(models=wm, source=WINE + ":%s_opcode_table:%d (MS has no %s page for %s: silence)" % (st, (sm_xref.LINES.get((st, n)) or [0])[0], st, k.split(":")[1]),
                                        models_wine_added=wm, xref="union"); ops["%s:%s" % (st, k.split(":")[1])] = ne
            conflicts.append(dict(kind="opcode_models", id="%s:%d" % (st, n), decision="union", rule=RULE, ms=dict(page="none: no %s instruction page for %s" % (st, "/".join(k.split(":")[1] for k, _ in sib)), section="the %s pages' version tables" % st),
                                  wine=wrec(st, n, "%s_opcode_table entry accepts %s" % (st, ", ".join(wm))), cite=wcite(st, n), models_added=wm,
                                  note="MS has no %s page for this opcode (silence); Wine 9.0's decoder accepts it; universe uses the UNION" % st))
    # operand counts vs wine shader_sm1.c (total operands dst+src, model by model, only where the entry exists)
    for k, e in ops.items():
        if e.get("decl") or e.get("opnum") is None: continue
        for m in e["models"]:
            mine = (1 if e["d"] else 0) + len(e["s3"] if "s3" in e and m.endswith("3_0") else e["s"])
            for nm, tab, sub in (("wine", wine, 0),):
                for (nd, ns, lo, hi) in tab.get((k[:2], e["opnum"]), []):
                    if sm_xref.in_range(m, lo, hi) and nd + ns != mine and m not in e.setdefault("opcount_diff", {}):
                        e["opcount_diff"][m] = dict(mine=mine, **{nm: nd + ns})
    # registers: wine tables
    wr = sm_xref.wine_regs(a.wine)
    regx = []     # wine's tables are lenient (shared vs_2_0/2_x, ps_2_0 temps 32): a conflict is a MS register wine lacks or wine's count below MS's
    keys = {"oD": "oD", "oT": "oT", "oPos": "rastout", "oFog": "rastout", "oPts": "rastout", "vPos": "misc", "vFace": "misc"}
    for m in ALL:
        tab = wr[sm_xref.WINE_TABLE[m]]
        for r, spec in REG[m].items():
            wk = keys.get(r, r); n = spec["n"]; nmin = n if isinstance(n, int) else n["min"]
            if wk not in tab: regx.append((m, r, "count_ms=%s absent in wine table" % (n,))); continue
            wn = tab[wk][0]
            if wk == "rastout": bad = wn < 3
            elif wk == "misc": bad = wn < 2 if m == P30 else False
            else: bad = wn is not None and wn < nmin and r not in ("l", "oC")
            if bad: regx.append((m, r, "count_ms=%s wine=%s" % (n, wn)))
    for m, r, why in regx:
        conflicts.append(dict(kind="register", id="%s:%s" % (m, r), note=why, ms_src=REG_SRC[m], wine="asmparser.c %s" % sm_xref.WINE_TABLE[m]))
    # ---- decisions that no per-opcode rule yields: each cites the Microsoft page line and the Wine 9.0 file line; build() reads both lines and
    # fails if the quoted phrase is not on the cited line of the pinned mirror (the citation is checked, not trusted) ----
    D6 = "dlls/wined3d/shader_sm1.c"; G = "dlls/wined3d/glsl_shader.c"; DS = "desktop-src/direct3dhlsl/"; RA = "dx9-graphics-reference-asm-"
    UN = RULE; IL = "CLAUDE.md: Where the Microsoft page is narrower (Microsoft states the opposite: Microsoft holds)"
    DEC = [
     ("vs_a0_src", "illegal", None, DS + RA + "vs-registers-address.md", 25, "cannot be read by a vertex shader", "line 25: the address register cannot be read by a vertex shader; it is used only for relative addressing of a constant register",
      "a0 as a direct ALU source operand (vs_1_1..vs_3_0): MS says the address register cannot be read by a vertex shader. Excluded from universe.", None),
     ("vs_bi_alu", "illegal", None, DS + RA + "vs-registers-constant-integer.md", 16, "used only by", "line 16: constant integer registers are used only by loop and rep (b#: see the constant-boolean page, flow control only)",
      "i# as ALU source in vs: MS says constant integer registers are used only by loop and rep. Excluded (same rule for b#).", None),
     ("mov_a0_vs2", "illegal", None, DS + "mova---vs.md", 31, "dst must be", "line 31: for vs_2_0 and above the destination a0 is written by mova (mov to a0 is documented for vs_1_1 only)",
      "mov to a0 (instead of mova) in vs_2_0+: the MS mova page says dst must be a0. Excluded.", None),
     ("rcp_mask", "illegal", None, DS + RA + "vs-registers-modifiers-masking.md", 38, "single write mask", "line 38: exp, expp, log, logp, pow, rcp and rsq need a single write mask (vs); multi-component masks are illegal",
      "MS vs masking page: exp/expp/log/logp/pow/rcp/rsq need a single-component write mask; ps pages silent; the ps rows use the same masks.", None),
     ("vs1_0_ps1_0", "union", "included", "none: no vs_1_0/ps_1_0 page in the D3D9 asm reference (instruction pages start at vs_1_1/ps_1_1)", 0, "", "every instruction version table",
      "vs_1_0/ps_1_0 via Create*Shader: not in any MS page of the D3D9 asm reference. Included as 1_1 grammar (superset).",
      (D6, 543, "WINED3D_SHADER_VERSION(3, 0)", "shader_sm1_init: any version <= 3.0 is accepted after the type check (546-566), so vs_1_0 and ps_1_0 parse")),
     ("temp_uninit_flow", "union", "included", DS + RA + "ps-registers-ps-1-x.md", 45, "has not been written by a previous instruction", "temporary register: uninitialised-read validator rule, ps_1_x only; sm2/3 pages are silent",
      "read of a temp written only inside if/rep: MS states the uninitialised-read validator rule for ps_1_x only; sm2/3 silent. Included.",
      (D6, 593, "shader_sm1_read_src_param", "shader_sm1_read_src_param (593-608) parses a temp source with no written-before check; wined3d has no uninitialised-temp check")),
    ]
    CONF = [
     ("vs:sgn@vs_3_0", "operand_count", DS + "sgn---vs.md", 42, "| sgn", "Syntax (three sources in every version) and Remarks, version table (line 42)",
      "MS sgn---vs syntax has 3 sources in all versions; wined3d shader_sm1.c: sgn has 1 source in 3_0; universe has both forms",
      (D6, 254, "WINED3D_SM1_OP_SGN", "vs_3_0 SGN has one source (line 253 keeps the three-source form for vs_2_0..2_x); the universe keeps both forms")),
     ("ps_3_0:vFace_src", "legacy", DS + RA + "ps-registers-ps-3-0.md", 30, "The face register (vFace)", "Face register (line 30): condition of setp_comp, if_comp or break_comp only",
      "MS ps_3_0 register page: vFace only as a condition of setp_comp, if_comp or break_comp; Wine 9.0 (glsl_shader.c:2873-2886 reads MISCTYPE 1 as vFace in any source operand) accepts it as a source of every instruction, and the shipped PS e92b2db2 (cmp r0.x, vFace, c1.x, c1.y) uses it so; universe uses the UNION",
      (G, 2873, "WINED3DSPR_MISCTYPE", "MISCTYPE 1 is vFace as any source operand (2873-2886)")),
     ("pair:vs_3_0+ffp_ps", "pairing", DS + "shader-model-3.md", 16, "you may not use vs", "opening paragraph: for hardware shaders, vs_3_0 and ps_3_0 may not be used with the fixed function pipeline (an application restriction; the page names no runtime answer)",
      "the Microsoft page restricts the application and is silent on what the runtime does when a vs_3_0 vertex shader is drawn with the fixed-function pixel stage; Wine 9.0 draws it (L6 decision): the pair stays in the universe and is linked by the varying completion rule (fnvnx_dk_layout.h); universe uses the UNION",
      (G, 10366, "ffp_shader", "set_glsl_shader_program: with a vertex shader of major < 4 and no pixel shader, shader_glsl_find_ffp_fragment_shader supplies the fixed-function fragment program; shader_glsl_generate_vs3_rasterizer_input_setup completes the unwritten varyings")),
    ]
    def mline(page, n): return open(os.path.join(a.docs, page) if os.path.exists(os.path.join(a.docs, page)) else os.path.join(a.docs, "desktop-src", "direct3dhlsl", os.path.basename(page)), encoding="utf8").read().split("\n")[n - 1]
    def wcheck(f, n, tok):
        t = open(os.path.join(a.wine, f), encoding="utf8").read().split("\n")
        assert any(tok in l for l in t[n - 1:n + 14]), "wine citation %s:%d lacks %r" % (f, n, tok)
    undet = []
    for id_, dec, rows, page, line, quote, sect, note, wine_ in DEC:
        if line: assert quote in mline(page, line), "MS citation %s:%d lacks %r" % (page, line, quote)
        r = dict(id=id_, decision=dec, rule=UN if dec == "union" else IL, note=note, ms=dict(page=page, section=sect), rows="illegal" if dec == "illegal" else rows)
        if wine_: wcheck(wine_[0], wine_[1], wine_[2]); r["wine"] = dict(file=wine_[0], line=wine_[1], what=wine_[3])
        undet.append(r)
    for id_, kind, page, line, quote, sect, note, wine_ in CONF:
        assert quote in mline(page, line), "MS citation %s:%d lacks %r" % (page, line, quote)
        wcheck(wine_[0], wine_[1], wine_[2])
        conflicts.append(dict(id=id_, kind=kind, decision="union", rule=UN, note=note, ms=dict(page=page, section=sect), wine=dict(file=wine_[0], line=wine_[1], what=wine_[3])))
    models = {m: dict(stage=stage(m), major=int(m[3]), minor=1 if m[5] == "x" else int(m[5]), creatable=True, regs=REG[m], regs_source=REG_SRC[m],
                      srcmods=SRCMOD[m], dstmods=DSTMOD[m], swizzle=SWZ[m], dst_regs=DST_OK[m], src_regs=SRC_OK[m], flow=FLOW_LIM.get(m)) for m in ALL}
    excl = {m: dict(creatable=False, source=MS + "software-shaders: *_sw are 'only supported by software vertex processing and/or a reference device'; this device is a hardware (HAL-like) device") for m in ("vs_2_sw", "vs_3_sw", "ps_2_sw", "ps_3_sw")}
    excl.update({m: dict(creatable="closed-world", included_in_universe=True, source="not in MS asm reference; see undetermined.vs1_0_ps1_0") for m in ("vs_1_0", "ps_1_0")})
    ill = []
    res = legacy_rows(a.legacy_dir, models, ops, single_mask_ops(a.docs), a.docs)
    return dict(schema=1, ref_commits=dict(wine=a.wine_commit, msdocs=a.docs_commit), models=models, excluded_models=excl, opcodes=ops, srcmod_source=SRCMOD_SRC, sat_except_vs=SAT_EXCEPT_VS,
                usages=USAGES, usage_source=DCL_SRC, sampler_types={k: v for k, v in SAMPLER_TYPES.items()}, caps=CAPS, env_default=ENV_DEFAULT,
                writemask=dict(general="any subset in xyzw order (MS ps-registers-modifiers-write-mask / vs-registers-modifiers-masking)",
                               vs_outputs={"oFog": ["", ".x"], "oPts": ["", ".x"], "oPos": [""], "oT": ["", ".x", ".xy", ".xyz"], "note": "oFog/oPts '.x' conflicts: vs page 'must use only one mask', ps page 'no explicit mask'"},
                               ps14="arbitrary r,g,b,a order from 1_4; ps_1_1-1_3: .rgba .rgb .a only"),
                swizzle_forms=SWZ_FORMS, instruction_limits=INSTR_LIMITS, conflicts=conflicts + res["conflicts"], undetermined=undet, resolved=resolved + res["resolved"],
                legacy=[r for r in res["rows"] if r["status"] != "illegal"], legacy_illegal=[r for r in res["rows"] if r["status"] == "illegal"]), regx

def pins(recipe):
    """ref_commits: the recipe's own external entries (build.json of this root) are the one record of the pinned reference trees"""
    ex = {e["name"]: e for e in json.load(open(recipe))["external"]}
    return {n: "fnv-nx-build %s commit %s tree_sha256 %s" % (ex[n]["ref"], ex[n]["commit"], ex[n]["tree_sha256"]) for n in ("msdocs-win32-d3d9", "wine-9.0-d3d9")}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", required=True); ap.add_argument("--wine", required=True); 
    ap.add_argument("--recipe", required=True, help="build.json of this root: the external pins"); ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--legacy-dir", dest="legacy_dir", default=HERE)
    a = ap.parse_args(); p = pins(a.recipe); a.docs_commit = p["msdocs-win32-d3d9"]; a.wine_commit = p["wine-9.0-d3d9"]           # wine-9.0 holds the decoder table (wined3d)
    T, regx = build(a)
    tj = os.path.join(a.out, "sm_table.json")
    leg = T["legacy"] + T.pop("legacy_illegal")   # sm_table.json holds the non-illegal legacy rows; sm_illegal.tsv the illegal ones: one record per fact
    json.dump(T, open(tj, "w"), indent=1, sort_keys=True)
    import gen_sm_rows; open(os.path.join(a.out, "inc", "fnvnx_sm_rows.h"), "w").write(gen_sm_rows.render(tj))
    with open(os.path.join(a.out, "sm_legacy.tsv"), "w") as o:
        o.write("model\tkind\tfeature\tstatus\tsource\n")
        for r in leg: o.write("\t".join([r["model"], r["kind"], r["feature"], r["status"], r["source"]]) + "\n")
    extra = [("vs_3_0", "opcode", "sgn@3src", S1 + "sgn---vs (syntax line is version-generic); wine-9.0:dlls/wined3d/shader_sm1.c:vs_opcode_table:254 {SGN,1,2,(3,0)-} (and :253 {SGN,1,4,(2,0)-(2,1)}) fixes vs_3_0 at one source (R2)", "sgn in vs_3_0 takes dst, src0 only"),
             ("ps_2_x", "opcode", "loop", S1 + "ps-instructions-ps-2-x / loop---ps (loop, endloop: 3_0 only)", "loop is not in the ps_2_x instruction set"),
             ("ps_2_x", "opcode", "endloop", S1 + "endloop---ps (3_0 only)", "endloop is not in the ps_2_x instruction set"),
             ("ps_3_0", "addr", "c[aL+n]", S1 + "ps-registers-ps-3-0 (c# RelAddr No); wine ps_3_reg_allowed CONST reladdr FALSE agrees", "relative addressing of c# invalid in ps_3_0 (census had it legal=y)")]
    with open(os.path.join(a.out, "sm_illegal.tsv"), "w") as o:
        o.write("model\tkind\tfeature\tsource\tvalidator_rule\tcreate_shader_result\torigin\n")
        for r in leg:
            if r["status"] == "illegal": o.write("\t".join([r["model"], r["kind"], r["feature"], r["source"], r["rule"], "D3DERR_INVALIDCALL", "census legal=?"]) + "\n")
        for m, k, f, src, rule in extra: o.write("\t".join([m, k, f, src, rule, "D3DERR_INVALIDCALL", "census legal=y"]) + "\n")
    ops = T["opcodes"]; print("opcodes", len(ops), "conflicts", len(T["conflicts"]), "undetermined", len(T["undetermined"]), "resolved", len(T["resolved"]))
    for c in T["conflicts"]: print(" ", c["kind"], c["id"], c.get("note", "")[:90])
    d = [(k, e["opcount_diff"]) for k, e in ops.items() if e.get("opcount_diff")]
    print("opcount diffs", len(d)); [print("  ", k, v) for k, v in d[:40]]

if __name__ == "__main__":
    main()
