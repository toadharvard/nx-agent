#!/usr/bin/env python3
"""Tests for verify/verdict.py (L4 verdict, merge gate, provenance, rule-defect queue).
Plain script: exit 0 on pass, one PASS/FAIL line per case. Uses temp git repositories and a temp CAS store."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import cas  # noqa: E402
import sandbox  # noqa: E402

BASE = "/root/l4/verdict-test"
os.makedirs(BASE, exist_ok=True)
ROOT = tempfile.mkdtemp(dir=BASE, prefix="vd-")
TOOL = os.path.join(VERIFY, "verdict.py")
FAILED = []
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
_n = [0]

def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)

def tmp(name):
    _n[0] += 1
    d = os.path.join(ROOT, "%02d-%s" % (_n[0], name))
    os.makedirs(d)
    return d

def tool(*a):
    p = subprocess.run([sys.executable, "-I", TOOL] + list(a), capture_output=True, text=True, env=ENV)
    return p.returncode, p.stdout, p.stderr

def git(repo, *a, inp=None):
    p = subprocess.run(["git", "-C", repo] + list(a), capture_output=True, text=True, env=ENV, input=inp)
    if p.returncode:
        raise RuntimeError(p.stderr)
    return p.stdout.strip()

def commit_file(repo, name, text, msg="c"):
    open(os.path.join(repo, name), "w").write(text)
    git(repo, "add", name)
    git(repo, "commit", "-m", msg)

def make_repo():
    r = tmp("repo")
    git(r, "init", "-b", "main")
    commit_file(r, "base.txt", "base\n")
    git(r, "branch", "feat")
    git(r, "checkout", "feat")
    commit_file(r, "feat.txt", "feat\n")
    git(r, "checkout", "main")
    return r

def candidate(r, into="main", frm="feat"):
    tree = git(r, "merge-tree", "--write-tree", into, frm).splitlines()[0]
    return git(r, "commit-tree", tree, "-p", into, "-p", frm, "-m", "candidate"), tree

def gate(name, status="green", universe=2, certified=2, rc=0, write="ok", uncertified=()):
    res = {"format": "fnvnx-gate/1", "name": name, "status": status, "universe": universe, "certified": certified,
           "uncertified": list(uncertified), "detail": "d"}
    code = "import os,sys\n"
    if write == "ok":
        code += "open(os.path.join(os.environ['GATE_OUT'],'r.json'),'w').write(%r)\n" % json.dumps(res)
    elif write == "bad":
        code += "open(os.path.join(os.environ['GATE_OUT'],'r.json'),'w').write('{nope')\n"
    code += "sys.exit(%d)\n" % rc
    return {"name": name, "argv": [sys.executable, "-I", "-c", code], "result": "r.json"}

def gates_file(d, gates, fname="gates.json"):
    p = os.path.join(d, fname)
    json.dump({"format": "fnvnx-gates/1", "gates": gates}, open(p, "w"))
    return p

def run_verdict(r, c, gp, out, *extra):
    return tool("run", "--repo", r, "--commit", c, "--gates", gp, "--out", out, *extra)

def head(r, ref="main"):
    return git(r, "rev-parse", "refs/heads/" + ref)

def blocked(name, r, rc_exp, args, word):
    before = head(r)
    rc, out, err = tool("merge", "--repo", r, "--into", "main", "--from", "feat", *args)
    check(name, rc == rc_exp and head(r) == before and word in err, (rc, out, err))

def prepared(gates, name="p"):
    r = make_repo()
    d = tmp(name)
    gp = gates_file(d, gates)
    c, tree = candidate(r)
    v = os.path.join(d, "v.json")
    rc, out, err = run_verdict(r, c, gp, v)
    vd = os.path.join(d, "verdicts")
    os.makedirs(vd)
    return r, d, gp, c, tree, v, vd, rc

# 1 green path end to end
t0 = time.time()
r, d, gp, c, tree, v, vd, rc = prepared([gate("a"), gate("b")], "green")
vj = json.load(open(v))
check("run green exit 0 and fields", rc == 0 and vj["verdict"] == "green" and vj["tree"] == tree and vj["commit"] == c
      and len(vj["gates"]) == 2 and vj["gates_sha256"] == hashlib.sha256(open(gp, "rb").read()).hexdigest()
      and vj["over_budget"] is False, vj)
rc2, _, e2 = tool("store", "--verdict", v, "--verdicts", vd)
check("store green", rc2 == 0 and os.path.exists(os.path.join(vd, tree + ".json")), e2)
old = head(r)
rc3, out3, e3 = tool("merge", "--repo", r, "--into", "main", "--from", "feat", "--verdicts", vd, "--gates", gp)
new = head(r)
body = git(r, "log", "-1", "--format=%B", new)
lines = body.rstrip("\n").split("\n")
check("merge green moves branch, 2 parents, message = subject and the verdict line", rc3 == 0 and out3.strip() == new and new != old
      and git(r, "rev-parse", new + "^1") == old and git(r, "rev-parse", new + "^2") == head(r, "feat")
      and git(r, "rev-parse", new + "^{tree}") == tree
      and lines == ["merge feat", "", "verdict: green, tree %s, gates 2" % tree], (rc3, e3, body))
print("INFO green path seconds: %.2f" % (time.time() - t0))

# 2 blocking reasons
r, d, gp, c, tree, v, vd, _ = prepared([gate("a")], "noverdict")
blocked("block: no verdict file", r, 2, ["--verdicts", vd, "--gates", gp], "no verdict")

r, d, gp, c, tree, v, vd, rc = prepared([gate("a"), gate("b", status="red")], "red")
tool("store", "--verdict", v, "--verdicts", vd)
check("red verdict stored", rc == 2 and os.path.exists(os.path.join(vd, tree + ".json")))
blocked("block: red verdict", r, 2, ["--verdicts", vd, "--gates", gp], "red")

r, d, gp, c, tree, v, vd, rc = prepared([gate("a")], "moved")
tool("store", "--verdict", v, "--verdicts", vd)
git(r, "checkout", "main")
commit_file(r, "other.txt", "x\n")
blocked("block: verdict for another tree (branch moved)", r, 2, ["--verdicts", vd, "--gates", gp], "no verdict")

r, d, gp, c, tree, v, vd, rc = prepared([gate("a")], "gatesdiff")
tool("store", "--verdict", v, "--verdicts", vd)
gp2 = os.path.join(d, "gates2.json")
open(gp2, "wb").write(open(gp, "rb").read() + b"\n")
blocked("block: different gates file", r, 2, ["--verdicts", vd, "--gates", gp2], "gates")

r, d, gp, c, tree, v, vd, rc = prepared([gate("a")], "ratchet")
tool("store", "--verdict", v, "--verdicts", vd)
prev = os.path.join(d, "prev.json")
json.dump({"format": "fnvnx-verdict/1", "commit": "0" * 40, "tree": "1" * 40, "gates_sha256": "0" * 64, "verdict": "green",
           "over_budget": False, "seconds": 0, "gates": [{"name": n, "status": "green", "universe": 1, "certified": 1,
                                                          "seconds": 0, "reason": ""} for n in ("a", "b")]}, open(prev, "w"))
blocked("block: gate removed vs prev verdict", r, 2, ["--verdicts", vd, "--gates", gp, "--prev-verdict", prev], "b")

r, d, gp, c, tree, v, vd, rc = prepared([gate("a")], "conflict")
git(r, "checkout", "main"); commit_file(r, "feat.txt", "other\n", "m")
blocked("block: merge conflict exit 3", r, 3, ["--verdicts", vd, "--gates", gp], "conflict")

# CAS: another writer moves the branch between commit-tree and update-ref
import verdict  # noqa: E402
r, d, gp, c, tree, v, vd, rc = prepared([gate("a")], "cas")
tool("store", "--verdict", v, "--verdicts", vd)
real = verdict._git
moved = []
def racing(repo, *a, **k):
    out = real(repo, *a, **k)
    if a[0] == "commit-tree":
        git(r, "checkout", "-q", "main")
        commit_file(r, "race.txt", "r\n")
        moved.append(head(r))
    return out
verdict._git = racing
before_err = sys.stderr
rcc = verdict.main(["merge", "--repo", r, "--into", "main", "--from", "feat", "--verdicts", vd, "--gates", gp])
verdict._git = real
check("block: concurrent ref change (CAS fails)", rcc == 2 and moved and head(r) == moved[0], (rcc, moved))

# 3 gate result classes
def red_case(name, g, exp):
    r, d, gp, c, tree, v, vd, rc = prepared([g], name)
    vj = json.load(open(v))
    gr = vj["gates"][0]
    check("gate class: " + name, rc == 2 and vj["verdict"] == "red" and gr["status"] == "red" and exp in gr["reason"], (rc, vj))
red_case("missing file", gate("g", write="none"), "missing")
red_case("bad json", gate("g", write="bad"), "invalid")
red_case("exit nonzero with green", gate("g", rc=3), "exit")
red_case("certified < universe", gate("g", universe=5, certified=3, uncertified=["i1", "i2"]), "i1,i2")
red_case("certified > universe", gate("g", universe=1, certified=2), "certified")
red_case("name mismatch", dict(gate("zz"), name="g"), "name")
r = make_repo(); d = tmp("zero"); gp = gates_file(d, []); v = os.path.join(d, "v.json")
rc, _, _ = run_verdict(r, head(r), gp, v)
check("gate class: zero gates is red", rc == 2 and json.load(open(v))["verdict"] == "red")

r = make_repo(); d = tmp("budget"); gp = gates_file(d, [gate("a")]); v = os.path.join(d, "v.json")
rc, _, _ = run_verdict(r, head(r), gp, v, "--budget-s", "0")
vj = json.load(open(v))
check("over_budget reported, verdict stays green", rc == 0 and vj["over_budget"] is True and vj["verdict"] == "green", vj)

# 4 provenance
def prov_env():
    d = tmp("prov")
    store, docs = os.path.join(d, "store"), os.path.join(d, "docs")
    os.makedirs(store); os.makedirs(docs)
    return d, store, docs

def tree_of(d, files):
    p = os.path.join(d, "t%d" % len(os.listdir(d)))
    for n, t in files.items():
        os.makedirs(os.path.dirname(os.path.join(p, n)), exist_ok=True)
        open(os.path.join(p, n), "w").write(t)
    os.makedirs(p, exist_ok=True)
    return p

def stage(d, store, docs, name, cls, inputs, out_tree, with_result=True):
    doc = {"format": "fnvnx-stage/1", "stage": name, "argv": ["/bin/true"], "cwd": "/", "env": {},
           "inputs": [{"at": at, "tree": t} for at, t in inputs], "outputs": ["/out"]}
    if cls:
        doc["class"] = cls
    json.dump(doc, open(os.path.join(docs, name + ".json"), "w"))
    if with_result:
        sandbox._store_result(store, sandbox.key_of(doc), {"format": "fnvnx-stage-result/1", "stage": name,
                                                           "outputs": {"/out": out_tree}})

def ing(d, store, files):
    return cas.ingest(tree_of(d, files), store)

d, store, docs = prov_env()
src, b1, v1 = ing(d, store, {"s": "1"}), ing(d, store, {"b": "1"}), ing(d, store, {"v": "1"})
stage(d, store, docs, "b1", "build", [("/in", src)], b1)
stage(d, store, docs, "v1", "verification", [("/in", b1)], v1)
rc, out, err = tool("provenance", "--store", store, "--docs", docs)
check("provenance: clean graph exit 0", rc == 0, (out, err))

stage(d, store, docs, "b2", "build", [("/in", v1)], ing(d, store, {"b": "2"}))
rc, out, err = tool("provenance", "--store", store, "--docs", docs)
check("provenance: direct taint", rc == 2 and "b2" in out and "v1" in out and "/in" in out, (rc, out, err))

d, store, docs = prov_env()
src, b1, v1 = ing(d, store, {"s": "1"}), ing(d, store, {"b": "1"}), ing(d, store, {"v": "1"})
stage(d, store, docs, "b1", "build", [("/in", src)], b1)
stage(d, store, docs, "v1", "verification", [("/in", b1)], v1)
wrap = ing(d, store, {"sub/dir/v": "1", "other": "x"})
stage(d, store, docs, "b2", "build", [("/in", wrap)], ing(d, store, {"b": "2"}))
rc, out, err = tool("provenance", "--store", store, "--docs", docs)
check("provenance: taint through a subtree", rc == 2 and "b2" in out, (rc, out, err))

d, store, docs = prov_env()
src, b1, v1 = ing(d, store, {"s": "1"}), ing(d, store, {"b": "1"}), ing(d, store, {"v": "1"})
b2o, b3o = ing(d, store, {"b": "2"}), ing(d, store, {"b": "3"})
stage(d, store, docs, "b1", "build", [("/in", src)], b1)
stage(d, store, docs, "v1", "verification", [("/in", b1)], v1)
stage(d, store, docs, "b2", "build", [("/in", v1)], b2o)
stage(d, store, docs, "b3", "build", [("/a", src), ("/b", ing(d, store, {"deep/x": "y", "deep/q/b": "2"}))], b3o)
rc, out, err = tool("provenance", "--store", store, "--docs", docs)
check("provenance: transitive taint through a build stage", rc == 2 and "b2" in out and "b3" in out and "b1 " not in out, (rc, out, err))

d, store, docs = prov_env()
src, b1 = ing(d, store, {"s": "1"}), ing(d, store, {"b": "1"})
stage(d, store, docs, "b1", None, [("/in", src)], b1)
rc, out, err = tool("provenance", "--store", store, "--docs", docs)
check("provenance: missing class exit 1", rc == 1 and "class" in err, (rc, out, err))
check("sandbox: class field accepted, bad class rejected, part of key",
      sandbox.check_doc(dict(json.load(open(os.path.join(docs, "b1.json"))), **{"class": "build"})) is None
      and sandbox.key_of(dict(json.load(open(os.path.join(docs, "b1.json"))), **{"class": "build"}))
      != sandbox.key_of(dict(json.load(open(os.path.join(docs, "b1.json"))), **{"class": "verification"})))
try:
    sandbox.check_doc(dict(json.load(open(os.path.join(docs, "b1.json"))), **{"class": "x"}))
    check("sandbox: bad class rejected", False)
except sandbox.SbxError:
    check("sandbox: bad class rejected", True)

# 5 defect queue
d = tmp("defect"); q = os.path.join(d, "q"); ev = os.path.join(d, "ev.txt"); open(ev, "w").write("evidence\n")
a = ["defect", "--queue", q, "--bench", "fuzz", "--head", "abc", "--item", "f1", "--evidence", ev]
rc1, o1, _ = tool(*a); rc2, o2, _ = tool(*a)
rc3, _, _ = tool("defect", "--queue", q, "--bench", "fuzz", "--head", "abc", "--item", "f2", "--evidence", ev)
rc4, _, _ = tool("defect", "--queue", q, "--bench", "boot", "--head", "abc", "--item", "f1", "--evidence", ev)
files = sorted(os.listdir(q))
ent = json.load(open(os.path.join(q, files[0])))
cont_ok = all(f == hashlib.sha256(cas.canon(json.load(open(os.path.join(q, f))))).hexdigest() + ".json" for f in files)
rcd, od, _ = tool("defects", "--queue", q)
check("defect queue: idempotent, content-addressed, counts",
      (rc1, rc2, rc3, rc4) == (0, 0, 0, 0) and len(files) == 3 and o1 == o2 and cont_ok
      and ent["format"] == "fnvnx-rule-defect/1" and ent["evidence_name"] == "ev.txt"
      and ent["evidence_sha256"] == hashlib.sha256(b"evidence\n").hexdigest()
      and "fuzz\t2" in od and "boot\t1" in od, (files, od))

shutil.rmtree(ROOT, ignore_errors=True)
print("%d failed" % len(FAILED))
sys.exit(1 if FAILED else 0)
