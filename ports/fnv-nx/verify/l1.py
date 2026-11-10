#!/usr/bin/env python3
"""L1: the one command a merger runs. Run as `python3 -I l1.py CMD ...`. Exit codes: 0 green/merged, 2 red/blocked, 3 conflict, 4 blocked: env, 1 error.
  verdict  --repo R --commit C --verdicts VDIR [--budget-s 300]
           verdict.py run with the gates file OF THAT COMMIT (ports/fnv-nx/verify/gates.json read from C's tree), then store.
  merge    --repo R --into BRANCH --from REF --verdicts VDIR [--budget-s 300]
           Computes the merge tree as verdict.py merge does. If VDIR has no verdict for it, makes the merge commit object with
           commit-tree (no ref moves) to have something to check out, runs `verdict` on it and stores it. Then verdict.py merge
           re-derives the tree and moves BRANCH only on green. The gates file is the one in the MERGE RESULT tree (a branch
           cannot drop gates): the ratchet's previous verdict is the stored green verdict of BRANCH's current tree, if any;
           otherwise this is the bootstrap case and `bootstrap: no previous verdict` is printed.
Both run `envcheck.py check` FIRST against the verify/env.json OF THAT COMMIT/TREE; on a mismatch they print `blocked: env` and the
mismatch lines, exit 4, run no gate and store nothing. The verdict carries "env_id" (sha256 of that env.json); merge only accepts a
stored verdict with the env_id of the merge tree's env.json.
Both print `L1 <green|red> <tree> <seconds>s gates:<n> over_budget:<bool>`; merge adds `merged <commit>` or `blocked: <reason>`."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import envcheck  # noqa: E402
import verdict  # noqa: E402

GATES = "ports/fnv-nx/verify/gates.json"
ENV = "ports/fnv-nx/verify/env.json"

def _opts(a, extra=()):
    return verdict._opts(a, dict({"--repo": 0, "--verdicts": 0, "--budget-s": 1}, **{k: 0 for k in extra}))

def _line(v):
    return "L1 %s %s %ss gates:%d over_budget:%s" % (v["verdict"], v["tree"], v["seconds"], len(v["gates"]), v["over_budget"])

def _gates_file(repo, rev, td):
    p = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (rev, GATES)], capture_output=True)
    if p.returncode:
        raise verdict.Err("blocked: no %s in %s" % (GATES, rev), 2)
    path = os.path.join(td, "gates.json")
    with open(path, "wb") as fh:
        fh.write(p.stdout)
    return path

def _env(repo, rev):
    """(env_id, mismatch lines) of this machine against the env.json of REV's tree (a missing file is a mismatch)."""
    p = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (rev, ENV)], capture_output=True)
    if p.returncode:
        return None, ["env: %s: want a declared environment in %s, have none" % (ENV, rev)]
    doc = json.loads(p.stdout)
    return envcheck.env_id(doc), envcheck.check(doc)

def _blocked_env(bad):
    print("\n".join(["blocked: env"] + bad))
    return 4

def _check(repo, commit, vdir, budget, gates, td, env_id):
    """verdict.py run + store for COMMIT; returns the stored verdict dict."""
    outp = os.path.join(td, "verdict.json")
    args = ["--repo", repo, "--commit", commit, "--gates", gates, "--out", outp, "--env-id", env_id]
    if budget is not None:
        args += ["--budget-s", budget]
    verdict.cmd_run(args)
    with contextlib.redirect_stdout(io.StringIO()):
        verdict.cmd_store(["--verdict", outp, "--verdicts", vdir])
    return json.load(open(outp))

def cmd_verdict(a):
    o = _opts(a, ("--commit",))
    env_id, bad = _env(o["--repo"], o["--commit"])
    if bad:
        return _blocked_env(bad)
    with tempfile.TemporaryDirectory() as td:
        v = _check(o["--repo"], o["--commit"], o["--verdicts"], o.get("--budget-s"), _gates_file(o["--repo"], o["--commit"], td), td, env_id)
    print(_line(v))
    return 0 if v["verdict"] == "green" else 2

def cmd_merge(a):
    o = _opts(a, ("--into", "--from"))
    repo, vdir = o["--repo"], o["--verdicts"]
    old = verdict._git_ok(repo, "rev-parse", "--verify", "refs/heads/" + o["--into"])
    src = verdict._git_ok(repo, "rev-parse", "--verify", o["--from"] + "^{commit}")
    rc, out, err = verdict._git(repo, "merge-tree", "--write-tree", old, src)
    if rc == 1:
        print("blocked: conflict, merge of %s into %s does not apply cleanly" % (o["--from"], o["--into"]))
        return 3
    if rc:
        raise verdict.Err("git merge-tree: " + err)
    tree = out.splitlines()[0]
    env_id, bad = _env(repo, tree)
    if bad:
        return _blocked_env(bad)
    with tempfile.TemporaryDirectory() as td:
        gates = _gates_file(repo, tree, td)
        vpath = os.path.join(vdir, tree + ".json")
        if not os.path.isfile(vpath):
            cand = verdict._git_ok(repo, "commit-tree", tree, "-p", old, "-p", src, inp="l1 candidate merge of %s\n" % o["--from"])
            v = _check(repo, cand, vdir, o.get("--budget-s"), gates, td, env_id)
        else:
            v = json.load(open(vpath))
        print(_line(v))
        args = ["--repo", repo, "--into", o["--into"], "--from", o["--from"], "--verdicts", vdir, "--gates", gates, "--env-id", env_id]
        prev = os.path.join(vdir, verdict._git_ok(repo, "rev-parse", old + "^{tree}") + ".json")
        pv = None
        if os.path.isfile(prev):
            try:
                pv = json.load(open(prev))
            except ValueError:
                pv = None
        if pv is not None and pv.get("verdict") == "green":
            args += ["--prev-verdict", prev]
        else:
            print("bootstrap: no previous verdict")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = verdict.cmd_merge(args)
        except verdict.Err as e:
            print("blocked: " + str(e).replace("blocked: ", "", 1))
            return e.rc
    print("merged " + buf.getvalue().strip())
    return rc

def main(argv):
    cmds = {"verdict": cmd_verdict, "merge": cmd_merge}
    if not argv or argv[0] not in cmds:
        print("usage: l1.py verdict|merge ... (see docstring)", file=sys.stderr)
        return 1
    try:
        return cmds[argv[0]](argv[1:])
    except verdict.Err as e:
        print(str(e), file=sys.stderr)
        return e.rc
    except (verdict.cas.CasError, OSError, KeyError, ValueError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
