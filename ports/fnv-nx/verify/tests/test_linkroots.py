#!/usr/bin/env python3
"""Tests for recipe.resolve_link_root and its use in link_errors: a link.roots entry is the root's recipe for the image's own target. Pure."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import recipe  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


R = {"g": {"path": "g", "class": "product", "target": "guest", "variants": {"host": {"target": "host"}}},
     "h": {"path": "h", "class": "product", "target": "host"},
     "g2": {"path": "g2", "class": "product", "target": "guest", "variants": {"a": {"target": "host"}, "b": {"target": "host"}}},
     "g0": {"path": "g0", "class": "product", "target": "guest"}}
rs = lambda d, t: recipe.resolve_link_root(R, d, t)  # noqa: E731
check("a base root of the image's target is itself", rs("g", "guest")[0] == "g" and rs("h", "host")[0] == "h", (rs("g", "guest"), rs("h", "host")))
check("a guest base root in a host image is its variant of target host", rs("g", "host")[0] == "g@host", rs("g", "host"))
sp, why = rs("g0", "host")
check("no variant of the image's target: none, naming the root", sp is None and "g0" in why and "no variant" in why, (sp, why))
sp, why = rs("g2", "host")
check("two variants of the image's target are ambiguous: none (an explicit root@variant is needed)", sp is None and "several" in why, (sp, why))
check("an explicit root@variant of the image's target is itself", rs("g2@a", "host")[0] == "g2@a" and rs("g@host", "host")[0] == "g@host", (rs("g2@a", "host"), rs("g@host", "host")))
sp, why = rs("g@host", "guest")
check("an explicit root@variant of another target is none", sp is None and "target" in why, (sp, why))
sp, why = rs("h", "guest")
check("a host base root in a guest image, with no guest variant, is none", sp is None, (sp, why))
check("an unadmitted root is none", rs("zz", "host")[0] is None, rs("zz", "host"))
# link_errors: link.roots is derived, a written key is a check error; link_candidates and definer_closure are the derivation
files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split("\n")
roots = dict(recipe.load_admit(REPO))
for p, tg in (("ports/fnv-nx/zh", "host"), ("ports/fnv-nx/zg", "guest")):
    roots[p] = {"path": p, "class": "product", "target": tg}
lk = {"output": "x.elf", "kind": "pie", "flags": ["-m", "aarch64linux32"], "script": "ports/fnv-nx/link/guest.ld", "roots": ["ports/fnv-nx/lib/sha256"]}
e = [x for x in recipe.link_errors(lk, "ports/fnv-nx/zg", set(), roots, set(), REPO, files, "") if "link.roots" in x]
check("a written link.roots is a check error (the members are derived)", len(e) == 1 and "derived" in e[0], e)
lk.pop("roots")
e = [x for x in recipe.link_errors(lk, "ports/fnv-nx/zg", set(), roots, set(), REPO, files, "") if "link.roots" in x]
check("an image without link.roots has no link.roots error", e == [], e)
SHA = "ports/fnv-nx/lib/sha256"
TB = {"tool": "ports/fnv-nx/lift/descs/descs.py", "args": ["--out", "x"], "unit": "gen/tab.cc", "section": ".fnvnx.disp"}
terr = lambda tb: [x for x in recipe.link_errors(dict(lk, tables=tb), "ports/fnv-nx/zg", set(), roots, set(), REPO, files, "") if "link.tables" in x]
check("a well-formed link.tables entry has no error", terr([TB]) == [], terr([TB]))
for what, bad in (("an unknown key", dict(TB, x=1)), ("a tool that is not tracked", dict(TB, tool="ports/fnv-nx/nope.py")), ("a unit with no source extension", dict(TB, unit="gen/tab.txt")),
                  ("an absolute unit path", dict(TB, unit="/gen/tab.cc")), ("a section without the dot", dict(TB, section="fnvnx.disp")), ("args that are not strings", dict(TB, args=[1]))):
    check("link.tables: %s is a check error" % what, len(terr([bad])) == 1, terr([bad]))
check("link.tables: two entries with one unit are a check error", len(terr([TB, dict(TB)])) == 1, terr([TB, dict(TB)]))
cg = recipe.link_candidates(roots, "ports/fnv-nx/zg", "guest", REPO)
check("link candidates of a guest image: admitted product roots with a recipe for the guest target, never the image itself, no unadmitted root",
      SHA in cg and "ports/fnv-nx/zg" not in cg and "ports/fnv-nx/zh" not in cg, cg)
ch = recipe.link_candidates(roots, "ports/fnv-nx/zh", "host", REPO)
check("a host image's candidates are resolved to the host recipe of each root (sha256 has a host variant)", SHA + "@host" in ch or SHA in ch, ch)
idx = {"A": {"defined": [{"symbol": "f", "bind": "global"}], "undefined": ["g"]}, "B": {"defined": [{"symbol": "g", "bind": "global"}, {"symbol": "w", "bind": "weak"}], "undefined": []},
       "C": {"defined": [{"symbol": "x", "bind": "global"}], "undefined": []}, "D": {"defined": [{"symbol": "g", "bind": "global"}], "undefined": []}}
m, amb, miss = recipe.definer_closure([], ["f", "h"], {k: idx[k] for k in "ABC"})
check("closure: f pulls A, A's undefined g pulls B, C is not linked, the symbol no root defines is reported with its referencer", (m, amb, miss) == (["A", "B"], {}, {"h": ["(own)"]}), (m, amb, miss))
m, amb, miss = recipe.definer_closure(["f"], ["f", "g"], {k: idx[k] for k in "ABC"})
check("closure: a symbol the image defines itself pulls nothing", m == ["B"], m)
m, amb, miss = recipe.definer_closure([], ["g"], idx)
check("closure: two roots defining g is reported (onedef's rule), neither is linked", m == [] and amb == {"g": ["B", "D"]}, (m, amb))
m, amb, miss = recipe.definer_closure([], ["w"], {"B": idx["B"], "E": {"defined": [{"symbol": "w", "bind": "global"}], "undefined": []}})
check("closure: a global definition wins over a weak one", m == ["E"] and not amb, (m, amb))
m1 = recipe.definer_closure([], ["g", "f"], {k: idx[k] for k in "ABC"})
m2 = recipe.definer_closure([], ["f", "g"], {k: idx[k] for k in "CBA"})
check("closure: the result does not depend on the order of the inputs", m1 == m2, (m1, m2))
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
