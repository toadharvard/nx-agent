#!/usr/bin/env python3
"""bridgefacts (tool root): the inputs the bridge table derives from live below every consumer. Pure: reads the recipes and runs comrows.py on the
real spec and on toys; no private input."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TREE = os.path.join(HERE, "..", "..")
sys.path.insert(0, os.path.join(HERE, ".."))
import recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:300]))
    fails += 0 if ok else 1


repo = os.path.join(TREE, "..", "..")
BF = "ports/fnv-nx/bridgefacts"
roots = recipe.load_admit(repo)
check("bridgefacts is an admitted tool root with a recipe", roots.get(BF, {}).get("class") == "tool", roots.get(BF))
rec = recipe.load_recipe(repo, roots, BF)
check("it declares the COM rows and words as outputs and uses nothing (a root below every consumer: no uses cycle)",
      rec["outputs"] == {"COM_ROWS": "com_rows.tsv", "COM_WORDS": "com_words.tsv"} and not rec.get("uses"), rec.get("outputs"))
for r in ("ports/fnv-nx/loader", "ports/fnv-nx/platform"):
    u = recipe.load_recipe(repo, roots, r).get("uses") or []
    check("%s takes COM_ROWS through uses" % r, {"root": BF, "output": "COM_ROWS"} in u, u)
check("no consumer root owns a copy of the inputs (spec, name table): the old paths are gone",
      not any(os.path.exists(os.path.join(repo, "ports/fnv-nx", p)) for p in ("platform/com/com_spec.json", "platform/crt/msvcrt_names.tsv", "platform/com/spec_extract.py")))
# the {root, path} ref: a tracked file of a tool root; a product root and an untracked path are red
host, ident = recipe.resolve_input("ports/fnv-nx/platform", {"root": BF, "path": "crt/msvcrt_va.tsv"}, repo)
check("resolve_input {root, path} reads a tracked file of the tool root", ident == BF + "/crt/msvcrt_va.tsv" and os.path.isfile(host), ident)
for why, ref in (("a product root", {"root": "ports/fnv-nx/loader", "path": "build.json"}), ("an unadmitted root", {"root": "ports/fnv-nx/nope", "path": "x"}),
                 ("a path leaving the tree", {"root": BF, "path": "../x"}), ("a missing path", {"root": BF, "path": "crt/none.tsv"})):
    try:
        recipe.resolve_input("ports/fnv-nx/platform", ref, repo)
        check("resolve_input {root, path}: %s is red" % why, False)
    except ValueError:
        check("resolve_input {root, path}: %s is red" % why, True)
# comrows.py on the real spec: one row per method of every non-hidden interface and export; words for every method incl. hidden ones
spec = json.load(open(os.path.join(repo, BF, "com/com_spec.json")))
with tempfile.TemporaryDirectory() as td:
    r = subprocess.run([sys.executable, "-I", os.path.join(repo, BF, "com/comrows.py"), os.path.join(repo, BF, "com/com_spec.json"), td + "/r", td + "/w"], capture_output=True, text=True)
    rows = [l.split("\t") for l in open(td + "/r").read().split("\n") if l] if r.returncode == 0 else []
    words = [l.split("\t") for l in open(td + "/w").read().split("\n") if l] if r.returncode == 0 else []
nm = sum(i["slots"] for i in spec["interfaces"].values() if not i["hidden"])
check("comrows: rows = methods of the non-hidden interfaces + exports, 8 columns, bind sym", r.returncode == 0 and len(rows) == nm + len(spec["exports"]) and all(len(x) == 8 and x[7] == "sym" for x in rows), (r.stderr, len(rows)))
check("comrows: words = every method of every interface (hidden ones included)", len(words) == sum(i["slots"] for i in spec["interfaces"].values()), len(words))
bad = json.loads(json.dumps(spec))
bad["methods"][0]["params"] = [{"kind": "nosuchkind", "name": "x", "ctype": "int", "byref": False}]
with tempfile.TemporaryDirectory() as td:
    json.dump(bad, open(td + "/s.json", "w"))
    r = subprocess.run([sys.executable, "-I", os.path.join(repo, BF, "com/comrows.py"), td + "/s.json", td + "/r", td + "/w"], capture_output=True, text=True)
check("comrows: a parameter kind without a rule stops the tool (exit 2)", r.returncode == 2, r.returncode)
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
