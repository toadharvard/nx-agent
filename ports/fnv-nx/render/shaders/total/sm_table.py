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

REF = "/home/user/work/l6/ref"
MOD_ALL = set(ALL)
def stage(m): return m[:2]

def op_defs():
    d = {}
    for dd in (ALU, FLOW, TEX2, TEX1, SETP): d.update(dd)
    return d

def build(a):
    D, ops = op_defs(), {}
    enum = {k.upper(): v for k, v in sm_xref.wine_ops(a.wine)[1].items()}
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
            for key, label in variants:
                if label not in vt: continue
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
    conflicts, xr = [], {}
    by = {}
    for k, e in ops.items():
        if e.get("opnum") is not None: by.setdefault((k[:2], e["opnum"]), set()).update(e["models"])
    for (st, n), ms in sorted(by.items()):
        w = set(sm_xref.models_of(wine.get((st, n), []), st)); v = set(sm_xref.models_of(vk.get((st, n), []), st))
        ok = ms == w == v
        xr[(st, n)] = dict(ms=sorted(ms, key=ALL.index), wine=sorted(w, key=ALL.index), vkd3d=sorted(v, key=ALL.index), status="agree" if ok else "conflict")
        if not ok: conflicts.append(dict(kind="opcode_models", id="%s:%d" % (st, n), ms=xr[(st, n)]["ms"], wine=xr[(st, n)]["wine"], vkd3d=xr[(st, n)]["vkd3d"],
                                         note="sources disagree on the model set of this opcode; universe uses the UNION"))
    for k, e in ops.items():
        if e.get("opnum") is not None: e["xref"] = xr[(k[:2], e["opnum"])]["status"]
    # opcodes the two implementations list for a stage but MS has no page for
    for (st, n), w in sorted(wine.items()):
        if (st, n) not in by and n < 0xFFFC:
            ms_models = []; wm = sm_xref.models_of(w, st); vm = sm_xref.models_of(vk.get((st, n), []), st)
            if wm or vm: conflicts.append(dict(kind="opcode_models", id="%s:%d" % (st, n), ms=[], wine=wm, vkd3d=vm, note="no MS instruction page for this opcode in this stage"))
    # operand counts vs wine/vkd3d (total operands dst+src, model by model, only where the entry exists)
    for k, e in ops.items():
        if e.get("decl") or e.get("opnum") is None: continue
        for m in e["models"]:
            mine = (1 if e["d"] else 0) + len(e["s"])
            for nm, tab, sub in (("wine", wine, 0), ("vkd3d", vk, 0)):
                for (nd, ns, lo, hi) in tab.get((k[:2], e["opnum"]), []):
                    if sm_xref.in_range(m, lo, hi) and nd + ns != mine and m not in e.setdefault("opcount_diff", {}):
                        e["opcount_diff"][m] = dict(mine=mine, **{nm: nd + ns})
    # registers: wine tables
    wr = sm_xref.wine_regs(a.wine)
    regx = []
    for m in ALL:
        tab = wr[sm_xref.WINE_TABLE[m]]
        keys = {"oD": "oD", "oT": "oT", "oPos": "rastout", "oFog": "rastout", "oPts": "rastout"}
        for r, spec in REG[m].items():
            wk = keys.get(r, r)
            if wk not in tab:
                regx.append((m, r, "ms_only")); continue
            n = spec["n"]; nmin = n if isinstance(n, int) else n["min"]; nmax = n if isinstance(n, int) else n.get("max", n["min"])
            wn = tab[wk][0]
            if wk == "rastout": ok = wn == 3
            else: ok = wn is None or wn in (nmin, nmax) or r in ("l", "oC")
            if not ok: regx.append((m, r, "count_ms=%s wine=%s" % (n, wn)))
        for r, (wn, rel) in tab.items():
            if r not in REG[m] and not (r == "rastout" and "oPos" in REG[m]) and not (r == "misc" and "vPos" in REG[m]) and r != "a" or (r == "a" and "a0" not in REG[m]):
                regx.append((m, r, "wine_only count=%s" % wn))
    for m, r, why in regx:
        if why.startswith("count") or why.startswith("wine_only"):
            conflicts.append(dict(kind="register", id="%s:%s" % (m, r), note=why, ms_src=REG_SRC[m], wine="asmparser.c %s" % sm_xref.WINE_TABLE[m]))
    undet = [
     dict(id="vs_a0_src", note="a0 as a direct ALU source operand (vs_1_1..vs_3_0): MS lists a0 R/W; wine allows; no instruction page says it is a legal source. Excluded from universe.", rows="excluded"),
     dict(id="vs_bi_alu", note="b#/i# as ALU source in vs: MS ps pages say flow-control-only, vs pages are silent. Excluded (same rule as ps).", rows="excluded"),
     dict(id="mov_a0_vs2", note="mov to a0 (instead of mova) in vs_2_0+: MS mova page says dst must be a0; mov-to-a0 only documented for vs_1_1. Excluded.", rows="excluded"),
     dict(id="vs1_0_ps1_0", note="vs_1_0/ps_1_0 via Create*Shader: not in any MS page of the D3D9 asm reference; wine d3d9 has no test, only d3d8. Included as 1_1 grammar (superset).", rows="included"),
     dict(id="temp_uninit_flow", note="read of a temp written only inside if/rep: MS states the uninitialised-read validator rule for ps_1_x only; sm2/3 silent. Included.", rows="included"),
     dict(id="rcp_mask", note="MS vs masking page: exp/expp/log/logp/pow/rcp/rsq need a single-component write mask; ps pages silent; universe uses single-component masks plus full mask. Included.", rows="included"),
    ]
    models = {m: dict(stage=stage(m), major=int(m[3]), minor=1 if m[5] == "x" else int(m[5]), creatable=True, regs=REG[m], regs_source=REG_SRC[m],
                      srcmods=SRCMOD[m], dstmods=DSTMOD[m], swizzle=SWZ[m], dst_regs=DST_OK[m], src_regs=SRC_OK[m], flow=FLOW_LIM.get(m)) for m in ALL}
    excl = {m: dict(creatable=False, source=MS + "software-shaders: *_sw are 'only supported by software vertex processing and/or a reference device'; this device is a hardware (HAL-like) device") for m in ("vs_2_sw", "vs_3_sw", "ps_2_sw", "ps_3_sw")}
    excl.update({m: dict(creatable="undetermined", included_in_universe=True, source="not in MS asm reference; see undetermined.vs1_0_ps1_0") for m in ("vs_1_0", "ps_1_0")})
    ill = []
    res = legacy_rows(a.legacy_dir, models, ops)
    return dict(schema=1, ref_commits=dict(wine=a.wine_commit), models=models, excluded_models=excl, opcodes=ops, srcmod_source=SRCMOD_SRC, sat_except_vs=SAT_EXCEPT_VS,
                usages=USAGES, usage_source=DCL_SRC, sampler_types={k: v for k, v in SAMPLER_TYPES.items()}, caps=CAPS, env_default=ENV_DEFAULT,
                writemask=dict(general="any subset in xyzw order (MS ps-registers-modifiers-write-mask / vs-registers-modifiers-masking)",
                               vs_outputs={"oFog": ["", ".x"], "oPts": ["", ".x"], "oPos": [""], "oT": ["", ".x", ".xy", ".xyz"], "note": "oFog/oPts '.x' conflicts: vs page 'must use only one mask', ps page 'no explicit mask'"},
                               ps14="arbitrary r,g,b,a order from 1_4; ps_1_1-1_3: .rgba .rgb .a only"),
                swizzle_forms=SWZ_FORMS, instruction_limits=INSTR_LIMITS, conflicts=conflicts + res["conflicts"], undetermined=undet,
                legacy=res["rows"]), regx

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default=REF + "/ms/i"); ap.add_argument("--wine", default=REF + "/wine"); ap.add_argument("--vkd3d", default=REF + "/vkd3d")
    ap.add_argument("--legacy-dir", dest="legacy_dir", default=os.path.join(HERE, "../../total/census"))
    a = ap.parse_args(); a.wine_commit = open(os.path.join(a.wine, ".git/HEAD")).read().strip() if os.path.exists(os.path.join(a.wine, ".git/HEAD")) else "?"
    T, regx = build(a)
    json.dump(T, open(os.path.join(HERE, "sm_table.json"), "w"), indent=1, sort_keys=True)
    ops = T["opcodes"]; print("opcodes", len(ops), "conflicts", len(T["conflicts"]), "undetermined", len(T["undetermined"]))
    for c in T["conflicts"]: print(" ", c["kind"], c["id"], c.get("note", "")[:90])
    d = [(k, e["opcount_diff"]) for k, e in ops.items() if e.get("opcount_diff")]
    print("opcount diffs", len(d)); [print("  ", k, v) for k, v in d[:40]]

if __name__ == "__main__":
    main()
