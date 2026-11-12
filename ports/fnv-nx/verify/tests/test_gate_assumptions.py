#!/usr/bin/env python3
"""Tests for verify/gate_assumptions.py. Plain script: exit 0 on pass, one PASS/FAIL line per case.
Synthetic trees only. Ids are built by concatenation so that this file mentions no real id."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import atexit  # noqa: E402
import shutil  # noqa: E402
tempfile.tempdir = tempfile.mkdtemp(prefix="scratch-")  # every scratch dir of this test lives here and is removed at exit
atexit.register(shutil.rmtree, tempfile.tempdir, True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import gate_assumptions as G  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


def asm(n):
    return "ASM-" + "%03d" % n


T = "ports/fnv-nx/"
OWNER_TEXT = "# Owner\n\nOWNER, the only accepted ones:\n\n1. the compiler is correct;\n2. SHA-256;\n\nOther text.\n"
FILES = {
    "CLAUDE.md": OWNER_TEXT,
    T + "docs/a.md": "Uses %s %s %s here.\n" % (asm(1), asm(2), asm(10)),
    T + "verify/gates.d/g.json": "{}",
}
TRACKED = set(FILES)


def read(p):
    return FILES[p]


RANGES = {"L1": [100, 199], "L3a": [200, 299], "L3b": [300, 399], "L4": [400, 499], "L5": [500, 599], "L6": [600, 699],
          "L6A": [700, 799], "L2": [800, 899]}


def base():
    return {"format": G.FORMAT, "ranges": RANGES, "retired": [], "entries": [
        {"id": asm(1), "text": "t", "used_by": [T + "docs/a.md"], "aliases": ["c"], "status": "OWNER",
         "decision": {"file": "CLAUDE.md", "quote": "1. the compiler is correct;"}},
        {"id": asm(2), "text": "t", "used_by": [T + "docs/a.md"], "aliases": [], "status": "OWNER",
         "decision": {"file": "CLAUDE.md", "quote": "2. SHA-256;"}},
        {"id": asm(10), "text": "t", "used_by": [T + "docs/a.md"], "aliases": ["x"], "status": "REMOVE",
         "removed_by": "planned: a gate", "owner": "L1"}]}


def run(doc, gates=("g",), tracked=TRACKED):
    return G.judge_registry(doc, tracked, read, set(gates))


def has(errs, word):
    return any(word in e for e in errs)


def codes(errs):
    return {getattr(e, "code", None) for e in errs}


e, c = run(base())
check("registry positive", e == [] and c["OWNER"] == 2 and c["REMOVE"] == {"L1": 1} and c["planned"] == 1, (e, c))

d = base()
d["entries"].append({"id": asm(3), "text": "t", "used_by": [T + "docs/a.md"], "aliases": [], "status": "OWNER",
                     "decision": {"file": "CLAUDE.md", "quote": "2. SHA-256;"}})
e, c = run(d)
check("tenth OWNER entry without the owner's item is red", e != [], e)

d = base()
d["entries"][1]["decision"]["quote"] = "3. invented;"
e, c = run(d)
check("owner quote not verbatim is red", has(e, "verbatim"), e)

d = base()
d["entries"][1]["decision"]["quote"] = "Other text."
check("owner quote outside the list items is red", run(d)[0] != [], run(d)[0])

d = base()
d["entries"].pop(1)
e, c = run(d)
check("owner item without an entry is red", has(e, "has no OWNER entry"), e)

d = base()
d["entries"][2]["id"] = asm(1)
check("repeated id is red", has(run(d)[0], "repeated"), run(d)[0])

d = base()
d["entries"][2]["id"] = "ASM-1"
check("malformed id is red", has(run(d)[0], "not ASM-NNN"), run(d)[0])

d = base()
d["entries"][2]["aliases"] = ["c"]
check("alias of two entries is red", has(run(d)[0], "alias"), run(d)[0])

d = base()
d["entries"][2]["used_by"] = []
check("dead entry is red", has(run(d)[0], "dead entry"), run(d)[0])

d = base()
d["entries"][2]["used_by"] = [T + "docs/none.md"]
check("used_by absent file is red", has(run(d)[0], "not a tracked file"), run(d)[0])

d = base()
d["entries"][2]["used_by"] = [T + "verify/gates.d/g.json"]
check("used_by file that does not cite the id is red", has(run(d)[0], "does not cite"), run(d)[0])

d = base()
d["entries"][2]["owner"] = "L9"
check("bad owner is red", has(run(d)[0], "owner"), run(d)[0])

d = base()
d["entries"][2]["owner"] = "L1+L4"
check("joined owners are green", run(d)[0] == [] and run(d)[1]["REMOVE"] == {"L1+L4": 1}, run(d))

d = base()
d["entries"][2]["owner"] = "L1+L1"
check("repeated joined owner is red", has(run(d)[0], "owner"), run(d)[0])

for rb in ("no gate yet", "planned:", "gate:nope", "fact:ports/fnv-nx/none", "whatever", ""):
    d = base()
    d["entries"][2]["removed_by"] = rb
    check("removed_by %r is red" % rb, has(run(d)[0], "removed_by"), run(d)[0])

for rb, key in (("gate:g", None), ("fact:" + T + "docs/a.md", None), ("no gate yet: the fact", "no gate yet"), ("OPEN: nobody", "OPEN")):
    d = base()
    d["entries"][2]["removed_by"] = rb
    e, c = run(d)
    check("removed_by %r is green" % rb, e == [] and (key is None or c[key] == 1), (e, c))

d = base()
d["entries"][2]["pending"] = "claude/x@abcdef1"
check("a pending key is red (the field is removed)", has(run(d)[0], "keys are not"), run(d)[0])

d = base()
d["entries"][2]["used_by"] = [T + "other/branch.md"]
check("used_by of a file that is not in the tree is red", has(run(d)[0], "not a tracked file"), run(d)[0])

d = base()
d["entries"][2]["removed_by"] = "gate:later"
check("removed_by of a gate that does not exist is red", has(run(d)[0], "removed_by"), run(d)[0])

d = base()
d["entries"][2]["owner"] = "L6A"
check("owner L6A is green", run(d)[0] == [], run(d)[0])

for i, ow, ok in ((250, "L3a", True), (250, "L3b", False), (700, "L6A", True), (350, "L3b+L4", True), (350, "L4+L3b", False),
                  (850, "L2", True), (250, "L1", False), (199, "L1", True), (200, "L1", False), (99, "L5", True), (900, "L2", False)):
    d = base()
    d["entries"][2]["id"] = asm(i)
    d["entries"][2]["owner"] = ow
    d["entries"][2]["used_by"] = [T + "docs/a.md"]
    e = run(d)[0]
    check("id %d owner %s range %s" % (i, ow, "green" if ok else "red"), ("id-range" not in codes(e)) == ok, e)
d = base()
d["entries"][2]["id"] = asm(1)
check("an id defined by two records is red", "id-repeated" in codes(run(d)[0]), run(d)[0])
d = base()
d["entries"][2]["aliases"] = ["c"]
check("an alias claimed by two records is red", "alias-claimed" in codes(run(d)[0]), run(d)[0])
def withret(d, ret):
    d = copy.deepcopy(d)
    d["retired"] = ret
    return d


# never reuse: retired ids and aliases
d = withret(base(), [{"id": asm(11), "aliases": ["gone alias"]}])
check("a retired record is green", run(d)[0] == [], run(d)[0])
d = withret(base(), [{"id": asm(10), "aliases": []}])
check("a new entry with a retired id is red", "id-retired-reused" in codes(run(d)[0]), run(d)[0])
d = withret(base(), [{"id": asm(11), "aliases": ["x"]}])
check("a live alias that is retired is red", "alias-retired-reused" in codes(run(d)[0]), run(d)[0])
check("retired twice is red", "retired-form" in codes(run(withret(base(), [{"id": asm(11), "aliases": []}] * 2))[0]), "")
check("a malformed retired item is red", "retired-form" in codes(run(withret(base(), [{"id": "x"}]))[0]), "")
check("retired not a list is red", "retired-form" in codes(run(withret(base(), {}))[0]), "")
R = {"id": asm(11), "aliases": ["gone alias"]}
FR = {T + "docs/a.md": "see %s\n" % asm(11), T + "docs/b.md": "rests on gone alias\n", T + "docs/c.md": "fine\n"}
errs = G.judge_retired(sorted(FR), {R["id"]: R["aliases"]}, FR.__getitem__)
check("a cited retired id and a cited retired alias are red, an uncited file is not",
      sorted(e.subject for e in errs) == [T + "docs/a.md", T + "docs/b.md"] and codes(errs) == {"retired-cited"}, errs)
# the merge ratchet
B = base()
N = copy.deepcopy(B)
check("ratchet: same registry is green", G.ratchet_errors(B, N) == [], "")
N["entries"].pop(2)
check("ratchet: a vanished id is red", codes(G.ratchet_errors(B, N)) == {"ratchet-id", "ratchet-alias"}, G.ratchet_errors(B, N))
N["retired"] = [{"id": asm(10), "aliases": ["x"]}]
check("ratchet: retiring the id and its aliases is green", G.ratchet_errors(B, N) == [], G.ratchet_errors(B, N))
N = copy.deepcopy(B)
N["entries"][2]["aliases"] = []
check("ratchet: a dropped alias is red", codes(G.ratchet_errors(B, N)) == {"ratchet-alias"}, G.ratchet_errors(B, N))
N["retired"] = [{"id": asm(77), "aliases": ["x"]}]
check("ratchet: an alias that moved to retired is green", G.ratchet_errors(B, N) == [], G.ratchet_errors(B, N))
check("ratchet: a base without a registry holds nothing", G.ratchet_errors(None, N) == [], "")
check("ranges missing is red", "registry-form" in codes(run({"format": G.FORMAT, "entries": base()["entries"]})[0]) or
      "ranges-form" in codes(run({"format": G.FORMAT, "entries": base()["entries"]})[0]), run({"format": G.FORMAT, "entries": []})[0])
for name, rg in (("a lead missing", {k: v for k, v in RANGES.items() if k != "L2"}), ("overlapping", dict(RANGES, L2=[150, 899])),
                 ("below the first ranged id", dict(RANGES, L1=[1, 199])), ("not integers", dict(RANGES, L1=["a", "b"]))):
    d = base()
    d["ranges"] = rg
    check("ranges with %s is red" % name, "ranges-form" in codes(run(d)[0]), run(d)[0])

d = base()
d["entries"][2]["extra"] = 1
check("unknown key is red", has(run(d)[0], "keys are not"), run(d)[0])

check("missing registry is red", run(None)[0] != [], run(None))
check("wrong format is red", run({"format": "x", "entries": []})[0] != [], "")

# mentions
ids = {asm(1), asm(10)}
M = {
    "a.md": "An assumption without an id.\n\nAn assumption with %s.\n\n- assumed here\n- ok %s assumed\n" % (asm(1), asm(10)),
    "b.md": "Assumption: %s is unknown.\n" % asm(999),
    "c.md": "no word at all\n",
    "d.txt": "assumed\n",
}
m, bad = G.judge_mentions(sorted(M), ids, lambda p: M[p])
check("mentions counted", len(m) == 6, m)
check("block without id is red", ("a.md", 1) in bad and ("a.md", 5) in bad, bad)
check("block with a known id is certified", ("a.md", 3) not in bad and ("a.md", 6) not in bad, bad)
check("unknown id in a mention is red", "not in the registry" in bad.get(("b.md", 1), ""), bad)
check("block without the word is not a mention", all(f != "c.md" for f, _ in m), m)
check("a list item is its own block", ("a.md", 5) in bad, bad)
check("non-md text counts whole", ("d.txt", 1) in bad, bad)

PY = '"""doc line\n\nA docstring paragraph with an assumption.\n"""\n\nassumption = "assumed"\n\n# comment about assumed %s\n\nx = 1  # assumes it\n' % asm(1)
m, bad = G.judge_mentions(["p.py"], ids, lambda p: PY)
check("python: only comments and docstrings are prose", m == [("p.py", 3), ("p.py", 8), ("p.py", 10)], m)
check("python: an uncertified docstring paragraph is red", ("p.py", 3) in bad and ("p.py", 10) in bad, bad)
check("python: a certified comment is green", ("p.py", 8) not in bad, bad)
PY2 = 'name = "an assumption in a string"\nassumption = 1\n'
m, bad = G.judge_mentions(["q.py"], ids, lambda p: PY2)
check("python: code and string values hide nothing and show nothing", m == [], m)
m, bad = G.judge_mentions(["r.py"], ids, lambda p: "def (:\n# assumed\n")
check("python that does not parse is scanned whole", len(m) == 1, m)

# stray ids
S = {"a.json": '{"x": "%s"}' % asm(998), "b.md": "ok %s" % asm(1)}
check("stray id in any text file is red", G.judge_stray(sorted(S), ids, lambda p: S[p]) == {"a.json": [asm(998)]}, "")

# the owner's list
check("owner items found", len(G.owner_items(OWNER_TEXT)) == 2, G.owner_items(OWNER_TEXT))
check("no head, no items", G.owner_items("1. x\n") == [], "")
check("two heads, no items", G.owner_items(OWNER_TEXT + OWNER_TEXT) == [], "")

# whole gate in a temporary git tree
GATES = json.dumps({"format": "fnvnx-gatefile/1", "name": "g", "argv": ["x"], "result": "g.json", "covers": ["tool"]})
ADMIT = json.dumps({"roots": [{"path": T + "journal", "record": True}]})


def tree(extra=None, doc=None):
    td = tempfile.mkdtemp()
    files = dict(FILES)
    files[T + "verify/gates.d/g.json"] = GATES
    files[T + "verify/admit.json"] = ADMIT
    files[T + "verify/ASSUMPTIONS.json"] = json.dumps(doc if doc is not None else base())
    files[T + "journal/log.md"] = "An assumption in the append-only log.\n"
    files.update(extra or {})
    for p, t in files.items():
        os.makedirs(os.path.dirname(os.path.join(td, p)) or td, exist_ok=True)
        with open(os.path.join(td, p), "w", encoding="utf-8") as fh:
            fh.write(t)
    subprocess.run(["git", "init", "-q"], cwd=td, check=True)
    subprocess.run(["git", "add", "-A"], cwd=td, check=True)
    return td


def gate(td):
    out = tempfile.mkdtemp()
    env = dict(os.environ, GATE_OUT=out)
    r = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(HERE), "gate_assumptions.py")], cwd=td, env=env,
                       capture_output=True, text=True)
    with open(os.path.join(out, "assumptions.json")) as fh:
        return r.returncode, json.load(fh)


rc, res = gate(tree())
check("gate: green tree, the log is not scanned", rc == 0 and res["status"] == "green" and res["universe"] == 0, (rc, res))
rc, res = gate(tree({T + "docs/b.md": "An assumption nobody numbered.\n"}))
check("gate: a new mention without an id is red", rc == 2 and res["status"] == "red" and res["uncertified"] == [T + "docs/b.md:1"], (rc, res))
rc, res = gate(tree({T + "shim/include/x.h": "/* An assumption nobody numbered. */\n"}))
check("gate: an unnumbered mention in a header of a unit-less product root is red (the universe is every tracked file, not units)",
      rc == 2 and res["status"] == "red" and res["uncertified"] == [T + "shim/include/x.h:1"], (rc, res))
rc, res = gate(tree({T + "docs/b.md": "An assumption, %s.\n" % asm(1)}))
check("gate: a new mention with an id is green", rc == 0 and res["universe"] == 1 and res["certified"] == 1, (rc, res))
rc, res = gate(tree({T + "docs/b.md": "An assumption, %s.\n" % asm(77)}))
check("gate: a mention of an id without an entry is red", rc == 2, (rc, res))
rc, res = gate(tree({T + "data.json": '{"a": "%s"}' % asm(77)}))
check("gate: a stray id in a json file is red", rc == 2 and has(res["errors"], asm(77)), (rc, res))
for name, path, body in (("a .py comment", T + "verify/gate_x.py", "x = 1  # rests on %s\n"),
                         ("a .py docstring", T + "lift/gates/gate_y.py", '"""The decoder (%s)."""\n'),
                         ("a test file", T + "verify/tests/test_z.py", "# %s\n"),
                         ("a gate file of gates.d", T + "verify/gates.d/h.json", '{"note": "%s"}\n'),
                         ("a shell script", T + "farm/run.sh", "# %s\n")):
    rc, res = gate(tree({path: body % asm(43)}))
    check("gate: an unrecorded id in %s is red, the file is named" % name, rc == 2 and res["status"] == "red" and has(res["errors"], asm(43)) and has(res["errors"], path), (rc, res))
    rc, res = gate(tree({path: body % asm(1)}))
    check("gate: a recorded id in %s is green" % name, rc == 0 and res["status"] == "green", (rc, res))
rc, res = gate(tree({T + "data.json": '{"a": "assumption"}'}))
check("gate: a json file is data, not a mention", rc == 0, (rc, res))
d = base()
d["entries"].append({"id": asm(3), "text": "t", "used_by": [T + "docs/a.md"], "aliases": [], "status": "OWNER",
                     "decision": {"file": "CLAUDE.md", "quote": "1. the compiler is correct;"}})
rc, res = gate(tree({T + "docs/a.md": "x %s %s %s %s\n" % (asm(1), asm(2), asm(3), asm(10))}, d))
check("gate: a tenth OWNER entry is red", rc == 2, (rc, res))
rc, res = gate(tree({T + "docs/c.py": "# assumed\nx = 1\n"}))
check("gate: a python comment is a mention", rc == 2 and res["universe"] == 1, (rc, res))
rc, res = gate(tree({T + "docs/c.py": "assumed = 1\nname = 'assumption'\n"}))
check("gate: python code is not a mention", rc == 0, (rc, res))

# whole gate
rc, res = gate(tree({T + "docs/b.md": "x gone alias\n"}, withret(base(), [R])))
check("gate: a retired alias cited in the tree is red", rc == 2 and has(res["errors"], "gone alias"), (rc, res))
rc, res = gate(tree({T + "docs/b.md": "x %s\n" % asm(11)}, withret(base(), [R])))
check("gate: a retired id cited in the tree is red", rc == 2 and has(res["errors"], asm(11)), (rc, res))
rc, res = gate(tree(None, withret(base(), [R])))
check("gate: a retired id in the registry only is green", rc == 0, (rc, res))

# the full findings file: uncapped, sorted, counted per code, named in the summary line
d = base()
for i in range(60):
    d["entries"].append({"id": asm(100 + i), "text": "", "used_by": [T + "docs/a.md"], "aliases": [], "status": "REMOVE",
                         "removed_by": "planned: a gate", "owner": "L5"})
out = tempfile.mkdtemp()
td = tree(None, d)
env = dict(os.environ, GATE_OUT=out, GATE_DETAIL=os.path.join(out, "detail"))
r = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(HERE), "gate_assumptions.py")], cwd=td, env=env,
                   capture_output=True, text=True)
fp = os.path.join(out, "detail", "assumptions.findings.json")
ok = os.path.exists(fp)
doc = json.load(open(fp)) if ok else {}
ids = {f["id"] for f in doc.get("findings", [])}
check("findings file holds every finding, not a capped list", ok and doc["total"] == len(doc["findings"]) >= 120 and
      len(ids) >= 60 and sum(doc["counts"].values()) == doc["total"], doc.get("counts"))
check("findings file is sorted by code", ok and [f["code"] for f in doc["findings"]] == sorted(f["code"] for f in doc["findings"]), "")
check("stdout prints at most 20 findings and the summary names the file", r.returncode == 2 and fp in r.stdout and
      r.stdout.count("\n  ") <= 22, r.stdout[-300:])

print("fails: %d" % fails)
sys.exit(1 if fails else 0)
