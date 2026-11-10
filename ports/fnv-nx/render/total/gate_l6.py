#!/usr/bin/env python3
"""Gate "l6" (fnvnx-gate/1) for verify/verdict.py: every registered L6 test passes.
    python3 -I ports/fnv-nx/render/total/gate_l6.py [--timeout-s N] [--only NAME]...     run in the checkout root,
    env GATE_OUT; writes GATE_OUT/l6.json. Exit 0 iff green.
Universe = ITEMS below (name -> argv, cwd = ports/fnv-nx). Certified = exit status 0. No skip logic, as in
verify/gate_tests.py: an item that cannot run here is uncertified, so only tests that need nothing but gcc/g++,
python3 and EGL surfaceless (llvmpipe) are listed. Tests that need uam, a translator build directory, Wine or another
lead's live files are listed in render/total/VERDICT_PROPOSAL.md and enter ITEMS when their environment is a pinned input."""
import json, os, signal, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, "..", ".."))
OUT = lambda: os.environ["GATE_OUT"]
PY = [sys.executable, "-I"]
ITEMS = {
    "s6n-unit":     lambda o: ["make", "-C", "tests/s6n", "OUT=%s/s6n_unit" % o],
    "s6n-scenes":   lambda o: ["make", "-C", "tests/s6n", "scenes", "OUT=%s/s6n_scenes" % o],
    "s6n-capture":  lambda o: ["make", "-C", "tests/s6n", "capture", "OUT=%s/s6n_capture" % o],
    "s6n-nx_render": lambda o: ["make", "-C", "tests/s6n", "nx_render", "OUT=%s/s6n_nxr" % o],
    "l6-spec":      lambda o: PY + ["tests/l6/test_spec.py"],
    "l6-wrap":      lambda o: PY + ["tests/l6/wrap/test_wrap.py"],
    "l6-cmp":       lambda o: PY + ["tests/l6/cmp/test_cmp.py"],
    "l6-dev":       lambda o: PY + ["tests/l6/dev/test_dev.py"],
    "l6-res":       lambda o: PY + ["tests/l6/res/test_res.py"],
    "no-old-stack": lambda o: ["bash", "render/total/no_old_stack.sh"],
}

def run(argv, timeout):
    p = subprocess.Popen(argv, cwd=PORT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    try:
        return p.wait(timeout=timeout) == 0
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL); p.wait(); return False

def main(argv):
    timeout, only = 900.0, []
    while argv[:1] and argv[0] in ("--timeout-s", "--only") and len(argv) > 1:
        if argv[0] == "--timeout-s": timeout = float(argv[1])
        else: only.append(argv[1])
        argv = argv[2:]
    names = [n for n in ITEMS if not only or n in only]
    t0 = time.time(); unc = []
    for n in names:
        if not run(ITEMS[n](OUT()), timeout): unc.append(n)
    ok = len(names) - len(unc); green = not unc
    res = {"format": "fnvnx-gate/1", "name": "l6", "status": "green" if green else "red", "universe": len(names),
           "certified": ok, "uncertified": unc[:50], "detail": "%d of %d L6 tests pass, %.1f s" % (ok, len(names), time.time() - t0)}
    with open(os.path.join(OUT(), "l6.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    return 0 if green else 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
