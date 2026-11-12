#!/usr/bin/env python3
"""Tests for header roots (a product root with no unit and no link: RECIPE.md "Header roots") and for generator inputs (any tracked file of any
admitted root), on the toy product root of test_recipe.py. Plain script: PASS/FAIL lines, exit 0 on pass."""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_recipe as TR  # noqa: E402
import recipe  # noqa: E402

T0 = time.time()
FAILED, check = TR.FAILED, TR.check
T = TR.T
HR = {"path": T + "hdr", "class": "product", "target": "host", "gates": [], "recipe": T + "hdr/build.json"}
HR2 = {"path": T + "hdr2", "class": "product", "target": "guest", "gates": [], "recipe": T + "hdr2/build.json"}
A = '#include "h.h"\nint f_a(void) { return HV; }\n'
BASE = {"toy/a.c": A, "hdr/build.json": "{}", "hdr2/build.json": "{}", "hdr/h.h": "#define HV 3\n", "hdr2/h2.h": "#define HV2 4\n",
        "toy/constants.tsv": None, "toy/os.S": None}
REC = lambda **kw: TR.mut(**dict(dict(link=None, os_layer=[], constants=None, generated=[], include=[TR.ROOT + "/inc", T + "hdr"]), **kw))  # noqa: E731


def mk(name, files=None, rec=None, roots=(HR, HR2)):
    return TR.make_repo(name, rec=rec or REC(), files=dict(BASE, **(files or {})), roots=list(roots))


def build(r, tag):
    return recipe.build(r, TR.ROOT, TR.BASE + "/st", TR.BASE + "/o-" + tag, ext=TR.BASE + "/ec")


def main():
    r = mk("hr-ok")
    ad = recipe.load_admit(r)
    check("header_roots: product roots with no unit and no link", recipe.header_roots(ad, recipe.tracked(r), r) == {T + "hdr", T + "hdr2"}, recipe.header_roots(ad, recipe.tracked(r), r))
    # a root of either target includes a header root (host-target and guest-target header roots alike)
    for inc, what in ((T + "hdr", "a host header root"), (T + "hdr2", "a guest header root")):
        e = recipe.check(REC(include=[TR.ROOT + "/inc", inc]), TR.ROOT, recipe.tracked(r), ad, None, r)
        check("the toy root includes %s: no include error" % what, not any("include" in x and "lies in" in x for x in e), e)
    # a product root with units of the other target is no home
    oth = {"path": T + "oth", "class": "product", "target": "guest", "gates": [], "recipe": T + "oth/build.json"}
    ro = mk("hr-oth", {"oth/build.json": "{}", "oth/x.c": "int x;\n", "oth/x.h": "int x;\n"}, roots=(HR, oth))
    e = recipe.check(REC(include=[T + "oth"]), TR.ROOT, recipe.tracked(ro), recipe.load_admit(ro), None, ro)
    check("include of a product root of the other target (it has units) is refused", any("lies in no header root" in x for x in e), e)
    # build: the header root's header includes only standard headers, other header roots or externals; the PP read set decides
    res = build(r, "ok")
    check("a unit includes a header root's header: green", res["status"] == "green", res.get("failure"))
    r2 = mk("hr-std", {"hdr/h.h": "#include <stddef.h>\n#define HV 3\n"})
    res = build(r2, "std")
    check("header root header including a standard header: green", res["status"] == "green", res.get("failure"))
    r3 = mk("hr-other", {"hdr/h.h": '#include "h2.h"\n#define HV HV2\n'}, rec=REC(include=[TR.ROOT + "/inc", T + "hdr", T + "hdr2"]))
    res = build(r3, "other")
    check("header root header including another header root's header: green", res["status"] == "green", res.get("failure"))
    r4 = mk("hr-own", {"hdr/h.h": '#include "toy.h"\n#define HV TOY\n'})
    res = build(r4, "own")
    check("header root header including a header of a non-header product root: red at pp:a.c, naming both files",
          res["status"] == "red" and res["failure"]["stage"] == "pp:a.c" and any("hdr/h.h" in x and "toy.h" in x for x in res["failure"]["stderr"]), res.get("failure"))
    # generator inputs: any tracked file of any admitted root
    gen = lambda p: REC(generated=[{"tool": T + "gen/gen.py", "args": [], "inputs": [p], "outputs": ["gen/gen.h"]}])  # noqa: E731
    docr = {"path": T + "docs", "class": "doc", "gates": []}
    prodr = {"path": T + "other", "class": "product", "target": "host", "gates": [], "recipe": T + "other/build.json"}
    rg = mk("hr-gen", {"docs/x.txt": "x\n", "other/build.json": "{}", "other/y.txt": "y\n", "nowhere/z.txt": "z\n"}, rec=gen(T + "hdr/h.h"), roots=(HR, docr, prodr))
    ad = recipe.load_admit(rg)
    for what, p, ok in (("a header root", T + "hdr/h.h", True), ("a tool root", T + "gen/gen.py", True), ("its own root", TR.ROOT + "/a.c", True),
                        ("a doc root", T + "docs/x.txt", True), ("a product root of another target", T + "other/y.txt", True),
                        ("a tracked file in no admitted root", T + "nowhere/z.txt", False), ("an untracked path", T + "hdr/none.h", False),
                        ("a generated output (no uses)", TR.ROOT + "/gen/gen.h", False)):
        e = recipe.check(gen(p), TR.ROOT, recipe.tracked(rg), ad, None, rg)
        bad = [x for x in e if "generated[0].inputs" in x]
        check("generator input from %s: %s" % (what, "accepted" if ok else "refused"), (not bad) if ok else bool(bad), e)
    print("%d failed, wall %.1f s" % (len(FAILED), time.time() - T0))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
