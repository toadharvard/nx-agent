#!/usr/bin/env python3
"""A header root (class header: no recipe, no units) has one implicit output INCLUDE = its own directory, reached only through `uses` (recipe.root_outputs):
  - a `uses` entry of a header root's INCLUDE, with `include` of its directory: green
  - an `include` of a header root's directory without that `uses` entry: red
  - a header root with a recipe is red (gate_admit)
  - another output name of a header root is red
  - a path twice in admit.json is red (one path, one row)
Real recipe.check and gate_admit on a toy checkout. Run: python3 -I <this file> (cwd any; the toy is a temporary git repository)."""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import gate_admit  # noqa: E402
import recipe  # noqa: E402

fails = 0
T = "ports/fnv-nx/"


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


def w(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def toy(td, name, uses, extra_roots=()):
    repo = os.path.join(td, name)
    os.makedirs(repo)
    subprocess.run(["git", "-C", repo, "init", "-q"], check=True)
    w(repo, T + "hdr/x.h", "#define X 1\n")
    w(repo, T + "app/a.c", '#include "x.h"\nint a(void){return X;}\n')
    rec = {"format": "fnvnx-recipe/1", "include": [T + "hdr"], "units": "all"}
    if uses:
        rec["uses"] = [{"root": T + "hdr", "output": uses}]
    w(repo, T + "app/build.json", json.dumps(rec))
    roots = [{"path": T + "hdr", "class": "header"}, {"path": T + "app", "class": "product", "target": "host", "recipe": T + "app/build.json"},
             {"path": T + "verify", "class": "tool"}] + list(extra_roots)
    w(repo, T + "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": roots}))
    w(repo, T + "verify/x.py", "")
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "toy"], check=True)
    return repo


def errs(repo):
    roots = recipe.load_admit(repo)
    rec = json.load(open(os.path.join(repo, T + "app/build.json")))
    return recipe.check(rec, T + "app", recipe.tracked(repo), roots, None, repo)


with tempfile.TemporaryDirectory() as td:
    e = [x for x in errs(toy(td, "ok", "INCLUDE")) if "include" in x or "uses" in x]
    check("a uses entry of the header root's INCLUDE makes its directory an include home: green", not e, e)
    e = errs(toy(td, "nouses", None))
    check("an include of a header root without the uses entry is red", any("lies outside root" in x for x in e), e)
    e = errs(toy(td, "badname", "OTHER"))
    check("another output name of a header root is red", any("declares no output" in x for x in e), e)
    repo = toy(td, "withrec", "INCLUDE")
    ad = json.load(open(os.path.join(repo, T + "verify/admit.json")))
    ad["roots"][0]["recipe"] = T + "hdr/build.json"
    _, errsa = gate_admit.judge(recipe.tracked(repo), ad, lambda f: True)
    check("a header root with a recipe is red", any("header root has no" in x for x in errsa), errsa)
    ad["roots"][0].pop("recipe")
    ad["roots"].append({"path": T + "hdr", "class": "header"})
    _, errsb = gate_admit.judge(recipe.tracked(repo), ad, lambda f: True)
    check("one path, at most one row: a path given twice is red", any("named twice" in x for x in errsb), errsb)
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
