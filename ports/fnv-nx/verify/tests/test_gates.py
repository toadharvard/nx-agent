#!/usr/bin/env python3
"""Tests for verify/gate_pure.py, verify/gate_tests.py, verify/gates.d and verify/l1.py.
Plain script: exit 0 on pass, one PASS/FAIL line per case. Uses temp git repositories."""
import itertools
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
SMALL = ("gate_admit.py", "gatesdir.py", "verdict.py", "gate_pure.py", "gate_tests.py", "puregate.py", "puregate_policy.py", "cas.py", "envcheck.py")
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

V = "ports/fnv-nx/"

def adm(*entries, extra=()):
    """admit.json of a toy tree: tool roots link and rules naming gate pure; ENTRIES are repo-relative stage entry points (of link/)."""
    roots = [{"path": V + "link", "class": "tool", "gates": ["pure", "selftests"], "entry": [V + "link/" + e for e in entries]},
             {"path": V + "rules", "class": "tool", "gates": ["pure"], "entry": [V + "rules/" + e for e in extra]}]
    return json.dumps({"format": "fnvnx-admit/1", "roots": roots})

def pure(d, *entries, extra=()):
    put(d, V + "verify/admit.json", adm(*entries, extra=extra))
    return gate("gate_pure.py", d, [])

# ---- gate_pure (the universe is the import closure of the stage entry points)
d = tmp("pure-none")
rc, r, e = pure(d)
check("pure: no entry points -> green, universe 0", rc == 0 and r and r["name"] == "pure" and r["format"] == "fnvnx-gate/1"
      and r["status"] == "green" and r["universe"] == 0 and r["certified"] == 0 and "nothing to certify" in r["detail"], (rc, r, e))

d = tmp("pure-clean")
put(d, V + "link/a.py", CLEAN)
put(d, V + "rules/b.py", CLEAN)
rc, r, e = pure(d, "a.py", extra=["b.py"])
check("pure: clean entry points certified", rc == 0 and r["status"] == "green" and (r["universe"], r["certified"]) == (2, 2), (rc, r, e))

d = tmp("pure-bad")
put(d, V + "link/a.py", CLEAN)
put(d, V + "link/bad.py", BAD)
rc, r, e = pure(d, "a.py", "bad.py")
check("pure: random -> red, path listed", rc != 0 and r["status"] == "red" and (r["universe"], r["certified"]) == (2, 1)
      and r["uncertified"] == [V + "link/bad.py"], (rc, r, e))

d = tmp("pure-sh")
put(d, V + "link/run.sh", "#!/bin/sh\necho hi\n", 0o755)
put(d, V + "link/a.py", CLEAN)
rc, r, e = pure(d, "a.py")
check("pure: a shell file that no entry point imports is outside the universe", rc == 0 and r["status"] == "green" and r["universe"] == 1, (rc, r, e))

d = tmp("pure-tests")
put(d, V + "link/a.py", CLEAN)
put(d, V + "link/tests/test_a.py", BAD)
rc, r, e = pure(d, "a.py")
check("pure: files nobody imports (tests/) are outside the universe", rc == 0 and r["status"] == "green"
      and (r["universe"], r["certified"]) == (1, 1), (rc, r, e))

d = tmp("pure-gate-driver")
put(d, V + "link/a.py", CLEAN)
put(d, V + "link/gate_x.py", BAD)
rc, r, e = pure(d, "a.py")
check("pure: a gate driver that is no entry point is outside the universe", rc == 0 and r["universe"] == 1, (rc, r, e))

d = tmp("pure-import")
put(d, V + "link/a.py", "import b\n")
put(d, V + "link/b.py", CLEAN)
rc, r, e = pure(d, "a.py")
check("pure: the closure follows project imports", rc == 0 and (r["universe"], r["certified"]) == (2, 2), (rc, r, e))

d = tmp("pure-missing-entry")
rc, r, e = pure(d, "gone.py")
check("pure: an entry point that does not exist is red", rc != 0 and r["status"] == "red" and r["uncertified"] == [V + "link/gone.py"], (rc, r, e))

d = tmp("pure-cap")
for i in range(60):
    put(d, V + "link/m%02d.py" % i, BAD)
rc, r, e = pure(d, *["m%02d.py" % i for i in range(60)])
check("pure: uncertified capped at 50, sorted", r["universe"] == 60 and len(r["uncertified"]) == 50
      and r["uncertified"] == sorted(r["uncertified"]) and r["uncertified"][0] == V + "link/m00.py", r)

# ---- gate_tests
def declare(d, dirs=("verify",)):
    """verify/admit.json of the toy checkout D: one tool root per DIR, each declaring selftests (the universe of gate_tests.py is derived from it)."""
    put(d, "ports/fnv-nx/verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
        {"path": x, "class": "tool", "gates": ["selftests"], "entry": []} for x in dirs]}))

def tests(d, dirs=("verify",), extra=()):
    declare(d, dirs)
    return gate("gate_tests.py", d, list(extra))

d = tmp("tests-none")
rc, r, e = tests(d)
check("tests: a root that declares selftests and has no test file is red and named (the universe is derived from admit.json)",
      rc != 0 and r["status"] == "red" and r["name"] == "selftests" and r["universe"] == 1 and r["certified"] == 0
      and r["uncertified"] == ["verify (declares selftests, no tests/test_*.py)"], (rc, r, e))

# the universe is the admit.json derivation: roots that declare selftests, all of their trees, in admit order; other roots do not count
d = tmp("tests-admit")
for x, n in (("a", "test_a1"), ("a", "test_a2"), ("b", "test_b"), ("c", "test_c"), ("t2", "test_t")):
    put(d, "%s/tests/%s.py" % (x, n), "pass\n")
put(d, "ports/fnv-nx/verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
    {"path": "b", "class": "tool", "gates": ["selftests"], "entry": []},
    {"path": "c", "class": "tool", "gates": ["pure"], "entry": []},
    {"path": "a", "class": "tool", "gates": ["selftests"], "entry": [], "trees": ["a", "t2"]}]}))
rc, r, e = gate("gate_tests.py", d, [])
check("tests: the universe is derived from admit.json: selftests roots only, each tree of a root, in admit order", rc == 0
      and [f["name"] for f in r["files"]] == ["b/tests/test_b.py", "a/tests/test_a1.py", "a/tests/test_a2.py", "t2/tests/test_t.py"], (rc, r, e))
rc, r, e = gate("gate_tests.py", d, ["a"])
check("tests: a directory argument is refused (no list in argv)", rc == 1, (rc, r, e))
put(d, "ports/fnv-nx/verify/admit.json", "{")
rc, r, e = gate("gate_tests.py", d, [])
check("tests: a malformed admit.json is red", rc != 0 and r["status"] == "red" and r["certified"] == 0, (rc, r, e))

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
order = [k for k, _ in itertools.groupby(open(log).read().split())]   # every file ran twice (testdet), one after the other
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
rows = {x["name"].rsplit("/", 1)[-1]: x for x in r["files"]}
check("tests: the scratch a file leaves in its temporary directory is removed, passed or failed; the leak is uncertified and named",
      rc != 0 and r["certified"] == 0 and len(left) == 4 and not any(os.path.exists(x) for x in left)
      and all(x.startswith("/nxroot/tmp/left-") for x in left)
      and "leaked in TMPDIR  left-" in rows["test_leaves.py"]["reason"] and "exit" in rows["test_leaves_fails.py"]["reason"] + "FAIL", (rc, r, left))

d = tmp("tests-env")
put(d, "verify/tests/test_env.py", "import os, sys\nsys.exit(1 if [k for k in os.environ if k.startswith('GATE_')] else 0)\n")
rc, r, e = tests(d)
check("tests: a test file does not see the gate's GATE_* variables", rc == 0 and r["certified"] == 1, (rc, r, e))

# ---- gates.d
sys.path.insert(0, VERIFY)
import gatesdir  # noqa: E402
spec = gatesdir.load_dir(os.path.join(VERIFY, "gates.d"))
files_d = sorted(f[:-5] for f in os.listdir(os.path.join(VERIFY, "gates.d")))
check("gates.d: no gates.json is tracked and every file is one gate named like it", not os.path.exists(os.path.join(VERIFY, "gates.json"))
      and [g["name"] for g in spec["gates"]] == files_d and len(files_d) == len(set(files_d)), files_d)
check("gates.d: every gate file declares valid covers", all(isinstance(g.get("covers"), list) and g["covers"] and all(c in gatesdir.ROOT_CLASSES for c in g["covers"])
                                                          for g in spec["gates"]), spec)
byname = {g["name"]: g for g in spec["gates"]}
CONTRACT = ["n1", "n2_data", "n2_call", "n2_vcall", "xc", "xunit", "rtti", "pcrel", "bodysel", "abs", "disp", "reach", "reachlink", "gpa"]
dirs_pure = byname["pure"]["argv"][3:]
dirs_tests = [a.rsplit("/", 1)[-1] for a in byname["selftests"]["argv"][7:]]
check("gates.d: contract gates run gate_contract.py --gate NAME", all(byname[n]["argv"] == ["python3", "-I", "ports/fnv-nx/verify/gate_contract.py", "--gate", n] and byname[n]["result"] == n + ".json" for n in CONTRACT), spec)
check("gates.d: format and the gates", spec["format"] == "fnvnx-gates/1" and {"pure", "selftests", "admit", "oracle", "exepin", "assumptions", "build", "objstub", "linkstrict", "determinism", "argordergate", "imports", "machine", "hostapi", "datamodel", "d3d9total", "abi", "namegate", "r7"}.union(CONTRACT) <= set(byname) and {"order", "addr32", "eh"} <= set(byname)
      and all(byname[n]["argv"] == ["python3", "-I", "ports/fnv-nx/verify/gate_%s.py" % n] and byname[n]["result"] == n + ".json"
              for n in ("build", "objstub", "linkstrict", "determinism", "hostapi"))
      and byname["argordergate"]["argv"] == ["python3", "-I", "ports/fnv-nx/verify/gate_argorder.py"] and byname["argordergate"]["result"] == "argordergate.json"
      and byname["datamodel"]["argv"] == ["python3", "-I", "ports/fnv-nx/verify/gate_datamodel.py"] and byname["datamodel"]["result"] == "datamodel.json"
      and byname["d3d9total"]["argv"] == ["python3", "-I", "ports/fnv-nx/render/total/gate_d3d9total.py"] and byname["d3d9total"]["result"] == "d3d9total.json"
      and byname["pure"]["result"] == "pure.json" and byname["selftests"]["result"] == "selftests.json"
      and dirs_pure == [] and dirs_tests == [] and byname["selftests"]["argv"] == ["python3", "-I", "ports/fnv-nx/verify/gate_tests.py", "--jobs", "2", "--timeout-s", "900"]
      and all(byname[n]["argv"] == ["python3", "-I", "ports/fnv-nx/verify/gate_lift.py", "--gate", n] and byname[n]["result"] == n + ".json" for n in ("order", "addr32", "eh"))
      and byname["pure"]["argv"][:3] == ["python3", "-I", "ports/fnv-nx/verify/gate_pure.py"]
      and byname["selftests"]["argv"][:7] == ["python3", "-I", "ports/fnv-nx/verify/gate_tests.py", "--jobs", "2", "--timeout-s", "900"], spec)

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
    for g in (gates or two_gates())["gates"]:
        put(r, "ports/fnv-nx/verify/gates.d/%s.json" % g["name"], json.dumps(dict(g, format="fnvnx-gatefile/1")))
    shutil.copy(os.path.join(VERIFY, "env.json"), os.path.join(r, "ports/fnv-nx/verify/env.json"))
    put(r, "ports/fnv-nx/link/tests/test_ok.py", "import sys\nsys.exit(0)\n")
    put(r, V + "link/stage.py", CLEAN)  # one stage entry point: a pure gate over an empty universe is vacuous (verdict.py)
    put(r, V + "verify/admit.json", adm("stage.py", *[k.rsplit("/", 1)[-1] for k in (link or {}) if k.endswith("/bad.py")]))
    for k, v in (link or {}).items():
        put(r, k, v)
    git(r, "add", "-A")
    git(r, "commit", "-m", "base")
    return r

def two_gates():
    v = "ports/fnv-nx/verify/"
    return {"format": "fnvnx-gates/1", "gates": [
        {"name": "pure", "argv": ["python3", "-I", v + "gate_pure.py"], "result": "pure.json", "covers": ["tool"]},
        {"name": "selftests", "argv": ["python3", "-I", v + "gate_tests.py"], "result": "selftests.json", "covers": ["tool"]}]}

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
      and "gates:3" in line and os.path.isfile(os.path.join(vd, tree + ".json")), (rc, line, out, err))

vd = tmp("verdicts")
r2 = small_repo(link={"ports/fnv-nx/link/bad.py": BAD})
rc, line, out, err = l1("verdict", "--repo", r2, "--commit", "main", "--verdicts", vd)
check("l1 verdict: red file -> red, exit 2", rc == 2 and line.startswith("L1 red "), (rc, line, out, err))

vd = tmp("verdicts")
r3 = small_repo(link={"ports/fnv-nx/link/tests/test_x.py": "import sys\nprint('boom: [Errno 28] No space left on device')\nsys.exit(1)\n"})
rc, line, out, err = l1("verdict", "--repo", r3, "--commit", "main", "--verdicts", vd)
tree3 = git(r3, "rev-parse", "main^{tree}")
stored = json.load(open(os.path.join(vd, tree3 + ".json")))
gt = [g for g in stored["gates"] if g["name"] == "selftests"][0]
check("l1 verdict: the reason of an uncertified test (exit status, errno text) is in the stored verdict and the printed gate line",
      rc == 2 and "test_x.py" in gt["reason"] and "exit 1" in gt["reason"] and "[Errno 28] No space left on device" in gt["reason"]
      and "[Errno 28]" in out, (rc, gt, out))

# gate TMPDIR rule: private empty TMPDIR per gate, removed on any outcome; leftovers in it or in the worktree make the gate red
LEAKY = ("import json, os, sys\nk = sys.argv[1]\n"
         "if k == 'tmp': open(os.path.join(os.environ['TMPDIR'], 'junk'), 'w').write('x')\n"
         "if k == 'wt': open('stray.txt', 'w').write('x')\n"
         "json.dump({'format': 'fnvnx-gate/1', 'name': 'leaky', 'status': 'green', 'universe': 1, 'certified': 1, 'uncertified': [],\n"
         "           'detail': os.environ['TMPDIR']}, open(os.path.join(os.environ['GATE_OUT'], 'leaky.json'), 'w'))\n")
def leaky_run(kind):
    r = small_repo(gates={"format": "fnvnx-gates/1", "gates": [
        {"name": "leaky", "argv": ["python3", "-I", "ports/fnv-nx/verify/leaky.py", kind], "result": "leaky.json", "covers": ["tool"]}]},
        link={"ports/fnv-nx/verify/leaky.py": LEAKY})
    vd = tmp("verdicts")
    rc, line, out, err = l1("verdict", "--repo", r, "--commit", "main", "--verdicts", vd)
    return rc, out, err
rc, out, err = leaky_run("clean")
check("gate tmpdir: a gate that leaves nothing is green", rc == 0, (rc, out, err))
rc, out, err = leaky_run("tmp")
check("gate tmpdir: a gate that leaves a file in its TMPDIR is red and the file is named", rc == 2 and "in its TMPDIR: junk" in out, (rc, out, err))
rc, out, err = leaky_run("wt")
check("gate tmpdir: a gate that leaves a file in the worktree is red and the file is named", rc == 2 and "in the worktree: stray.txt" in out, (rc, out, err))

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
branch(r, "bad", {"ports/fnv-nx/link/bad.py": BAD, V + "verify/admit.json": adm("stage.py", "bad.py")})
before = git(r, "rev-parse", "main")
rc, line, out, err = l1("merge", "--repo", r, "--into", "main", "--from", "bad", "--verdicts", vd)
check("l1 merge: red file in link/ is blocked, ref does not move", rc == 2 and line.startswith("L1 red ")
      and "blocked:" in out and git(r, "rev-parse", "main") == before, (rc, line, out, err))

# green second merge uses the ratchet (previous verdict exists)
branch(r, "feat2", {"ports/fnv-nx/link/ok2.py": CLEAN})
rc, line, out, err = l1("merge", "--repo", r, "--into", "main", "--from", "feat2", "--verdicts", vd)
check("l1 merge: second green merge, previous verdict used", rc == 0 and "bootstrap" not in out and "merged " in out, (rc, line, out, err))

# ratchet: gate dropped
branch(r, "drop", {}, delete=["ports/fnv-nx/verify/gates.d/selftests.json"])
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
    declare(d, dirs)
    return gate("gate_tests.py", d, ["--cache", cache] + list(extra))

# TMPDIR rule: private TMPDIR per test file, removed whatever the outcome; leftovers are red; the reason is in the result
def gate_tmp(d, extra=()):
    """gate_tests.py over verify/ in D with the gate's TMPDIR in a fresh directory; -> (rc, result, entries left there afterwards)"""
    gt = tempfile.mkdtemp(dir=ROOT, prefix="gt-")
    out = tempfile.mkdtemp(dir=ROOT, prefix="out-")
    declare(d)
    p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "gate_tests.py")] + list(extra), cwd=d,
                       env=dict(os.environ, GATE_OUT=out, TMPDIR=gt), capture_output=True, text=True)
    return p.returncode, json.load(open(os.path.join(out, "selftests.json"))), sorted(os.listdir(gt)), p.stderr

d = tmp("tests-tmpdir")
mk(d, test_a="import tempfile\nd = tempfile.mkdtemp()\nopen(d + '/x', 'w').write('x' * 1000)\nimport shutil\nshutil.rmtree(d)\n",
   test_b="import tempfile\ntempfile.mkdtemp(prefix='leak-')\n",
   test_c="import os, sys, time\nopen(os.path.join(os.environ['TMPDIR'], 'big'), 'w').write('y')\ntime.sleep(60)\n",
   test_d="import sys\nprint('boom: [Errno 28] No space left on device')\nsys.exit(1)\n")
rc, r, left, e = gate_tmp(d, ("--timeout-s", "3"))
rows = {f["name"].rsplit("/", 1)[-1]: f for f in r["files"]}
check("tests tmpdir: a clean test passes; a test leaving an entry in its TMPDIR is uncertified and names it", rc != 0
      and rows["test_a.py"]["status"] == "passed" and rows["test_b.py"]["status"] == "failed"
      and "leaked in TMPDIR  leak-" in rows["test_b.py"]["reason"], (rc, r, e))
check("tests tmpdir: a timeout kill is a failure with the reason, and its scratch is removed", rows["test_c.py"]["status"] == "failed"
      and "timeout" in rows["test_c.py"]["reason"] and "leaked in TMPDIR  big" in rows["test_c.py"]["reason"], r)
check("tests tmpdir: the exit status and the tail of the output (errno text) are the reason", rows["test_d.py"]["status"] == "failed"
      and "exit 1" in rows["test_d.py"]["reason"] and "[Errno 28] No space left on device" in rows["test_d.py"]["reason"], r)
check("tests tmpdir: after the run the gate's TMPDIR holds no new entry, whatever the outcomes", left == [], left)

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
declare(d)
subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "gate_tests.py")], cwd=d, env=dict(os.environ, GATE_OUT=out))
times = json.load(open(os.path.join(out, "selftests.times.json")))
check("tests: per-file seconds in selftests.times.json", [(t["name"], t["status"]) for t in times]
      == [("verify/tests/test_a.py", "passed"), ("verify/tests/test_b.py", "failed")] and all(isinstance(t["seconds"], float) for t in times), times)

d = tmp("tests-cache")
cache = os.path.join(d, "cache")
RL = os.path.join(tmp("rl"), "ran.log")
mk(d, test_a="open(%r, 'a').write('a\\n')\n" % RL, test_b="open(%r, 'a').write('b\\n')\n" % RL)
def ran():  # every cold execution runs twice (testdet): one entry per execution PAIR
    got = open(RL).read().split() if os.path.exists(RL) else []
    return sorted(k for k in set(got) for _ in range(got.count(k) // 2))
kfiles = lambda: sorted(os.listdir(os.path.join(cache, "selftests"))) if os.path.isdir(os.path.join(cache, "selftests")) else []
rc, r, e = tests_c(d, cache)
check("tests cache: first run executes every file and stores one key per file", rc == 0 and sorted(ran()) == ["a", "b"]
      and r["recomputed"] == 2 and len(kfiles()) == 2 and not any(f["cached"] for f in r["files"]), (rc, r, e, ran()))
rows1 = [(f["name"], f["status"]) for f in r["files"]]
rc, r, e = tests_c(d, cache)
check("tests cache: unchanged inputs -> certified without running, same result (two runs agree)", rc == 0 and len(ran()) == 2
      and r["certified"] == 2 and r["recomputed"] == 0 and all(f["cached"] for f in r["files"])
      and [(f["name"], f["status"]) for f in r["files"]] == rows1 and "2 cached" in r["detail"], (rc, r, e, ran()))
declare(d)
rc, r, e = gate("gate_tests.py", d, [])
check("tests cache: no --cache and no GATE_CACHE -> always runs", rc == 0 and len(ran()) == 4 and r["recomputed"] == 2, (r, ran()))
os.utime(os.path.join(d, "verify/tests/test_a.py"), (5, 5))
rc, r, e = tests_c(d, cache)
check("tests cache: an mtime change is not a change of content", r["recomputed"] == 0 and len(ran()) == 4, (r, ran()))
mk(d, test_a="open(%r, 'a').write('a\\n')\n# changed\n" % RL)
rc, r, e = tests_c(d, cache)
check("tests cache: a changed test file reruns only itself", rc == 0 and ran().count("a") == 3 and ran().count("b") == 2 and r["recomputed"] == 1
      and [f["cached"] for f in r["files"]] == [False, True], (r, ran()))
rc, r, e = tests_c(d, cache)
check("tests cache: and is then cached again", r["recomputed"] == 0 and len(ran()) == 5, (r, ran()))
put(d, "verify/helper.py", "X = 1\n")
rc, r, e = tests_c(d, cache)
check("tests cache: new content in the view (a file that is no test) -> every test reruns", rc == 0 and r["recomputed"] == 2
      and len(ran()) == 7, (r, ran()))
put(d, "other/data.txt", "1\n")
rc, r, e = tests_c(d, cache)
n1 = len(ran())
put(d, "other/data.txt", "2\n")
rc, r, e = tests_c(d, cache)
check("tests cache: a changed file in ANY directory of the checkout is a changed view", r["recomputed"] == 2 and len(ran()) == n1 + 2, (r, ran()))
rc, r, e = tests_c(d, cache, extra=("--timeout-s", "200"))
check("tests cache: another timeout is another key", r["recomputed"] == 2, (r, ran()))
mk(d, test_c="import os, sys\nsys.exit(1 if os.path.exists('verify/tests/test_a.py') or os.path.exists('verify/tests/test_b.py') else 0)\n")
rc, r, e = tests_c(d, cache)
check("tests: a test runs in a view without the other test files", rc == 0 and r["universe"] == 3 and r["certified"] == 3, (rc, r, e))
d = tmp("tests-cache-closure")
cache = os.path.join(d, "cache")
Q = "import os, sys\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nimport test_p\n"
mk(d, test_p="pass\n", test_r="pass\n", test_q=Q, test_s=Q)
put(d, "verify/tests/oracle.json", json.dumps({"format": "fnvnx-oracle/1", "tests": {"test_q.py": {"uses": ["verify/tests/test_p.py"]}}}))
rc, r, e = tests_c(d, cache)
st = {f["name"][-9:]: f["status"] for f in r["files"]}
check("tests: a declared sibling is in the view, an undeclared one is missing and the test is red",
      st == {"test_p.py": "passed", "test_q.py": "passed", "test_r.py": "passed", "test_s.py": "failed"} and rc != 0, (rc, r, e))
mk(d, test_p="pass\n# changed\n")
rc, r, e = tests_c(d, cache)
check("tests cache: a changed test reruns itself and the tests that declare it, not the others",
      [(f["name"][-9:], f["cached"]) for f in r["files"]] == [("test_p.py", False), ("test_q.py", False), ("test_r.py", True), ("test_s.py", True)], (r, e))

d = tmp("tests-det")
cache = os.path.join(d, "cache")
mk(d, test_ok="print('PASS x')\n", test_flaky="import random\nprint('PASS ' + str(random.random()))\n",
   test_timing="import time\nprint('PASS t')\nprint('INFO %r' % time.time())\n")
rc, r, e = tests_c(d, cache)
check("testdet: a test whose PASS/FAIL record differs between its two runs is red and not cached; timing info is not the record",
      rc != 0 and r["uncertified"] == ["verify/tests/test_flaky.py"] and r["testdet"]["nondeterministic"] == ["verify/tests/test_flaky.py"]
      and "nondeterministic" in r["detail"] and len(os.listdir(os.path.join(cache, "selftests"))) == 2, (rc, r, e))

d = tmp("tests-hermetic")
mk(d, test_net="import socket, sys\ns = socket.socket()\ns.settimeout(2)\n"
   "try:\n    s.connect(('1.1.1.1', 80))\nexcept OSError:\n    sys.exit(0)\nsys.exit(1)\n",
   test_env="import os, sys\nsys.exit(0 if (os.environ.get('TZ'), os.environ.get('LC_ALL'), os.getcwd()) == ('UTC', 'C', '/nxroot/view') and not os.environ.get('HTTPS_PROXY') else 1)\n")
rc, r, e = tests(d, extra=())
check("tests: a test runs without network, with the fixed environment and the fixed cwd", rc == 0 and r["certified"] == 2, (rc, r, e))

# env.json of the checkout is part of the key
e1 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "env.json")
d2 = tmp("tests-cache-env")
mk(d2, test_a="pass\n")
declare(d2)
for n in ("gate_tests.py", "cas.py", "envcheck.py", "gate_admit.py", "gatesdir.py"):
    shutil.copy(os.path.join(VERIFY, n), os.path.join(d2, n))
def gate_in(dirpath, cache):
    out = tempfile.mkdtemp(dir=ROOT, prefix="out-")
    subprocess.run([sys.executable, "-I", os.path.join(dirpath, "gate_tests.py"), "--cache", cache], cwd=d2,
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
check("tests cache: another env.json is another key (the file is also in the view)", r["recomputed"] == 0 and r2["recomputed"] == 1, (r, r2))

d = tmp("tests-cache-fail")
NL = os.path.join(tmp("nl"), "n.log")
cache = os.path.join(d, "cache")
mk(d, test_ok="pass\n", test_bad="import sys\nopen(%r, 'a').write('x')\nprint('boom-text')\nsys.exit(1)\n" % NL)
rc1, r1, _ = tests_c(d, cache)
rc2, r2, _ = tests_c(d, cache)
nlog = lambda: len(open(NL).read())
check("tests cache: a failing file stays red from the cache with the same failure text, and is not rerun", rc1 != 0 and rc2 != 0
      and nlog() == 2 and r2["recomputed"] == 0 and r2["uncertified"] == ["verify/tests/test_bad.py"] and r2["certified"] == 1
      and "boom-text" in r1["detail"] and r1["detail"].split("first failure")[1] == r2["detail"].split("first failure")[1], (r1, r2))
put(d, "verify/tests/test_bad.py", "pass\n")
rc3, r3, _ = tests_c(d, cache)
rc4, r4, _ = tests_c(d, cache)
check("tests cache: fixed file is a new key, passes, then is cached", rc3 == 0 and r3["recomputed"] == 1 and rc4 == 0 and r4["recomputed"] == 0, (r3, r4))

d = tmp("tests-cache-timeout")
cache = os.path.join(d, "cache")
mk(d, test_slow="import time\ntime.sleep(3600)\n")
rc, r, e = tests_c(d, cache, extra=("--timeout-s", "1"))
rc2, r2, e = tests_c(d, cache, extra=("--timeout-s", "1"))
check("tests cache: a timeout is never cached", rc != 0 and rc2 != 0 and r2["recomputed"] == 1 and not os.path.isdir(os.path.join(cache, "selftests")), (r, r2))

# l1 passes VDIR/cache as GATE_CACHE: the second verdict of an unchanged tree finds the selftests keyed result
r = small_repo()
vd = tmp("verdicts")
rc, line, out, err = l1("verdict", "--repo", r, "--commit", "main", "--verdicts", vd)
cdir = os.path.join(vd, "cache")
keys1 = sorted(os.listdir(cdir)) if os.path.isdir(cdir) else []
rc2, line2, out2, err2 = l1("verdict", "--repo", r, "--commit", "main", "--verdicts", vd)
check("l1 verdict: gates get GATE_CACHE=VDIR/cache; same tree again reuses the keyed selftests result", rc == 0 and rc2 == 0
      and keys1 == ["gates", "selftests"] and sorted(os.listdir(cdir)) == keys1 and re.match(LINE, line2), (rc, rc2, keys1, out, err, out2, err2))

import contextlib  # noqa: E402
import io  # noqa: E402
sys.path.insert(0, VERIFY)
# ---- gate "warm" (VERDICT.md, budget) and the gate-result cache
def stored(vd):
    fs = [f for f in os.listdir(vd) if f.endswith(".json")]
    return json.load(open(os.path.join(vd, fs[-1])))

def warm_gates(budget, extra=()):
    g = two_gates()
    g["gates"] += list(extra)
    if budget is not None:
        g["warm_budget_s"] = budget
    return g

def wl(r, gd, vd):
    """The verdict of main of repo R with the explicit gates document GD (the budget under test), through l1's own _check: (rc, text, "")."""
    import l1 as l1mod
    td = tmp("wl")
    gp = os.path.join(td, "gates.json")
    with open(gp, "w") as fh:
        json.dump(gd, fh)
    env_id, bad = l1mod._env(r, "main")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        v = l1mod._check(r, git(r, "rev-parse", "main"), vd, None, gp, td, env_id)
    return (0 if v["verdict"] == "green" else 2), buf.getvalue(), ""

gd = warm_gates(None)
r = small_repo(gd)
vd = tmp("v-nowarm")
rc, out, err = wl(r, gd, vd)
v = stored(vd)
check("warm: a gates file without warm_budget_s has no gate warm", rc == 0 and [g["name"] for g in v["gates"]] == ["pure", "selftests"]
      and "warm" not in v and [g["recomputed"] for g in v["gates"]][0] == 1, (rc, out, err, v))
rc, out, err = wl(r, gd, tmp("v-nowarm2"))

gd = warm_gates(60)
r = small_repo(gd)
vd = tmp("v-warm")
rc, out, err = wl(r, gd, vd)
v = stored(vd)
w = [g for g in v["gates"] if g["name"] == "warm"]
check("warm: cold verdict recomputes every gate, the no-change rerun recomputes none, gate warm is green 2/2",
      rc == 0 and all(g["recomputed"] >= 1 for g in v["gates"] if g["name"] != "warm") and v["warm"]["recomputed"] == 0
      and w and (w[0]["universe"], w[0]["certified"], w[0]["status"]) == (2, 2, "green") and v["warm"]["budget_s"] == 60.0
      and v["warm"]["seconds"] <= 60, (rc, out, err, v))
# the stored gate results are used by a second verdict (new verdicts dir, same cache)
rc, out, err = wl(r, gd, vd)
v2 = stored(vd)
check("warm: a second verdict of the same commit recomputes nothing at all", rc == 0
      and all(g["recomputed"] == 0 for g in v2["gates"]), (rc, out, err, v2))

# deterministic part: a gate that destroys the cached keys of the gates before it makes the no-change rerun recompute them
evil = {"name": "evil", "result": "evil.json", "covers": ["tool"], "argv": ["python3", "-I", "-c",
        "import glob, json, os\nfor f in glob.glob(os.environ['GATE_CACHE'] + '/gates/*.json'): os.remove(f)\n"
        "json.dump({'format': 'fnvnx-gate/1', 'name': 'evil', 'status': 'green', 'universe': 1, 'certified': 1, 'uncertified': [], "
        "'detail': ''}, open(os.environ['GATE_OUT'] + '/evil.json', 'w'))\n"]}
gd = warm_gates(60, (evil,))
r = small_repo(gd)
vd = tmp("v-warm-evil")
rc, out, err = wl(r, gd, vd)
v = stored(vd)
w = [g for g in v["gates"] if g["name"] == "warm"]
check("warm: work on a no-change rerun makes gate warm red (deterministic part), naming the gates", rc == 2 and v["verdict"] == "red"
      and w and w[0]["status"] == "red" and w[0]["certified"] == 1 and "recomputed on a no-change rerun: pure=1" in w[0]["reason"]
      and v["warm"]["recomputed"] > 0, (rc, out, err, v))

# wall-clock part: a budget no rerun can meet
gd = warm_gates(1e-9)
r = small_repo(gd)
vd = tmp("v-warm-slow")
rc, out, err = wl(r, gd, vd)
v = stored(vd)
w = [g for g in v["gates"] if g["name"] == "warm"]
check("warm: a no-change rerun over the budget makes gate warm red (wall-clock part)", rc == 2 and w and w[0]["status"] == "red"
      and w[0]["certified"] == 1 and "> budget" in w[0]["reason"] and v["warm"]["recomputed"] == 0, (rc, out, err, v))

# a red gate is not stored: it is computed again, and gate warm is not evaluated on a red run
gd = warm_gates(60)
r = small_repo(gd, link={"ports/fnv-nx/link/tests/test_ok.py": "import sys\nsys.exit(1)\n"})
vd = tmp("v-warm-red")
rc, out, err = wl(r, gd, vd)
v = stored(vd)
check("warm: a red gate is never stored and gate warm is not run on a red verdict", rc == 2 and "warm" not in v
      and not any(g["name"] == "warm" for g in v["gates"]), (rc, out, v))

# the budget written in VERDICT.md is the one in gatesdir.py (the assembled gates document) (one number, two places, mechanically equal)
spec = gatesdir.load_dir(os.path.join(VERIFY, "gates.d"))
md = open(os.path.join(VERIFY, "VERDICT.md")).read()
mm = re.search(r"warm_budget_s = ([0-9.]+) ", md)
check("warm: the gates document has warm_budget_s, equal to B in VERDICT.md, at most 10", isinstance(spec.get("warm_budget_s"), (int, float))
      and mm and float(mm.group(1)) == float(spec["warm_budget_s"]) and 0 < spec["warm_budget_s"] <= 10, (spec.get("warm_budget_s"), mm))

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
branch(r, "bad", {"ports/fnv-nx/link/bad.py": BAD, V + "verify/admit.json": adm("stage.py", "bad.py")})
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
