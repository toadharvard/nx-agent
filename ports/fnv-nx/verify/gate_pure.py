#!/usr/bin/env python3
"""Gate "pure" (fnvnx-gate/1): every Python file of the stage-tool directories is accepted by puregate.
    python3 -I gate_pure.py DIR...        run in the checkout root, env GATE_OUT (result dir); writes GATE_OUT/pure.json
Universe: the items puregate checks below the existing DIRs given (a missing DIR contributes nothing): every .py file, and
every shell file (a shell file is an item and is never certified: `shell-unanalysable`). An item is a repo-relative path.
Certified: items with zero puregate violations. All DIRs are checked in ONE puregate run, so project imports resolve across them; the policy table IMPORT_ROOTS (admitted tool
roots, e.g. verify/) is passed as --import-roots: a module found there is importable iff its file is itself accepted (puregate.py).
uncertified: the first 50 uncertified paths, sorted. Zero items: green, universe 0 (nothing to certify yet), said in "detail".
The universe is the STAGE-TOOL directories only (contract link rules lift). verify/ and farm/ are NOT stage tools: the
sandbox, the tracer and the farm scripts legitimately read the host (clock, processes, directory listings), so they are
outside this gate; their correctness is the business of the "selftests" gate. Files below a directory named `tests` are test
code, not stage tools: they are outside the universe too (the "selftests" gate runs them; they may use temp directories).
Exit 0 iff the result is green."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import puregate_policy as P  # noqa: E402

ADMIT = "ports/fnv-nx/verify/admit.json"

def import_roots():
    """(roots, problem): the policy table IMPORT_ROOTS, each existing directory must be admitted as a tool root by verify/admit.json."""
    present = [r for r in P.IMPORT_ROOTS if os.path.isdir(r)]  # a missing directory contributes nothing, like the DIRs
    if not present:
        return [], ""
    try:
        tools = {r["path"] for r in json.load(open(ADMIT))["roots"] if r.get("class") == "tool"}
    except (OSError, ValueError, KeyError, TypeError) as e:
        return [], "cannot read %s: %s" % (ADMIT, type(e).__name__)
    bad = [r for r in present if r not in tools]
    return ([] if bad else present), ("import root is not an admitted tool root of %s: %s" % (ADMIT, ", ".join(bad))) if bad else ""

def main(argv):
    dirs = [os.path.normpath(d) for d in argv if os.path.isdir(d)]
    seen, bad, why = [], set(), ""
    iroots, why = import_roots() if dirs else ([], "")
    if dirs and not why:
        with tempfile.TemporaryDirectory() as td:
            js = os.path.join(td, "pg.json")
            p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "puregate.py"), "check"] + dirs + (["--import-roots"] + iroots if iroots else []) + ["--json", js],
                               capture_output=True, text=True)
            if p.returncode not in (0, 2) or not os.path.isfile(js):
                why = "puregate failed (exit %d): %s" % (p.returncode, p.stderr.strip()[-200:])
            else:
                doc = json.load(open(js))
                stage = lambda f: "tests" not in os.path.normpath(f).split(os.sep)[:-1]
                seen = sorted(os.path.normpath(f) for f in doc["files"] if stage(f))
                bad = {os.path.normpath(v["file"]) for v in doc["violations"] if stage(v["file"])}
    unc = [f for f in seen if f in bad]
    if why:
        status, detail = "red", why
    elif unc:
        status, detail = "red", "%d of %d files have puregate violations" % (len(unc), len(seen))
    elif not seen:
        status, detail = "green", "no stage-tool files at this commit (nothing to certify yet)"
    else:
        status, detail = "green", "%d of %d files accepted by puregate" % (len(seen), len(seen))
    res = {"format": "fnvnx-gate/1", "name": "pure", "status": status, "universe": len(seen), "certified": len(seen) - len(unc),
           "uncertified": unc[:50], "detail": detail}
    with open(os.path.join(os.environ["GATE_OUT"], "pure.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    return 0 if status == "green" else 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
