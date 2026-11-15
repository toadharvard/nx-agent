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
check("it declares the COM rows, COM words, d3d9 rows and the import bridge table as outputs and uses nothing (a root below every consumer: no uses cycle)",
      rec["outputs"] == {"COM_ROWS": "com_rows.tsv", "COM_WORDS": "com_words.tsv", "D9_ROWS": "d9bridge_rows.tsv", "BRIDGE": "bridge.tsv"} and not rec.get("uses"), rec.get("outputs"))
u = recipe.load_recipe(repo, roots, "ports/fnv-nx/loader").get("uses") or []
check("ports/fnv-nx/loader takes the import bridge table BRIDGE through uses", {"root": BF, "output": "BRIDGE"} in u, u)
plat = recipe.load_recipe(repo, roots, "ports/fnv-nx/platform")
ins = [i for g in plat["generated"] for i in g["inputs"] if isinstance(i, str)]
check("ports/fnv-nx/platform reads the msvcrt names by the tracked path of the tool root", BF + "/crt/msvcrt_names.tsv" in ins, ins[:12])
check("no consumer root owns a copy of the inputs (spec, name table): the old paths are gone",
      not any(os.path.exists(os.path.join(repo, "ports/fnv-nx", p)) for p in ("platform/com/com_spec.json", "platform/crt/msvcrt_names.tsv", "platform/com/spec_extract.py")))
# the tool root's inputs are tracked files (a generate input by repo-relative path; recipe.check, run by load_recipe above, judges the declaration)
tracked = subprocess.run(["git", "-C", repo, "ls-files", "--", BF + "/crt"], capture_output=True, text=True).stdout.split()
check("the name table and the va record are tracked files of the tool root", {BF + "/crt/msvcrt_names.tsv", BF + "/crt/msvcrt_va.tsv"} <= set(tracked), tracked)
# {root, output}: needs the consumer's `uses`; an undeclared output or an output not used is red (checked before any build)
for why, ref in (("an output the consumer does not use", {"root": BF, "output": "COM_WORDS_X"}), ("another root's output without `uses`", {"root": BF, "output": "COM_WORDS"})):
    try:
        recipe.resolve_input("ports/fnv-nx/loader", ref, repo, "/nonexistent")
        check("resolve_input {root, output}: %s is red" % why, False)
    except ValueError:
        check("resolve_input {root, output}: %s is red" % why, True)
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
