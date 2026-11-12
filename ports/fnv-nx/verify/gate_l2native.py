#!/usr/bin/env python3
"""gate_l2native - the shared part of the L2 per-root gates on the NATIVE bodies of the exe's functions (gates `namegate` and `r7`).
Not a gate itself (gates.json names gate_namegate.py and gate_r7gate.py). Library only.

Universe of those two gates (VERDICT.md class 8): the rows of link/gate_names.py rows() (resp. link/gate_r7.py rows()) that concern a native body of an
exe function, in each admitted product root with "target": "guest", "cxx_origin": "native" and a `game` directory. A root without `game` has no
exe-function bodies: universe 0 for it.
The static facts of the join, all outputs of L1's own chain (gate_contract.chain, no new source):
  - addrmap.tsv (the link stage's map): row (object basename, symbol) -> exe address. A symbol with no row has no exe address, so no exe function
    stands on it. The object of the game unit UNIT (root-relative path) is `src_` + UNIT with `/` -> `_` + `.o` (gate_contract.assemble).
  - bodysel.tsv (contract/bodysel.py, run by the chain): exe address -> native | lifted. A symbol is a NATIVE exe-function body iff its address is native.
  - a unit is NATIVE-BEARING iff at least one of its symbols is a native exe-function body (r7 judges the types of the unit: the types a native body
    uses are among the unit's complete records, `.fnvnx.layout`; nothing narrower is claimed).
Inputs of the certificate: the root's game objects (staged as `<UNIT with / -> _>.o`, the stem convention of link/unitpairs.py), the cl reference objects
`<msvc external>/obj/u<N>.obj` (the dir contract_inputs.json `msvc` names, the same one n1 reads) and the root's units.json. A missing input is a
red item `<root>:inputs` naming it and its owner, never skipped. Stated limit: no `--n3` is passed by the bodysel stage of gate_contract.py yet, so
bodysel gives no NATIVE and the universe is 0 until L1/L2 wire the N3 verdicts (reported, not worked around)."""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "link"))
sys.path.insert(0, os.path.join(HERE, "..", "contract"))
import gate_build  # noqa: E402
import gate_contract as gc  # noqa: E402
import recipe  # noqa: E402


def object_name(unit):
    """addrmap.tsv's object basename of the game unit UNIT (gate_contract.assemble)."""
    return "src_" + unit.replace("/", "_") + ".o"


def stem_of(unit):
    return unit.replace("/", "_")


def load_addrmap(path):
    """{(object basename, symbol): exe address} (contract/objfacts.py load_addrmap_tsv)."""
    import objfacts
    return objfacts.load_addrmap_tsv(path)


def load_bodysel(path):
    """{exe address: 'native' | 'lifted'} of bodysel.tsv."""
    out = {}
    for ln in open(path):
        p = ln.rstrip("\n").split("\t")
        out[int(p[0], 16)] = p[1]
    return out


def native_symbols(amap, verdicts):
    """-> (set of (object basename, symbol) that are a native exe-function body)."""
    return {k for k, a in amap.items() if verdicts.get(a) == "native"}


def select(rows, stem_of_row, sym_of_row, native):
    """Rows of the universe: the rows whose (stem, symbol) is a native body, and every row of a native-bearing unit that has no symbol (an error row).
    ROWS: library rows; STEM_OF_ROW(row) -> stem; SYM_OF_ROW(row) -> symbol or ''. NATIVE: native_symbols()."""
    bearing = {o for o, _ in native}
    out = []
    for r in rows:
        o = "src_" + stem_of_row(r) + ".o"
        s = sym_of_row(r)
        if (o, s) in native or (not s and o in bearing):
            out.append(r)
    return out


def judge_files(game, objects, cl, units, amap, verdicts, rows_of, select_rows, certified, row_id, root=""):
    """The rule on files: GAME the root-relative game dir, OBJECTS [{unit, object}] of the root, CL the dir of cl objects, UNITS units.json path, AMAP the
    addrmap, VERDICTS {address: native|lifted}. -> (universe, certified, ids, detail)."""
    native = native_symbols(amap, verdicts)
    stage = tempfile.mkdtemp(prefix="l2native-")
    try:
        game = game.strip("/") + "/"
        for o in objects:
            if o["unit"].startswith(game) and o["unit"].endswith(recipe.CXX_EXT):
                os.symlink(o["object"], os.path.join(stage, stem_of(o["unit"]) + ".o"))
        rows = rows_of([stage], [cl], units)
        uni = select_rows(rows, native)
        bad = [r for r in uni if not certified(r)]
        return len(uni), len(uni) - len(bad), [root + ":" + row_id(r) for r in bad], \
            "%d native-body rows of %d, %d native symbols" % (len(uni), len(rows), len(native))
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def judge_native_root(gate, repo, cache, root, rows_of, select_rows, certified, row_id):
    """-> (universe, certified, ids, detail) of one native guest root. ROWS_OF(gcc_dir, cl_dir, units) -> rows; SELECT_ROWS(rows, native) -> universe rows;
    CERTIFIED(row) -> bool; ROW_ID(row) -> str. Never raises on a missing input: a red item naming it."""
    try:
        rec = recipe.load_recipe(repo, recipe.load_admit(repo), root)
        if not isinstance(rec.get("game"), str) or not rec["game"]:
            return 0, 0, [], "root has no game directory: no exe-function bodies (universe 0)"
        with gc.chain("bodysel", repo, cache, root) as (doc, rec, objs, out, rp, ext, w):
            if not os.path.isfile(w + "/bodysel.tsv"):
                raise gc.Red("bodysel per-symbol verdict missing (L1): %s/bodysel.tsv" % w)
            cl = os.path.join(gc.P2(ext, doc["msvc"]), "obj")
            if not os.path.isdir(cl):
                raise gc.Red("the cl reference objects %s/obj are missing (owner L2): the pristine MSVC objects of the msvc external" % doc["msvc"])
            return judge_files(rec["game"], objs["objects"], cl, rp["units"], load_addrmap(rp["addrmap"]), load_bodysel(w + "/bodysel.tsv"),
                               rows_of, select_rows, certified, row_id, root)
    except gc.Skip as e:
        return 0, 0, [], str(e)
    except gc.Red as e:
        return 1, 0, [root + ":inputs"], str(e)[:600]
    except (recipe.RecipeError, recipe.BuildRed, OSError, KeyError, ValueError) as e:
        return 1, 0, [root + ":inputs"], "input error: %s" % (str(e)[:300] or type(e).__name__)


def select_units(rows, stem_of_row, native):
    """r7: every row of a native-bearing unit (the unit's complete records bound the types a native body of it uses)."""
    bearing = {o for o, _ in native}
    return [r for r in rows if "src_" + stem_of_row(r) + ".o" in bearing]


def run_gate(name, judge_root):
    """The main() of a per-root gate over the native guest roots (gate_argorder.py's shape): writes GATE_OUT/<name>.json, returns the exit code.
    JUDGE_ROOT(repo, cache, root) -> (universe, certified, ids, detail)."""
    import json
    import gate_argorder
    repo = "."
    cache, tmp = os.environ.get("GATE_CACHE"), None
    if not cache:
        cache = tmp = tempfile.mkdtemp(prefix="gate-%s-" % name)
    try:
        u = c = 0
        ids, why = [], []
        roots = gate_argorder.native_guest(repo)
        for r in roots:
            ru, rc, rids, rd = judge_root(repo, cache, r)
            u, c, ids = u + ru, c + rc, ids + rids
            if rc != ru:
                why.append("%s: %s" % (r, rd))
        detail = "; ".join(why) or ("%d native guest roots" % len(roots) if roots else "no native guest root (universe 0)")
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    res = {"format": "fnvnx-gate/1", "name": name, "status": "green" if u == c else "red", "universe": u, "certified": c,
           "uncertified": sorted(ids)[:50], "detail": detail[:900]}
    with open(os.path.join(os.environ["GATE_OUT"], name + ".json"), "w") as f:
        json.dump(res, f, sort_keys=True)
    print("%s: %s %d/%d %s" % (name, res["status"], c, u, detail[:200]))
    return 0 if u == c else 2
