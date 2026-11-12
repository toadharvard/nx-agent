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
import scratch  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

scratch.install()





T = "ports/fnv-nx/"
PIN = "a" * 64
SRC = {
    T + "verify/tool.py": '"""Rule of the tool: sorted output."""\nimport helper\n',
    T + "verify/helper.py": "from pkg import deep\n",
    T + "verify/pkg/deep.py": "x = 1\n",
    T + "verify/model.py": "y = 2\n",
    T + "verify/table.tsv": "a\tb\n",
    T + "verify/broken.py": "def (:\n",
    T + "verify/tests/test_a.py": 'from testcase import case\ncase("a1", True)\ncase("a2", True)\n',
    T + "verify/tests/test_b.py": 'import testcase\ntestcase.case("b1", True)\n',
    T + "docs/SPEC.md": "# Spec\n## 4.2 Rounding\ntext\n## Twice\n## Twice\n",
    T + "docs/TOOL.md": "## tool\nRule of the tool: sorted output.\n",
    T + "game/body.cc": "// Section G\nint f();\n",
    T + "game/ref.tsv": "1\n",
    T + "game/build.json": "{}",
    T + "refs/dump.tsv": "z\n",
}
TRACKED = set(SRC)
ROOTS = [{"path": T + "verify", "class": "tool"}, {"path": T + "docs", "class": "doc"},
         {"path": T + "game", "class": "product"}, {"path": T + "refs", "class": "ref"}]
PINS = {"exe_sha256": PIN}
FILES = [T + "verify/tests/test_a.py", T + "verify/tests/test_b.py"]
D = T + "verify/tests"
SPEC = {"kind": "spec", "path": T + "docs/TOOL.md", "anchor": "## tool"}
TREE = "e" * 64
ART = {"b" * 40: (TREE, {"ref/x.bin", "ref/y.bin"}), "f" * 40: (None, "not our ref")}
WINE = {"kind": "wine-exe", "wine": "9.0", "exe_sha256": PIN,
        "artifact": {"repo": "fnv-nx-build", "commit": "b" * 40, "path": "ref/x.bin", "tree_sha256": TREE}}
GOOD = {"format": "fnvnx-oracle/2", "tests": {
    "test_a.py": {"subject": [T + "verify/tool.py"], "references": [SPEC]},
    "test_b.py": {"subject": [T + "game/body.cc"], "references": [WINE, {"kind": "model", "path": T + "verify/model.py"}]}}}


C1, C2, C3 = "1" * 40, "2" * 40, "3" * 40
T1, T2, T3 = "5" * 64, "6" * 64, "7" * 64
ART.update({C1: (T1, {"p/probe.c", "p/dump.bin"}), C2: (T2, {"w/os"}), C3: (T3, {"x"})})
EXTP = {"probes": {(C1, T1)}, "wineplat": {(C2, T2)}, "twice": {(C1, T1), (C3, T3)}, "badtree": {(C3, "8" * 64)}}


def conv(doc):
    """a test written as {subject, references} -> the per-case form: every case of the file owes all the references"""
    if not isinstance(doc, dict) or not isinstance(doc.get("tests"), dict):
        return doc
    out = dict(doc, tests={})
    for fn, e in doc["tests"].items():
        if isinstance(e, dict) and set(e) == {"subject", "references"} and isinstance(e["references"], list):
            names = G.case_names(SRC[D + "/" + fn])[0] if D + "/" + fn in SRC else []
            refs = {"r%d" % i: r for i, r in enumerate(e["references"])}
            e = {"subject": e["subject"], "refs": refs, "cases": {n: sorted(refs) for n in names}}
        out["tests"][fn] = e
    return out


def judge2(doc, files=FILES):
    return G.judge(files, {D: doc}, TRACKED, ROOTS, PINS, lambda p: SRC[p], lambda c: ART.get(c, (None, "unknown commit")), EXTP)


def run(doc, files=FILES):
    """{test file: reason} of the files with an uncertified case (the first reason)"""
    bad = {}
    for k, v in sorted(judge2(conv(doc), files)[1].items()):
        bad.setdefault(k.split("::")[0], v)
    return bad


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
    case('bad_a', set(r) == {D + "/test_a.py"} and word in r[D + "/test_a.py"], r, key=name)


def bad_b(name, doc, word):
    r = run(doc)
    case('bad_b', set(r) == {D + "/test_b.py"} and word in r[D + "/test_b.py"], r, key=name)


case("good declaration: all certified", run(GOOD) == {}, run(GOOD))
case("no universe: nothing uncertified", G.judge([], {}, TRACKED, ROOTS, PINS, lambda p: SRC[p], None) == ([], {}))
r = run(None)
case("missing oracle.json: every test of the DIR uncertified", set(r) == set(FILES), r)
r = run(dict(GOOD, format="fnvnx-oracle/1"))
case("wrong format: every test uncertified", set(r) == set(FILES), r)
r = run(dict(GOOD, note="x"))
case("unknown top-level key: every test uncertified", set(r) == set(FILES), r)
d = copy.deepcopy(GOOD)
d["tests"]["test_gone.py"] = d["tests"]["test_a.py"]
r = run(d)
case("entry without a test file: every test uncertified", set(r) == set(FILES) and "test_gone.py" in r[FILES[0]], r)
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
case("spec with a line range certified", run(with_a(references=[{"kind": "spec", "path": T + "docs/SPEC.md", "anchor": "L2-L3"}])) == {})
case("spec with a one-line range certified", run(with_a(references=[{"kind": "spec", "path": T + "docs/SPEC.md", "anchor": "L5-L5"}])) == {})
for bad in ("L0-L2", "L3-L2", "L2-L6", "L2", "L02-L3", "L2-L3x", "L-1-L2", 7):
    bad_a("spec line range %r refused" % (bad,), with_a(references=[{"kind": "spec", "path": T + "docs/SPEC.md", "anchor": bad}]), "anchor")
case("spec heading in a doc root certified", run(with_a(references=[
    {"kind": "spec", "path": T + "docs/SPEC.md", "anchor": "## 4.2 Rounding"}])) == {})
bad_a("tool citing its own file", with_a(references=[{"kind": "spec", "path": T + "verify/tool.py", "anchor": "L1-L1"}]), "import closure")
bad_a("tool citing a file it imports", with_a(references=[{"kind": "spec", "path": T + "verify/helper.py", "anchor": "L1-L1"}]),
      "import closure")
case("model outside the closure certified", run(with_a(references=[{"kind": "model", "path": T + "verify/model.py"}])) == {})
case("contract table certified", run(with_a(references=[{"kind": "contract", "path": T + "verify/table.tsv"}])) == {})
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
r = G.judge(FILES, {D: conv(GOOD)}, TRACKED, ROOTS, {}, lambda p: SRC[p], lambda c: ART[c])[1]
case("no pinned exe: wine-exe uncertified", set(k.split("::")[0] for k in r) == {D + "/test_b.py"}, r)
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
case("product subject with a spec outside the product root certified", run(with_b(references=[
    {"kind": "spec", "path": T + "docs/SPEC.md", "anchor": "## 4.2 Rounding"}])) == {})

# closure: every name python could load
c, why = G.closure([T + "verify/tool.py"], TRACKED, lambda p: SRC[p])
case("closure follows import and from-import", why is None and c == {T + "verify/tool.py", T + "verify/helper.py",
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
case("fetch: tree hash of the commit and its files", tree == cas.tree_hash(plain) and files == {"ref/x.bin"}, (tree, files))
case("fetch: the bare fetch is kept by commit", os.path.isdir(os.path.join(ftmp, "cache", commit)))
t2, why = G.fetcher(os.path.join(ftmp, "cache"))("9" * 40)
case("fetch: an unknown commit is an error, not a tree", t2 is None and isinstance(why, str) and why, (t2, why))
del os.environ["ARTIFACTS_URL"]

# repeated key in oracle.json
tmp = tempfile.mkdtemp(prefix="oracle-test-")
p = os.path.join(tmp, "o.json")
open(p, "w").write('{"format": "fnvnx-oracle/2", "tests": {"test_a.py": 1, "test_a.py": 2}}')
case("repeated entry is not a declaration", G.load(p) is None)
open(p, "w").write("{")
case("malformed json is not a declaration", G.load(p) is None)

# the gate program on a synthetic checkout: one tool, one test, one spec reference
root = os.path.join(tmp, "repo")
T = "ports/fnv-nx"
tree = {T + "/verify/gates.d/selftests.json": json.dumps({"format": "fnvnx-gatefile/1", "name": "selftests", "result": "selftests.json",
                                                         "argv": ["python3", "-I", T + "/verify/gate_tests.py", T + "/x"], "covers": ["tool"]}),
        T + "/verify/admit.json": json.dumps({"roots": [{"path": T + "/x", "class": "tool", "gates": []}]}),
        T + "/verify/oracle_pins.json": "{}",
        T + "/x/a.py": "def f():\n    return 1\n",
        T + "/x/RULE.md": "# rule\nf returns 1\n",
        T + "/x/tests/test_a.py": "from testcase import case\ncase('f returns 1', True)\n",
        T + "/x/tests/oracle.json": json.dumps({"format": "fnvnx-oracle/2", "tests": {"test_a.py": {
            "subject": [T + "/x/a.py"], "refs": {"r": {"kind": "spec", "path": T + "/x/RULE.md", "anchor": "# rule"}},
            "cases": {"f returns 1": ["r"]}}}})}
for f, text in tree.items():
    os.makedirs(os.path.dirname(os.path.join(root, f)), exist_ok=True)
    open(os.path.join(root, f), "w").write(text)
subprocess.run(["git", "init", "-q", root], check=True)
subprocess.run(["git", "-C", root, "add", "-A"], check=True)
out = os.path.join(tmp, "out")
os.makedirs(out)
env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_")}
U = "ports/fnv-nx/"
# oracle REF kinds external and probe; class ref
EXT = {"kind": "external", "name": "probes", "path": "p/dump.bin"}
PROBE = {"kind": "probe", "probe": {"external": "probes", "path": "p/probe.c"}, "platform": "wineplat", "dump_sha256": "9" * 64}
case("external: pinned name and a file of it -> green", run(with_a(references=[EXT])) == {}, run(with_a(references=[EXT])))
case("probe: probe source, platform and dump hash -> green", run(with_a(references=[PROBE])) == {}, run(with_a(references=[PROBE])))
case("external + probe + spec together -> green", run(with_a(references=[SPEC, EXT, PROBE])) == {})
bad_a("external: a name no recipe pins is red", with_a(references=[dict(EXT, name="nope")]), "not pinned")
bad_a("external: a name pinned to two commits is red", with_a(references=[dict(EXT, name="twice")]), "more than one")
bad_a("external: a path that is not a file of the external is red", with_a(references=[dict(EXT, path="p/none")]), "not a file")
bad_a("external: a tree_sha256 that differs from the pin is red", with_a(references=[dict(EXT, name="badtree", path="x")]), "tree_sha256")
bad_a("external: unknown key is red", with_a(references=[dict(EXT, extra=1)]), "keys")
bad_a("probe: probe path not in the external is red", with_a(references=[dict(PROBE, probe={"external": "probes", "path": "p/none"})]), "not a file")
bad_a("probe: unpinned platform is red", with_a(references=[dict(PROBE, platform="nope")]), "not pinned")
bad_a("probe: dump_sha256 not 64 hex is red", with_a(references=[dict(PROBE, dump_sha256="12")]), "64 hex")
bad_a("probe: missing platform key is red", with_a(references=[{k: v for k, v in PROBE.items() if k != "platform"}]), "keys")
d = copy.deepcopy(GOOD)
d["tests"]["test_b.py"]["references"] = [EXT]
bad_b("a product subject with only an external reference is red", d, "wine-exe or spec")
REFROOT = {"kind": "contract", "path": U + "refs/dump.tsv"}
d = copy.deepcopy(GOOD)
d["tests"]["test_b.py"]["references"] = [WINE, REFROOT]
case("a product subject may cite a file of a ref root", run(d) == {}, run(d))
d = copy.deepcopy(GOOD)
d["tests"]["test_b.py"]["references"] = [WINE, {"kind": "model", "path": U + "game/ref.tsv"}]
bad_b("a record whose ref points into a product root stays red", d, "product root")
bad_a("a subject inside a ref root is red", with_a(subject=[U + "refs/dump.tsv"]), "class ref")

# ---- per-case rule: a reference is owed per case, not per file
def cases(src):
    return G.case_names(src)


case("cases: a literal testcase.case call is a case, found through `from testcase import case`",
     cases('from testcase import case\ncase("a", 1)\ncase("b", 1)\n') == (["a", "b"], None))
case("cases: found through `import testcase` and the module attribute",
     cases('import testcase\ntestcase.case("a", 1)\n') == (["a"], None))
case("cases: found through an alias of the import",
     cases('from testcase import case as c\nc("a", 1)\n') == (["a"], None))
case("cases: a def test_* and a unittest.TestCase method are cases",
     cases('import unittest\ndef test_x(): pass\nclass K(unittest.TestCase):\n    def test_y(self): pass\n    def helper(self): pass\n')
     == (["test_x", "K.test_y"], None))
case("cases: a file with neither form is red", cases('print(1)\n')[1] is not None and "no test case" in cases('print(1)\n')[1])
case("cases: a helper named check is not a case (no guessing by name)", cases('def check(n, ok): pass\ncheck("a", 1)\n')[1] is not None)
case("cases: a case name that is not a literal is red", "not a string constant" in (cases('from testcase import case\nn = "a"\ncase(n, 1)\n')[1] or ""))
case("cases: a name built by + is red even with key=", "not a string constant" in (cases('from testcase import case\ncase("a" + "b", 1, key=3)\n')[1] or ""))
case("cases: a duplicate case name in one file is red", "duplicate" in (cases('from testcase import case\ncase("a", 1)\ncase("a", 2)\n')[1] or ""))
case("cases: a local function named case is red", "binds" in (cases('def case(n, ok): pass\ncase("a", 1)\n')[1] or ""))
case("cases: a local def case next to the import is red", "binds" in (cases('from testcase import case\ndef case(n, ok): pass\ncase("a", 1)\n')[1] or ""))
case("cases: a loop variable named case is red", "binds" in (cases('from testcase import case\nfor case in []: pass\ncase("a", 1)\n')[1] or ""))
case("cases: a case in a loop with key= is one static case",
     cases('from testcase import case\nfor k in range(3):\n    case("a", True, key=k)\n') == (["a"], None))
case("cases: a call of a function with another source named case is not the shared one",
     cases('from other import case\ncase("a", 1)\n')[1] is not None)

def tests_doc(names_to_refs, extra=None):
    e = {"subject": [U + "verify/tool.py"], "refs": {"r1": SPEC, "r2": {"kind": "model", "path": U + "verify/model.py"}},
         "cases": names_to_refs}
    e.update(extra or {})
    return {"format": "fnvnx-oracle/2", "tests": {"test_a.py": e, "test_b.py": {"subject": [U + "game/body.cc"], "refs": {"w": WINE}, "cases": {"b1": ["w"]}}}}


u, bad = judge2(tests_doc({"a1": ["r1"], "a2": ["r2"]}))
case("per case: every case with its own reference -> green; the universe counts cases", bad == {} and u == [D + "/test_a.py::a1", D + "/test_a.py::a2", D + "/test_b.py::b1"], (u, bad))
u, bad = judge2(tests_doc({"a1": ["r1", "r2"]}))
case("per case: a case without an entry is uncertified although another case of its file has one",
     set(bad) == {D + "/test_a.py::a2"} and "no entry" in bad[D + "/test_a.py::a2"], bad)
u, bad = judge2(tests_doc({"a1": ["r1"], "a2": ["r1"], "ghost": ["r1"]}))
case("per case: an entry for a case that does not exist makes the file uncertified",
     set(bad) == {D + "/test_a.py::a1", D + "/test_a.py::a2"} and "ghost" in bad[D + "/test_a.py::a1"], bad)
u, bad = judge2(tests_doc({"a1": ["r1"], "a2": []}, {"refs": {"r1": SPEC}}))
case("per case: empty references of one case uncertify that case only", set(bad) == {D + "/test_a.py::a2"} and "empty" in bad[D + "/test_a.py::a2"], bad)
u, bad = judge2(tests_doc({"a1": ["r1"], "a2": ["zz"]}, {"refs": {"r1": SPEC}}))
case("per case: an id that refs does not hold is red for that case", set(bad) == {D + "/test_a.py::a2"} and "refs does not hold" in bad[D + "/test_a.py::a2"], bad)
u, bad = judge2(tests_doc({"a1": ["r1"], "a2": ["r1"]}))
case("per case: a reference no case uses is an error of the file", set(bad) == {D + "/test_a.py::a1", D + "/test_a.py::a2"} and "not used" in bad[D + "/test_a.py::a1"], bad)
u, bad = judge2(tests_doc({"a1": ["r1"], "a2": ["r1"]}, {"refs": {"r1": SPEC}}))
case("per case: all references used -> green", bad == {}, bad)
SAVE = SRC[D + "/test_a.py"]
SRC[D + "/test_a.py"] = "print(1)\n"
u, bad = judge2(tests_doc({}))
case("script-style: a file without cases is one uncertified item, the file itself", D + "/test_a.py" in bad and D + "/test_a.py" in u, (u, bad))
SRC[D + "/test_a.py"] = 'from testcase import case\nn = "x"\ncase(n, 1)\n'
u, bad = judge2(tests_doc({}))
case("script-style: a case with a non-literal name makes the file uncertified", "not a string constant" in bad.get(D + "/test_a.py", ""), bad)
SRC[D + "/test_a.py"] = SAVE

GATE = os.path.join(os.path.dirname(HERE), "gate_oracle.py")
rc = subprocess.run([sys.executable, "-I", GATE], cwd=root, env=dict(env, GATE_OUT=out)).returncode
res = json.load(open(os.path.join(out, "oracle.json")))
case("gate program: green, result in the gate format", rc == 0 and res["status"] == "green" and res["name"] == "oracle"
      and res["format"] == "fnvnx-gate/1" and res["universe"] == res["certified"] == 1 and res["uncertified"] == []
      and res["references"] == {"spec": 1}, res)
os.remove(os.path.join(root, T + "/x/tests/oracle.json"))
rc = subprocess.run([sys.executable, "-I", GATE], cwd=root, env=dict(env, GATE_OUT=out)).returncode
res = json.load(open(os.path.join(out, "oracle.json")))
case("gate program: test without a declaration -> red, exit 2", rc == 2 and res["status"] == "red"
      and res["uncertified"] == [T + "/x/tests/test_a.py::f returns 1"], res)
sys.exit(testcase.finish())
