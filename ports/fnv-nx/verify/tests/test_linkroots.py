#!/usr/bin/env python3
"""Tests for link.kind / link.roots validation (recipe.link_errors, recipe.check) and the admit.json variant_of rule. Pure: no build."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import gate_admit as ga  # noqa: E402
import recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


T = "ports/fnv-nx/"
P, V, H, TST, IMG = T + "platform", T + "platform-asan", T + "hostlib", T + "tests/t1", T + "img"
ROOTS = {
    P: {"path": P, "class": "product", "target": "guest"},
    V: {"path": V, "class": "product", "target": "guest", "variant_of": P},
    H: {"path": H, "class": "product", "target": "host"},
    TST: {"path": TST, "class": "test", "target": "guest"},
    IMG: {"path": IMG, "class": "product", "target": "guest"},
    T + "tests/th": {"path": T + "tests/th", "class": "test", "target": "host"},
    T + "doc": {"path": T + "doc", "class": "doc"},
}


def lk(kind, roots, flags=()):
    return {"script": "l.ld", "output": "x.elf", "flags": list(flags), "kind": kind, "roots": list(roots)}


def errs(root, link):
    return recipe.link_errors(link, root, ROOTS)


check("an image links base roots: green", errs(IMG, lk("pie", [P])) == [], errs(IMG, lk("pie", [P])))
check("an image refuses a variant root", any("variant" in e for e in errs(IMG, lk("pie", [V]))), errs(IMG, lk("pie", [V])))
check("a test root links a variant and a base", errs(TST, lk("pie", [V, P])) == [], errs(TST, lk("pie", [V, P])))
check("a guest root refuses kind exe", any("needs target host" in e for e in errs(IMG, lk("exe", []))), errs(IMG, lk("exe", [])))
check("a host root refuses kind pie", any("needs target guest" in e for e in errs(H, lk("pie", []))), errs(H, lk("pie", [])))
check("a host test root links host roots with kind exe", errs(T + "tests/th", lk("exe", [H])) == [], errs(T + "tests/th", lk("exe", [H])))
check("a host test root refuses a guest root", any("target" in e for e in errs(T + "tests/th", lk("exe", [P]))), errs(T + "tests/th", lk("exe", [P])))
check("an unknown kind is red", any("not pie or exe" in e for e in errs(IMG, lk("dll", []))), "")
check("a root that is not an admitted root is red", any("not an admitted product root" in e for e in errs(IMG, lk("pie", [T + "nope"]))), "")
check("a doc root and a test root are not linkable roots", len(errs(IMG, lk("pie", [T + "doc", TST]))) == 2, errs(IMG, lk("pie", [T + "doc", TST])))
check("the root itself is red", any("itself" in e for e in errs(IMG, lk("pie", [IMG]))), "")
check("a repeated root is red", any("distinct" in e for e in errs(IMG, lk("pie", [P, P]))), "")
check("roots that is not a list is red", any("distinct" in e for e in errs(IMG, dict(lk("pie", []), roots="x"))), "")
# the refusal set names the class and reaches the link flags of every root class
for f in (["--unresolved-symbols=ignore-all"], ["-Wl,--unresolved-symbols=ignore-all"], ["--noinhibit-exec"], ["-z", "undefs"], ["--warn-unresolved-symbols"],
          ["--allow-shlib-undefined"], ["-z", "lazy"], ["-zlazy"], ["-Wl,-z,lazy"], ["-Xlinker", "--unresolved-symbols=ignore-in-object-files"]):
    check("resolution-relaxing option %s is refused" % " ".join(f), recipe.refused(f) != [], f)
check("an ordinary flag is not refused", recipe.refused(["-e", "entry", "-z", "now"]) == [], recipe.refused(["-e", "entry", "-z", "now"]))
# admit.json: variant_of
def judge(roots):
    files = [r["path"] + "/f.c" for r in roots]
    rs = [dict(r, gates=list(ga.REQUIRED["product"] if r["class"] == "product" else ga.REQUIRED_TEST), recipe=r["path"] + "/build.json") for r in roots]
    names = sorted(set(ga.REQUIRED["product"]) | set(ga.REQUIRED_TEST))
    return ga.judge(files, {"format": "fnvnx-admit/1", "roots": rs}, names, lambda p: True)


base = {"path": P, "class": "product", "target": "guest"}
bad, err = judge([base, {"path": V, "class": "product", "target": "guest", "variant_of": P}])
check("a variant of a base root of the same target is admitted", not bad and not err, (bad, err))
bad, err = judge([base, {"path": V, "class": "product", "target": "host", "variant_of": P}])
check("a variant of another target is refused", bool(bad), bad)
bad, err = judge([base, {"path": V, "class": "product", "target": "guest", "variant_of": T + "x"},])
check("a variant of a root that is not admitted is refused", bool(bad), bad)
v1 = {"path": V, "class": "product", "target": "guest", "variant_of": P}
bad, err = judge([base, v1, {"path": T + "v2", "class": "product", "target": "guest", "variant_of": V}])
check("a variant of a variant is refused", list(bad) == [T + "v2/f.c"], bad)
bad, err = judge([base, {"path": TST, "class": "test", "target": "guest", "variant_of": P}])
check("a test root cannot be a variant", bool(bad), bad)
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
