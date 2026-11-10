#!/usr/bin/env python3
"""Gate "selftests" (fnvnx-gate/1): every test file of the given directories passes.
    python3 -I gate_tests.py [--timeout-s N] [--jobs N] [--cache DIR] [--key-dirs D...] [--] DIR...
Run in the checkout root with env GATE_OUT; writes GATE_OUT/selftests.json (+ GATE_OUT/selftests.times.json).
Universe: DIR/tests/test_*.py for each DIR given (a missing DIR contributes nothing), in argument order, each DIR's files
sorted by name; items are repo-relative paths. Each file runs as `python3 -I FILE` in the checkout with a per-file timeout
(default 300 s; a timeout kills the process group and is a failure). Certified: exit status 0. No skip logic: a test that
cannot run in this environment (root, namespaces, strace) is uncertified. Zero files: green, universe 0. Exit 0 iff green.
--jobs N (default 1): N test files run at once, each in its own process; results are reported in sorted universe order.
  A test must therefore keep its scratch under its own tempfile.mkdtemp directory (no fixed path). No test needs a serial
  group: sandbox namespaces are per process and every store/work directory is per run.
--cache DIR (default $GATE_CACHE, see verdict.py; none = no caching): a test file's result is a function of the content of the
  directories the gate declares and of the declared environment. KEY = sha256 over: cas.tree_hash of every key dir
  (--key-dirs, default: the DIRs scanned; whole directory content, no mtimes, __pycache__ skipped), the envcheck id of the
  env.json next to this script, this gate's argv without --cache/--jobs, and the python version string. If DIR/<KEY>.json
  records every file of the universe as passed, they are certified WITHOUT running (detail: `N of M pass, cached key <12 hex>`).
  Otherwise all files run and the per-file results are stored under KEY only when the run completed. All-or-nothing per key:
  no per-file dependency guessing; a failed file is never recorded as passed, a key with a failure re-runs everything.
Result extra key "files": [{"name","seconds","status"}] (status passed|failed|cached); same list in selftests.times.json."""
import concurrent.futures
import glob
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cas  # noqa: E402
import envcheck  # noqa: E402

CACHE_FORMAT = "fnvnx-selftests-cache/1"

def run_one(path, timeout):
    """(passed, seconds)"""
    t0 = time.time()
    p = subprocess.Popen([sys.executable, "-I", path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        ok = p.wait(timeout=timeout) == 0
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        ok = False
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

def main(argv):
    o, dirs = parse(argv)
    files = []
    for d in dirs:
        files += sorted(os.path.normpath(f) for f in glob.glob(os.path.join(glob.escape(d), "tests", "test_*.py")))
    t0 = time.time()
    key = cpath = None
    if o["cache"] and files:
        key = key_of(strip_argv(argv), o["key_dirs"] if o["key_dirs"] is not None else dirs)
        cpath = os.path.join(o["cache"], key + ".json")
    if cpath and load_hit(cpath, files):
        rows = [{"name": f, "seconds": 0.0, "status": "cached"} for f in files]
        note = ", cached key %s" % key[:12]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=o["jobs"]) as ex:
            res = list(ex.map(lambda f: run_one(f, o["timeout"]), files))
        rows = [{"name": f, "seconds": s, "status": "passed" if ok else "failed"} for f, (ok, s) in zip(files, res)]
        note = ""
        if cpath:
            store(cpath, key, rows)
    secs = time.time() - t0
    unc = [r["name"] for r in rows if r["status"] == "failed"]
    n = len(files) - len(unc)
    status = "green" if not unc else "red"
    detail = ("%d of %d test files pass%s, %.1f s" % (n, len(files), note, secs)) if files else "no test files at this commit (nothing to certify yet)"
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
