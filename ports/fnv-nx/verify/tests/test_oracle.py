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
    T + "docs/SPEC.md": "# Spec\n## 4.2 Rounding\ntext\n## Twice\n## Twice\n",
    T + "docs/TOOL.md": "## tool\nRule of the tool: sorted output.\n",
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
SPEC = {"kind": "spec", "path": T + "docs/TOOL.md", "anchor": "## tool"}
TREE = "e" * 64
ART = {"b" * 40: (TREE, {"ref/x.bin", "ref/y.bin"}), "f" * 40: (None, "not our ref")}
WINE = {"kind": "wine-exe", "wine": "9.0", "exe_sha256": PIN,
        "artifact": {"repo": "fnv-nx-build", "commit": "b" * 40, "path": "ref/x.bin", "tree_sha256": TREE}}
GOOD = {"format": "fnvnx-oracle/1", "tests": {
    "test_a.py": {"subject": [T + "verify/tool.py"], "references": [SPEC]},
    "test_b.py": {"subject": [T + "game/body.cc"], "references": [WINE, {"kind": "model", "path": T + "verify/model.py"}]}}}


def run(doc, files=FILES):
    return G.judge(files, {D: doc}, TRACKED, ROOTS, PINS, lambda p: SRC[p], lambda c: ART.get(c, (None, "unknown commit")))


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
check("no universe: nothing uncertified", G.judge([], {}, TRACKED, ROOTS, PINS, lambda p: SRC[p], None) == {})
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
bad_a("free-text spec (document, section, no file) is not a reference",
      with_a(references=[{"kind": "spec", "document": "IEEE 754-2008", "section": "5.4.1"}]), "spec: keys")
bad_a("spec with a url instead of a file", with_a(references=[{"kind": "spec", "url": "https://x", "anchor": "#a"}]), "spec: keys")
bad_a("spec anchor is not a heading of the file", with_a(references=[dict(SPEC, anchor="## nobody wrote this")]), "anchor")
bad_a("spec anchor is body text, not a heading", with_a(references=[dict(SPEC, anchor="Rule of the tool: sorted output.")]), "anchor")
bad_a("spec heading that occurs twice", with_a(references=[{"kind": "spec", "path": T + "docs/SPEC.md", "anchor": "## Twice"}]), "anchor")
bad_a("spec path untracked", with_a(references=[dict(SPEC, path=T + "docs/NONE.md")]), "not a tracked file")
check("spec with a line range certified", run(with_a(references=[{"kind": "spec", "path": T + "docs/SPEC.md", "anchor": "L2-L3"}])) == {})
check("spec with a one-line range certified", run(with_a(references=[{"kind": "spec", "path": T + "docs/SPEC.md", "anchor": "L5-L5"}])) == {})
for bad in ("L0-L2", "L3-L2", "L2-L6", "L2", "L02-L3", "L2-L3x", "L-1-L2", 7):
    bad_a("spec line range %r refused" % (bad,), with_a(references=[{"kind": "spec", "path": T + "docs/SPEC.md", "anchor": bad}]), "anchor")
check("spec heading in a doc root certified", run(with_a(references=[
    {"kind": "spec", "path": T + "docs/SPEC.md", "anchor": "## 4.2 Rounding"}])) == {})
bad_a("tool citing its own file", with_a(references=[{"kind": "spec", "path": T + "verify/tool.py", "anchor": "L1-L1"}]), "import closure")
bad_a("tool citing a file it imports", with_a(references=[{"kind": "spec", "path": T + "verify/helper.py", "anchor": "L1-L1"}]),
      "import closure")
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
bad_b("artifact tree_sha256 upper case", with_b(references=[dict(WINE, artifact=dict(WINE["artifact"], tree_sha256="E" * 64))]), "64 hex")
bad_b("artifact tree is not the declared one", with_b(references=[dict(WINE, artifact=dict(WINE["artifact"], tree_sha256="d" * 64))]),
      "tree_sha256 of")
bad_b("artifact commit cannot be fetched", with_b(references=[dict(WINE, artifact=dict(WINE["artifact"], commit="f" * 40))]),
      "cannot be fetched")
bad_b("artifact path is not a file of the commit", with_b(references=[dict(WINE, artifact=dict(WINE["artifact"], path="ref/z.bin"))]),
      "not a file of")
bad_b("artifact in another repository", with_b(references=[dict(WINE, artifact=dict(WINE["artifact"], repo="nx-agent"))]),
      "fnv-nx-build")
bad_b("artifact with a missing key", with_b(references=[dict(WINE, artifact={"repo": "fnv-nx-build"})]), "artifact keys")
bad_b("wine-exe with an extra key", with_b(references=[dict(WINE, note="x")]), "wine-exe: keys")
r = G.judge(FILES, {D: GOOD}, TRACKED, ROOTS, {}, lambda p: SRC[p], lambda c: ART[c])
check("no pinned exe: wine-exe uncertified", set(r) == {D + "/test_b.py"}, r)
bad_b("product subject with a model only", with_b(references=[{"kind": "model", "path": T + "verify/model.py"}]),
      "no wine-exe or spec")
bad_b("product subject with a contract only", with_b(references=[{"kind": "contract", "path": T + "verify/table.tsv"}]),
      "no wine-exe or spec")
bad_b("product subject citing its own text", with_b(references=[
    {"kind": "spec", "path": T + "game/body.cc", "anchor": "L1-L1"}]), "import closure")
bad_b("product subject citing a text of the product root", with_b(references=[
    {"kind": "spec", "path": T + "game/ref.tsv", "anchor": "L1-L1"}]), "lies in a product root")
bad_b("product subject with a table of the product root", with_b(references=[WINE, {"kind": "contract", "path": T + "game/ref.tsv"}]),
      "lies in a product root")
check("product subject with a spec outside the product root certified", run(with_b(references=[
    {"kind": "spec", "path": T + "docs/SPEC.md", "anchor": "## 4.2 Rounding"}])) == {})

# closure: every name python could load
c, why = G.closure([T + "verify/tool.py"], TRACKED, lambda p: SRC[p])
check("closure follows import and from-import", why is None and c == {T + "verify/tool.py", T + "verify/helper.py",
                                                                       T + "verify/pkg/deep.py"}, (c, why))
# the artifact fetch: a local repository stands in for the artifacts repository
def git(d, *a):
    return subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@invalid"] + list(a), capture_output=True,
                          text=True, check=True).stdout.strip()


ftmp = tempfile.mkdtemp(prefix="oracle-test-fetch-")
repo = os.path.join(ftmp, "repo")
os.makedirs(os.path.join(repo, "ref"))
git(repo, "init", "-q")
open(os.path.join(repo, "ref", "x.bin"), "w").write("dump\n")
git(repo, "add", "-A")
git(repo, "commit", "-qm", "ref")
commit = git(repo, "rev-parse", "HEAD")
git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
os.environ["ARTIFACTS_URL"] = repo
fetch = G.fetcher(os.path.join(ftmp, "cache"))
os.makedirs(os.path.join(ftmp, "cache"))
tree, files = fetch(commit)
sys.path.insert(0, os.path.dirname(HERE))
import cas  # noqa: E402
plain = os.path.join(ftmp, "plain")
os.makedirs(os.path.join(plain, "ref"))
open(os.path.join(plain, "ref", "x.bin"), "w").write("dump\n")
check("fetch: tree hash of the commit and its files", tree == cas.tree_hash(plain) and files == {"ref/x.bin"}, (tree, files))
check("fetch: the bare fetch is kept by commit", os.path.isdir(os.path.join(ftmp, "cache", commit)))
t2, why = G.fetcher(os.path.join(ftmp, "cache"))("9" * 40)
check("fetch: an unknown commit is an error, not a tree", t2 is None and isinstance(why, str) and why, (t2, why))
del os.environ["ARTIFACTS_URL"]

# repeated key in oracle.json
tmp = tempfile.mkdtemp(prefix="oracle-test-")
p = os.path.join(tmp, "o.json")
open(p, "w").write('{"format": "fnvnx-oracle/1", "tests": {"test_a.py": 1, "test_a.py": 2}}')
check("repeated entry is not a declaration", G.load(p) is None)
open(p, "w").write("{")
check("malformed json is not a declaration", G.load(p) is None)

# the gate program on a synthetic checkout: one tool, one test, one spec reference
root = os.path.join(tmp, "repo")
T = "ports/fnv-nx"
tree = {T + "/verify/gates.d/selftests.json": json.dumps({"format": "fnvnx-gatefile/1", "name": "selftests", "result": "selftests.json",
                                                         "argv": ["python3", "-I", T + "/verify/gate_tests.py", T + "/x"], "covers": ["tool"]}),
        T + "/verify/admit.json": json.dumps({"roots": [{"path": T + "/x", "class": "tool", "gates": []}]}),
        T + "/verify/oracle_pins.json": "{}",
        T + "/x/a.py": "def f():\n    return 1\n",
        T + "/x/RULE.md": "# rule\nf returns 1\n",
        T + "/x/tests/test_a.py": "",
        T + "/x/tests/oracle.json": json.dumps({"format": "fnvnx-oracle/1", "tests": {"test_a.py": {
            "subject": [T + "/x/a.py"], "references": [{"kind": "spec", "path": T + "/x/RULE.md", "anchor": "# rule"}]}}})}
for f, text in tree.items():
    os.makedirs(os.path.dirname(os.path.join(root, f)), exist_ok=True)
    open(os.path.join(root, f), "w").write(text)
subprocess.run(["git", "init", "-q", root], check=True)
subprocess.run(["git", "-C", root, "add", "-A"], check=True)
out = os.path.join(tmp, "out")
os.makedirs(out)
env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_")}
GATE = os.path.join(os.path.dirname(HERE), "gate_oracle.py")
rc = subprocess.run([sys.executable, "-I", GATE], cwd=root, env=dict(env, GATE_OUT=out)).returncode
res = json.load(open(os.path.join(out, "oracle.json")))
check("gate program: green, result in the gate format", rc == 0 and res["status"] == "green" and res["name"] == "oracle"
      and res["format"] == "fnvnx-gate/1" and res["universe"] == res["certified"] == 1 and res["uncertified"] == []
      and res["references"] == {"spec": 1}, res)
os.remove(os.path.join(root, T + "/x/tests/oracle.json"))
rc = subprocess.run([sys.executable, "-I", GATE], cwd=root, env=dict(env, GATE_OUT=out)).returncode
res = json.load(open(os.path.join(out, "oracle.json")))
check("gate program: test without a declaration -> red, exit 2", rc == 2 and res["status"] == "red"
      and res["uncertified"] == [T + "/x/tests/test_a.py"], res)
sys.exit(1 if fails else 0)
