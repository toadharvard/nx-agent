#!/usr/bin/env python3
"""Host test of farm/runner.sh. A clone of this checkout's HEAD stands in for the trunk (farm/runner.sh of the working
tree is committed on top, so the script under test is the one of this tree), a local bare git repository for the
artifacts repository, a scratch directory for FARM_HOME. The verified commits are made in the clone: the HEAD tree, the
same tree with an untrue declaration (test_cas.py declares a subject it never opens) and the same tree with a changed
env.json. No network. Plain script: exit 0 on pass. Scratch: tempfile.mkdtemp, removed at exit."""
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
NX = os.path.dirname(os.path.dirname(HERE))
SCRATCH = tempfile.mkdtemp(prefix="runner-test-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
T = "ports/fnv-nx"
CAS = T + "/verify/tests/test_cas.py"




def git(cwd, *args, inp=None):
    return subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@invalid"] + list(args), input=inp,
                          capture_output=True, text=True, check=True).stdout.strip()


top = git(NX, "rev-parse", "--show-toplevel")
repo = os.path.join(SCRATCH, "trunk")
subprocess.run(["git", "clone", "-q", "--no-hardlinks", top, repo], check=True, capture_output=True)
shutil.copy(os.path.join(NX, "farm", "runner.sh"), os.path.join(repo, T, "farm", "runner.sh"))
git(repo, "add", "-A")
git(repo, "commit", "-q", "--allow-empty", "-m", "the tree under test")
good = git(repo, "rev-parse", "HEAD")
decl = os.path.join(repo, T, "verify/tests/oracle.json")
doc = json.load(open(decl))
doc["tests"]["test_cas.py"]["subject"].append(T + "/verify/graph.py")
json.dump(doc, open(decl, "w"), indent=1, sort_keys=True)
git(repo, "commit", "-qam", "untrue declaration")
untrue = git(repo, "rev-parse", "HEAD")
git(repo, "checkout", "-q", good)
envf = os.path.join(repo, T, "verify/env.json")
env_doc = json.load(open(envf))
env_doc["uid"] = env_doc["uid"] + 1
json.dump(env_doc, open(envf, "w"))
git(repo, "commit", "-qam", "another environment")
badenv = git(repo, "rev-parse", "HEAD")
git(repo, "checkout", "-q", good)

bare = os.path.join(SCRATCH, "artifacts.git")
subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
art = os.path.join(SCRATCH, "artifacts")
subprocess.run(["git", "clone", "-q", bare, art], check=True, capture_output=True)
ENV = dict({k: v for k, v in os.environ.items() if not k.startswith("GATE_")}, FARM_HOME=os.path.join(SCRATCH, "farm"),
           ARTIFACTS=art, RUNNER_JOBS="1")
FINAL = re.compile(r"^AFTERMERGE commit=([0-9a-f]{12}) jobs=(\d+) violated=(\S+) published=(\S+)@([0-9a-f]{12})$")


def run(*args):
    p = subprocess.run(["bash", os.path.join(repo, T, "farm", "runner.sh")] + list(args), env=ENV, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def branches():
    return git(bare, "for-each-ref", "--format=%(refname)").split()


rc, out, err = run("1234", "oraclecheck")
case("a commit that is not 40 hex digits: exit 1", rc == 1 and "40 hex" in err and out == "", (rc, out, err))
rc, out, err = run(good, "sanitize")
case("a job outside the table: exit 1", rc == 1 and "unknown job sanitize" in err and out == "", (rc, out, err))
rc, out, err = run(good)
case("no job: exit 1", rc == 1 and "usage" in err, (rc, out, err))
rc, out, err = run("0" * 40, "oraclecheck")
case("a commit the repository does not have: exit 1", rc == 1 and out == "", (rc, out, err))
rc, out, err = run(badenv, "oraclecheck=" + CAS)
case("a commit whose env.json this machine does not match: exit 1, envcheck named", rc == 1 and "envcheck" in err and out == "", (rc, out, err))
case("nothing published by the failed runs", branches() == [], branches())

rc, out, err = run(good, "oraclecheck=" + CAS)
m = FINAL.match(out.strip().splitlines()[-1]) if out.strip() else None
case("true declaration: exit 0, final line", rc == 0 and m and m.group(1) == good[:12] and m.group(2) == "1" and m.group(3) == "none"
      and m.group(4) == "l4-verify/after-merge/" + good[:12], (rc, out, err))
ref = "refs/heads/l4-verify/after-merge/" + good[:12]
case("the branch is the published commit", m and branches() == [ref] and git(bare, "rev-parse", ref).startswith(m.group(5)), branches())
if m:
    files = git(bare, "ls-tree", "--name-only", ref).split()
    case("published files", files == ["oraclecheck.json", "oraclecheck.log", "report.json"], files)
    rep = json.loads(git(bare, "show", ref + ":report.json"))
    env_id = subprocess.run([sys.executable, "-I", os.path.join(repo, T, "verify/envcheck.py"), "id"], capture_output=True, text=True).stdout.strip()
    case("report.json", sorted(rep) == ["commit", "env_id", "format", "host_cpu_model", "jobs"] and rep["format"] == "fnvnx-aftermerge/1"
          and rep["commit"] == good and rep["env_id"] == env_id and rep["host_cpu_model"] and len(rep["jobs"]) == 1
          and {k: v for k, v in rep["jobs"][0].items() if k != "seconds"} == {"name": "oraclecheck", "args": [CAS], "rc": 0,
                                                                              "result": "oraclecheck.json"}, rep)
    oc = json.loads(git(bare, "show", ref + ":oraclecheck.json"))
    case("the result is the checker's report of the named test", oc["format"] == "fnvnx-oraclecheck/1" and [t["test"] for t in oc["tests"]] == [CAS]
          and oc["tests"][0]["pass"] is True, oc)
    msg = git(bare, "log", "-1", "--format=%B", ref)
    case("commit: orphan, one message line", git(bare, "rev-list", "--count", ref) == "1" and len(msg.splitlines()) == 1, msg)
case("the worktree of the run is removed", len(git(repo, "worktree", "list").splitlines()) == 1, git(repo, "worktree", "list"))

rc, out, err = run(untrue, "oraclecheck=" + CAS)
m = FINAL.match(out.strip().splitlines()[-1]) if out.strip() else None
ref2 = "refs/heads/l4-verify/after-merge/" + untrue[:12]
case("untrue declaration: exit 2, violated=oraclecheck, published", rc == 2 and m and m.group(3) == "oraclecheck" and ref2 in branches(), (rc, out, err))
if m and ref2 in branches():
    oc = json.loads(git(bare, "show", ref2 + ":oraclecheck.json"))
    rep = json.loads(git(bare, "show", ref2 + ":report.json"))
    case("the published result names the unopened subject", oc["tests"][0]["pass"] is False
          and oc["tests"][0]["unopened"] == [T + "/verify/graph.py"] and rep["jobs"][0]["rc"] == 2, oc)
sys.exit(testcase.finish())
