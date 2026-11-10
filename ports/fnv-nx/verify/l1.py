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
Gates get GATE_CACHE = VDIR/cache (content-keyed results of gates such as selftests; see verdict.py).
  merge --remote NAME: the remote branch moves only from the exact commit the verdict was computed against, to the exact merge commit
           that was judged, in one atomic step. `git fetch NAME BRANCH` refreshes refs/remotes/NAME/BRANCH; its tip is the BASE of the
           merge (a local BRANCH that exists and differs from that tip is `blocked: local branch differs from NAME/BRANCH`; the caller
           brings it to the tip). REF is resolved as given: the caller fetches it. After green the merge commit (parents BASE, REF) is
           pushed with --force-with-lease=refs/heads/BRANCH:BASE; only after the push succeeded are refs/heads/BRANCH and
           refs/remotes/NAME/BRANCH set to it. A lease rejection is `blocked: moved (NAME/BRANCH is no longer <BASE12>)`, exit 5; any
           other push (or fetch) failure is `blocked: push failed: <first line>`, exit 6; in both nothing local changes and the stored green
           verdict stays (keyed by tree). Without --remote only the local ref moves and the last line ends `local only`.
  Exit codes of merge: 0 merged, 2 red/blocked by verdict, 3 conflict, 4 env, 5 moved, 6 push failed (1 error).
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

def _opts(a, extra=(), optional=()):
    return verdict._opts(a, dict({"--repo": 0, "--verdicts": 0, "--budget-s": 1}, **{k: 0 for k in extra}, **{k: 1 for k in optional}))

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
    args = ["--repo", repo, "--commit", commit, "--gates", gates, "--out", outp, "--env-id", env_id,
            "--cache", os.path.join(vdir, "cache")]  # content-keyed gate results (verdict.py gate contract, GATE_CACHE)
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
    o = _opts(a, ("--into", "--from"), ("--remote",))
    o.setdefault("--remote", None)
    repo, vdir, remote, bname = o["--repo"], o["--verdicts"], o["--remote"], o["--into"]
    if remote:
        rc, _, err = verdict._git(repo, "fetch", "--quiet", remote, "+refs/heads/%s:refs/remotes/%s/%s" % (bname, remote, bname))
        if rc:
            print("blocked: push failed: fetch: " + (err.splitlines() or ["?"])[0])
            return 6
        old = verdict._git_ok(repo, "rev-parse", "--verify", "refs/remotes/%s/%s^{commit}" % (remote, bname))
        rc, loc, _ = verdict._git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/" + bname)
        if rc == 0 and loc != old:
            print("blocked: local branch differs from %s/%s" % (remote, bname))
            return 2
    else:
        old = verdict._git_ok(repo, "rev-parse", "--verify", "refs/heads/" + bname)
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
        try:
            stored = json.load(open(vpath)) if os.path.isfile(vpath) else None
        except ValueError:
            stored = None
        # only a GREEN stored verdict is reused: green records that every gate passed on this tree; a red one is computed again
        # (it can turn green only by every gate passing), so a red verdict is never a permanent mark on a tree
        if stored is None or stored.get("verdict") != "green":
            cand = verdict._git_ok(repo, "commit-tree", tree, "-p", old, "-p", src, inp="l1 candidate merge of %s\n" % o["--from"])
            v = _check(repo, cand, vdir, o.get("--budget-s"), gates, td, env_id)
        else:
            v = stored
        print(_line(v))
        args = ["--repo", repo, "--into", o["--into"], "--from", o["--from"], "--verdicts", vdir, "--gates", gates, "--env-id", env_id]
        if remote:
            args += ["--base", old]
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
    new = buf.getvalue().strip()
    if not remote:
        print("merged %s local only" % new)
        return rc
    return _push(repo, remote, bname, old, new)

def _push(repo, remote, bname, base, new):
    """Publish NEW over BASE with a lease on BASE itself, then (only then) move the local refs."""
    if verdict._git(repo, "merge-base", "--is-ancestor", base, new)[0]:
        print("blocked: merge commit does not descend from %s/%s, refusing to push" % (remote, bname))
        return 2
    ref = "refs/heads/" + bname
    rc, out, err = verdict._git(repo, "push", "--porcelain", "--force-with-lease=%s:%s" % (ref, base), remote, "%s:%s" % (new, ref))
    if rc:
        if "stale info" in out + err:
            print("blocked: moved (%s/%s is no longer %s)" % (remote, bname, base[:12]))
            return 5
        first = ([x for x in err.splitlines() if x.strip()] or [x for x in out.splitlines() if x.strip()] or ["?"])[0]
        print("blocked: push failed: " + first)
        return 6
    verdict._git_ok(repo, "update-ref", ref, new)
    verdict._git_ok(repo, "update-ref", "refs/remotes/%s/%s" % (remote, bname), new)
    print("merged %s pushed %s/%s" % (new, remote, bname))
    return 0

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
