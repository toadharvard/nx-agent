#!/usr/bin/env python3
"""Tests for verify/gate_pure.py, verify/gate_tests.py, verify/gates.json and verify/l1.py.
Plain script: exit 0 on pass, one PASS/FAIL line per case. Uses temp git repositories."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
BASE = "/root/l4/gates-test"
os.makedirs(BASE, exist_ok=True)
ROOT = tempfile.mkdtemp(dir=BASE, prefix="g-")
FAILED = []
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
_n = [0]
# the tool files the gate machinery itself needs in a checkout (verdict.py/l1.py run from VERIFY, on the repos built here);
# the synthetic repos hold only tiny test files, never this directory's tests (no nested run of the real test suite)
SMALL = ("gate_pure.py", "gate_tests.py", "puregate.py", "puregate_policy.py", "cas.py", "envcheck.py")
CLEAN = "import sys\n\ndef f(x):\n    return sorted(x)\n"
BAD = "import random\n\nx = random.random()\n"

def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)

def tmp(name):
    _n[0] += 1
    d = os.path.join(ROOT, "%02d-%s" % (_n[0], name))
    os.makedirs(d)
    return d

def git(repo, *a, inp=None):
    p = subprocess.run(["git", "-C", repo] + list(a), capture_output=True, text=True, env=ENV, input=inp)
    if p.returncode:
        raise RuntimeError(p.stderr)
    return p.stdout.strip()

def put(d, rel, text, mode=None):
    p = os.path.join(d, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write(text)
    if mode:
        os.chmod(p, mode)

def gate(script, cwd, args):
    out = tempfile.mkdtemp(dir=ROOT, prefix="out-")
    p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, script)] + args, cwd=cwd, capture_output=True, text=True,
                       env=dict(os.environ, GATE_OUT=out, GATE_COMMIT="0" * 40))
    res = None
    for f in sorted(x for x in os.listdir(out) if not x.endswith(".times.json")):
        res = json.load(open(os.path.join(out, f)))
    return p.returncode, res, p.stderr

def pure(d, dirs=("link", "rules")):
    return gate("gate_pure.py", d, list(dirs))

# ---- gate_pure
d = tmp("pure-none")
rc, r, e = pure(d)
check("pure: no dirs -> green, universe 0", rc == 0 and r and r["name"] == "pure" and r["format"] == "fnvnx-gate/1"
      and r["status"] == "green" and r["universe"] == 0 and r["certified"] == 0 and "nothing to certify" in r["detail"], (rc, r, e))

d = tmp("pure-clean")
put(d, "link/a.py", CLEAN)
put(d, "rules/b.py", CLEAN)
rc, r, e = pure(d)
check("pure: clean files certified", rc == 0 and r["status"] == "green" and (r["universe"], r["certified"]) == (2, 2), (rc, r, e))

d = tmp("pure-bad")
put(d, "link/a.py", CLEAN)
put(d, "link/bad.py", BAD)
rc, r, e = pure(d)
check("pure: random -> red, path listed", rc != 0 and r["status"] == "red" and (r["universe"], r["certified"]) == (2, 1)
      and r["uncertified"] == ["link/bad.py"], (rc, r, e))

d = tmp("pure-sh")
put(d, "link/run.sh", "#!/bin/sh\necho hi\n", 0o755)
rc, r, e = pure(d)
check("pure: shell file is an uncertified item", rc != 0 and r["status"] == "red" and r["universe"] == 1
      and r["certified"] == 0 and r["uncertified"] == ["link/run.sh"], (rc, r, e))

d = tmp("pure-tests")
put(d, "link/a.py", CLEAN)
put(d, "link/tests/test_a.py", BAD)
rc, r, e = pure(d)
check("pure: files below tests/ are outside the universe", rc == 0 and r["status"] == "green"
      and (r["universe"], r["certified"]) == (1, 1), (rc, r, e))

d = tmp("pure-import")
put(d, "link/a.py", "import b\n")
put(d, "link/b.py", CLEAN)
rc, r, e = pure(d)
check("pure: project imports resolve across the run", rc == 0 and (r["universe"], r["certified"]) == (2, 2), (rc, r, e))

d = tmp("pure-cap")
for i in range(60):
    put(d, "link/m%02d.py" % i, BAD)
rc, r, e = pure(d)
check("pure: uncertified capped at 50, sorted", r["universe"] == 60 and len(r["uncertified"]) == 50
      and r["uncertified"] == sorted(r["uncertified"]) and r["uncertified"][0] == "link/m00.py", r)

# ---- gate_tests
def tests(d, dirs=("verify",), extra=()):
    return gate("gate_tests.py", d, list(extra) + list(dirs))

d = tmp("tests-none")
rc, r, e = tests(d)
check("tests: no test files -> green, universe 0", rc == 0 and r["name"] == "selftests" and r["status"] == "green"
      and r["universe"] == 0, (rc, r, e))

d = tmp("tests-mix")
put(d, "verify/tests/test_b.py", "import sys\nsys.exit(0)\n")
put(d, "verify/tests/test_a.py", "import sys\nsys.exit(0)\n")
put(d, "verify/tests/test_c.py", "import sys\nsys.exit(3)\n")
put(d, "verify/tests/helper.py", "import sys\nsys.exit(3)\n")
rc, r, e = tests(d)
check("tests: pass/fail counted, helper ignored", rc != 0 and r["status"] == "red" and (r["universe"], r["certified"]) == (3, 2)
      and r["uncertified"] == ["verify/tests/test_c.py"] and r["detail"].startswith("2 of 3 test files pass, "), (rc, r, e))

d = tmp("tests-order")
log = os.path.join(d, "order.log")
for n in ("test_z", "test_m", "test_a"):
    put(d, "verify/tests/%s.py" % n, "open(%r, 'a').write(%r + '\\n')\n" % (log, n))
put(d, "link/tests/test_k.py", "open(%r, 'a').write('link\\n')\n" % log)
rc, r, e = tests(d, ("verify", "link"))
order = open(log).read().split()
check("tests: deterministic order (directory argument order, then name)", rc == 0 and order == ["test_a", "test_m", "test_z", "link"]
      and r["universe"] == 4, (rc, order, r))

d = tmp("tests-timeout")
# no wall-clock assertion: the slow file outlives any plausible limit, the quick one has 15 s on any loaded machine
put(d, "verify/tests/test_slow.py", "import time\ntime.sleep(3600)\n")
put(d, "verify/tests/test_ok.py", "pass\n")
rc, r, e = tests(d, extra=("--timeout-s", "15"))
check("tests: timeout is a failure", rc != 0 and r["uncertified"] == ["verify/tests/test_slow.py"] and r["certified"] == 1,
      (rc, r, e))

d = tmp("tests-scratch")
log = os.path.join(d, "where.txt")
put(d, "verify/tests/test_leaves.py", "import tempfile\nd = tempfile.mkdtemp(prefix='left-')\nopen(d + '/f', 'w').write('x')\n"
    "open(%r, 'a').write(d + '\\n')\n" % log)
put(d, "verify/tests/test_leaves_fails.py", "import sys, tempfile\nopen(%r, 'a').write(tempfile.mkdtemp(prefix='left-') + '\\n')\nsys.exit(3)\n" % log)
rc, r, e = tests(d)
left = open(log).read().split()
check("tests: the scratch a file leaves in its temporary directory is removed, passed or failed", rc != 0 and r["certified"] == 1
      and len(left) == 2 and not any(os.path.exists(x) or os.path.exists(os.path.dirname(x)) for x in left)
      and all(os.path.basename(os.path.dirname(x)).startswith("selftest-") for x in left), (rc, r, left))

d = tmp("tests-env")
put(d, "verify/tests/test_env.py", "import os, sys\nsys.exit(1 if [k for k in os.environ if k.startswith('GATE_')] else 0)\n")
rc, r, e = tests(d)
check("tests: a test file does not see the gate's GATE_* variables", rc == 0 and r["certified"] == 1, (rc, r, e))

# ---- gates.json
spec = json.load(open(os.path.join(VERIFY, "gates.json")))
byname = {g["name"]: g for g in spec["gates"]}
CONTRACT = ["n1", "n2_data", "n2_call", "n2_vcall", "xc", "xunit", "rtti", "pcrel", "bodysel", "abs", "disp", "reach", "reachlink", "gpa"]
dirs_pure = [a.rsplit("/", 1)[-1] for a in byname["pure"]["argv"][3:]]
dirs_tests = [a.rsplit("/", 1)[-1] for a in byname["selftests"]["argv"][5:]]
check("gates.json: contract gates run gate_contract.py --gate NAME", all(byname[n]["argv"] == ["python3", "-I", "ports/fnv-nx/verify/gate_contract.py", "--gate", n] and byname[n]["result"] == n + ".json" for n in CONTRACT), spec)
check("gates.json: format and the gates", spec["format"] == "fnvnx-gates/1" and [g["name"] for g in spec["gates"]] == ["pure", "selftests", "admit", "oracle", "build", "objstub", "linkstrict", "determinism", "fixtures"] + CONTRACT + ["argordergate", "imports", "machine", "hostapi", "datamodel"]
      and all(byname[n]["argv"] == ["python3", "-I", "ports/fnv-nx/verify/gate_%s.py" % n] and byname[n]["result"] == n + ".json"
              for n in ("build", "objstub", "linkstrict", "determinism", "hostapi"))
      and byname["argordergate"]["argv"] == ["python3", "-I", "ports/fnv-nx/verify/gate_argorder.py"] and byname["argordergate"]["result"] == "argordergate.json"
      and byname["datamodel"]["argv"] == ["python3", "-I", "ports/fnv-nx/verify/gate_datamodel.py"] and byname["datamodel"]["result"] == "datamodel.json"
      and byname["pure"]["result"] == "pure.json" and byname["selftests"]["result"] == "selftests.json"
      and dirs_pure == ["contract", "link", "rules", "lift"] and dirs_tests == ["verify", "farm", "contract", "link", "rules", "lift"]
      and byname["pure"]["argv"][:3] == ["python3", "-I", "ports/fnv-nx/verify/gate_pure.py"]
      and byname["selftests"]["argv"][:5] == ["python3", "-I", "ports/fnv-nx/verify/gate_tests.py", "--jobs", "3"], spec)

# ---- l1
def l1(*a):
    p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "l1.py")] + list(a), capture_output=True, text=True, env=ENV)
    lines = [x for x in p.stdout.splitlines() if x.startswith("L1 ")]
    return p.returncode, lines[-1] if lines else "", p.stdout, p.stderr

def small_repo(gates=None, link=None):
    """Repo with the small verify subset, a pure gate and a selftests gate over link/, on branch main."""
    r = tmp("repo")
    git(r, "init", "-b", "main")
    os.makedirs(os.path.join(r, "ports/fnv-nx/verify"))
    for f in SMALL:
        shutil.copy(os.path.join(VERIFY, f), os.path.join(r, "ports/fnv-nx/verify", f))
    put(r, "ports/fnv-nx/verify/gates.json", json.dumps(gates or two_gates()))
    shutil.copy(os.path.join(VERIFY, "env.json"), os.path.join(r, "ports/fnv-nx/verify/env.json"))
    put(r, "ports/fnv-nx/link/tests/test_ok.py", "import sys\nsys.exit(0)\n")
    for k, v in (link or {}).items():
        put(r, k, v)
    git(r, "add", "-A")
    git(r, "commit", "-m", "base")
    return r

def two_gates():
    v = "ports/fnv-nx/verify/"
    return {"format": "fnvnx-gates/1", "gates": [
        {"name": "pure", "argv": ["python3", "-I", v + "gate_pure.py", "ports/fnv-nx/link"], "result": "pure.json"},
        {"name": "selftests", "argv": ["python3", "-I", v + "gate_tests.py", "ports/fnv-nx/link"], "result": "selftests.json"}]}

def branch(r, name, files, base="main", delete=()):
    git(r, "checkout", "-b", name, base)
    for k, v in files.items():
        put(r, k, v)
    for k in delete:
        os.remove(os.path.join(r, k))
    git(r, "add", "-A")
    git(r, "commit", "-m", name)
    git(r, "checkout", "main")

LINE = r"^L1 (green|red) [0-9a-f]{40} [0-9.]+s gates:\d+ over_budget:(True|False)$"

r = small_repo()
vd = tmp("verdicts")
rc, line, out, err = l1("verdict", "--repo", r, "--commit", "main", "--verdicts", vd)
tree = git(r, "rev-parse", "main^{tree}")
check("l1 verdict: green line, stored by tree", rc == 0 and re.match(LINE, line) and line.split()[1:3] == ["green", tree]
      and "gates:2" in line and os.path.isfile(os.path.join(vd, tree + ".json")), (rc, line, out, err))

vd = tmp("verdicts")
r2 = small_repo(link={"ports/fnv-nx/link/bad.py": BAD})
rc, line, out, err = l1("verdict", "--repo", r2, "--commit", "main", "--verdicts", vd)
check("l1 verdict: red file -> red, exit 2", rc == 2 and line.startswith("L1 red "), (rc, line, out, err))

# merge: bootstrap
r = small_repo()
vd = tmp("verdicts")
branch(r, "feat", {"ports/fnv-nx/link/ok.py": CLEAN})
old = git(r, "rev-parse", "main")
rc, line, out, err = l1("merge", "--repo", r, "--into", "main", "--from", "feat", "--verdicts", vd)
new = git(r, "rev-parse", "main")
check("l1 merge: bootstrap merges on green", rc == 0 and re.match(LINE, line) and "bootstrap: no previous verdict" in out
      and "merged " + new in out and new != old and git(r, "rev-parse", new + "^1") == old, (rc, line, out, err))

# red branch blocked
branch(r, "bad", {"ports/fnv-nx/link/bad.py": BAD})
before = git(r, "rev-parse", "main")
rc, line, out, err = l1("merge", "--repo", r, "--into", "main", "--from", "bad", "--verdicts", vd)
check("l1 merge: red file in link/ is blocked, ref does not move", rc == 2 and line.startswith("L1 red ")
      and "blocked:" in out and git(r, "rev-parse", "main") == before, (rc, line, out, err))

# green second merge uses the ratchet (previous verdict exists)
branch(r, "feat2", {"ports/fnv-nx/link/ok2.py": CLEAN})
rc, line, out, err = l1("merge", "--repo", r, "--into", "main", "--from", "feat2", "--verdicts", vd)
check("l1 merge: second green merge, previous verdict used", rc == 0 and "bootstrap" not in out and "merged " in out, (rc, line, out, err))

# ratchet: gate dropped
g1 = two_gates()
g1["gates"] = g1["gates"][:1]
branch(r, "drop", {"ports/fnv-nx/verify/gates.json": json.dumps(g1)})
before = git(r, "rev-parse", "main")
rc, line, out, err = l1("merge", "--repo", r, "--into", "main", "--from", "drop", "--verdicts", vd)
check("l1 merge: dropping a gate is blocked by the ratchet", rc == 2 and "ratchet" in out + err and "selftests" in out + err
      and git(r, "rev-parse", "main") == before, (rc, line, out, err))

# ---- environment of the verdict (envcheck first, env_id in the verdict, merge needs the same env_id)
ENVF = os.path.join(VERIFY, "env.json")
sys.path.insert(0, VERIFY)
import envcheck  # noqa: E402
want_id = envcheck.env_id(json.load(open(ENVF)))
r = small_repo()
vd = tmp("verdicts")
rc, line, out, err = l1("verdict", "--repo", r, "--commit", "main", "--verdicts", vd)
tree = git(r, "rev-parse", "main^{tree}")
check("l1 verdict: stored verdict carries env_id of the commit's env.json", rc == 0 and json.load(open(os.path.join(vd, tree + ".json")))
      .get("env_id") == want_id, (rc, line, out, err))

def doctored(**kw):
    doc = json.load(open(ENVF))
    doc["packages"]["coreutils"] = "0.0-doctored"
    doc.update(kw)
    return json.dumps(doc)

r = small_repo()
branch(r, "badenv", {"ports/fnv-nx/verify/env.json": doctored()})
vd = tmp("verdicts")
rc, line, out, err = l1("verdict", "--repo", r, "--commit", "badenv", "--verdicts", vd)
check("l1 verdict: mismatching env.json -> blocked: env, exit 4, no gate run, nothing stored", rc == 4 and out.startswith("blocked: env\n")
      and "env: package coreutils: want 0.0-doctored, have " in out and not line and os.listdir(vd) == [], (rc, out, err, os.listdir(vd)))
r = small_repo()
git(r, "rm", "-q", "ports/fnv-nx/verify/env.json")
git(r, "commit", "-m", "no env")
rc, line, out, err = l1("verdict", "--repo", r, "--commit", "main", "--verdicts", vd)
check("l1 verdict: commit without env.json -> blocked: env, exit 4", rc == 4 and "blocked: env" in out and os.listdir(vd) == [], (rc, out, err))

r = small_repo()
vd = tmp("verdicts")
branch(r, "feat", {"ports/fnv-nx/link/ok.py": CLEAN})
branch(r, "feat-env", {"ports/fnv-nx/verify/env.json": doctored()})
before = git(r, "rev-parse", "main")
rc, line, out, err = l1("merge", "--repo", r, "--into", "main", "--from", "feat-env", "--verdicts", vd)
check("l1 merge: env.json of the merge tree mismatches -> blocked: env, exit 4, ref and store untouched",
      rc == 4 and "blocked: env" in out and git(r, "rev-parse", "main") == before and os.listdir(vd) == [], (rc, out, err))
rc, line, out, err = l1("merge", "--repo", r, "--into", "main", "--from", "feat", "--verdicts", vd)
check("l1 merge: green merge in the declared environment", rc == 0 and "merged " in out, (rc, out, err))
git(r, "update-ref", "refs/heads/main", before)
vf = os.path.join(vd, git(r, "merge-tree", "--write-tree", "main", "feat").splitlines()[0] + ".json")
v = json.load(open(vf))
v["env_id"] = "0" * 64
json.dump(v, open(vf, "w"))
rc, line, out, err = l1("merge", "--repo", r, "--into", "main", "--from", "feat", "--verdicts", vd)
check("l1 merge: a stored green verdict of another env_id does not count", rc == 2 and "another environment" in out
      and git(r, "rev-parse", "main") == before, (rc, out, err))

# ---- gate_tests: concurrency, content-keyed cache, per-file seconds (synthetic directories only)
def mk(d, **files):
    for n, body in files.items():
        put(d, "verify/tests/%s.py" % n, body)

def tests_c(d, cache, extra=(), dirs=("verify",)):
    return gate("gate_tests.py", d, ["--cache", cache] + list(extra) + list(dirs))

d = tmp("tests-jobs")
# concurrency is proved by a rendezvous, not by the clock: each file leaves a marker and passes only once all three exist,
# which can happen only while all three run at the same time (a sequential run would hit the 3600 s limit instead)
RDV = ("import os, sys, time\nd = %r\nopen(os.path.join(d, %%r), 'w').close()\n"
       "while len(os.listdir(d)) < 3:\n    time.sleep(0.05)\n") % tmp("tests-jobs-rdv")
mk(d, test_a=RDV % "a", test_b=RDV % "b", test_c=RDV % "c")
rc, r, e = tests(d, extra=("--jobs", "3", "--timeout-s", "3600"))
par = None
check("tests: --jobs 3 runs files concurrently, reported in sorted order", rc == 0 and r["universe"] == 3
      and [f["name"] for f in r["files"]] == ["verify/tests/test_a.py", "verify/tests/test_b.py", "verify/tests/test_c.py"]
      and all(f["status"] == "passed" for f in r["files"]), (rc, par, r, e))

d = tmp("tests-times")
mk(d, test_a="pass\n", test_b="import sys\nsys.exit(1)\n")
out = tempfile.mkdtemp(dir=ROOT, prefix="out-")
subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "gate_tests.py"), "verify"], cwd=d, env=dict(os.environ, GATE_OUT=out))
times = json.load(open(os.path.join(out, "selftests.times.json")))
check("tests: per-file seconds in selftests.times.json", [(t["name"], t["status"]) for t in times]
      == [("verify/tests/test_a.py", "passed"), ("verify/tests/test_b.py", "failed")] and all(isinstance(t["seconds"], float) for t in times), times)

d = tmp("tests-cache")
cache = os.path.join(d, "cache")
mk(d, test_a="open('ran.log', 'a').write('a\\n')\n", test_b="open('ran.log', 'a').write('b\\n')\n")
ran = lambda: open(os.path.join(d, "ran.log")).read().split() if os.path.exists(os.path.join(d, "ran.log")) else []
rc, r, e = tests_c(d, cache)
check("tests cache: first run executes every file and stores one key", rc == 0 and sorted(ran()) == ["a", "b"] and "cached" not in r["detail"]
      and len([f for f in os.listdir(cache) if f.endswith(".json")]) == 1, (rc, r, e, ran()))
rc, r, e = tests_c(d, cache)
check("tests cache: unchanged inputs -> certified without running", rc == 0 and len(ran()) == 2 and r["certified"] == 2
      and re.match(r"^2 of 2 test files pass, cached key [0-9a-f]{12}, ", r["detail"]) and all(f["status"] == "cached" for f in r["files"]), (rc, r, e, ran()))
rc, r, e = gate("gate_tests.py", d, ["verify"])
check("tests cache: no --cache and no GATE_CACHE -> always runs", rc == 0 and len(ran()) == 4 and "cached" not in r["detail"], (r, ran()))
os.utime(os.path.join(d, "verify/tests/test_a.py"), (5, 5))
rc, r, e = tests_c(d, cache)
check("tests cache: an mtime change is not a change of content", "cached key" in r["detail"] and len(ran()) == 4, (r, ran()))
put(d, "verify/helper.py", "X = 1\n")
rc, r, e = tests_c(d, cache)
check("tests cache: new content in a key dir -> miss, everything re-runs", rc == 0 and "cached" not in r["detail"] and len(ran()) == 6, (r, ran()))
rc, r, e = tests_c(d, cache, extra=("--timeout-s", "200"))
check("tests cache: another argv is another key", "cached" not in r["detail"] and len(ran()) == 8, (r, ran()))
put(d, "other/data.txt", "1\n")
rc, r, e = tests_c(d, cache, extra=("--key-dirs", "verify", "other", "--"))
n8 = len(ran())
put(d, "other/data.txt", "2\n")
rc, r, e = tests_c(d, cache, extra=("--key-dirs", "verify", "other", "--"))
check("tests cache: --key-dirs content change (a dir that holds no tests) -> miss", n8 == 10 and len(ran()) == 12 and "cached" not in r["detail"], (r, ran()))
rc, r, e = tests_c(d, cache, extra=("--key-dirs", "verify", "other", "--"))
check("tests cache: same key dirs and content again -> hit", "cached key" in r["detail"] and len(ran()) == 12, (r, ran()))
# env.json of the checkout is part of the key
e1 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "env.json")
d2 = tmp("tests-cache-env")
mk(d2, test_a="pass\n")
for n in ("gate_tests.py", "cas.py", "envcheck.py"):
    shutil.copy(os.path.join(VERIFY, n), os.path.join(d2, n))
def gate_in(dirpath, cache):
    out = tempfile.mkdtemp(dir=ROOT, prefix="out-")
    subprocess.run([sys.executable, "-I", os.path.join(dirpath, "gate_tests.py"), "--cache", cache, "verify"], cwd=d2,
                   env=dict(os.environ, GATE_OUT=out), capture_output=True)
    return json.load(open(os.path.join(out, "selftests.json")))
shutil.copy(e1, os.path.join(d2, "env.json"))
c2 = os.path.join(d2, "cache")
gate_in(d2, c2)
r = gate_in(d2, c2)
doc = json.load(open(e1))
doc["packages"]["coreutils"] = "0.0-other"
json.dump(doc, open(os.path.join(d2, "env.json"), "w"))
r2 = gate_in(d2, c2)
check("tests cache: another env.json is another key", "cached key" in r["detail"] and "cached" not in r2["detail"], (r, r2))

d = tmp("tests-cache-fail")
cache = os.path.join(d, "cache")
mk(d, test_ok="pass\n", test_bad="import sys\nsys.exit(1)\n")
rc1, r1, _ = tests_c(d, cache)
rc2, r2, _ = tests_c(d, cache)
check("tests cache: a failing file is never certified from the cache; the key re-runs everything", rc1 != 0 and rc2 != 0
      and "cached" not in r2["detail"] and r2["uncertified"] == ["verify/tests/test_bad.py"] and r2["certified"] == 1, (r1, r2))
put(d, "verify/tests/test_bad.py", "pass\n")
rc3, r3, _ = tests_c(d, cache)
rc4, r4, _ = tests_c(d, cache)
check("tests cache: fixed file is a new key, passes, then is cached", rc3 == 0 and "cached" not in r3["detail"] and rc4 == 0 and "cached key" in r4["detail"], (r3, r4))

d = tmp("tests-cache-timeout")
cache = os.path.join(d, "cache")
mk(d, test_slow="import time\ntime.sleep(3600)\n")
rc, r, e = tests_c(d, cache, extra=("--timeout-s", "1"))
rc2, r2, e = tests_c(d, cache, extra=("--timeout-s", "1"))
check("tests cache: a timeout is never cached as passed", rc != 0 and rc2 != 0 and "cached" not in r2["detail"], (r, r2))

# l1 passes VDIR/cache as GATE_CACHE: the second verdict of an unchanged tree finds the selftests keyed result
r = small_repo()
vd = tmp("verdicts")
rc, line, out, err = l1("verdict", "--repo", r, "--commit", "main", "--verdicts", vd)
cdir = os.path.join(vd, "cache")
keys1 = sorted(os.listdir(cdir)) if os.path.isdir(cdir) else []
rc2, line2, out2, err2 = l1("verdict", "--repo", r, "--commit", "main", "--verdicts", vd)
check("l1 verdict: gates get GATE_CACHE=VDIR/cache; same tree again reuses the keyed selftests result", rc == 0 and rc2 == 0
      and len(keys1) == 1 and sorted(os.listdir(cdir)) == keys1 and re.match(LINE, line2), (rc, rc2, keys1, out, err, out2, err2))

# ---- l1 merge --remote: the remote branch moves only from BASE to the judged merge commit, atomically
import contextlib  # noqa: E402
import io  # noqa: E402
import l1 as l1mod  # noqa: E402

def l1_inproc(*a, hook=None):
    """l1.main in this process (so a hook can stand in for the verdict step); returns (rc, stdout)."""
    orig = l1mod._check
    if hook:
        def patched(*x, **k):
            hook()
            return orig(*x, **k)
        l1mod._check = patched
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = l1mod.main(list(a))
    finally:
        l1mod._check = orig
    return rc, buf.getvalue()

def remote_setup(*feats):
    r = small_repo()
    bare = tmp("bare") + "/r.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
    git(r, "remote", "add", "origin", bare)
    git(r, "push", "-q", "origin", "main")
    git(r, "fetch", "-q", "origin")
    for n in feats:
        branch(r, n, {"ports/fnv-nx/link/%s.py" % n: CLEAN})
    other = tmp("other")
    subprocess.run(["git", "clone", "-q", bare, other], check=True)
    return r, bare, other

def push_other(other, name):
    put(other, "ports/fnv-nx/link/%s.py" % name, CLEAN)
    git(other, "add", "-A")
    git(other, "commit", "-m", name)
    git(other, "push", "-q", "origin", "main")
    return git(other, "rev-parse", "HEAD")

M = ("merge", "--into", "main", "--verdicts")

r, bare, other = remote_setup("feat")
vd = tmp("verdicts")
base = git(r, "rev-parse", "main")
feat = git(r, "rev-parse", "feat")
rc, out = l1_inproc("merge", "--repo", r, "--into", "main", "--from", "feat", "--verdicts", vd, "--remote", "origin")
new = git(bare, "rev-parse", "main")
check("push: green -> remote main is the merge commit, parents (BASE, REF), local refs follow, line printed",
      rc == 0 and new != base and git(bare, "rev-parse", new + "^1") == base and git(bare, "rev-parse", new + "^2") == feat
      and git(r, "rev-parse", "main") == new and git(r, "rev-parse", "refs/remotes/origin/main") == new
      and out.splitlines()[-1] == "merged %s pushed origin/main" % new and "L1 green" in out, (rc, out, new, base))

r, bare, other = remote_setup("feat")
vd = tmp("verdicts")
base = git(r, "rev-parse", "main")
moved = []
def move():
    moved.append(push_other(other, "racer"))
rc, out = l1_inproc("merge", "--repo", r, "--into", "main", "--from", "feat", "--verdicts", vd, "--remote", "origin", hook=move)
check("push: remote moved after the fetch -> exit 5, remote keeps the other commit, local untouched",
      rc == 5 and "blocked: moved (origin/main is no longer %s)" % base[:12] in out and git(bare, "rev-parse", "main") == moved[0]
      and git(r, "rev-parse", "main") == base and git(r, "rev-parse", "refs/remotes/origin/main") == base, (rc, out, moved))
# retry: local branch must be brought to the remote tip by the caller; the verdict is for the NEW tree
tree_old = git(r, "merge-tree", "--write-tree", base, "feat").splitlines()[0]
git(r, "fetch", "-q", "origin")
git(r, "update-ref", "refs/heads/main", moved[0])
rc, out = l1_inproc("merge", "--repo", r, "--into", "main", "--from", "feat", "--verdicts", vd, "--remote", "origin")
new = git(bare, "rev-parse", "main")
tree_new = git(bare, "rev-parse", new + "^{tree}")
check("push: retry after exit 5 merges onto the new tip and judges the new tree",
      rc == 0 and git(bare, "rev-parse", new + "^1") == moved[0] and tree_new != tree_old
      and os.path.isfile(os.path.join(vd, tree_new + ".json")) and "L1 green %s" % tree_new in out, (rc, out))

r, bare, other = remote_setup()
vd = tmp("verdicts")
branch(r, "bad", {"ports/fnv-nx/link/bad.py": BAD})
base = git(r, "rev-parse", "main")
rc, out = l1_inproc("merge", "--repo", r, "--into", "main", "--from", "bad", "--verdicts", vd, "--remote", "origin")
check("push: red verdict -> remote and local untouched", rc == 2 and git(bare, "rev-parse", "main") == base and git(r, "rev-parse", "main") == base, (rc, out))

r, bare, other = remote_setup("c1")
vd = tmp("verdicts")
git(r, "checkout", "-q", "-b", "c2", "main")
put(r, "ports/fnv-nx/link/c1.py", CLEAN + "# other\n")
git(r, "add", "-A")
git(r, "commit", "-m", "c2")
git(r, "checkout", "-q", "main")
base = git(r, "rev-parse", "main")
git(r, "merge", "-q", "--no-edit", "c1")
git(r, "push", "-q", "origin", "main")
base = git(r, "rev-parse", "main")
rc, out = l1_inproc("merge", "--repo", r, "--into", "main", "--from", "c2", "--verdicts", vd, "--remote", "origin")
check("push: conflict -> exit 3, remote untouched", rc == 3 and git(bare, "rev-parse", "main") == base, (rc, out))

r, bare, other = remote_setup("feat")
vd = tmp("verdicts")
remote_tip = git(bare, "rev-parse", "main")
put(r, "ports/fnv-nx/link/loc.py", CLEAN)
git(r, "add", "-A")
git(r, "commit", "-m", "local only")
loc = git(r, "rev-parse", "main")
rc, out = l1_inproc("merge", "--repo", r, "--into", "main", "--from", "feat", "--verdicts", vd, "--remote", "origin")
check("push: local branch differs from the remote tip -> blocked, nothing moves",
      rc == 2 and "blocked: local branch differs from origin/main" in out and git(bare, "rev-parse", "main") == remote_tip
      and git(r, "rev-parse", "main") == loc, (rc, out))

r, bare, other = remote_setup("feat")
vd = tmp("verdicts")
base = git(r, "rev-parse", "main")
rc, line, out, err = l1("merge", "--repo", r, "--into", "main", "--from", "feat", "--verdicts", vd)
new = git(r, "rev-parse", "main")
check("push: no --remote -> old behaviour, last line says local only", rc == 0 and new != base and git(bare, "rev-parse", "main") == base
      and out.splitlines()[-1] == "merged %s local only" % new, (rc, out, err))

r, bare, other = remote_setup("feat")
vd = tmp("verdicts")
base = git(r, "rev-parse", "main")
git(r, "config", "remote.origin.pushurl", os.path.join(ROOT, "no-such-remote.git"))
rc, out = l1_inproc("merge", "--repo", r, "--into", "main", "--from", "feat", "--verdicts", vd, "--remote", "origin")
tree = git(r, "merge-tree", "--write-tree", base, "feat").splitlines()[0]
check("push: push to a nonexistent URL -> exit 6, nothing changed, green verdict kept",
      rc == 6 and "blocked: push failed: " in out and git(bare, "rev-parse", "main") == base and git(r, "rev-parse", "main") == base
      and git(r, "rev-parse", "refs/remotes/origin/main") == base and os.path.isfile(os.path.join(vd, tree + ".json")), (rc, out))

shutil.rmtree(ROOT, ignore_errors=True)
print("%d failed" % len(FAILED))
sys.exit(1 if FAILED else 0)
