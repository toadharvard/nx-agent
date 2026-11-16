#!/usr/bin/env python3
"""Gate "rawrite" (fnvnx-gate/1): the control-transfer register `cpu::ra` of the lifted runtime is written only by generated code and by the one runtime TU that
defines the native-transfer pair (ruling A, L3a: lift/rt/fnvl.h declares `ra` writable only where FNVL_PRELUDE_H or FNVL_RA_WRITE is defined, otherwise it is const).
    python3 -I gate_rawrite.py     cwd = checkout root, env GATE_OUT, optional GATE_CACHE; writes GATE_OUT/rawrite.json
Rule (class): a guest C/C++ unit of an admitted product root is a WRITER iff its preprocessed facts show write access to ra:
  - the PP stage result unit.ii has a line marker for the file fnvl_prelude.h (the unit includes the prelude), or
  - the macro-fact stage result unit.du (-dU: the macros the unit tests or expands, as `#define NAME VALUE`, or `#undef NAME` when undefined) holds `#define FNVL_RA_WRITE` or
    `#define FNVL_PRELUDE_H`, whether the unit gained them by -D, by its own #define, or through an include.
  A unit that merely includes fnvl.h tests both macros undefined (`#undef`): it is no writer, so every reader of the header is green.
A writer is green iff its source is a generator output (the unit path is a declared output of a `generated` or `run` step of the root's recipe: a lifted pack unit, an
impgen or exitgen bridge), or it is the single hand-written TU that DEFINES (its object's global symbols) both `fnvl_native_transfer` and `fnvl_raise_resume`
(WRITER_SYMBOLS: the pair is the interface fact, not the file's name). A hand-written writer that defines not both is red; a second TU that defines the pair is red too
(the exception is one TU over all product roots). Universe: every C/C++ unit that has a PP result in a product root (one item each) plus the root-level items (recipe invalid,
build red); certified: units with no finding. No product root: universe 0, green. The evidence is the build's own PP and macro-fact stages (gate_build.ensure), so the key of
every fact is the unit's preprocessor key."""
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
from fnvnx.verify import gate_build  # noqa: E402
from fnvnx.verify import recipe  # noqa: E402
from fnvnx.verify import unitstage  # noqa: E402

MACROS = ("FNVL_RA_WRITE", "FNVL_PRELUDE_H")   # the two spellings of write access in lift/rt/fnvl.h
PRELUDE = "fnvl_prelude.h"
WRITER_SYMBOLS = ("fnvl_native_transfer", "fnvl_raise_resume")   # defined together by the one hand-written writer TU


def includes_file(ii, base):
    """True iff the preprocessed text has a line marker (`# N "path" flags`) for a file named BASE."""
    for line in ii.splitlines():
        if line.startswith("# ") and line.count('"') >= 2 and os.path.basename(line.split('"')[1]) == base:
            return True
    return False


def defined_macros(du):
    """{name} of MACROS that the -dU text shows as defined (`#define NAME ...`); an `#undef` line is a test that found it undefined."""
    return {m.group(1) for m in re.finditer(r"^#define (%s)\b" % "|".join(MACROS), du, re.M)}


def is_writer(ii, du):
    """-> [reason] why a unit has write access to ra (empty = not a writer)."""
    why = []
    if includes_file(ii, PRELUDE):
        why.append("includes " + PRELUDE)
    why += ["defines " + m for m in sorted(defined_macros(du))]
    return why


def judge(units):
    """UNITS [{id, ii, du, generated: bool, defines: set of global symbols its object defines}] (all product roots) -> (universe, certified, ids [str]).
    A writer is green iff generated, or it is the one TU that defines every symbol of WRITER_SYMBOLS."""
    ids, pair = [], [u["id"] for u in units if set(WRITER_SYMBOLS) <= set(u.get("defines", ()))]
    if len(pair) > 1:
        ids += ["%s: defines %s, as does %s (the hand-written writer is one TU)" % (i, "+".join(WRITER_SYMBOLS), ", ".join(p for p in pair if p != i)) for i in pair]
    bad = set(pair) if len(pair) > 1 else set()
    for u in units:
        why = is_writer(u["ii"], u["du"])
        if why and not u["generated"] and u["id"] not in pair:
            ids.append("%s: hand-written unit with write access to ra (%s); only generated units and the TU that defines %s may" % (u["id"], ", ".join(why), "+".join(WRITER_SYMBOLS)))
            bad.add(u["id"])
    return len(units), len(units) - len(bad), sorted(ids)


def generated_units(rec):
    """{root-relative unit path} that are generator outputs: the declared outputs of the `generated` and `run` steps of the recipe. This holds for every origin of a root's C++ (recipe.py
    lifted-origin rule: a lifted root's unit must be an output of a step of lifted_by, in the pack form, the pack-root form and the stage form alike), so a lifted pack unit is generator
    output by the one rule, and a tracked file (all_units_of's own sources) never is."""
    return set(recipe.generated_outputs(rec))


def root_units(root, repo, cache):
    """One product ROOT -> ([unit dict for judge], None) or (None, (id, detail)) when its recipe is invalid or its build is red."""
    try:
        res, _ = gate_build.ensure(root, cache, repo)
    except recipe.RecipeError as e:
        return None, (root + ":recipe-invalid", "; ".join(e.errors)[:200])
    if res["status"] == "red":
        return None, (root + ":build-red", "stage %s" % res["failure"]["stage"])
    store = os.path.join(cache, "store")
    rec = recipe.load_recipe(repo, recipe.load_admit(repo), root)
    gen = generated_units(rec)
    objs = {o["unit"]: o["object"] for o in res["objects"]}
    out = []
    for u, k in sorted(res["pp_keys"].items()):
        dk = res["du_keys"].get(u)
        if dk is None:
            return None, (root + ":no-macro-facts:" + u, "unit %s has no macro-fact stage" % u)
        defs = set()
        if u in objs:
            defs = {d["symbol"] for d in recipe.symbols(objs[u], u)[0]}
        out.append({"id": "%s:%s" % (root, u), "generated": u in gen, "defines": defs,
                    "ii": open(unitstage.result_file(store, k, "unit.ii"), errors="replace").read(),
                    "du": open(unitstage.result_file(store, dk, "unit.du"), errors="replace").read()})
    return out, None


def main():
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-rawrite-")
    cache, repo = os.environ.get("GATE_CACHE") or tmp, "."
    try:
        units, items = [], []
        for root in gate_build.product_roots(repo):
            us, bad = root_units(root, repo, cache)
            if bad:
                items.append(bad)
            else:
                units += us
        u, c, ids = judge(units)
        u, ids, detail = u + len(items), [i for i, _ in items] + ids, "; ".join(d for _, d in items) + "; ".join(ids)
        status = "green" if c == u else "red"
    except (OSError, ValueError, KeyError) as e:
        u, c, ids, detail, status = 1, 0, ["input"], "input error: %s" % (str(e)[:300] or type(e).__name__), "red"
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    res = {"format": "fnvnx-gate/1", "name": "rawrite", "status": status, "universe": u, "certified": c, "uncertified": ids, "detail": detail[:900]}
    with open(os.path.join(os.environ["GATE_OUT"], "rawrite.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    print("rawrite: %s %d/%d %s" % (status, c, u, detail[:200]))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
