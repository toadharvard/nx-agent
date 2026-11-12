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
--cache DIR (default $GATE_CACHE, see verdict.py; none = no caching): a test file's result is a function of the content of the
  directories the gate declares and of the declared environment. KEY = sha256 over: cas.tree_hash of every key dir
  (--key-dirs, default: the DIRs scanned; whole directory content, no mtimes, __pycache__ skipped), the envcheck id of the
  env.json next to this script, this gate's argv without --cache/--jobs, and the python version string. If DIR/<KEY>.json
  records every file of the universe as passed, they are certified WITHOUT running (detail: `N of M pass, cached key <12 hex>`).
  Otherwise all files run and the per-file results are stored under KEY only when the run completed. All-or-nothing per key:
  no per-file dependency guessing; a failed file is never recorded as passed, a key with a failure re-runs everything.
A test that needs game data declares it in DIR/tests/inputs.json ({"format": "fnvnx-selftest-inputs/1", "tests": {"test_x.py": ["exe"]}}); the names
are externals of verify/contract_inputs.json (commit + tree_sha256, fetched like a recipe's external). The test gets FNVNX_INPUT_<NAME> = the checked-out
directory. If the input cannot be fetched or its tree hash differs, the file's status is `blocked-env`: uncertified (the gate is red), never skipped.
Result extra key "files": [{"name","seconds","status"}] (status passed|failed|cached); same list in selftests.times.json."""
import concurrent.futures
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

CACHE_FORMAT = "fnvnx-selftests-cache/1"

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

def run_one(path, timeout, extra=None):
    """(passed, seconds)"""
    t0 = time.time()
    # a test file is not a gate: it does not see GATE_OUT / GATE_COMMIT / GATE_CACHE (a test that runs gates of its own
    # would otherwise share this gate's result cache and outputs)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_")}
    # the file's scratch: TMPDIR is a directory of its own, removed when the file ends, however it ends (see the doc comment)
    tmp = tempfile.mkdtemp(prefix="selftest-")
    env["TMPDIR"] = tmp
    env.update(extra or {})
    try:
        p = subprocess.Popen([sys.executable, "-I", path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True, env=env)
        try:
            ok = p.wait(timeout=timeout) == 0
        except subprocess.TimeoutExpired:
            ok = False
        try:
            os.killpg(p.pid, signal.SIGKILL)   # whatever the file left running in its process group
        except ProcessLookupError:
            pass
        p.wait()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok, round(time.time() - t0, 3)

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

def key_of(argv, key_dirs):
    """KEY of the doc comment. argv: this gate's arguments without --cache/--jobs."""
    env_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.json")
    try:
        env = envcheck.env_id(json.load(open(env_json)))
    except (OSError, ValueError):
        env = "none"
    trees = {d: (cas.tree_hash(d, skip=("__pycache__",)) if os.path.exists(d) else "absent") for d in key_dirs}
    return hashlib.sha256(cas.canon({"format": CACHE_FORMAT, "trees": trees, "env": env, "argv": argv,
                                     "python": sys.version})).hexdigest()

def strip_argv(argv):
    out, i = [], 0
    while i < len(argv):
        if argv[i] in ("--cache", "--jobs") and i + 1 < len(argv):
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return out

def load_hit(path, files):
    try:
        doc = json.load(open(path))
        got = doc["files"]
        if doc["format"] == CACHE_FORMAT and sorted(got) == sorted(files) and all(got[f]["status"] == "passed" for f in files):
            return True
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return False

def store(path, key, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w") as fh:
        json.dump({"format": CACHE_FORMAT, "key": key, "files": {r["name"]: {"status": r["status"], "seconds": r["seconds"]}
                                                                 for r in rows}}, fh, sort_keys=True)
    os.replace(tmp, path)

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
    key = cpath = None
    if o["cache"] and files:
        key = key_of(strip_argv(argv), o["key_dirs"] if o["key_dirs"] is not None else dirs)
        cpath = os.path.join(o["cache"], key + ".json")
    if cpath and load_hit(cpath, files):
        rows = [{"name": f, "seconds": 0.0, "status": "cached"} for f in files]
        note = ", cached key %s" % key[:12]
    else:
        ws = tempfile.mkdtemp(prefix="selftest-inputs-")
        def one(f):
            names = declared_inputs(f)
            if names is None:
                return "failed", 0.0
            if names:
                got = fetch_inputs(names, o["cache"], ws)   # a test that needs game data declares it; no data here: blocked, never green, never skipped
                if isinstance(got, str):
                    return "blocked-env", 0.0
                ok, sec = run_one(f, o["timeout"], got)
            else:
                ok, sec = run_one(f, o["timeout"])
            return ("passed" if ok else "failed"), sec
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=o["jobs"]) as ex:
                res = list(ex.map(one, files))
        finally:
            shutil.rmtree(ws, ignore_errors=True)
        rows = [{"name": f, "seconds": s, "status": st} for f, (st, s) in zip(files, res)]
        note = ""
        if cpath:
            store(cpath, key, rows)
    secs = time.time() - t0
    unc = [r["name"] for r in rows if r["status"] in ("failed", "blocked-env")]
    blocked = [r["name"] for r in rows if r["status"] == "blocked-env"]
    n = len(files) - len(unc)
    status = "green" if not unc else "red"
    detail = ("%d of %d test files pass%s, %.1f s" % (n, len(files), note, secs)) if files else "no test files at this commit (nothing to certify yet)"
    if blocked:
        detail += "; blocked: env (declared input not available) for " + ", ".join(blocked)
    res = {"format": "fnvnx-gate/1", "name": "selftests", "status": status, "universe": len(files), "certified": n,
           "uncertified": unc[:50], "detail": detail, "files": rows}
    out = os.environ["GATE_OUT"]
    with open(os.path.join(out, "selftests.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    with open(os.path.join(out, "selftests.times.json"), "w") as fh:
        json.dump(rows, fh)
    return 0 if status == "green" else 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
