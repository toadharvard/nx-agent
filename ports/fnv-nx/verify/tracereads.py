#!/usr/bin/env python3
"""tracereads.py: write DIR/tests/reads.json ("fnvnx-selftest-reads/3") for the test files of gate selftests.
    python3 -I tracereads.py [--jobs N] [--check] [TEST...]     (run in the checkout root; no TEST = the whole universe, gate_tests.admit_universe)
Each test runs once in its hermetic view (gate_tests.run_one, the machine the gate uses) under strace, on the whole checkout view. Every path of the
view that the run opened, stat'ed, executed or read the link of, successfully, is a FILE of its record. A directory the run opened (a listing: glob,
os.walk, listdir, python's import path scan) is a read of that directory: every entry of it that is not already a file of the record is a NAME (present
in the view as an empty file, since a listing returns names only) and every sub-directory of it a DIR (present, empty at least); a directory only
stat'ed is a DIR. The record is a DECLARATION. gate_tests.py builds the test's view from exactly it and audits every cold run under strace: a lookup of a
checkout path outside the record is red ("undeclared read"); the gate never re-traces. --check: nothing is written; exit 1 if a committed record differs
from a fresh trace's record (the committed file must equal the normalised output of this tool)."""
import collections
import concurrent.futures
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_tests as gt  # noqa: E402

FORMAT = "fnvnx-selftest-reads/3"

def record_of(root, f, text):
    files, listed, dirs = set(), set(), set()
    for name, rel, ok, _ in gt.lookups(text):
        if not ok:
            continue
        real = os.path.join(root, rel)
        if os.path.isdir(real) and not os.path.islink(real):
            (listed if name in ("openat", "open") else dirs).add(rel)   # opened = listed
        elif os.path.lexists(real):
            files.add(rel)
    files.add(f)
    return {"files": sorted(files), "listed": sorted(listed), "dirs": sorted(dirs - listed)}

def main(argv):
    jobs, only, check, a = 1, [], False, list(argv)
    while a:
        x = a.pop(0)
        if x == "--jobs":
            jobs = int(a.pop(0))
        elif x == "--check":
            check = True
        else:
            only.append(x)
    root = os.getcwd()
    allu, _, err = gt.admit_universe()
    if err:
        print(err, file=sys.stderr)
        return 1
    files = [f for f in allu if not only or f in only]
    dec = gt.declared(allu)

    def one(f):
        fd, lp = tempfile.mkstemp(prefix="trace-")
        os.close(fd)
        try:
            st, _, tail, ended, _ = gt.run_one(root, f, set(allu) - dec[f][0], 900, None, dec[f][1], None, None, lp)
            text = open(lp, errors="replace").read()
        finally:
            os.unlink(lp)
        return f, st, ended, record_of(root, f, text), tail

    by, bad = collections.defaultdict(dict), 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for f, st, ended, rec, tail in pool.map(one, files):
            print("%s %s files=%d listed=%d dirs=%d" % (st, f, len(rec["files"]), len(rec["listed"]), len(rec["dirs"])), flush=True)
            if st != "passed" or not ended:
                print("  not recorded (the test must pass to be traced; the whole checkout view stays its view): " + tail[-300:].replace("\n", " | "), flush=True)
                continue
            by[os.path.dirname(f)][os.path.basename(f)] = rec
    for d, recs in by.items():
        path = os.path.join(d, "reads.json")
        old = {}
        try:
            doc = json.load(open(path))
            old = doc["tests"] if doc["format"] == FORMAT else {}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if check:
            for t, rec in recs.items():
                if old.get(t) != rec:
                    print("stale: %s %s" % (path, t))
                    bad = 1
            continue
        old.update(recs)
        with open(path, "w") as fh:
            json.dump({"format": FORMAT, "tests": old}, fh, indent=0, sort_keys=True)
            fh.write("\n")
    return bad

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
