#!/usr/bin/env python3
"""Gate "pure" (fnvnx-gate/1): every Python file a build stage can run is accepted by puregate.
    python3 -I gate_pure.py        run in the checkout root, env GATE_OUT (result dir); writes GATE_OUT/pure.json
Universe: the import closure, by puregate's own project-import resolution, of the STAGE ENTRY POINTS: (i) the `tool` scripts of the
`generated` steps of every recipe (build.json) of an admitted root, and (ii) the `entry` list of every admitted tool root
(verify/admit.json; required for class tool, may be empty). An item is a repo-relative .py path. A file reachable only from gate
drivers (scripts the verdict runs on the host) is outside the universe: it is not a stage, and it may read the host. No directory name
is special (a file in `tests/` is in the universe iff a stage imports it).
Certified: items with zero puregate violations that import no HOST-READER module. Each admitted root that names gate "pure" is
checked in its own puregate run: project imports resolve below the importing file's directory, the root's directories (`trees`) or their
parents, or the parent directory of a root it DECLARES in admit.json `imports`. An import of a module of another root that is not declared is
`import-unlisted` (red); a declared-import cycle, an unknown or repeated import is an error of admit.json (red). HOST-READER modules:
every file below ports/fnv-nx/verify or ports/fnv-nx/farm (the sandbox, the recipe, the gate drivers, the farm scripts legitimately read
the host: clock, processes, directory listings; their correctness is the business of the "selftests" gate); a universe file that
imports one is uncertified, and so is a file that imports one that imports it (those are in the universe, so each is judged itself:
a module that imports recipe or gate_build lies in verify/ or is red). An entry point or a reached file that is not in a root
naming gate "pure" is uncertified (nothing judged it).
uncertified: the first 50 uncertified paths, sorted. Zero items: green, universe 0, said in "detail". Exit 0 iff the result is green."""
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
HOST = ("ports/fnv-nx/verify", "ports/fnv-nx/farm")


def units(roots):
    """-> [(trees of the root, imported root paths)] of every root naming gate pure (one puregate run per root)."""
    out = [(tuple(os.path.normpath(t) for t in gate_admit.trees(r) if os.path.exists(t)), tuple(r.get("imports", [])))
           for r in roots if "pure" in r.get("gates", [])]
    return [u for u in out if u[0]]  # a tree that is not in this checkout contributes nothing


def run_unit(td, n, unit, imports):
    """One puregate run: the root's trees are checked; the declared imported roots are --lib (resolvable, judged in their own
    run) and their parent directories are import bases. -> (violating files, import graph {file: [files]}, why)"""
    js = os.path.join(td, "pg%d.json" % n)
    argv = [sys.executable, "-I", os.path.join(HERE, "puregate.py"), "check"] + list(unit)
    for t in unit:  # a root is importable by its own directory name too (that is how a declaring root reaches it)
        argv += ["--base", os.path.dirname(t) or "."]
    for i in imports:
        argv += ["--lib", i, "--base", os.path.dirname(os.path.normpath(i)) or "."]
    p = subprocess.run(argv + ["--json", js], capture_output=True, text=True)
    if p.returncode not in (0, 2) or not os.path.isfile(js):
        return set(), {}, "puregate failed on %s (exit %d): %s" % (" ".join(unit), p.returncode, p.stderr.strip()[-200:])
    doc = json.load(open(js))
    nm = lambda p: os.path.relpath(os.path.abspath(p))
    return ({nm(v["file"]) for v in doc["violations"]}, {nm(f): sorted(nm(x) for x in v) for f, v in doc["imports"].items()}, "")


def entries(roots):
    """-> (sorted entry points, errors): the `entry` of each tool root and the generator tools of each root's recipe."""
    out, errs = set(), []
    for r in roots:
        out |= {os.path.normpath(e) for e in r.get("entry", []) if isinstance(e, str)}
        rec = r.get("recipe")
        if rec:
            try:
                for g in json.load(open(rec)).get("generated", []):
                    out.add(os.path.normpath(g["tool"]))
            except (OSError, ValueError, KeyError, TypeError) as e:
                errs.append("recipe %s: %s" % (rec, e))
    return sorted(out), errs


def closure(entry, graph):
    seen, todo = set(), list(entry)
    while todo:
        f = todo.pop()
        if f not in seen:
            seen.add(f)
            todo += graph.get(f, [])
    return sorted(seen)


def main(argv):
    admit = json.load(open(ADMIT)) if os.path.isfile(ADMIT) else {}
    roots = admit.get("roots", [])
    bad, graph, why, universe = set(), {}, "", []
    errs = imports_errors(roots)
    entry, eerr = entries(roots)
    errs += eerr
    if errs:
        why = "admit.json: " + "; ".join(errs[:5])
    else:
        with tempfile.TemporaryDirectory() as td:
            for n, (u, imp) in enumerate(units(roots)):
                b, g, w = run_unit(td, n, u, imp)
                bad |= b
                graph.update(g)
                if w:
                    why = w
                    break
        if not why:
            universe = closure(entry, graph)
            host = lambda f: any(f == h or f.startswith(h + "/") for h in HOST)
            for f in universe:
                if f not in graph or any(host(x) for x in graph[f]) or host(f):
                    bad.add(f)
    unc = [f for f in universe if f in bad]
    if why:
        status, detail = "red", why
    elif unc:
        status, detail = "red", "%d of %d files have puregate violations, are host readers or import one" % (len(unc), len(universe))
    elif not universe:
        status, detail = "green", "no stage entry points at this commit (nothing to certify)"
    else:
        status, detail = "green", "%d of %d files accepted by puregate" % (len(universe), len(universe))
    res = {"format": "fnvnx-gate/1", "name": "pure", "status": status, "universe": len(universe), "certified": len(universe) - len(unc),
           "uncertified": unc[:50], "detail": detail}
    with open(os.path.join(os.environ["GATE_OUT"], "pure.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    return 0 if status == "green" else 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
