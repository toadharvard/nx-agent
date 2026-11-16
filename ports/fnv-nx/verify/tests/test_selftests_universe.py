#!/usr/bin/env python3
"""Tests of the selftests gate's two rules (verify/gate_tests.py, TOOLS.md "verify/gate_tests.py"): the universe is derived from verify/admit.json
(every tests/test_*.py of every admitted root: a new test file enters with no edit of a gate file), and a test input may be PRODUCED
({"root": R, "output": NAME}: R built through the recipe in GATE_CACHE, its declared output mounted as FNVNX_INPUT_<NAME>, the output's hash in the key).
Plain script: one PASS/FAIL line per case, exit 0 iff all pass. The fixture is a copy of the tracked tree with admit.json replaced by toy roots."""
# reads: ports/fnv-nx
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.realpath(os.path.join(HERE, "..", "..", "..", ".."))
T = "ports/fnv-nx/"
FAILED = []
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")

def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:600]))
    if not cond:
        FAILED.append(name)

def put(d, rel, body):
    p = os.path.join(d, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write(body if isinstance(body, str) else json.dumps(body))

def commit(d):
    subprocess.run(["git", "add", "-A"], cwd=d, check=True, env=ENV)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=d, check=True, env=ENV)

def run(d, cache, out):
    os.makedirs(out, exist_ok=True)
    p = subprocess.run([sys.executable, "-I", T + "verify/gate_tests.py", "--jobs", "2"], cwd=d, env=dict(os.environ, GATE_OUT=out, GATE_CACHE=cache), capture_output=True, text=True)
    r = json.load(open(os.path.join(out, "selftests.json")))
    return p.returncode, r, {os.path.basename(f["name"]): f for f in r["files"]}

PASS_BODY = "import os\nassert os.environ['FNVNX_INPUT_%s'], 'no input'\nd = os.environ['FNVNX_INPUT_%s']\nassert os.path.exists(d), d\n"

with tempfile.TemporaryDirectory() as d:
    subprocess.run("git ls-files -z ports/fnv-nx | xargs -0 cp --parents -t " + d, shell=True, cwd=REPO, check=True)
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    adm = json.load(open(os.path.join(d, T + "verify/admit.json")))
    adm["roots"] = [{"path": T + "h", "class": "header"},
                    {"path": T + "p", "class": "product", "target": "host", "recipe": T + "p/build.json"},
                    {"path": T + "bad", "class": "product", "target": "host", "recipe": T + "bad/build.json"},
                    {"path": T + "r", "class": "other"}]
    put(d, T + "verify/admit.json", adm)
    put(d, T + "h/include/x.h", "int x;\n")
    put(d, T + "h/include/other.h", "int o;\n")
    put(d, T + "p/build.json", {"format": "fnvnx-recipe/1", "outputs": {"DATA": "data"}})
    put(d, T + "p/data/f.txt", "data\n")
    put(d, T + "bad/build.json", {"format": "fnvnx-recipe/1", "outputs": {"GEN": "gen"},
                                  "generated": [{"tool": "nothere.py", "args": [], "inputs": [], "outputs": ["gen/a.h"]}]})
    put(d, T + "nonadm/data/f.txt", "data\n")
    ok = PASS_BODY
    put(d, T + "r/tests/test_plain.py", "import sys\nsys.exit(0)\n")
    put(d, T + "r/tests/test_hdr.py", "import os\nd = os.environ['FNVNX_INPUT_INCLUDE']\nassert open(os.path.join(d, 'include', 'x.h')).read() == 'int x;\\n', d\n")
    put(d, T + "r/tests/test_prod.py", ok % ("DATA", "DATA"))
    put(d, T + "r/tests/test_unadm.py", ok % ("DATA", "DATA"))
    put(d, T + "r/tests/test_undecl.py", ok % ("NOPE", "NOPE"))
    put(d, T + "r/tests/test_red.py", ok % ("GEN", "GEN"))
    put(d, T + "r/tests/test_dup.py", ok % ("INCLUDE", "INCLUDE"))
    put(d, T + "r/tests/inputs.json", {"format": "fnvnx-selftest-inputs/1", "tests": {
        "test_hdr.py": [{"root": T + "h", "output": "INCLUDE"}],
        "test_prod.py": [{"root": T + "p", "output": "DATA"}],
        "test_unadm.py": [{"root": T + "nonadm", "output": "DATA"}],
        "test_undecl.py": [{"root": T + "p", "output": "NOPE"}],
        "test_red.py": [{"root": T + "bad", "output": "GEN"}],
        "test_dup.py": [{"root": T + "h", "output": "INCLUDE"}, {"root": T + "p", "output": "INCLUDE"}]}})
    commit(d)
    cache, out = os.path.join(d, "..", "gc-" + os.path.basename(d)), os.path.join(d, "..", "go-" + os.path.basename(d))
    try:
        rc, r, rows = run(d, cache, out)
        check("derived: every test file of every admitted root is in the universe, with no DIR in the gate's argv",
              set(rows) == {"test_dup.py", "test_hdr.py", "test_plain.py", "test_prod.py", "test_red.py", "test_undecl.py", "test_unadm.py"} and r["universe"] == 7, r)
        check("derived: the gate file names no directory", json.load(open(os.path.join(REPO, T + "verify/gates.d/selftests.json")))["argv"][-2:] == ["--jobs", "3"])
        check("derived: a test of no input passes", rows["test_plain.py"]["status"] == "passed", rows["test_plain.py"])
        check("produced: a header root's INCLUDE is mounted and holds its files", rows["test_hdr.py"]["status"] == "passed", rows["test_hdr.py"])
        check("produced: a root that is not admitted is red", rows["test_unadm.py"]["status"] == "failed" and "is not admitted" in rows["test_unadm.py"]["reason"], rows["test_unadm.py"])
        check("produced: an output the root does not declare is red", rows["test_undecl.py"]["status"] == "failed" and "does not declare the output" in rows["test_undecl.py"]["reason"], rows["test_undecl.py"])
        check("produced: two inputs of one NAME are red", rows["test_dup.py"]["status"] == "failed" and "not a valid fnvnx-selftest-inputs/1" in rows["test_dup.py"]["reason"], rows["test_dup.py"])
        declared = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); import recipe; recipe.env_blocked(%r)" % (os.path.join(REPO, T + "verify"), d)], capture_output=True).returncode == 0
        want_red = "build is red" if declared else "not the declared environment"   # off the declared machine no stage runs: the cause says so
        check("produced: a root whose build is red is red, with its cause", rows["test_red.py"]["status"] == "failed" and want_red in rows["test_red.py"]["reason"], rows["test_red.py"])
        if declared:
            check("produced: a built root's declared output is mounted", rows["test_prod.py"]["status"] == "passed", rows["test_prod.py"])
        else:
            check("produced: off the declared machine a built root's output is red with the environment as cause", rows["test_prod.py"]["status"] == "failed" and "not the declared environment" in rows["test_prod.py"]["reason"], rows["test_prod.py"])
        # a new test file enters with no edit of a gate file
        put(d, T + "r/tests/test_new.py", "import sys\nsys.exit(0)\n")
        commit(d)
        rc2, r2, rows2 = run(d, cache, out)
        check("derived: a new test file under an admitted root enters with no edit of any gate file", "test_new.py" in rows2 and r2["universe"] == 8, r2)
        check("cache: an unchanged passing file is cached", rows2["test_plain.py"]["status"] == "cached", rows2["test_plain.py"])
        # the input's key is the output's tree hash: a change of the header root that the test does not read re-runs it
        put(d, T + "h/include/other.h", "int o2;\n")
        commit(d)
        rc3, r3, rows3 = run(d, cache, out)
        check("key: a change of a produced input's content (a file the test does not read) runs the file again", rows3["test_hdr.py"]["status"] == "passed", rows3["test_hdr.py"])
    finally:
        shutil.rmtree(cache, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)

print("FAILED: %d" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
