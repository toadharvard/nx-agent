#!/usr/bin/env python3
"""Gate "sm_link" (fnvnx-gate/1): every shipped gamedata shader translates, compiles and links on the pinned Mesa 25.2.8.
    GATE_OUT=<dir> GATE_CACHE=<dir> python3 -I ports/fnv-nx/render/total/gate_sm_link.py
Run in the checkout root (verify/verdict.py does). Writes GATE_OUT/sm_link.json; exit 0 iff green, 2 red.
The rule is render/shaders/total/SM_LINK_RULE.md. This gate computes nothing itself: the certificates are the outputs of the hermetic run
stages of the product roots render/shaders/sm_link_stage (stage certificates), sm_link_prog (link certificates) and sm_link_sum (their
completeness-checked summary), built by verify/recipe.py in GATE_CACHE; the summary is read through recipe.resolve_input
{"stage": "ports/fnv-nx/render/shaders/sm_link_sum:run:summary", "path": "summary.json"}, the one reader of a stage output.
Universe = the stage certificates (entry x translator flag set) plus the link certificates (program set); red = the summary's `red`
(refusals, not-ok stages, signature disagreements, link failures, missing or unexpected certificates). Green iff universe > 0 and red = 0."""
import json, os, re, sys

REPO = os.getcwd()
sys.path.insert(0, os.path.join(REPO, "ports", "fnv-nx", "verify"))
import recipe  # noqa: E402
import gate_build  # noqa: E402

ROOT = "ports/fnv-nx/render/shaders/sm_link_sum"


CHECKER = "ports/fnv-nx/render/shaders/sm_link"


def producers(cache):
    """The symbols the checker links from OTHER roots, derived from the link record of the checker's own build (elffacts `references`: the definer of every resolved undefined reference of a product object, from ld's cross
    reference table) and its symbol table (type): [{symbol, kind: function|data, root}] sorted by symbol. A reference counts iff its referencer is a unit of the checker's own root (label 00-own) and its definer is an
    object of another root of the link (/objs/<NN-root path with __ for />[@variant]/...). Nothing is listed by name."""
    res, _ = gate_build.ensure(CHECKER, cache, REPO)
    facts = json.load(open(res["elf_facts"]["path"]))
    ty = {x["name"]: x["type"] for x in facts["symbols"]}
    out = {}
    for key, refs in facts["references"].items():
        if not key.startswith("00-own:"):
            continue
        for r in refs:
            m = re.match(r"/objs/\d+-([^/@]+)(?:@[^/]*)?/", r["definer"])
            if not m or m.group(0).startswith("/objs/00-own/"):
                continue
            kind = {"FUNC": "function", "OBJECT": "data"}.get(ty.get(r["symbol"]))
            if kind is None:
                raise ValueError("producer %s has symbol type %r" % (r["symbol"], ty.get(r["symbol"])))
            out[r["symbol"]] = {"symbol": r["symbol"], "kind": kind, "root": m.group(1).replace("__", "/")}
    return [out[k] for k in sorted(out)]


def main():
    out, cache = os.environ["GATE_OUT"], os.environ["GATE_CACHE"]
    err, s = "", {}
    try:
        path, _ = recipe.resolve_input(ROOT, {"stage": ROOT + ":run:summary", "path": "summary.json"}, REPO, cache)
        s = json.load(open(path))
        if s.get("format") != "fnvnx-sm-link-summary/2":
            raise ValueError("unknown summary format %r" % s.get("format"))
    except (ValueError, OSError, KeyError) as x:
        err = str(x)[:400]
    d = s.get("details", {})
    prod = []
    if not err:
        try:
            prod = producers(cache)
        except (ValueError, OSError, KeyError, recipe.RecipeError) as x:
            err = "producers: " + str(x)[:300]
    unc = d.get("link_set_programs_uncovered", 0)   # programs with a non-empty link set L: not certified (the draw path translates them per pair)
    universe = d.get("stage_certificates", 0) + d.get("programs", 0) + unc + d.get("table_rows", 0)   # the gamedata set plus every row of the GL backend's internal program table
    red = (s.get("red", 1) + unc) if not err else 1
    green = not err and universe > 0 and red == 0
    res = {"format": "fnvnx-gate/1", "name": "sm_link", "status": "green" if green else "red", "universe": universe,
           "producers": prod, "universe_hash": s.get("universe_hash", ""), "universe_members": s.get("universe", []), "red": red, "certified": universe - (red if not err else universe), "details": d,
           "detail": ("error: " + err) if err else
                     "%d unique bytecodes (%d VS, %d PS), %d stage certificates (%d not ok, %d signature disagreements), %d link programs "
                     "(%d linked, %d failed), %d missing, %d unexpected, %d programs with a link set L not covered, %d pairs rejected; internal program table: %d rows, %d linked, %d failed, %d bad; universe %s" % (
                         d.get("unique_bytecodes", 0), d.get("vs", 0), d.get("ps", 0), d.get("stage_certificates", 0), d.get("stage_not_ok", 0),
                         d.get("signature_disagreements", 0), d.get("programs", 0), d.get("linked", 0), d.get("link_fail", 0),
                         d.get("missing_certificates", 0), d.get("unexpected_certificates", 0), unc, d.get("pairs_rejected", 0),
                         d.get("table_rows", 0), d.get("table_linked", 0), d.get("table_fail", 0), d.get("table_bad", 0), s.get("universe_hash", "")[:16])}
    with open(os.path.join(out, "sm_link.json"), "w") as f:
        f.write(json.dumps(res, sort_keys=True, indent=1) + "\n")
    return 0 if green else 2


if __name__ == "__main__":
    sys.exit(main())
