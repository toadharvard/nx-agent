#!/usr/bin/env python3
"""Tests for verify/gate_contract.py: inputs validation, missing input is RED (never green by absence), the n2 rule. No game data."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import scratch  # noqa: E402

scratch.install()
import gate_contract as gc  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

FAILED = testcase.FAILED
def raises(doc):
    d = tempfile.mkdtemp(prefix="gc-")
    p = os.path.join(d, "i.json")
    if doc is not None:
        open(p, "w").write(doc if isinstance(doc, str) else json.dumps(doc))
    try:
        gc.load_inputs(p)
        return None
    except gc.Red as e:
        return str(e)


ext = [{"name": "x", "repo": "r", "ref": "b", "commit": "c" * 40, "tree_sha256": "d" * 64, "at": "x"}]
RR = {"units": "units.json", "addrmap": "addrmap.tsv", "aliases": "aliases.ld"}
good = {"format": gc.FORMAT, "external": ext, "root_results": RR, "ranges": {"exe": "x/r", "module": "x/%s/r"}, **{k: "x/" + k for k in gc.PATHS}}
case("no inputs file is red with the missing input named", "exe-derived inputs" in (raises(None) or ""))
case("not JSON", "not JSON" in (raises("{") or ""))
case("wrong format", "format" in (raises(dict(good, format="z")) or ""))
case("valid inputs load", raises(good) is None)
case("unknown external name", "NAME/path" in (raises(dict(good, exe="y/exe")) or ""))
case("path escape", "NAME/path" in (raises(dict(good, exe="x/../exe")) or ""))
case("empty external", "non-empty" in (raises(dict(good, external=[])) or ""))
case("extra key", "keys must be" in (raises(dict(good, extra=1)) or ""))

u, c, ids = gc.n2_item(None, {"a": 2, "b": 1}, [0x10, 0x20, 0x20], {0x10: "lifted", 0x20: "native"})
case("n2: row not LIFTED is uncertified", (u, c, ids) == (3, 2, ["00000020"]), (u, c, ids))
u, c, ids = gc.n2_item(None, {"a": 3}, [0x10], {0x10: "lifted"})
case("n2: all rows lifted is green", (u, c, ids) == (3, 3, []))
u, c, ids = gc.n2_item(None, {"a": 3}, [0x10], {})
case("n2: row without a verdict is uncertified", c == 2)

case("root_results needs the three result names", "root_results" in (raises(dict(good, root_results={"units": "u"})) or ""))
case("root key is gone (the universe is admit.json)", "keys must be" in (raises(dict(good, root="r")) or ""))
case("images key is gone (derived, no hand list)", "keys must be" in (raises(dict(good, images=[])) or ""))
case("ranges shape", "ranges needs" in (raises(dict(good, ranges={"exe": "x/r"})) or "") and "ranges needs" in (raises(dict(good, ranges={"exe": "x/r", "module": "x/r"})) or ""))
case("optional keys accepted", raises(dict(good, root_results=dict(RR, entries="e"))) is None)

# the shared universe convention (gate_build.quantify)
import gate_build as gb  # noqa: E402
repo = tempfile.mkdtemp(prefix="gc-repo-")
subprocess.run(["git", "init", "-q", repo], check=True)
os.makedirs(repo + "/ports/fnv-nx/verify")


def admit(roots):
    json.dump({"roots": roots}, open(repo + "/ports/fnv-nx/verify/admit.json", "w"))


def run(g):
    out = tempfile.mkdtemp(prefix="gc-out-")
    p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "gate_contract.py"), "--gate", g], cwd=repo,
                       env=dict(os.environ, GATE_OUT=out), capture_output=True, text=True)
    return p, json.load(open(os.path.join(out, g + ".json")))


admit([])
case("quantify: no root -> 0, green, the convention detail", gb.quantify(repo, lambda r: 1 / 0) == (0, 0, [], gb.NO_ROOT))
for g in gc.GATES:
    p, r = run(g)
    case("%s: no product root -> universe 0, green (convention of build/objstub)",
          p.returncode == 0 and r["status"] == "green" and (r["universe"], r["certified"]) == (0, 0) and r["detail"] == gb.NO_ROOT, (p.returncode, r), key=g)
admit([{"path": "ports/fnv-nx/tool", "class": "tool", "recipe": "x"}])
case("a tool root is not in the universe", run("n1")[1]["universe"] == 0)
case("quantify sums roots and names the red one", gb.quantify(repo, lambda r: (3, 3, [], "ok")) == (0, 0, [], gb.NO_ROOT))
admit([{"path": "ports/fnv-nx/a", "class": "product", "recipe": "ports/fnv-nx/a/build.json"},
       {"path": "ports/fnv-nx/b", "class": "product", "recipe": "ports/fnv-nx/b/build.json"}])
res = gb.quantify(repo, lambda r: (3, 3, [], "ok") if r.endswith("a") else (2, 1, [r + ":x"], "bad"))
case("quantify: two roots, one red", res[:3] == (5, 4, ["ports/fnv-nx/b:x"]) and "ports/fnv-nx/b: bad" in res[3], res)

# an admitted root without its outputs is red with the reason, for every gate; no inputs file at all as well
admit([{"path": "ports/fnv-nx/p", "class": "product", "recipe": "ports/fnv-nx/p/build.json"}])
os.makedirs(repo + "/ports/fnv-nx/p")
# universe rule of the per-root gates: a root without game and link (e.g. a runtime root) has no items: green 0, for every gate, even with no inputs file
rec = {"format": "x"}
json.dump(rec, open(repo + "/ports/fnv-nx/p/build.json", "w"))
for g in gc.GATES:
    p, r = run(g)
    case("%s: root with neither game nor link -> green 0 (no inputs needed)",
          p.returncode == 0 and r["status"] == "green" and (r["universe"], r["certified"], r["uncertified"]) == (0, 0, []), (p.returncode, r), key=g)
# a root with game but no link: its game units are judged where they are linked, so it is red for the game gates and 0 for the link gates
json.dump(dict(rec, game="game"), open(repo + "/ports/fnv-nx/p/build.json", "w"))
for g in gc.GATES:
    p, r = run(g)
    if g in gc.LINK_GATES:
        case("%s: game but no link -> link gate universe 0, green", p.returncode == 0 and (r["universe"], r["certified"]) == (0, 0), (p.returncode, r), key=g)
    else:
        case("%s: game but no link -> red naming the missing link", p.returncode == 2 and "links no ELF" in r["detail"] and r["uncertified"] == ["ports/fnv-nx/p:inputs"], (p.returncode, r), key=g)
# an image root (link) with game: judged as before; the image root without a link or game is covered above
rec = {"format": "x", "link": {"script": "l.ld"}, "game": "game"}
json.dump(rec, open(repo + "/ports/fnv-nx/p/build.json", "w"))
for g in gc.GATES:
    p, r = run(g)
    case("%s: root, no inputs file -> red 1/0, item <root>:inputs", p.returncode == 2 and r["status"] == "red"
          and (r["universe"], r["certified"], r["uncertified"]) == (1, 0, ["ports/fnv-nx/p:inputs"]) and "exe-derived inputs" in r["detail"], (p.returncode, r), key=g)
open(repo + "/ports/fnv-nx/verify/contract_inputs.json", "w").write(json.dumps(good))
for g, key in (("reach", "reach_requests"), ("reachlink", "reach_requests"), ("gpa", "dynsites")):
    p, r = run(g)
    case("%s: inputs without %s -> red naming it", r["status"] == "red" and "no " + key in r["detail"], r, key=(g, key))
json.dump({k: v for k, v in rec.items() if k != "game"}, open(repo + "/ports/fnv-nx/p/build.json", "w"))
for g in gc.GATES:
    p, r = run(g)
    case("%s: image root (link) without game -> red naming build.json", p.returncode == 2 and r["status"] == "red" and '"game"' in r["detail"]
          and r["uncertified"] == ["ports/fnv-nx/p:inputs"], (p.returncode, r), key=g)

# built root without the link stage's result files (ensure stubbed in-process: the compiler is not needed to test the rule)
outd = tempfile.mkdtemp(prefix="gc-ens-")
elf = os.path.join(outd, "f.elf")
open(elf, "w").write("x")
gb.ensure = lambda root, cache, repo=".": ({"status": "green", "elf": {"path": elf, "sha256": "0" * 64}, "objects": []}, outd)
json.dump(dict(rec, link={"script": "l.ld"}, game="game"), open(repo + "/ports/fnv-nx/p/build.json", "w"))
for g in gc.GATES:
    r = gc.judge_root(g, repo, outd, "ports/fnv-nx/p")
    keyed = g in ("disp", "reach", "reachlink", "gpa")           # these name their missing shared key first
    case("%s: root without units/addrmap/aliases -> red naming them, owner L1 link stage",
          r[:3] == (1, 0, ["ports/fnv-nx/p:inputs"]) and (keyed or ("units.json" in r[3] and "addrmap.tsv" in r[3] and "L1 link stage" in r[3])), r, key=g)
open(repo + "/ports/fnv-nx/verify/contract_inputs.json", "w").write(json.dumps(dict(good, root_results=dict(RR, entries="entries.tsv"))))
r = gc.judge_root("disp", repo, outd, "ports/fnv-nx/p")
case("disp: entries.tsv named when missing", r[1] == 0 and "entries.tsv" in r[3], r)
p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "gate_contract.py"), "--gate", "zz"], capture_output=True, text=True)
case("unknown gate rejected", p.returncode == 1)
print("FAILED: %d" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
