#!/usr/bin/env python3
"""Tests for the per-unit flags of verify/recipe.py (`unit_flags`, `unit_facts`, RECIPE.md "Per-unit flags"): fpfast of the pi plugin stands on a
game C++ unit iff the unit's /fp:fast fact in units.json is true. Plain script, needs root (sandbox) and the compilers of verify/env.json."""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_plugins as tp
import test_guest as tg
from test_plugins import BASE, ROOT, T, REAL, check, git, w, make_repo, errs, build, REC, SRC
import recipe

F = recipe.FPFAST_FLAG
FACT = lambda *fast, other=(): json.dumps({"units": [{"source": u, "flags": ["/fp:fast"]} for u in fast] + [{"source": u, "flags": ["/O2"]} for u in other]})  # noqa: E731
FILES = {ROOT + "/g/a.cc": SRC, ROOT + "/g/b.cc": "int b() { return 2; }\n", ROOT + "/c.cc": "int c() { return 3; }\n"}


def rec_with(flags, facts="units.json", **kw):
    r = copy.deepcopy(REC)
    r["game"] = "g"
    if flags is not None:
        r["unit_flags"] = flags
    if facts is not None:
        r["unit_facts"] = facts
    r.update(kw)
    return r


def main():
    # --- shape (no compiler)
    ok = rec_with({"g/a.cc": [F]})
    f = dict(FILES, **{ROOT + "/units.json": FACT("g/a.cc")})
    r1 = make_repo("u1", ok, "native", f)
    check("unit_flags on a game C++ unit with unit_facts is valid", errs(r1, ok) == [], errs(r1, ok))
    for name, rec, want in (
            ("unit_flags on a unit outside game is red", rec_with({"c.cc": [F]}), "not a C++ unit under game"),
            ("unit_flags with another flag is red", rec_with({"g/a.cc": ["-O3"]}), "not a unit flag"),
            ("unit_flags without unit_facts is red", rec_with({"g/a.cc": [F]}, facts=None), "without unit_facts"),
            ("unit_facts that is no tracked file is red", rec_with(None, facts="nope.json"), "neither a tracked file"),
            ("unit_flags without the plugin fnvnx_pi is red", rec_with({"g/a.cc": [F]}, plugins=[p for p in tp.PLUGS if p["name"] != "fnvnx_pi"]), "fnvnx_pi")):
        e = errs(r1, rec)
        check(name, any(want in x for x in e), e)
    # --- a native guest root with game must declare unit_facts
    pin = json.load(open(os.path.join(REAL, T + "verify/guest_compiler.json")))
    gr = tg.guest_rec(pin["external"])
    gr["game"] = "g"
    g = tg.guest_repo("u2", gr, pin, files=f)
    e = errs(g, gr)
    check("native guest root with game and no unit_facts is red", any("needs unit_facts" in x for x in e), e)
    # --- the rule on the facts
    check("fact true, flag set: no error", recipe.fpfast_errors(ok, FACT("g/a.cc")) == [])
    check("fact true, flag missing: red", any("fact without" in x for x in recipe.fpfast_errors(rec_with({}), FACT("g/a.cc"))))
    check("fact false, flag set: red", any("without a /fp:fast fact" in x for x in recipe.fpfast_errors(ok, FACT(other=["g/a.cc"]))))
    check("unit without an entry has no fact: flag set is red, no flag is fine", recipe.fpfast_errors(rec_with({}), FACT()) == [] and
          recipe.fpfast_errors(ok, FACT()) != [])
    check("units.json that is not units.json is red", recipe.fpfast_errors(ok, "[]") != [])
    # --- builds: the verdict of the facts, and the flag is in the CC key
    ra, _ = build(r1, "UA")
    check("build with the flag where the fact is true: green", ra["status"] == "green", ra.get("failure"))
    bad = make_repo("u3", rec_with({}), "native", f)
    rb, _ = build(bad, "UB")
    check("build where the fact is true and the flag missing: red, stage unit_facts", rb["status"] == "red" and rb["failure"]["stage"] == "unit_facts", rb.get("failure"))
    none = make_repo("u4", rec_with({}), "native", dict(FILES, **{ROOT + "/units.json": FACT()}))
    rc, _ = build(none, "UC")
    check("build with no fact and no flag: green, the CC key of g/a.cc differs from the flagged build", rc["status"] == "green" and
          set(ra["stage_keys"]) != set(rc["stage_keys"]) and ra["pp_keys"] == rc["pp_keys"], (rc.get("failure"), len(ra["stage_keys"]), len(rc["stage_keys"])))


if __name__ == "__main__":
    main()
    print("FAILED: %s" % tp.FAILED if tp.FAILED else "all passed")
    sys.exit(1 if tp.FAILED else 0)
