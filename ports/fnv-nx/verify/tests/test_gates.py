#!/usr/bin/env python3
"""Tests for verify/gate_pure.py, verify/gate_tests.py, verify/gates.json and verify/l1.py.
Plain script: exit 0 on pass, one PASS/FAIL line per case. Uses temp git repositories."""
import json
import os
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
SMALL = ("gate_pure.py", "gate_tests.py", "puregate.py", "puregate_policy.py")
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
    for f in sorted(os.listdir(out)):
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
put(d, "verify/tests/test_slow.py", "import time\ntime.sleep(30)\n")
put(d, "verify/tests/test_ok.py", "pass\n")
t0 = time.time()
rc, r, e = tests(d, extra=("--timeout-s", "1"))
check("tests: timeout is a failure", rc != 0 and r["uncertified"] == ["verify/tests/test_slow.py"] and r["certified"] == 1
      and time.time() - t0 < 20, (rc, r, e))

# ---- gates.json
spec = json.load(open(os.path.join(VERIFY, "gates.json")))
byname = {g["name"]: g for g in spec["gates"]}
dirs_pure = [a.rsplit("/", 1)[-1] for a in byname["pure"]["argv"][3:]]
dirs_tests = [a.rsplit("/", 1)[-1] for a in byname["selftests"]["argv"][3:]]
check("gates.json: format and the two gates", spec["format"] == "fnvnx-gates/1" and [g["name"] for g in spec["gates"]] == ["pure", "selftests"]
      and byname["pure"]["result"] == "pure.json" and byname["selftests"]["result"] == "selftests.json"
      and dirs_pure == ["contract", "link", "rules", "lift"] and dirs_tests == ["verify", "farm", "contract", "link", "rules", "lift"]
      and byname["pure"]["argv"][:3] == ["python3", "-I", "ports/fnv-nx/verify/gate_pure.py"]
      and byname["selftests"]["argv"][:3] == ["python3", "-I", "ports/fnv-nx/verify/gate_tests.py"], spec)

# ---- l1
def l1(*a):
    p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "l1.py")] + list(a), capture_output=True, text=True, env=ENV)
    lines = [x for x in p.stdout.splitlines() if x.startswith("L1 ")]
    return p.returncode, lines[-1] if lines else "", p.stdout, p.stderr

def small_repo(gates=None, link=None):
    """Repo with the small verify subset, a pure gate and a selftests gate over link/, on branch main."""
    r = tmp("repo")
    git(r, "init", "-b", "main")
    for f in SMALL:
        shutil.copy(os.path.join(VERIFY, f), os.path.join(r, "ports/fnv-nx/verify/") if os.path.isdir(os.path.join(r, "ports/fnv-nx/verify")) else _mk(r, f))
    put(r, "ports/fnv-nx/verify/gates.json", json.dumps(gates or two_gates()))
    put(r, "ports/fnv-nx/link/tests/test_ok.py", "import sys\nsys.exit(0)\n")
    for k, v in (link or {}).items():
        put(r, k, v)
    git(r, "add", "-A")
    git(r, "commit", "-m", "base")
    return r

def _mk(r, f):
    os.makedirs(os.path.join(r, "ports/fnv-nx/verify"))
    return os.path.join(r, "ports/fnv-nx/verify/")

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
import re  # noqa: E402

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

# ---- l1 verdict with the real verify directory
r = tmp("repo-real")
git(r, "init", "-b", "main")
shutil.copytree(VERIFY, os.path.join(r, "ports/fnv-nx/verify"), ignore=shutil.ignore_patterns("__pycache__"))
git(r, "add", "-A")
git(r, "commit", "-m", "verify")
vd = tmp("verdicts")
t0 = time.time()
rc, line, out, err = l1("verdict", "--repo", r, "--commit", "main", "--verdicts", vd)
secs = time.time() - t0
tree = git(r, "rev-parse", "main^{tree}")
v = json.load(open(os.path.join(vd, tree + ".json")))
print("real verify copy: %s in %.1fs" % (line, secs))
for g in v["gates"]:
    print("  %s %s %d/%d %.1fs %s" % (g["name"], g["status"], g["certified"], g["universe"], g["seconds"], g["reason"][:200]))
check("l1 verdict: real verify copy, green iff the self-tests pass", re.match(LINE, line) and (rc == 0) == (v["verdict"] == "green")
      and rc in (0, 2) and line.split()[1] == v["verdict"] and [g["name"] for g in v["gates"]] == ["pure", "selftests"], (rc, line, out, err))

shutil.rmtree(ROOT, ignore_errors=True)
print("%d failed" % len(FAILED))
sys.exit(1 if FAILED else 0)
