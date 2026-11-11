#!/usr/bin/env python3
"""Gate "l6" (fnvnx-gate/1) for verify/verdict.py: every registered L6 test passes.
    python3 -I ports/fnv-nx/render/total/gate_l6.py [--timeout-s N] [--only NAME]...     run in the checkout root,
    env GATE_OUT; writes GATE_OUT/l6.json. Exit 0 iff green.
Universe = ITEMS below (name -> argv, cwd = ports/fnv-nx). Certified = exit status 0. No skip logic, as in
verify/gate_tests.py: an item that cannot run here is uncertified, so only tests that need nothing but gcc/g++,
python3 and EGL surfaceless (llvmpipe) are listed. Tests that need uam, a translator build directory, Wine or another
lead's live files enter ITEMS when their environment is a pinned input."""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, "..", ".."))
OUT = lambda: os.environ["GATE_OUT"]
PY = [sys.executable, "-I"]
ITEMS = {
    "l6-spec":      lambda o: PY + ["tests/l6/test_spec.py"],
    "l6-wrap":      lambda o: PY + ["tests/l6/wrap/test_wrap.py"],
    "l6-cmp":       lambda o: PY + ["tests/l6/cmp/test_cmp.py"],
    "l6-dev":       lambda o: PY + ["tests/l6/dev/test_dev.py"],
    "l6-res":       lambda o: PY + ["tests/l6/res/test_res.py"],
    "l6-exports":   lambda o: PY + ["tests/l6/exports/test_exports.py"],
    "l6-bridge":    lambda o: PY + ["tests/l6/bridge/test_bridge.py"],
}

def run(argv, timeout):
    p = subprocess.Popen(argv, cwd=PORT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        return p.wait(timeout=timeout) == 0
    except subprocess.TimeoutExpired:
        p.kill(); p.wait(); return False

def main(argv):
    timeout, only = 900.0, []
    while argv[:1] and argv[0] in ("--timeout-s", "--only") and len(argv) > 1:
        if argv[0] == "--timeout-s": timeout = float(argv[1])
        else: only.append(argv[1])
        argv = argv[2:]
    names = [n for n in ITEMS if not only or n in only]
    unc = []
    for n in names:
        if not run(ITEMS[n](OUT()), timeout): unc.append(n)
    ok = len(names) - len(unc); green = not unc
    res = {"format": "fnvnx-gate/1", "name": "l6", "status": "green" if green else "red", "universe": len(names),
           "certified": ok, "uncertified": unc[:50], "detail": "%d of %d L6 tests pass (timeout %.0f s each)" % (ok, len(names), timeout)}
    with open(os.path.join(OUT(), "l6.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    return 0 if green else 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
