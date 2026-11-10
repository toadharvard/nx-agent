#!/usr/bin/env python3
"""Gate "selftests" (fnvnx-gate/1): every test file of the given directories passes.
    python3 -I gate_tests.py [--timeout-s N] DIR...     run in the checkout root, env GATE_OUT; writes GATE_OUT/selftests.json
Universe: DIR/tests/test_*.py for each DIR given (a missing DIR contributes nothing), in argument order, each DIR's files
sorted by name; items are repo-relative paths. Each file runs as `python3 -I FILE` in the checkout, one after the other,
with a per-file timeout (default 300 s; a timeout kills the process group and is a failure). Certified: exit status 0.
No skip logic: a test that cannot run in this environment (root, namespaces, strace) is uncertified.
Zero files: green, universe 0. Exit 0 iff the result is green."""
import glob
import json
import os
import signal
import subprocess
import sys
import time

def run_one(path, timeout):
    p = subprocess.Popen([sys.executable, "-I", path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        return p.wait(timeout=timeout) == 0
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        return False

def main(argv):
    timeout, dirs = 300.0, list(argv)
    if dirs[:1] == ["--timeout-s"] and len(dirs) > 1:
        timeout, dirs = float(dirs[1]), dirs[2:]
    files = []
    for d in dirs:
        files += sorted(os.path.normpath(f) for f in glob.glob(os.path.join(glob.escape(d), "tests", "test_*.py")))
    t0 = time.time()
    unc = [f for f in files if not run_one(f, timeout)]
    secs = time.time() - t0
    n = len(files) - len(unc)
    status = "green" if not unc else "red"
    detail = "%d of %d test files pass, %.1f s" % (n, len(files), secs) if files else "no test files at this commit (nothing to certify yet)"
    res = {"format": "fnvnx-gate/1", "name": "selftests", "status": status, "universe": len(files), "certified": n,
           "uncertified": unc[:50], "detail": detail}
    with open(os.path.join(os.environ["GATE_OUT"], "selftests.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    return 0 if status == "green" else 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
