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
    # one declaration per symbol (gate_headers.twice): the compiler's own list of declarations, per header
    two = {"hdr/unused.h": None, "hdr/h.h": "#define HV 3\nint dup_sym(void);\n", "hdr/h2.h": "int dup_sym(void);\nint only_h2(void);\n",
           "toy/a.c": '#include "h.h"\n#include "h2.h"\nint f_a(void) { return HV + dup_sym(); }\n'}
    rc, g, txt = gate(mk("h-twice", two), "headers", "g5")
    check("gate headers: the same extern declared in two header files is red declared-twice, naming both places", rc == 2 and
          [f["code"] for f in g["findings"]] == ["declared-twice"] and "dup_sym" in g["findings"][0]["id"] and "h2.h" in g["findings"][0]["id"], (g, txt))
    rc, g, txt = gate(mk("h-once", {"hdr/unused.h": None, "hdr/h.h": "#define HV 3\nint one_sym(void);\n",
                                    "toy/a.c": '#include "h.h"\nint f_a(void) { return HV; }\n'}), "headers", "g6")
    check("gate headers: every symbol declared once (and in the include of every consumer): green", rc == 0 and g["findings"] == [], (g, txt))
    rc, g, txt = gate(mk("h-nonself", {"hdr/unused.h": None, "hdr/h.h": "#define HV 3\nuint32_t from_nowhere(void);\n",
                                       "toy/a.c": '#include <stdint.h>\n#include "h.h"\nint f_a(void) { return HV; }\n'}), "headers", "g7")
    check("gate headers: a header that does not parse alone (not self-contained) is red", rc == 2 and "does not parse alone" in g["detail"], (g, txt))
    # nested roots: a headers tree may sit inside a root of another class and owns its files; any other nesting overlaps
    outer = {"path": T + "lay", "class": "tool", "gates": ["selftests"]}
    inner = {"path": T + "lay/include", "class": "headers", "gates": ["headers"]}
    bad, errs_ = gate_admit.judge([T + "lay/x.py", T + "lay/include/a.h"], {"format": "fnvnx-admit/1", "roots": [outer, inner]}, ["headers", "selftests"], lambda p: True)
    check("admit: a headers tree inside a tool root owns its files; the tool root keeps the others", not bad and not errs_, (bad, errs_))
    bad, errs_ = gate_admit.judge([T + "lay/x.py"], {"format": "fnvnx-admit/1", "roots": [outer, dict(inner, **{"class": "tool", "gates": []})]}, [], lambda p: True)
    check("admit: a tool root inside a tool root overlaps", any("overlap" in x for x in errs_), (bad, errs_))
    bad, errs_ = gate_admit.judge([T + "lay/include/a.h"], {"format": "fnvnx-admit/1", "roots": [dict(outer, **{"class": "headers", "gates": ["headers"]}), inner]}, ["headers"], lambda p: True)
    check("admit: a headers tree inside a headers tree overlaps", any("overlap" in x for x in errs_), (bad, errs_))
    bad, errs_ = gate_admit.judge([T + "lay/include/a.h"], {"format": "fnvnx-admit/1", "roots": [outer, dict(inner, gates=[])]}, ["headers"], lambda p: True)
    check("admit: the nested headers tree is judged by its own gates (none named: its file is uncertified)", T + "lay/include/a.h" in bad, (bad, errs_))
    # the include rule resolves the innermost root
    nest = {"path": T + "lay", "class": "tool", "gates": []}
    ne = recipe.check(REC(include=[T + "lay/include"]), TR.ROOT, [T + "lay/include/a.h", T + "lay/x.py"], {TR.ROOT: {"class": "product", "path": TR.ROOT}, T + "lay": nest, T + "lay/include": inner})
    check("recipe check: an include of a headers tree nested in a tool root is accepted (innermost root decides)", not any("not a headers tree" in x for x in ne), ne)
    # a generator's declared inputs: own root, tool root, headers root; any other class is refused
    gen = lambda p: REC(generated=[{"tool": T + "gen/gen.py", "args": [], "inputs": [p], "outputs": ["gen/gen.h"]}])  # noqa: E731
    docr = {"path": T + "docs", "class": "doc", "gates": []}
    prodr = {"path": T + "other", "class": "product", "target": "host", "gates": [], "recipe": T + "other/build.json"}
    rg = TR.make_repo("h-gen", rec=gen(T + "hdr/h.h"), files=dict(FILES, **{"docs/x.txt": "x\n", "other/build.json": "{}", "other/y.txt": "y\n"}), roots=[HDR_ROOT, docr, prodr])
    ad = recipe.load_admit(rg)
    for what, p, ok in (("a headers root", T + "hdr/h.h", True), ("a tool root", T + "gen/gen.py", True), ("its own root", TR.ROOT + "/a.c", True),
                        ("a doc root", T + "docs/x.txt", False), ("another product root", T + "other/y.txt", False)):
        e = recipe.check(gen(p), TR.ROOT, recipe.tracked(rg), ad)
        bad = [x for x in e if "generated[0].inputs" in x and "class" in x]
        check("generator input from %s: %s" % (what, "accepted" if ok else "refused"), (not bad) if ok else bool(bad), e)
    print("%d failed, wall %.1f s" % (len(FAILED), time.time() - T0))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
