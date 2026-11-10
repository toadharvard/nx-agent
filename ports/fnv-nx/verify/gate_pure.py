#!/usr/bin/env python3
"""Gate "pure" (fnvnx-gate/1): every Python file of the stage-tool directories is accepted by puregate.
    python3 -I gate_pure.py DIR...        run in the checkout root, env GATE_OUT (result dir); writes GATE_OUT/pure.json
Universe: the items puregate checks below the existing DIRs given (a missing DIR contributes nothing): every .py file, and
every shell file (a shell file is an item and is never certified: `shell-unanalysable`). An item is a repo-relative path.
Certified: items with zero puregate violations. All DIRs are checked in ONE puregate run, so project imports resolve across them.
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

def main(argv):
    dirs = [os.path.normpath(d) for d in argv if os.path.isdir(d)]
    seen, bad, why = [], set(), ""
    if dirs:
        with tempfile.TemporaryDirectory() as td:
            js = os.path.join(td, "pg.json")
            p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "puregate.py"), "check"] + dirs + ["--json", js],
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
