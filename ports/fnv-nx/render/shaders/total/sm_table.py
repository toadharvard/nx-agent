#!/usr/bin/env python3
"""sm_table.py - generate sm_table.json (the machine-readable SM1-3 language definition) and sm_illegal.tsv / sm_legacy.tsv.
  python3 -I sm_table.py [--docs DIR] [--wine DIR] [--vkd3d DIR]    (defaults under /home/user/work/l6/ref; needs no network if --docs is warm)
Opcode model sets: MS version tables (per instruction page); cross-checked against wine wined3d + vkd3d d3dbc (set, operand counts);
registers: MS register tables vs wine d3dcompiler asmparser tables.  Disagreements -> `conflicts`; facts nobody states -> `undetermined`."""
import argparse, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sm_docs, sm_xref
from sm_rules import *
from sm_rules2 import *
from sm_legacy import legacy_rows

S1 = MS
REF = "/home/user/work/l6/ref"
MOD_ALL = set(ALL)
def stage(m): return m[:2]

def op_defs():
    d = {}
    for dd in (ALU, FLOW, TEX2, TEX1, SETP): d.update(dd)
    return d

def build(a):
    D, ops = op_defs(), {}
    enum = {k.upper(): v for k, v in sm_xref.wine_ops(a.wine)[1].items()}; enum["TEXLD"] = enum["TEX"]   # TEX and TEXLD share token 0x42
    wine, _ = sm_xref.wine_ops(a.wine); vk, _ = sm_xref.vkd3d_ops(a.vkd3d)
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
    # ---- cross-check (stage, opcode number): MS union vs wine vs vkd3d ----
    conflicts, xr, resolved = [], {}, []
    by = {}
    for k, e in ops.items():
        if e.get("opnum") is not None: by.setdefault((k[:2], e["opnum"]), set()).update(e["models"])
    for (st, n), ms in sorted(by.items()):
        w = set(sm_xref.models_of(wine.get((st, n), []), st)); v = set(sm_xref.models_of(vk.get((st, n), []), st))
        ok = ms == v and ms <= w     # wine shader_sm1.c is a decoder table (min/max 0 = unconstrained): wine wider than MS+vkd3d is silence, not disagreement
        xr[(st, n)] = dict(ms=sorted(ms, key=ALL.index), wine=sorted(w, key=ALL.index), vkd3d=sorted(v, key=ALL.index), status="agree" if ok else "conflict")
        if not ok:
            rec = dict(kind="opcode_models", id="%s:%d" % (st, n), ms=xr[(st, n)]["ms"], wine=xr[(st, n)]["wine"], vkd3d=xr[(st, n)]["vkd3d"])
            if ms <= v and v <= w:    # R1: both decoders (wine: unconstrained = silence; vkd3d: explicit version range) accept every model of MS and vkd3d's range adds models -> legal set = vkd3d's range
                resolved.append(dict(rec, rule="R1", resolution="legal models = vkd3d d3dbc.c range (a superset of the MS table, accepted by the wine decoder); the MS version table is a summary that is narrower than the token decoders; no source states the extra models illegal"))
            else: conflicts.append(dict(rec, note="MS version table disagrees with vkd3d d3dbc.c (or wine lacks a listed model); universe uses the UNION"))
    for k, e in ops.items():
        if e.get("opnum") is not None: e["xref"] = xr[(k[:2], e["opnum"])]["status"]
    # opcodes the two implementations list for a stage but MS has no page for
    for (st, n), w in sorted(wine.items()):
        if (st, n) not in by and n < 0xFFFC:
            ms_models = []; wm = sm_xref.models_of(w, st); vm = sm_xref.models_of(vk.get((st, n), []), st)
            if vm: conflicts.append(dict(kind="opcode_models", id="%s:%d" % (st, n), ms=[], wine=wm, vkd3d=vm, note="no MS instruction page for this opcode in this stage"))
    # operand counts vs wine/vkd3d (total operands dst+src, model by model, only where the entry exists)
    for k, e in ops.items():
        if e.get("decl") or e.get("opnum") is None: continue
        for m in e["models"]:
            mine = (1 if e["d"] else 0) + len(e["s3"] if "s3" in e and m.endswith("3_0") else e["s"])
            for nm, tab, sub in (("wine", wine, 0), ("vkd3d", vk, 0)):
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
    R4 = "R4 closed world: the universe holds every form that no source states illegal; legality of such a form is a validator fact (sm_illegal.tsv lists only forms a source states illegal); the translator's acceptance of each row is checked by sm_run, never decided by it"
    undet = []
    resolved += [
     dict(kind="undetermined", id="vs_a0_src", rule="R4", resolution="rows included (family_srcreg: a0 as ALU source, vs_1_1..vs_3_0). " + R4, evidence="MS vs register tables list a0 R/W; wine asmparser.c allows; no page states it illegal as a source"),
     dict(kind="undetermined", id="vs_bi_alu", rule="R4+R5", resolution="i# as ALU source: rows included (R4: " + R4 + "). b# as ALU source: NO rows (R5: a form whose result no source defines carries no translation obligation; the value a b# read yields as a float4 is stated by MS, wine and vkd3d nowhere; the translator emits a bool-to-vec4 assignment that uam rejects, 3 rows measured). Open item reported to the lead: b# source semantics need a source or an owner decision", evidence="MS vs pages silent (the ps pages' flow-control-only wording is a ps page); wine asmparser.c vs_2/vs_3_reg_allowed list CONSTBOOL/CONSTINT as source types without a value rule"),
     dict(kind="undetermined", id="mov_a0_vs2", rule="R4", resolution="rows included (mov to a0 in vs_2_0..vs_3_0). " + R4, evidence="MS mova page says mova's dst must be a0; no page states mov-to-a0 illegal in vs_2_0+; mov-to-a0 is documented for vs_1_1"),
     dict(kind="undetermined", id="vs1_0_ps1_0", rule="R4", resolution="rows included as the 1_1 grammar (version token 1.0). " + R4, evidence="no MS asm page lists vs_1_0/ps_1_0 and none states them illegal; wine/vkd3d decoders carry ranges from 1_0 (ps texreg2gb {1,0}-{1,3})"),
     dict(kind="undetermined", id="temp_uninit_flow", rule="R4", resolution="rows included (read of a temp written only inside if/rep). " + R4, evidence="MS states the uninitialised-read rule for ps_1_x only; the sm2/3 pages are silent"),
     dict(kind="undetermined", id="rcp_mask", rule="R4", resolution="rows included (single-component masks plus the full mask). " + R4, evidence="MS vs masking page says single component for exp/expp/log/logp/pow/rcp/rsq; ps pages silent; the validator side of a full mask in vs is a CreateVertexShader fact"),
    ]
    resolved.append(dict(kind="operand_count", id="vs:sgn@vs_3_0", rule="R2", resolution="sgn in vs_3_0 has ONE source (dst, src0): the 3-source form is illegal in vs_3_0 (sm_illegal.tsv); vs_2_0/2_x keep dst,src0,src1,src2",
                         evidence="MS sgn---vs: one syntax line for all versions (no per-version operand count); wine shader_sm1.c {SGN,1,2,...(3,0)} and vkd3d d3dbc.c {SGN,1,1,{3,0}} both fix 3_0 at one source, and {2,0}-{2,1} at three. The token decoders are version-exact where the MS syntax line is not"))
    models = {m: dict(stage=stage(m), major=int(m[3]), minor=1 if m[5] == "x" else int(m[5]), creatable=True, regs=REG[m], regs_source=REG_SRC[m],
                      srcmods=SRCMOD[m], dstmods=DSTMOD[m], swizzle=SWZ[m], dst_regs=DST_OK[m], src_regs=SRC_OK[m], flow=FLOW_LIM.get(m)) for m in ALL}
    excl = {m: dict(creatable=False, source=MS + "software-shaders: *_sw are 'only supported by software vertex processing and/or a reference device'; this device is a hardware (HAL-like) device") for m in ("vs_2_sw", "vs_3_sw", "ps_2_sw", "ps_3_sw")}
    excl.update({m: dict(creatable="closed-world", included_in_universe=True, source="not in MS asm reference; see resolved.vs1_0_ps1_0 (R4)") for m in ("vs_1_0", "ps_1_0")})
    ill = []
    res = legacy_rows(a.legacy_dir, models, ops)
    return dict(schema=1, ref_commits=dict(wine=a.wine_commit, vkd3d=a.vkd3d_commit), models=models, excluded_models=excl, opcodes=ops, srcmod_source=SRCMOD_SRC, sat_except_vs=SAT_EXCEPT_VS,
                usages=USAGES, usage_source=DCL_SRC, sampler_types={k: v for k, v in SAMPLER_TYPES.items()}, caps=CAPS, env_default=ENV_DEFAULT,
                writemask=dict(general="any subset in xyzw order (MS ps-registers-modifiers-write-mask / vs-registers-modifiers-masking)",
                               vs_outputs={"oFog": ["", ".x"], "oPts": ["", ".x"], "oPos": [""], "oT": ["", ".x", ".xy", ".xyz"], "note": "oFog/oPts '.x' conflicts: vs page 'must use only one mask', ps page 'no explicit mask'"},
                               ps14="arbitrary r,g,b,a order from 1_4; ps_1_1-1_3: .rgba .rgb .a only"),
                swizzle_forms=SWZ_FORMS, instruction_limits=INSTR_LIMITS, conflicts=conflicts + res["conflicts"], undetermined=undet, resolved=resolved + res["resolved"],
                legacy=res["rows"]), regx

def pin_ext(name, d):
    """a reference tree is the external `name` of render/total/externals.json (fnv-nx-build tools/vkd3d: commit + tree_sha256); a tree that hashes differently is refused"""
    sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "verify")); import cas
    e = next(x for x in json.load(open(os.path.join(HERE, "..", "..", "total", "externals.json")))["externals"] if x["name"] == name)
    h = cas.tree_hash(os.path.abspath(d), skip=(".git",))
    if h != e["tree_sha256"]: raise SystemExit(name + " tree_sha256 %s != externals.json %s" % (h, e["tree_sha256"]))
    return "fnv-nx-build %s commit %s tree_sha256 %s (upstream %s)" % (e["ref"], e["commit"], h, e["upstream"])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default=REF + "/ms/i"); ap.add_argument("--wine", default=REF + "/wine"); ap.add_argument("--vkd3d", default=REF + "/vkd3d")
    ap.add_argument("--legacy-dir", dest="legacy_dir", default=os.path.join(HERE, "../../total/census"))
    a = ap.parse_args(); a.vkd3d_commit = pin_ext("vkd3d", a.vkd3d); a.wine_commit = pin_ext("wine-sm1", a.wine)
    T, regx = build(a)
    json.dump(T, open(os.path.join(HERE, "sm_table.json"), "w"), indent=1, sort_keys=True)
    leg = T["legacy"]
    with open(os.path.join(HERE, "sm_legacy.tsv"), "w") as o:
        o.write("model\tkind\tfeature\tstatus\tsource\n")
        for r in leg: o.write("\t".join([r["model"], r["kind"], r["feature"], r["status"], r["source"]]) + "\n")
    extra = [("vs_3_0", "opcode", "sgn@3src", S1 + "sgn---vs (syntax line is version-generic); wine shader_sm1.c {SGN,1,2,(3,0)-} and vkd3d d3dbc.c {SGN,1,1,{3,0}} fix vs_3_0 at one source (R2)", "sgn in vs_3_0 takes dst, src0 only"),
             ("ps_2_x", "opcode", "loop", S1 + "ps-instructions-ps-2-x / loop---ps (loop, endloop: 3_0 only)", "loop is not in the ps_2_x instruction set"),
             ("ps_2_x", "opcode", "endloop", S1 + "endloop---ps (3_0 only)", "endloop is not in the ps_2_x instruction set"),
             ("ps_3_0", "addr", "c[aL+n]", S1 + "ps-registers-ps-3-0 (c# RelAddr No); wine ps_3_reg_allowed CONST reladdr FALSE agrees", "relative addressing of c# invalid in ps_3_0 (census had it legal=y)")]
    with open(os.path.join(HERE, "sm_illegal.tsv"), "w") as o:
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
