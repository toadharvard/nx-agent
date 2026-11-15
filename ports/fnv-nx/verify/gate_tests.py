#!/usr/bin/env python3
"""Gate "selftests" (fnvnx-gate/1): every test file of the given directories passes.
    python3 -I gate_tests.py [--timeout-s N] [--jobs N] [--cache DIR] [--key-dirs D...] [--] DIR...
Run in the checkout root with env GATE_OUT; writes GATE_OUT/selftests.json (+ GATE_OUT/selftests.times.json).
Universe: DIR/tests/test_*.py for each DIR given (a missing DIR contributes nothing), in argument order, each DIR's files
sorted by name; items are repo-relative paths. Each file runs as `python3 -I FILE` in the checkout with a per-file timeout
(default 300 s; a timeout kills the process group and is a failure). Certified: exit status 0. No skip logic: a test that
cannot run in this environment (root, namespaces, strace) is uncertified. Zero files: green, universe 0. Exit 0 iff green.
--jobs N (default 1): N test files run at once, each in its own process; results are reported in sorted universe order.
  A test must therefore keep its scratch under its own tempfile.mkdtemp directory (no fixed path). Each file runs with
  TMPDIR set to a fresh directory of its own, and the gate removes that directory (and kills what is left of the file's
  process group) when the file ends, passed, failed or timed out: no test leaves anything in the temporary directory. No test needs a serial
  group: sandbox namespaces are per process and every store/work directory is per run.
--cache DIR (default $GATE_CACHE, see verdict.py; none = no caching): ONE ENTRY PER TEST FILE, keyed by the content of what that file reads. A file passes
  once under `strace -f` (the call set of opentrace.py: opens, execs, stat family, access, readlink, getdents) and its READ SET is recorded: every path under the
  checkout that the run (and every process it started) opened, executed, stat-ed or listed, with the state at the end of the run (sha256 of the content; the
  target of a link; `absent`; for a listed directory the names in it, without __pycache__). KEY (the entry's directory under DIR/selftests/) = sha256 over: the
  file's path, the envcheck id of the env.json next to this script, this gate's argv without --cache/--jobs, the python version string, and the pin (commit,
  tree_sha256) of each declared input. A run is certified WITHOUT running iff an entry under its KEY has every recorded path in the same state now. Argument: the
  run is a deterministic function of its argv and environment (the KEY), the files it reads and what it sees of the directories; the recorded set is exactly
  what a pass read, and a run that follows the same reads through unchanged content reads the same set and gives the same result. A change of one file
  re-runs exactly the files whose read set holds it. Reads outside the checkout (the interpreter, system libraries, tools) are the declared environment
  (the env id); a file the run reads in its own TMPDIR is its own. A run that touched the checkout's git metadata (git ls-files, git show: the index and the
  objects are not files of the tree) is keyed by the tree hash of the commit as well (HEAD^{tree}): any change of the tree re-runs it. A failed file, a timeout
  and a leak are never recorded. No file is recorded without a trace (strace missing or a trace that cannot be read: the file is failed, never skipped).
  UNTRACED: a test that itself runs strace cannot run under it (one tracer per process). DIR/tests/inputs.json lists such files under "untraced"; they run
  untraced and their entry is keyed by whole directories: the tree hash (cas.tree_hash, no mtimes, __pycache__ skipped) of every --key-dirs directory
  (default: the DIRs scanned) in place of a read set. Detail of a green run: `N of M test files pass, K cached`.
A test that needs game data declares it in DIR/tests/inputs.json ({"format": "fnvnx-selftest-inputs/1", "tests": {"test_x.py": ["exe"]}, "untraced": ["test_y.py"]}); the names
are externals of verify/contract_inputs.json (commit + tree_sha256, fetched like a recipe's external). The test gets FNVNX_INPUT_<NAME> = the checked-out
directory. If the input cannot be fetched or its tree hash differs, the file's status is `blocked-env`: uncertified (the gate is red), never skipped.
Result extra key "files": [{"name","seconds","status"[,"reason"]}] (status passed|failed|cached; reason of a failed file: exit status or
timeout, names left in its TMPDIR, the exception/errno text and the tail of its output); same list in selftests.times.json.
TMPDIR rule: each test file runs with TMPDIR = a fresh directory of its own under the gate's temporary directory. The gate removes it
(and the log) after the test whatever the outcome, including a timeout kill, so no test can leave scratch behind; a test that
leaves entries in its TMPDIR at exit is uncertified ("leaked in TMPDIR"), so a test cleans up after itself. Scratch a test makes
outside TMPDIR (a fixed path) is not covered: tests use tempfile."""
import concurrent.futures
import contextlib
import glob
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cas  # noqa: E402
import envcheck  # noqa: E402
import opentrace  # noqa: E402

CACHE_FORMAT = "fnvnx-selftests-cache/2"   # one entry per test file: its key, its read set (or the whole-directory trees of an untraced file)
KEEP = 8   # entries kept per key (the states of the read set seen on recent commits)

PINS = "ports/fnv-nx/verify/contract_inputs.json"   # the pins of the declared inputs (the one registry of content-pinned fnv-nx-build inputs)

def declared_inputs(path):
    """{test file: [input names]} from DIR/tests/inputs.json ("fnvnx-selftest-inputs/1": {"tests": {"test_x.py": ["exe"]}}); {} if the file is absent."""
    try:
        doc = json.load(open(os.path.join(os.path.dirname(path), "inputs.json")))
        return {n: list(v) for n, v in doc["tests"].items()}.get(os.path.basename(path), []) if doc["format"] == "fnvnx-selftest-inputs/1" else None
    except OSError:
        return []
    except (ValueError, KeyError, TypeError, AttributeError):
        return None

def untraced_of(path):
    """True iff DIR/tests/inputs.json lists the test file under "untraced" (it runs strace itself, so cannot run under it)."""
    try:
        doc = json.load(open(os.path.join(os.path.dirname(path), "inputs.json")))
        return os.path.basename(path) in doc.get("untraced", [])
    except (OSError, ValueError, AttributeError, TypeError):
        return False

def fetch_inputs(names, cache, ws):
    """{FNVNX_INPUT_<NAME>: host dir} of the pinned externals NAMES (recipe.fetch_externals, commit + tree_sha256 checked), or a string: why not."""
    import recipe
    try:
        pins = {e["name"]: e for e in json.load(open(PINS))["external"]}
        ext = [pins[n] for n in names]
        got = recipe.fetch_externals(ext, os.path.join(cache or ws, "store"), os.path.join(cache or ws, "external"), ws)
    except recipe.BuildRed as x:
        return "%s: %s" % (x.args[0].get("stage"), "; ".join(x.args[0].get("stderr", [])[-3:]))
    except (OSError, ValueError, KeyError) as x:
        return "inputs %s: %s" % (",".join(names), x)
    return {"FNVNX_INPUT_" + n.upper(): got[n]["host"] for n in names}

def run_one(path, timeout, base, idx, extra=None, trace=None):
    """(passed, seconds, reason). The test runs with TMPDIR = its own directory BASE/tIDX, made here and removed here whatever the
    outcome (exit, failure, timeout kill, exception); EXTRA: the FNVNX_INPUT_<NAME> variables of the declared inputs: scratch cannot outlive a test. A test that leaves entries in it at exit is
    uncertified ("leaked"). reason: "" when passed, else the exit status, the leaked names and the tail of the test's output."""
    t0 = time.time()
    tdir, logp = os.path.join(base, "t%d" % idx), os.path.join(base, "t%d.log" % idx)
    os.mkdir(tdir)
    # a test file is not a gate: it does not see GATE_OUT / GATE_COMMIT / GATE_CACHE (a test that runs gates of its own
    # would otherwise share this gate's result cache and outputs)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_")}
    env["TMPDIR"] = tdir
    why = []
    env.update(extra or {})
    try:
        with open(logp, "wb") as log:
            cmd = [sys.executable, "-I", path]
            if trace:
                cmd = ["strace", "-f", "--seccomp-bpf", "-y", "-qq", "-e", "trace=" + opentrace.TRACE_SET + "," + opentrace.READ_EXTRA, "-o", trace] + cmd
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True, env=env)
            try:
                rc = p.wait(timeout=timeout)
                if rc:
                    why.append("exit %d" % rc)
            except subprocess.TimeoutExpired:
                why.append("timeout %gs" % timeout)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGKILL)   # whatever the file left running in its process group
            p.wait()
        try:
            left = sorted(os.listdir(tdir))
        except OSError as e:
            left = []
            why.append("TMPDIR unreadable: %s" % e)
        if left:
            why.append("leaked in TMPDIR: %s" % ", ".join(left[:8]) + (" ..." if len(left) > 8 else ""))
    except OSError as e:  # e.g. ENOSPC while starting the test: the cause is the reason
        why.append("%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(tdir, ignore_errors=True)
        if os.path.exists(tdir):
            why.append("cannot remove its TMPDIR %s" % tdir)
    if why:
        try:
            with open(logp, "rb") as fh:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - 1500))
                tail = fh.read().decode("utf-8", "replace")
        except OSError:
            tail = ""
        why.append("output tail: " + tail)
    with contextlib.suppress(OSError):
        os.unlink(logp)
    return not why, round(time.time() - t0, 3), "; ".join(why)

def parse(argv):
    o = {"timeout": 300.0, "jobs": 1, "cache": os.environ.get("GATE_CACHE") or None, "key_dirs": None}
    a, dirs, i = list(argv), [], 0
    while i < len(a):
        if a[i] == "--timeout-s" and i + 1 < len(a):
            o["timeout"], i = float(a[i + 1]), i + 2
        elif a[i] == "--jobs" and i + 1 < len(a):
            o["jobs"], i = max(1, int(a[i + 1])), i + 2
        elif a[i] == "--cache" and i + 1 < len(a):
            o["cache"], i = a[i + 1], i + 2
        elif a[i] == "--key-dirs":
            i += 1
            o["key_dirs"] = []
            while i < len(a) and not a[i].startswith("--"):
                o["key_dirs"].append(a[i])
                i += 1
        elif a[i] == "--":
            dirs += a[i + 1:]
            break
        else:
            dirs.append(a[i])
            i += 1
    return o, dirs

def env_id():
    env_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.json")
    try:
        return envcheck.env_id(json.load(open(env_json)))
    except (OSError, ValueError):
        return "none"

def strip_argv(argv):
    out, i = [], 0
    while i < len(argv):
        if argv[i] in ("--cache", "--jobs") and i + 1 < len(argv):
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return out

def key_of(path, argv, pins, eid):
    """KEY of the doc comment for test file PATH: its path, the env id, this gate's argv, the python version and the pins of its declared inputs."""
    return hashlib.sha256(cas.canon({"format": CACHE_FORMAT, "test": path, "env": eid, "argv": argv, "python": sys.version, "pins": pins})).hexdigest()

class State:
    """The state of paths of the checkout, read once per gate run (sha256 of content by path)."""
    def __init__(self, root):
        self.root, self.memo, self.tree = root, {}, None

    def of(self, rel):
        if rel in self.memo:
            return self.memo[rel]
        p = os.path.join(self.root, rel)
        if os.path.islink(p):
            v = "link:" + os.readlink(p)
        elif os.path.isdir(p):
            v = "dir"
        elif os.path.isfile(p):
            with open(p, "rb") as fh:
                v = hashlib.sha256(fh.read()).hexdigest()
        else:
            v = "absent"
        self.memo[rel] = v
        return v

    def listing(self, rel):
        key = "list:" + rel
        if key not in self.memo:
            try:
                names = sorted(n for n in os.listdir(os.path.join(self.root, rel)) if n != "__pycache__")
            except OSError:
                names = None
            self.memo[key] = hashlib.sha256(json.dumps(names).encode()).hexdigest()
        return self.memo[key]

    def git_tree(self):
        if self.tree is None:
            r = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=self.root, capture_output=True, text=True)
            self.tree = r.stdout.strip() if r.returncode == 0 else "no-tree"
        return self.tree

    def whole(self, dirs):
        return {d: (cas.tree_hash(os.path.join(self.root, d), skip=("__pycache__",)) if os.path.exists(os.path.join(self.root, d)) else "absent") for d in dirs}

def gitdirs(root):
    """The git metadata of the checkout (a worktree's gitdir and the common dir): files a test reads that are not files of the tree."""
    out = {os.path.join(root, ".git")}
    for flag in ("--git-dir", "--git-common-dir"):
        r = subprocess.run(["git", "rev-parse", "--absolute-git-dir" if flag == "--git-dir" else flag], cwd=root, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            out.add(os.path.realpath(os.path.join(root, r.stdout.strip())))
    return sorted(out)

def read_set(lines, root, skip, gits):
    """(files {rel: None}, dirs {rel}, git bool) of an strace log: what the run read under ROOT, without the SKIP directories (its own TMPDIR) and __pycache__."""
    files, dirs, git = {}, set(), False
    for _pid, kind, path, _fl in opentrace.parse(lines, root, extra=True):
        if any(opentrace.under(path, [g]) for g in gits):
            git = True
            continue
        if not opentrace.under(path, [root]) or path == root or any(opentrace.under(path, [x]) for x in skip) or "/__pycache__" in path + "/":
            continue
        rel = path[len(root) + 1:]
        (dirs.add(rel) if kind == "list" else files.__setitem__(rel, None))
    return files, dirs, git

def entry_dir(cache, key):
    return os.path.join(cache, "selftests", key)

def lookup(cache, key, st, whole_dirs):
    """True iff an entry under KEY has the recorded state now (traced: every path of its read set; untraced: the trees of its directories)."""
    d = entry_dir(cache, key)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return False
    for n in names:
        try:
            e = json.load(open(os.path.join(d, n)))
            if e["format"] != CACHE_FORMAT or e["status"] != "passed":
                continue
            if e["untraced"]:
                if e["trees"] == st.whole(whole_dirs):
                    return True
                continue
            if all(st.of(r) == v for r, v in e["files"].items()) and all(st.listing(r) == v for r, v in e["dirs"].items()) and (not e["git"] or e["tree"] == st.git_tree()):
                return True
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return False

def record(cache, key, st, entry):
    d = entry_dir(cache, key)
    os.makedirs(d, exist_ok=True)
    entry = dict(entry, format=CACHE_FORMAT, status="passed")
    h = hashlib.sha256(cas.canon(entry)).hexdigest()
    tmp = os.path.join(d, ".tmp%d-%s" % (os.getpid(), h[:8]))
    with open(tmp, "w") as fh:
        json.dump(entry, fh, sort_keys=True)
    os.replace(tmp, os.path.join(d, h + ".json"))
    old = sorted((os.path.getmtime(os.path.join(d, n)), n) for n in os.listdir(d) if n.endswith(".json"))
    for _, n in old[:-KEEP]:
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(d, n))

def universe(dirs):
    """the test files of the doc comment's universe (gate "oracle" judges the same list)"""
    files = []
    for d in dirs:
        files += sorted(os.path.normpath(f) for f in glob.glob(os.path.join(glob.escape(d), "tests", "test_*.py")))
    return files

def main(argv):
    o, dirs = parse(argv)
    files = universe(dirs)
    t0 = time.time()
    root = os.path.realpath(os.getcwd())
    st, eid, sargv = State(root), env_id(), strip_argv(argv)
    whole_dirs = o["key_dirs"] if o["key_dirs"] is not None else dirs
    try:
        pinned = {e["name"]: e for e in json.load(open(PINS))["external"]}
    except (OSError, ValueError, KeyError, TypeError):
        pinned = {}
    plan = {}   # file -> (key, untraced, input names)
    for f in files:
        names = declared_inputs(f)
        pins = [[n, pinned.get(n, {}).get("commit"), pinned.get(n, {}).get("tree_sha256")] for n in (names or [])]
        plan[f] = (key_of(f, sargv, pins, eid), untraced_of(f), names)
    hit = {f for f in files if o["cache"] and plan[f][2] is not None and lookup(o["cache"], plan[f][0], st, whole_dirs)}
    todo = [f for f in files if f not in hit]
    res = {}
    if todo:
        base = tempfile.mkdtemp(prefix="selftests-")  # in the gate's own TMPDIR; removed below whatever happens
        ws = os.path.join(base, "inputs")
        os.mkdir(ws)
        gits = gitdirs(root)

        def one(a):
            idx, f = a
            key, untraced, names = plan[f]
            if names is None:
                return "failed", 0.0, "tests/inputs.json is not a valid fnvnx-selftest-inputs/1 file"
            extra = None
            if names:
                got = fetch_inputs(names, o["cache"], ws)   # a test that needs game data declares it; no data here: blocked, never green, never skipped
                if isinstance(got, str):
                    return "blocked-env", 0.0, got
                extra = got
            tracing = bool(o["cache"]) and not untraced
            if tracing and shutil.which("strace") is None:
                return "failed", 0.0, "strace is not installed: the read set of the file cannot be recorded"
            tr = os.path.join(base, "t%d.strace" % idx) if tracing else None
            ok, sec, why = run_one(f, o["timeout"], base, idx, extra, tr)
            if ok and o["cache"]:
                if untraced:
                    record(o["cache"], key, st, {"untraced": True, "trees": st.whole(whole_dirs), "files": {}, "dirs": {}, "git": False, "tree": None})
                else:
                    try:
                        lines = open(tr, errors="replace").read().splitlines()
                    except OSError as x:
                        return "failed", sec, "no trace of the run: %s" % x
                    fs, ds, git = read_set(lines, root, [base], gits)
                    record(o["cache"], key, st, {"untraced": False, "trees": {}, "files": {r: st.of(r) for r in sorted(fs)}, "dirs": {r: st.listing(r) for r in sorted(ds)},
                                                 "git": git, "tree": st.git_tree() if git else None})
                    with contextlib.suppress(OSError):
                        os.unlink(tr)
            return ("passed" if ok else "failed"), sec, why
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=o["jobs"]) as ex:
                for f, r in zip(todo, ex.map(one, [(i, f) for i, f in enumerate(todo)])):
                    res[f] = r
        finally:
            shutil.rmtree(base, ignore_errors=True)
    rows = []
    for f in files:
        if f in hit:
            rows.append({"name": f, "seconds": 0.0, "status": "cached"})
        else:
            stt, sec, why = res[f]
            rows.append(dict({"name": f, "seconds": sec, "status": stt}, **({} if stt == "passed" else {"reason": why})))
    note = ", %d cached" % len(hit) if o["cache"] else ""
    secs = time.time() - t0
    unc = [r["name"] for r in rows if r["status"] in ("failed", "blocked-env")]
    blocked = [r["name"] for r in rows if r["status"] == "blocked-env"]
    n = len(files) - len(unc)
    status = "green" if not unc else "red"
    detail = ("%d of %d test files pass%s, %.1f s" % (n, len(files), note, secs)) if files else "no test files at this commit (nothing to certify yet)"
    if blocked:
        detail += "; blocked: env (declared input not available) for " + ", ".join(blocked)
    res = {"format": "fnvnx-gate/1", "name": "selftests", "status": status, "universe": len(files), "certified": n,
           "uncertified": unc[:50], "detail": detail, "files": rows,
           "reasons": {r["name"]: r["reason"] for r in rows if r["status"] in ("failed", "blocked-env") and r.get("reason") and r["name"] in unc[:50]}}
    out = os.environ["GATE_OUT"]
    with open(os.path.join(out, "selftests.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    with open(os.path.join(out, "selftests.times.json"), "w") as fh:
        json.dump(rows, fh)
    return 0 if status == "green" else 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
