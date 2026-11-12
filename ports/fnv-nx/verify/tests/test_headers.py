#!/usr/bin/env python3
"""Tests for the admit class "headers" (gate_admit.py), the include rule of recipe.py check and gate_headers.py (RECIPE.md "Headers trees"), on the
toy product root of test_recipe.py with the real gates. Plain script: PASS/FAIL lines, exit 0 on pass."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import test_recipe as TR  # noqa: E402
import gate_admit  # noqa: E402
import recipe  # noqa: E402
import verdict  # noqa: E402

T0 = time.time()
FAILED, check = TR.FAILED, TR.check
H = T = TR.T
HDR_ROOT = {"path": T + "hdr", "class": "headers", "gates": ["headers"]}
GC = TR.BASE + "/gcache"
A = '#include "h.h"\nint f_a(void) { return HV; }\n'
FILES = {"toy/a.c": A, "hdr/h.h": "#define HV 3\n", "hdr/unused.h": "#define UNUSED 1\n"}
REC = lambda **kw: TR.mut(**dict(dict(link=None, os_layer=[], constants=None, generated=[], include=[T + "hdr"]), **kw))  # noqa: E731


def mk(name, files=None, rec=None, roots=(HDR_ROOT,)):
    r = TR.make_repo(name, rec=rec or REC(), files=dict(FILES, **(files or {}), **{"toy/constants.tsv": None, "toy/os.S": None}), roots=list(roots))
    return r


def gate(repo, name, out):
    rc, g, _, txt = TR.gate(repo, name, TR.BASE + "/" + out, GC)
    return rc, g, txt


def main():
    r = mk("h-ok", {"hdr/unused.h": None})
    res = recipe.build(r, TR.ROOT, TR.BASE + "/st", TR.BASE + "/o1", ext=TR.BASE + "/ec")
    check("include from a headers tree builds (the unit reads <hdr>/h.h by a quoted include)", res["status"] == "green", res.get("failure"))
    rc, g, txt = gate(r, "headers", "g1")
    check("gate headers: every header read by an admitted unit: green 1/1", rc == 0 and g["universe"] == 1 == g["certified"], (g, txt))
    rc, g, txt = gate(mk("h-unused"), "headers", "g2")
    check("gate headers: a header no unit reads is not certified (counted in the detail)", rc == 2 and g["universe"] == 2 and g["certified"] == 1
          and g["uncertified"] == [T + "hdr/unused.h"] and "1 of 2" in g["detail"], (g, txt))
    rc, g, txt = gate(TR.make_repo("h-none"), "headers", "g3")
    check("gate headers: no headers tree: universe 0, green", rc == 0 and g["universe"] == 0, (g, txt))
    # a root that does not list the tree does not see it: the mirror holds declared files only (no ambient -I)
    res = recipe.build(mk("h-nolist", {"toy/a.c": '#include "../hdr/h.h"\nint f_a(void) { return HV; }\n'}, REC(include=[])), TR.ROOT, TR.BASE + "/st", TR.BASE + "/o2", ext=TR.BASE + "/ec")
    check("a root that does not list the headers tree cannot include it (missing file, red at pp:a.c)", res["status"] == "red" and res["failure"]["stage"] == "pp:a.c", res.get("failure"))
    # include rules of check()
    lib = {"path": T + "lib", "class": "tool", "gates": []}
    rl = TR.make_repo("h-lib", rec=REC(include=[T + "lib"]), files=dict(FILES, **{"lib/x.h": "int x;\n"}), roots=[HDR_ROOT, lib])
    e = recipe.check(REC(include=[T + "lib"]), TR.ROOT, recipe.tracked(rl), recipe.load_admit(rl))
    check("include of another root's own tree (not a headers tree) is refused", any("is not a headers tree" in x for x in e), e)
    e = recipe.check(REC(include=[T + "nowhere"]), TR.ROOT, recipe.tracked(rl), recipe.load_admit(rl))
    check("include of a path in no root and no external is refused", any("not a path inside an admitted root" in x for x in e), e)
    e = recipe.check(REC(include=[T + "hdr"]), TR.ROOT, recipe.tracked(rl), recipe.load_admit(rl))
    check("include of a headers tree is accepted by check()", not e, e)
    # admit: the class
    files = [T + "hdr/h.h", T + "hdr/k.c"]
    bad, errs_ = gate_admit.judge(files, {"format": "fnvnx-admit/1", "roots": [HDR_ROOT]}, ["headers"], lambda p: True)
    check("admit: a unit file inside a headers tree is refused", T + "hdr/k.c" in bad and "unit file" in bad[T + "hdr/k.c"], (bad, errs_))
    for what, ent, want in (("a recipe", dict(HDR_ROOT, recipe=T + "hdr/build.json"), "no recipe"), ("no gate headers", dict(HDR_ROOT, gates=[]), "required gate headers")):
        bad, errs_ = gate_admit.judge([T + "hdr/h.h"], {"format": "fnvnx-admit/1", "roots": [ent]}, ["headers"], lambda p: True)
        check("admit: a headers root with %s is refused" % what, any(want in x for x in errs_), (bad, errs_))
    bad, errs_ = gate_admit.judge([T + "hdr/h.h", T + "hdr/x.h"], {"format": "fnvnx-admit/1", "roots": [HDR_ROOT]}, ["headers"], lambda p: True)
    check("admit: headers tree of header files only is covered by its entry", not bad and not errs_, (bad, errs_))
    # a stub-like header: its function body is built into the unit that includes it, so objstub judges it (header code is never exempt)
    STUB = '#include <stdlib.h>\n#define HV 3\nvoid hstub(void) { abort(); }\n'
    rs = mk("h-stub", {"hdr/h.h": STUB, "hdr/unused.h": None})
    rc, g, txt = gate(rs, "objstub", "g4")
    check("a stub-like function in a header (abort path) is judged by objstub through the unit that includes it: red", rc == 2 and "T2" in g["detail"], (g, txt))
    print("%d failed, wall %.1f s" % (len(FAILED), time.time() - T0))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
