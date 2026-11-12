#!/usr/bin/env python3
"""Tests for link.kind / link.roots validation (recipe.link_errors) with `root@NAME` variants, and the admit.json `variants` rule. Pure: no build."""
import json
import os
import sys
import tempfile

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
P, H, TST, IMG = T + "platform", T + "hostlib", T + "tests/t1", T + "img"
V = P + "@asan"   # the sanitizer variant of the platform: a flags variant of the same root
ROOTS = {
    P: {"path": P, "class": "product", "target": "guest", "variants": {"asan": {}}},
    H: {"path": H, "class": "product", "target": "host"},
    TST: {"path": TST, "class": "test", "target": "guest"},
    IMG: {"path": IMG, "class": "product", "target": "guest"},
    T + "tests/th": {"path": T + "tests/th", "class": "test", "target": "host"},
    T + "doc": {"path": T + "doc", "class": "doc"},
}
REPO = tempfile.mkdtemp(prefix="linkroots-")
for p, r in ROOTS.items():
    if r["class"] != "doc":
        r["recipe"] = p + "/build.json"
        os.makedirs(os.path.join(REPO, p), exist_ok=True)
        json.dump({"variants": {"asan": {"flags": {"c": ["-fsanitize=address"]}}}} if p == P else {}, open(os.path.join(REPO, r["recipe"]), "w"))


def lk(kind, roots, flags=()):
    return {"script": "l.ld", "output": "x.elf", "flags": list(flags) or ["-m", {"pie": "aarch64linux32", "exe": "elf_x86_64"}.get(kind, "x")], "kind": kind, "roots": list(roots)}


def errs(root, link):
    return recipe.link_errors(link, root, {"l.ld"}, ROOTS, set(), REPO)


check("an image links base roots: green", errs(IMG, lk("pie", [P])) == [], errs(IMG, lk("pie", [P])))
check("an image refuses a variant root", any("variant" in e for e in errs(IMG, lk("pie", [V]))), errs(IMG, lk("pie", [V])))
check("a test root links a variant and a base", errs(TST, lk("pie", [V, P])) == [], errs(TST, lk("pie", [V, P])))
check("a guest root refuses kind exe", any("host executable" in e for e in errs(IMG, lk("exe", []))), errs(IMG, lk("exe", [])))
check("a host test root links host roots with kind exe", errs(T + "tests/th", lk("exe", [H])) == [], errs(T + "tests/th", lk("exe", [H])))
check("a host test root refuses a guest root", any("target" in e or "ILP32" in e or "exe" in e for e in errs(T + "tests/th", lk("exe", [P]))), errs(T + "tests/th", lk("exe", [P])))
check("an unknown kind is red", bool(errs(IMG, lk("dll", []))), "")
check("a root that is not an admitted root is red", any("not an admitted product root" in e for e in errs(IMG, lk("pie", [T + "nope"]))), "")
check("a doc root and a test root are not linkable roots", len(errs(IMG, lk("pie", [T + "doc", TST]))) >= 2, errs(IMG, lk("pie", [T + "doc", TST])))
check("the root itself is red", any("the root itself" in e for e in errs(IMG, lk("pie", [IMG]))), "")
check("a repeated root is red", any("listed twice" in e for e in errs(IMG, lk("pie", [P, P]))), "")
check("roots that is not a list is red", bool(errs(IMG, dict(lk("pie", []), roots="x"))), "")
# the refusal set names the class and reaches the link flags of every root class
for f in (["--unresolved-symbols=ignore-all"], ["-Wl,--unresolved-symbols=ignore-all"], ["--noinhibit-exec"], ["-z", "undefs"], ["--warn-unresolved-symbols"],
          ["--allow-shlib-undefined"], ["-Xlinker", "--unresolved-symbols=ignore-in-object-files"]):
    check("resolution-relaxing option %s is refused" % " ".join(f), recipe.refused(f) != [], f)
check("an ordinary flag is not refused", recipe.refused(["-e", "entry", "-z", "now"]) == [], recipe.refused(["-e", "entry", "-z", "now"]))
# admit.json: variants
def judge(roots):
    files = [r["path"] + "/f.c" for r in roots]
    rs = [dict(r, gates=list(ga.REQUIRED["product"] if r["class"] == "product" else ga.REQUIRED_TEST), recipe=r["path"] + "/build.json") for r in roots]
    names = sorted(set(ga.REQUIRED["product"]) | set(ga.REQUIRED_TEST))
    return ga.judge(files, {"format": "fnvnx-admit/1", "roots": rs}, names, lambda p: True)


def prod(**kw):
    return dict({"path": P, "class": "product", "target": "guest"}, **kw)


bad, err = judge([prod(variants={"asan": {}})])
check("variants {NAME: {}} of a product root is admitted", not bad and not err, (bad, err))
bad, err = judge([prod(variants={"host": {"target": "host"}})])
check("a variant may name its own target", not bad and not err, (bad, err))
bad, err = judge([prod(variants={"x": {"target": "arm"}})])
check("a variant with another target value is refused", bool(bad), bad)
bad, err = judge([prod(variants={"x": {"flags": ["-O0"]}})])
check("flags live in the recipe: a variant entry names only a target", bool(bad), bad)
bad, err = judge([prod(variants={})])
check("empty variants is refused", bool(bad), bad)
bad, err = judge([prod(variants=["x"])])
check("variants that is not a mapping is refused", bool(bad), bad)
bad, err = judge([{"path": TST, "class": "test", "target": "guest", "variants": {"a": {}}}])
check("a test root has no variants", bool(bad), bad)
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
