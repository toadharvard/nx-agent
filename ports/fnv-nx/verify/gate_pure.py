#!/usr/bin/env python3
"""Gate "pure" (fnvnx-gate/1): every Python file of the stage-tool directories is accepted by puregate.
    python3 -I gate_pure.py DIR...        run in the checkout root, env GATE_OUT (result dir); writes GATE_OUT/pure.json
Universe: the items puregate checks below the existing DIRs given (a missing DIR contributes nothing): every .py file, and
every shell file (a shell file is an item and is never certified: `shell-unanalysable`). An item is a repo-relative path.
Certified: items with zero puregate violations. Each admitted root inside a DIR (verify/admit.json; DIR itself when it holds none) is
checked in its own puregate run: project imports resolve below the importing file's directory, the root's directory, or the parent
directory of a root it DECLARES in admit.json `imports`. An import of a module of another root that is not declared is
`import-unlisted` (red); a declared-import cycle, an unknown or repeated import is an error of admit.json (red).
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
import gate_admit  # noqa: E402
from gate_admit import imports_errors  # noqa: E402

ADMIT = "ports/fnv-nx/verify/admit.json"


def units(dirs, roots):
    """-> [(unit paths, imported root paths)]: for each admitted root the trees of it that lie inside a DIR (or hold one: the DIR
    itself) form ONE unit (one puregate run, so trees of one root resolve each other); a DIR no root touches is a unit alone."""
    cut = lambda t, d: t if t == d or t.startswith(d + os.sep) else d if d.startswith(t + os.sep) else None
    out, touched = [], set()
    for r in roots:
        ts = sorted({cut(os.path.normpath(t), d) for t in gate_admit.trees(r) for d in dirs} - {None})
        if ts:
            out.append((tuple(ts), tuple(r.get("imports", []))))
            touched |= {d for d in dirs if any(cut(os.path.normpath(t), d) for t in gate_admit.trees(r))}
    return sorted(out + [((d,), ()) for d in dirs if d not in touched])


def run_unit(td, n, unit, imports):  # unit: tuple of paths
    """One puregate run: UNIT checked; the declared imported roots are --lib (resolvable, judged in their own run) and
    their parent directories are import bases. -> (seen, bad, why)"""
    js = os.path.join(td, "pg%d.json" % n)
    argv = [sys.executable, "-I", os.path.join(HERE, "puregate.py"), "check"] + list(unit)
    for i in imports:
        argv += ["--lib", i, "--base", os.path.dirname(os.path.normpath(i)) or "."]
    p = subprocess.run(argv + ["--json", js], capture_output=True, text=True)
    if p.returncode not in (0, 2) or not os.path.isfile(js):
        return [], set(), "puregate failed on %s (exit %d): %s" % (" ".join(unit), p.returncode, p.stderr.strip()[-200:])
    doc = json.load(open(js))
    stage = lambda f: "tests" not in os.path.normpath(f).split(os.sep)[:-1]
    return ([os.path.normpath(f) for f in doc["files"] if stage(f)],
            {os.path.normpath(v["file"]) for v in doc["violations"] if stage(v["file"])}, "")


def main(argv):
    dirs = sorted({os.path.normpath(d) for d in argv if os.path.isdir(d)})
    seen, bad, why = [], set(), ""
    admit = json.load(open(ADMIT)) if os.path.isfile(ADMIT) else {}
    roots = admit.get("roots", [])
    errs = imports_errors(roots)
    if errs:
        why = "admit.json imports: " + "; ".join(errs[:5])
    elif dirs:
        with tempfile.TemporaryDirectory() as td:
            for n, (u, imp) in enumerate(units(dirs, roots)):
                s, b, w = run_unit(td, n, u, imp)
                seen += s
                bad |= b
                if w:
                    why = w
                    break
    seen = sorted(set(seen))
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
