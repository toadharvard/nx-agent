#!/usr/bin/env python3
"""Gate "flaglive" (fnvnx-gate/1, VERDICT.md class F-lift: a lifted flag write omitted where a path can read it; proof/FLAGLIVE.md rule 2): the pack's flag-drop list is a derived fact, recomputed and compared.
It reads the declared outputs FLAGLIVE and PACK of the lift/liftsrc root as published in GATE_CACHE (never runs a producer).
    python3 -I gate_flaglive.py        cwd = checkout root, env GATE_OUT, GATE_CACHE; writes GATE_OUT/flaglive.json
The producer is the flaglive check stages of lift/liftsrc (tools/flagcheck.py; tools/liftgen.py --flagcheck): for every function of every shard, from a fresh lift through the lifter's own code path, the dead-flag
fixpoint is recomputed; the pack's flagdrop.tsv must equal it at every instruction, and the generated text must hold exactly the flag stores of F minus D in every Form block. A difference fails the stage, so the root's
FLAGLIVE output exists only when there is none. Universe: every function of the pack (the rows of PACK/entries.tsv, by the one rule that lists the pack's entries; no list is kept here). Certified: the functions the check
covered. An item is certified iff ALL hold: the FLAGLIVE output is a fnvnx-flaglive/1 record with 0 differences; its entry count equals the universe; its pack-list sha256 equals the sha256 of PACK/flagdrop.tsv
(the list that the check read is the list the pack carries). Red when either output is not in the cache (named), and a pack with no function. A root with no lifted product root: universe 0, green.
Proof: the three conditions say that the check ran over exactly the pack's functions and the pack's own list. Assumptions: ASM-001 (the compiler), as for every gate."""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_build  # noqa: E402

TREE = "ports/fnv-nx"
ADMIT = TREE + "/verify/admit.json"
SRC_ROOT = TREE + "/lift/liftsrc"


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def universe_of(pack):
    """The functions of the pack: the non-comment rows of entries.tsv."""
    with open(os.path.join(pack, "entries.tsv")) as f:
        return sum(1 for l in f if l.strip() and not l.startswith("#"))


def judge(doc, pack):
    """(universe, certified, [reason]) of the check record DOC against the pack directory PACK."""
    try:
        u = universe_of(pack)
        psha = sha(os.path.join(pack, "flagdrop.tsv"))
    except OSError as e:
        return 0, 0, ["the pack output is incomplete: %s" % e]
    why = []
    if not isinstance(doc, dict) or doc.get("format") != "fnvnx-flaglive/1":
        return u, 0, ["flaglive.json is not a fnvnx-flaglive/1 record"]
    if doc.get("differences") != 0:
        why.append("%r differences" % (doc.get("differences"),))
    if doc.get("entries") != u:
        why.append("the check covered %r functions, the pack has %d" % (doc.get("entries"), u))
    if doc.get("pack_list_sha256") != psha:
        why.append("the checked list is not the pack's flagdrop.tsv")
    return u, 0 if why else doc["entries"], why


def main():
    out, cache = os.environ["GATE_OUT"], os.environ["GATE_CACHE"]
    with open(ADMIT) as f:
        roots = [r for r in json.load(f).get("roots", []) if isinstance(r, dict)]
    if not [r for r in roots if r.get("class") == "product" and isinstance(r.get("cxx_origin"), dict)]:
        u, c, why, status, detail = 0, 0, [], "green", "no lifted root: universe 0"
    else:
        try:
            fp = os.path.join(gate_build.published_output(cache, SRC_ROOT, "FLAGLIVE")[0], "flaglive.json")
            pack = gate_build.published_output(cache, SRC_ROOT, "PACK")[0]
            with open(fp) as f:
                doc = json.load(f)
            u, c, why = judge(doc, pack)
        except (LookupError, OSError, ValueError) as e:
            u, c, why = 1, 0, [str(e)]
        status = "green" if not why and u else "red"
        detail = "; ".join(why) or "%d of %d functions checked, 0 differences" % (c, u)
    doc = {"format": "fnvnx-gate/1", "name": "flaglive", "status": status, "universe": u, "certified": c, "uncertified": ([SRC_ROOT] if why else []), "detail": detail[:2000], "errors": why[:20]}
    with open(os.path.join(out, "flaglive.json"), "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("flaglive: %s %d/%d %s" % (status, c, u, detail[:300]))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
