#!/usr/bin/env python3
"""L4 verdict: gate certificates, merge gate, build/verification separation, rule-defect queue. stdlib only.
Run as `python3 -I verdict.py CMD ...`. Exit codes: 0 ok, 1 usage/format error, 2 red/blocked, 3 merge conflict.
  run         --repo R --commit C --gates GATES.json --out V.json [--budget-s 300] [--env-id H] [--cache DIR]
              Runs each gate (argv, cwd = clean git worktree of C, env + GATE_OUT + GATE_COMMIT [+ GATE_CACHE]), in file order.
              Gate contract for the environment: GATE_OUT (result directory), GATE_COMMIT (commit under test) and, when --cache is
              given, GATE_CACHE (absolute path of a caller-owned directory that outlives the run). A gate may store results in
              GATE_CACHE only under a key that covers everything it reads (its inputs' content, the declared environment, its own
              arguments); it must work, and give the same verdict, without GATE_CACHE. A gate writes
              {"format":"fnvnx-gate/1","name","status","universe","certified","uncertified":[<=50 ids],"detail"[,"reasons":{id: text}]} at GATE_OUT/<result>
              (reasons: why an uncertified id is uncertified; the first 10, each cut to its last 400 characters, join the gate's red reason).
              TMPDIR: each gate gets a private empty directory inside the run's work dir, removed after the gate on any outcome. A gate
              that leaves entries in it, or any untracked or ignored file in its worktree (but Python's __pycache__/*.pyc), is red with the names.
              Gate green iff exit 0, valid result, name equal, status green and certified == universe (completeness of
              certificates, not a run counter; certified > universe is invalid). Verdict green iff >=1 gate and all gates green.
              over_budget is reported only; it never changes the verdict.
  store       --verdict V.json --verdicts VDIR   copy to VDIR/<tree>.json if self-consistent (red verdicts are stored too).
  merge       --repo R --into B --from REF --verdicts VDIR --gates GATES.json [--prev-verdict V.json] [--message-prefix T]
              Merge result computed with `git merge-tree --write-tree` (no worktree touched). Needs VDIR/<tree>.json: green, same
              tree, same gates_sha256 as GATES.json. Ratchet (exact and nothing more): the set of gates never shrinks, i.e. every
              gate name of the previous green verdict must be present in the new one (uncertified == 0 is already required by the
              gate rule). Then a two-parent commit via commit-tree and refs/heads/B moved by update-ref compare-and-swap.
              A fast-forward still needs the verdict for REF's tree (a merge commit is made).
  provenance  --store S --docs DIR   Verification never feeds the build: every stage document needs "class"; a build stage whose
              input equals or CONTAINS (cas.listing) an output root of a verification stage, or of a tainted build stage
              (transitively), is tainted. Exit 2 lists chains. Stage results: S/keys/<key>/result.json (sandbox.py).
  defect / defects   rule-defect queue: content-addressed JSON entries, verification-class data only.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cas  # noqa: E402
import gatesdir  # noqa: E402
import sandbox  # noqa: E402

HEX = re.compile(r"^([0-9a-f]{40}|[0-9a-f]{64})$")

class Err(Exception):
    def __init__(self, msg, rc=1):
        Exception.__init__(self, msg)
        self.rc = rc

def _git(repo, *a, inp=None, env=None):
    p = subprocess.run(["git", "-C", repo] + list(a), capture_output=True, text=True, input=inp, env=env)
    return p.returncode, p.stdout.strip(), p.stderr.strip()

def _git_ok(repo, *a, inp=None):
    rc, out, err = _git(repo, *a, inp=inp)
    if rc:
        raise Err("git %s: %s" % (a[0], err))
    return out

def _load(path, what):
    try:
        return json.load(open(path))
    except (OSError, ValueError) as e:
        raise Err("%s %s: %s" % (what, path, e))

def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()

def _opts(a, names):
    o, i = {}, 0
    while i < len(a):
        if a[i] in names and i + 1 < len(a):
            o[a[i]] = a[i + 1]
            i += 2
        else:
            raise Err("unexpected argument %r" % a[i])
    miss = [n for n in names if n not in o and not names[n]]
    if miss:
        raise Err("missing " + ",".join(miss))
    return o

def _gate_result(g, out_dir, rc):
    """Returns (status, universe, certified, reason) from a gate's result file."""
    path = os.path.join(out_dir, g["result"])
    if not os.path.isfile(path):
        return "red", 0, 0, "missing result file %s" % g["result"]
    try:
        r = json.load(open(path))
        ok = (isinstance(r, dict) and r.get("format") == "fnvnx-gate/1" and r.get("status") in ("green", "red")
              and all(type(r.get(k)) is int and r[k] >= 0 for k in ("universe", "certified"))
              and isinstance(r.get("uncertified"), list) and all(isinstance(x, str) for x in r["uncertified"])
              and isinstance(r.get("detail"), str))
    except ValueError:
        ok = False
    if not ok:
        return "red", 0, 0, "invalid result file"
    u, c = r["universe"], r["certified"]
    if r.get("name") != g["name"]:
        return "red", u, c, "name mismatch: result says %r" % r.get("name")
    if c > u:
        return "red", u, c, "certified %d > universe %d" % (c, u)
    if c < u:
        why = r.get("reasons") if isinstance(r.get("reasons"), dict) else {}   # optional {id: text}: why a test/item is uncertified
        more = "".join("\n  %s: %s" % (x, str(why[x]).replace("\n", " ")[-400:]) for x in r["uncertified"][:10] if isinstance(why.get(x), str))
        return "red", u, c, "certified %d < universe %d; uncertified: %s%s" % (c, u, ",".join(r["uncertified"][:50]), more)
    if rc != 0:
        return "red", u, c, "exit %d" % rc
    if r["status"] != "green":
        return "red", u, c, "gate status red: " + r["detail"]
    return "green", u, c, ""

def _present_classes(wt):
    """The set of root classes of the admitted roots of the tree under test (gatesdir.root_classes); empty when admit.json is absent."""
    try:
        with open(os.path.join(wt, gatesdir.ADMIT), encoding="utf-8") as fh:
            return set().union(*gatesdir.root_classes(json.load(fh), wt).values())
    except (OSError, ValueError):
        return set()


def _covers_verdict(g, st, u, present):
    """No vacuous green (gatesdir.py): a gate whose `covers` is missing or not a non-empty list of root classes is red, and a gate whose
    universe is 0 while an admitted root of a covered class exists is red. Returns (status, reason or '')."""
    cv = g.get("covers")
    if not (isinstance(cv, list) and cv and all(c in gatesdir.ROOT_CLASSES for c in cv) and len(set(cv)) == len(cv)):
        return "red", "gate file has no valid covers (a non-empty list of %s)" % "/".join(gatesdir.ROOT_CLASSES)
    seen = sorted(set(cv) & present)
    if st == "green" and u == 0 and seen:
        return "red", "vacuous: universe 0 while admitted roots of class %s exist" % ",".join(seen)
    return st, ""


def cmd_run(a):
    o = _opts(a, {"--repo": 0, "--commit": 0, "--gates": 0, "--out": 0, "--budget-s": 1, "--env-id": 1, "--cache": 1})
    spec = _load(o["--gates"], "gates")
    gates = spec.get("gates") if isinstance(spec, dict) and spec.get("format") == "fnvnx-gates/1" else None
    if not isinstance(gates, list) or not all(isinstance(g, dict) and isinstance(g.get("name"), str) and g.get("argv")
                                              and isinstance(g.get("result"), str) for g in gates):
        raise Err("malformed gates file")
    if len({g["name"] for g in gates}) != len(gates):
        raise Err("duplicate gate names")
    commit = _git_ok(o["--repo"], "rev-parse", "--verify", o["--commit"] + "^{commit}")
    tree = _git_ok(o["--repo"], "rev-parse", commit + "^{tree}")
    work = tempfile.mkdtemp(prefix="verdict-")
    wt = os.path.join(work, "wt")
    _git_ok(o["--repo"], "worktree", "add", "--detach", wt, commit)
    genv = dict(os.environ, GATE_COMMIT=commit)
    genv.pop("GATE_CACHE", None)
    if "--cache" in o:
        genv["GATE_CACHE"] = os.path.abspath(o["--cache"])
        os.makedirs(genv["GATE_CACHE"], exist_ok=True)
    res, t_all = [], time.time()
    try:
        present = _present_classes(wt)
        for g in gates:
            out = os.path.join(work, "gate-%d" % len(res))
            tmp = os.path.join(work, "tmp-%d" % len(res))
            os.makedirs(out)
            os.makedirs(tmp)
            t0 = time.time()
            try:
                rc = subprocess.run(g["argv"], cwd=wt, env=dict(genv, GATE_OUT=out, TMPDIR=tmp),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
                st, u, c, why = _gate_result(g, out, rc)
            except OSError as e:
                st, u, c, why = "red", 0, 0, "cannot run: %s" % e
            left = sorted(os.listdir(tmp))
            shutil.rmtree(tmp, ignore_errors=True)   # removed on every outcome
            # interpreter bytecode caches (`python -I` ignores PYTHONDONTWRITEBYTECODE) are source-validated, not gate output
            dirty = [x for x in (l[3:] for l in _git(wt, "status", "--porcelain", "--ignored", "--untracked-files=all")[1].splitlines())
                     if not (x.endswith(".pyc") and "/__pycache__/" in "/" + x)]
            if left or dirty:
                st = "red"
                why = (why + "; " if why else "") + "gate left " + "; ".join(
                    x for x in ("in its TMPDIR: " + ",".join(left[:20]) if left else "",
                                "in the worktree: " + ",".join(dirty[:20]) if dirty else "") if x)
                _git(wt, "clean", "-fdxq")
            st2, why2 = _covers_verdict(g, st, u, present)
            if st2 != st:
                st, why = st2, why2 if st == "green" else why + "; " + why2
            res.append({"name": g["name"], "status": st, "universe": u, "certified": c,
                        "seconds": round(time.time() - t0, 3), "reason": why})
    finally:
        _git(o["--repo"], "worktree", "remove", "--force", wt)
        shutil.rmtree(work, ignore_errors=True)
    total = round(time.time() - t_all, 3)
    v = {"format": "fnvnx-verdict/1", "commit": commit, "tree": tree, "gates_sha256": _sha(o["--gates"]), "gates": res,
         "seconds": total, "over_budget": total > float(o.get("--budget-s", "300")),
         "verdict": "green" if res and all(r["status"] == "green" and r["certified"] == r["universe"] for r in res) else "red"}
    if "--env-id" in o:
        v["env_id"] = o["--env-id"]  # sha256 of the env.json the verdict was computed in (envcheck.py id)
    open(o["--out"], "wb").write(cas.canon(v) + b"\n")
    for r in res:
        print("%-5s %-24s %d/%d %s" % (r["status"], r["name"], r["certified"], r["universe"], r["reason"][:1500]))
    print("verdict %s (%.1fs%s)" % (v["verdict"], total, ", over budget" if v["over_budget"] else ""))
    return 0 if v["verdict"] == "green" else 2

def _check_verdict(v):
    ok = (isinstance(v, dict) and v.get("format") == "fnvnx-verdict/1" and isinstance(v.get("tree"), str)
          and HEX.match(v["tree"]) and v.get("verdict") in ("green", "red") and isinstance(v.get("gates"), list)
          and isinstance(v.get("gates_sha256"), str) and all(isinstance(g, dict) and isinstance(g.get("name"), str) for g in v["gates"]))
    if not ok:
        raise Err("inconsistent verdict")
    if v["verdict"] == "green" and not (v["gates"] and all(g.get("status") == "green" and g.get("certified") == g.get("universe")
                                                           for g in v["gates"])):
        raise Err("verdict says green but its gates are not")

def cmd_store(a):
    o = _opts(a, {"--verdict": 0, "--verdicts": 0})
    v = _load(o["--verdict"], "verdict")
    _check_verdict(v)
    os.makedirs(o["--verdicts"], exist_ok=True)
    dest = os.path.join(o["--verdicts"], v["tree"] + ".json")
    tmp = dest + ".tmp%d" % os.getpid()
    shutil.copyfile(o["--verdict"], tmp)
    os.replace(tmp, dest)
    print(dest)
    return 0

def cmd_merge(a):
    o = _opts(a, {"--repo": 0, "--into": 0, "--from": 0, "--verdicts": 0, "--gates": 0, "--prev-verdict": 1, "--message-prefix": 1, "--env-id": 1, "--base": 1})
    repo, ref = o["--repo"], "refs/heads/" + o["--into"]
    # --base C: merge onto commit C and move NO ref (the caller publishes the commit and moves refs itself; l1.py merge --remote)
    old = o["--base"] if "--base" in o else _git_ok(repo, "rev-parse", "--verify", ref)
    src = _git_ok(repo, "rev-parse", "--verify", o["--from"] + "^{commit}")
    rc, out, err = _git(repo, "merge-tree", "--write-tree", old, src)
    if rc == 1:
        print("conflict: merge of %s into %s does not apply cleanly" % (o["--from"], o["--into"]), file=sys.stderr)
        return 3
    if rc:
        raise Err("git merge-tree: " + err)
    tree = out.splitlines()[0]
    path = os.path.join(o["--verdicts"], tree + ".json")
    if not os.path.isfile(path):
        raise Err("blocked: no verdict for merge tree %s" % tree, 2)
    v = _load(path, "verdict")
    _check_verdict(v)
    if v["verdict"] != "green":
        raise Err("blocked: verdict for tree %s is red" % tree, 2)
    if v["tree"] != tree:
        raise Err("blocked: verdict file holds tree %s, merge tree is %s" % (v["tree"], tree), 2)
    if v["gates_sha256"] != _sha(o["--gates"]):
        raise Err("blocked: verdict was made with a different gates file", 2)
    if "--env-id" in o and v.get("env_id") != o["--env-id"]:
        raise Err("blocked: verdict was computed in another environment (env_id %s, merge tree %s)" % (v.get("env_id"), o["--env-id"]), 2)
    if "--prev-verdict" in o:
        prev = _load(o["--prev-verdict"], "previous verdict")
        _check_verdict(prev)
        if prev["verdict"] != "green":
            raise Err("blocked: previous verdict is not green", 2)
        gone = sorted({g["name"] for g in prev["gates"]} - {g["name"] for g in v["gates"]})
        if gone:
            raise Err("blocked: ratchet, gate(s) removed since previous green verdict: " + ",".join(gone), 2)
    msg = "%smerge %s\n\nverdict: green, tree %s, gates %d\n" % (o.get("--message-prefix", ""), o["--from"], tree, len(v["gates"]))
    new = _git_ok(repo, "commit-tree", tree, "-p", old, "-p", src, inp=msg)
    rc, _, err = (0, "", "") if "--base" in o else _git(repo, "update-ref", ref, new, old)
    if rc:
        raise Err("blocked: %s moved concurrently, compare-and-swap failed: %s" % (o["--into"], err), 2)
    print(new)
    return 0

def _roots(store, tree):
    """Hashes of the tree itself and of every object inside it, as they appear as stage outputs."""
    out = {tree: ""}
    for rel, e in cas.listing(tree, store):
        out[e["h"] if e["t"] == "d" else hashlib.sha256(cas.canon(e)).hexdigest()] = rel
    return out

def cmd_provenance(a):
    o = _opts(a, {"--store": 0, "--docs": 0})
    store, stages = o["--store"], []
    for n in sorted(os.listdir(o["--docs"])):
        if n.endswith(".json"):
            doc = _load(os.path.join(o["--docs"], n), "stage document")
            if not isinstance(doc, dict) or doc.get("class") not in ("build", "verification"):
                raise Err("%s: stage document without valid \"class\" (build|verification)" % n)
            sandbox.check_doc(doc)
            res = _load(os.path.join(store, "keys", sandbox.key_of(doc), "result.json"), "result of stage %s" % doc["stage"])
            stages.append((doc, list(res["outputs"].values())))
    bad = {}                               # output-root hash -> producer stage name, for verification and tainted outputs
    for doc, outs in stages:
        if doc["class"] == "verification":
            bad.update({h: doc["stage"] for h in outs})
    lines, tainted, changed = [], set(), True
    while changed:
        changed = False
        for doc, outs in stages:
            if doc["class"] != "build" or doc["stage"] in tainted:
                continue
            for i in doc["inputs"]:
                hit = [(h, rel) for h, rel in _roots(store, i["tree"]).items() if h in bad]
                if hit:
                    h, rel = hit[0]
                    lines.append("%s (input at %s%s) <- %s" % (doc["stage"], i["at"], "/" + rel if rel else "", bad[h]))
                    tainted.add(doc["stage"])
                    bad.update({x: doc["stage"] for x in outs})
                    changed = True
                    break
    for ln in lines[:50]:
        print("TAINT " + ln)
    print("%d tainted build stage(s) of %d stage(s)" % (len(lines), len(stages)))
    return 2 if lines else 0

def cmd_defect(a):
    o = _opts(a, {"--queue": 0, "--bench": 0, "--head": 0, "--item": 0, "--evidence": 0})
    e = {"format": "fnvnx-rule-defect/1", "bench": o["--bench"], "head": o["--head"], "item": o["--item"],
         "evidence_sha256": _sha(o["--evidence"]), "evidence_name": os.path.basename(o["--evidence"])}
    data = cas.canon(e)
    os.makedirs(o["--queue"], exist_ok=True)
    dest = os.path.join(o["--queue"], hashlib.sha256(data).hexdigest() + ".json")
    if not os.path.exists(dest):
        tmp = dest + ".tmp%d" % os.getpid()
        open(tmp, "wb").write(data)
        os.replace(tmp, dest)
    print(dest)
    return 0

def cmd_defects(a):
    o = _opts(a, {"--queue": 0})
    n = {}
    for f in sorted(os.listdir(o["--queue"])) if os.path.isdir(o["--queue"]) else []:
        if f.endswith(".json") and not f.count(".tmp"):
            b = _load(os.path.join(o["--queue"], f), "entry")["bench"]
            n[b] = n.get(b, 0) + 1
    for b in sorted(n):
        print("%s\t%d" % (b, n[b]))
    return 0

def main(argv):
    cmds = {"run": cmd_run, "store": cmd_store, "merge": cmd_merge, "provenance": cmd_provenance,
            "defect": cmd_defect, "defects": cmd_defects}
    if not argv or argv[0] not in cmds:
        print("usage: verdict.py run|store|merge|provenance|defect|defects ... (see docstring)", file=sys.stderr)
        return 1
    try:
        return cmds[argv[0]](argv[1:])
    except Err as e:
        print(str(e), file=sys.stderr)
        return e.rc
    except (cas.CasError, OSError, KeyError, ValueError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
