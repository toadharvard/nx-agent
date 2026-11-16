#!/usr/bin/env python3
"""Tests for the `link.loader` key of recipe.link_errors: a shared image declares the root of the image that loads it. Pure: synthetic admit entries."""
import os
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


import subprocess  # noqa: E402
files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split("\n")
roots = dict(recipe.load_admit(REPO))
SO, LD, GU, TS, NR = "ports/fnv-nx/sx", "ports/fnv-nx/lx", "ports/fnv-nx/gx", "ports/fnv-nx/tx", "ports/fnv-nx/nx"
for p, cl, tg, rc in ((SO, "product", "host", "ports/fnv-nx/sx/build.json"), (LD, "product", "host", "ports/fnv-nx/lx/build.json"),
                      (GU, "product", "guest", "ports/fnv-nx/gx/build.json"), (TS, "test", "host", "ports/fnv-nx/tx/build.json"), (NR, "product", "host", None)):
    roots[p] = dict({"path": p, "class": cl, "target": tg}, **({"recipe": rc} if rc else {}))
VS = "ports/fnv-nx/link/guest.ld"      # any tracked file of an admitted tool tree stands for the version script here


def errs(loader, kind="shared", root=SO):
    lk = {"output": "x.so" if kind == "shared" else "x.elf", "kind": kind, "flags": ["-m", "elf_x86_64"], "loader": loader}
    if kind == "shared":
        lk["version_script"] = VS
    return [e for e in recipe.link_errors(lk, root, set(), roots, set(), REPO, files, "") if "loader" in e]


check("a product root with a recipe is an admitted loader", errs(LD) == [], errs(LD))
check("a test root with a recipe is an admitted loader too", errs(TS) == [], errs(TS))
e = errs("ports/fnv-nx/nowhere")
check("a loader that is not an admitted root is red", len(e) == 1 and "not an admitted root" in e[0], e)
e = errs(NR)
check("an admitted root without a recipe cannot be a loader (nothing to build): red", len(e) == 1, e)
e = errs(SO)
check("the image itself is not its loader", len(e) == 1 and "the root itself" in e[0], e)
e = errs(GU)
check("a loader of another target than the image is red", len(e) == 1 and "target" in e[0], e)
e = errs(LD, kind="pie", root=GU)
check("only a shared image declares a loader", any("only a shared image" in x for x in e), e)
e = errs(7)
check("a loader that is not a string is red", len(e) == 1, e)
check("the loader is an edge of the root graph (cycle and build order see it)", recipe.root_edges({"link": {"loader": LD + "@x"}}) == [LD], recipe.root_edges({"link": {"loader": LD + "@x"}}))
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
