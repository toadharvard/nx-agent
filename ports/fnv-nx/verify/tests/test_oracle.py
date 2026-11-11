#!/usr/bin/env python3
"""Tests for verify/gate_oracle.py. Plain script: exit 0 on pass, one PASS/FAIL line per case."""
import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import gate_oracle as G  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


T = "ports/fnv-nx/"
PIN = "a" * 64
SRC = {
    T + "verify/tool.py": '"""Rule of the tool: sorted output."""\nimport helper\n',
    T + "verify/helper.py": "from pkg import deep\n",
    T + "verify/pkg/deep.py": "x = 1\n",
    T + "verify/model.py": "y = 2\n",
    T + "verify/table.tsv": "a\tb\n",
    T + "verify/broken.py": "def (:\n",
    T + "verify/tests/test_a.py": "",
    T + "verify/tests/test_b.py": "",
    T + "docs/SPEC.md": "## 4.2 Rounding\ntext\n",
    T + "game/body.cc": "// Section G\nint f();\n",
    T + "game/ref.tsv": "1\n",
    T + "game/build.json": "{}",
}
TRACKED = set(SRC)
ROOTS = [{"path": T + "verify", "class": "tool"}, {"path": T + "docs", "class": "doc"},
         {"path": T + "game", "class": "product"}]
PINS = {"exe_sha256": PIN}
FILES = [T + "verify/tests/test_a.py", T + "verify/tests/test_b.py"]
D = T + "verify/tests"
SPEC = {"kind": "spec", "document": "tool", "section": "Rule of the tool: sorted output.", "path": T + "verify/tool.py"}
WINE = {"kind": "wine-exe", "wine": "9.0", "exe_sha256": PIN,
        "artifact": {"repo": "fnv-nx-build", "commit": "b" * 40, "path": "ref/x.bin", "sha256": "c" * 64}}
GOOD = {"format": "fnvnx-oracle/1", "tests": {
    "test_a.py": {"subject": [T + "verify/tool.py"], "references": [SPEC]},
    "test_b.py": {"subject": [T + "game/body.cc"], "references": [WINE, {"kind": "model", "path": T + "verify/model.py"}]}}}


def run(doc, files=FILES):
    return G.judge(files, {D: doc}, TRACKED, ROOTS, PINS, lambda p: SRC[p])


def with_a(**kw):
    d = copy.deepcopy(GOOD)
    d["tests"]["test_a.py"].update(kw)
    return d


def with_b(**kw):
    d = copy.deepcopy(GOOD)
    d["tests"]["test_b.py"].update(kw)
    return d


def bad_a(name, doc, word):
    r = run(doc)
    check(name, set(r) == {D + "/test_a.py"} and word in r[D + "/test_a.py"], r)


def bad_b(name, doc, word):
    r = run(doc)
    check(name, set(r) == {D + "/test_b.py"} and word in r[D + "/test_b.py"], r)


check("good declaration: all certified", run(GOOD) == {}, run(GOOD))
check("no universe: nothing uncertified", G.judge([], {}, TRACKED, ROOTS, PINS, lambda p: SRC[p]) == {})
r = run(None)
check("missing oracle.json: every test of the DIR uncertified", set(r) == set(FILES), r)
r = run(dict(GOOD, format="fnvnx-oracle/2"))
check("wrong format: every test uncertified", set(r) == set(FILES), r)
r = run(dict(GOOD, note="x"))
check("unknown top-level key: every test uncertified", set(r) == set(FILES), r)
d = copy.deepcopy(GOOD)
d["tests"]["test_gone.py"] = d["tests"]["test_a.py"]
r = run(d)
check("entry without a test file: every test uncertified", set(r) == set(FILES) and "test_gone.py" in r[FILES[0]], r)
d = copy.deepcopy(GOOD)
del d["tests"]["test_a.py"]
bad_a("test without an entry", d, "no entry")
d = with_a()
d["tests"]["test_a.py"]["why"] = "x"
bad_a("unknown entry key", d, "entry keys")
bad_a("empty subject", with_a(subject=[]), "subject is empty")
bad_a("untracked subject", with_a(subject=[T + "verify/nope.py"]), "not a tracked file")
SRC2 = dict(SRC)
TRACKED.add(T + "stray.py")
bad_a("subject outside every root", with_a(subject=[T + "stray.py"]), "not a tracked file inside a root")
TRACKED.discard(T + "stray.py")
bad_a("empty references", with_a(references=[]), "references is empty")
bad_a("unknown kind", with_a(references=[{"kind": "self", "path": T + "verify/tool.py"}]), "unknown reference kind")
bad_a("reference is not an object", with_a(references=["spec"]), "not an object")
bad_a("spec with an extra key", with_a(references=[dict(SPEC, note="x")]), "spec: keys")
bad_a("spec with path and url", with_a(references=[dict(SPEC, url="https://x")]), "spec: keys")
bad_a("spec with empty section", with_a(references=[dict(SPEC, section=" ")]), "empty")
bad_a("spec section not in the cited file", with_a(references=[dict(SPEC, section="Rule nobody wrote")]), "does not occur")
bad_a("spec path untracked", with_a(references=[dict(SPEC, path=T + "docs/NONE.md")]), "not a tracked file")
u = {"kind": "spec", "document": "IEEE 754-2008", "section": "5.4.1", "url": "https://example.org/754"}
check("spec with url certified", run(with_a(references=[u])) == {})
bad_a("spec with empty url", with_a(references=[dict(u, url="")]), "url is empty")
check("spec in a doc root certified", run(with_a(references=[
    {"kind": "spec", "document": "SPEC", "section": "## 4.2 Rounding", "path": T + "docs/SPEC.md"}])) == {})
check("model outside the closure certified", run(with_a(references=[{"kind": "model", "path": T + "verify/model.py"}])) == {})
check("contract table certified", run(with_a(references=[{"kind": "contract", "path": T + "verify/table.tsv"}])) == {})
bad_a("model is the subject", with_a(references=[{"kind": "model", "path": T + "verify/tool.py"}]), "import closure")
bad_a("model imported by the subject", with_a(references=[{"kind": "model", "path": T + "verify/helper.py"}]), "import closure")
bad_a("model imported through from-import of a package", with_a(references=[{"kind": "model", "path": T + "verify/pkg/deep.py"}]),
      "import closure")
bad_a("contract inside the closure", with_a(subject=[T + "verify/tool.py", T + "verify/table.tsv"],
                                             references=[{"kind": "contract", "path": T + "verify/table.tsv"}]), "import closure")
bad_a("model with an extra key", with_a(references=[{"kind": "model", "path": T + "verify/model.py", "x": 1}]), "keys")
bad_a("subject that does not parse", with_a(subject=[T + "verify/broken.py"]), "does not parse")
bad_b("wine version is not 9.0", with_b(references=[dict(WINE, wine="9.1")]), "wine is not 9.0")
bad_b("exe hash is not the pinned one", with_b(references=[dict(WINE, exe_sha256="d" * 64)]), "pinned exe")
bad_b("artifact commit is not 40 hex", with_b(references=[dict(WINE, artifact=dict(WINE["artifact"], commit="b" * 39))]), "40 hex")
bad_b("artifact sha256 upper case", with_b(references=[dict(WINE, artifact=dict(WINE["artifact"], sha256="C" * 64))]), "64 hex")
bad_b("artifact in another repository", with_b(references=[dict(WINE, artifact=dict(WINE["artifact"], repo="nx-agent"))]),
      "fnv-nx-build")
bad_b("artifact with a missing key", with_b(references=[dict(WINE, artifact={"repo": "fnv-nx-build"})]), "artifact keys")
bad_b("wine-exe with an extra key", with_b(references=[dict(WINE, note="x")]), "wine-exe: keys")
r = G.judge(FILES, {D: GOOD}, TRACKED, ROOTS, {}, lambda p: SRC[p])
check("no pinned exe: wine-exe uncertified", set(r) == {D + "/test_b.py"}, r)
bad_b("product subject with a model only", with_b(references=[{"kind": "model", "path": T + "verify/model.py"}]),
      "no wine-exe or spec")
bad_b("product subject with a contract only", with_b(references=[{"kind": "contract", "path": T + "verify/table.tsv"}]),
      "no wine-exe or spec")
bad_b("product subject citing its own text", with_b(references=[
    {"kind": "spec", "document": "body", "section": "// Section G", "path": T + "game/body.cc"}]), "lies in a product root")
bad_b("product subject with a table of the product root", with_b(references=[WINE, {"kind": "contract", "path": T + "game/ref.tsv"}]),
      "lies in a product root")
check("product subject with a spec outside the product root certified", run(with_b(references=[
    {"kind": "spec", "document": "SPEC", "section": "## 4.2 Rounding", "path": T + "docs/SPEC.md"}])) == {})
check("tool subject citing its own stated rule certified", run(with_a(references=[SPEC])) == {})

# closure: every name python could load
c, why = G.closure([T + "verify/tool.py"], TRACKED, lambda p: SRC[p])
check("closure follows import and from-import", why is None and c == {T + "verify/tool.py", T + "verify/helper.py",
                                                                       T + "verify/pkg/deep.py"}, (c, why))
# repeated key in oracle.json
tmp = tempfile.mkdtemp(prefix="oracle-test-")
p = os.path.join(tmp, "o.json")
open(p, "w").write('{"format": "fnvnx-oracle/1", "tests": {"test_a.py": 1, "test_a.py": 2}}')
check("repeated entry is not a declaration", G.load(p) is None)
open(p, "w").write("{")
check("malformed json is not a declaration", G.load(p) is None)

# the gate on this checkout: every test of the tree is certified
root = subprocess.run(["git", "-C", HERE, "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
out = os.path.join(tmp, "out")
os.makedirs(out)
env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_")}
rc = subprocess.run([sys.executable, "-I", "ports/fnv-nx/verify/gate_oracle.py"], cwd=root, env=dict(env, GATE_OUT=out)).returncode
res = json.load(open(os.path.join(out, "oracle.json")))
check("gate on this tree: green, result in the gate format", rc == 0 and res["status"] == "green" and res["name"] == "oracle"
      and res["format"] == "fnvnx-gate/1" and res["universe"] == res["certified"] > 0 and res["uncertified"] == [], res)
sys.exit(1 if fails else 0)
