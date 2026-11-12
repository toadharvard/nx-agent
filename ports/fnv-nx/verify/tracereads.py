#!/usr/bin/env python3
"""tracereads.py: write DIR/tests/reads.json ("fnvnx-selftest-reads/1") for the test files of the given directories.
    python3 -I tracereads.py [--jobs N] [TEST...]        (run in the checkout root; no TEST = the whole universe of gate selftests, gate_tests.admit_universe)
Each test runs once in its hermetic view (gate_tests.run_one, the same machine the gate uses) under strace; every path of the view that the
run opened, stat'ed, executed or read the link of, successfully, is a file of its record; a directory that the run opened (listed) contributes every
entry of that directory the view held (files as files, sub-directories as directories); a directory only stat'ed is a directory entry. The record is a
DECLARATION: gate_tests.py then builds the view of the test from exactly these files, so a read the record lacks fails in the view (red), and an edit of a
file outside the record cannot change the test's result. The tracer decides nothing about a result; it only writes the declaration to be reviewed."""
import collections
import concurrent.futures
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_tests as gt  # noqa: E402

Q = re.compile(r'"((?:[^"\\]|\\.)*)"')
FD = re.compile(r'^(?:(\w+)\()?(?:AT_FDCWD|\d+<([^>]*)>)')

def parse(text):
    """[(syscall, fd dir or None, path, ok)] of a strace -f -y log"""
    out, pend = [], {}
    for ln in text.splitlines():
        m = re.match(r"(\d+)\s+(.*)$", ln)
        if not m:
            continue
        pid, rest = m.groups()
        if rest.endswith("<unfinished ...>"):
            pend[pid] = rest[:-len("<unfinished ...>")]
            continue
        r = re.match(r"<\.\.\. (\w+) resumed>(.*)$", rest)
        if r:
            if pid not in pend:
                continue
            rest = pend.pop(pid) + r.group(2)
        c = re.match(r"(\w+)\((.*)\)\s+=\s+(-?\d+)", rest, re.S)
        if not c:
            continue
        name, args, ret = c.groups()
        q = Q.search(args)
        if not q or int(ret) < 0:
            continue
        pre = args[:q.start()]
        d = re.search(r"\d+<([^>]*)>", pre)
        out.append((name, d.group(1) if d else None, q.group(1).encode().decode("unicode_escape", "replace"), "O_DIRECTORY" in args))
    return out

def reads_of(root, f, text, view_excluded, skip):
    files, dirs = set(), set()
    for name, dfd, path, isdirflag in parse(text):
        if name == "chdir" or not path:
            continue
        p = path if path.startswith("/") else os.path.join(dfd or gt.VIEWDIR, path)
        p = os.path.normpath(p)
        if not (p == gt.VIEWDIR or p.startswith(gt.VIEWDIR + "/")):
            continue
        rel = os.path.relpath(p, gt.VIEWDIR)
        parts = rel.split(os.sep)
        if rel == "." or ".git" in parts or "__pycache__" in parts:
            continue
        real = os.path.join(root, rel)
        if os.path.isdir(real) and not os.path.islink(real):
            dirs.add(rel)
            if name in ("openat", "open") and not (os.path.abspath(real) == skip):   # opened = listed: every entry the view held
                for e in sorted(os.listdir(real)):
                    if e in (".git", "__pycache__"):
                        continue
                    er = os.path.join(rel, e)
                    if os.path.isdir(os.path.join(root, er)) and not os.path.islink(os.path.join(root, er)):
                        if os.path.abspath(os.path.join(root, er)) != skip:
                            dirs.add(er)
                    elif er not in view_excluded:
                        files.add(er)
        elif os.path.lexists(real) and rel not in view_excluded:
            files.add(rel)
    files.add(f)
    return sorted(files), sorted(d for d in dirs if not any(x.startswith(d + "/") for x in files))

def main(argv):
    jobs, dirs = 1, []
    a = list(argv)
    while a:
        x = a.pop(0)
        if x == "--jobs":
            jobs = int(a.pop(0))
        else:
            dirs.append(x)
    root = os.getcwd()
    allu, _, err = gt.admit_universe()
    if err:
        print(err, file=sys.stderr)
        return 1
    files = [f for f in allu if not dirs or f in dirs]
    dec = gt.declared(allu)

    def one(f):
        ex = set(allu) - dec[f][0]
        fd, lp = tempfile.mkstemp(prefix="trace-")
        os.close(fd)
        try:
            st, _, tail, ended, _ = gt.run_one(root, f, ex, 900, None, dec[f][1], None, None, lp)
            text = open(lp, errors="replace").read()
        finally:
            os.unlink(lp)
        fl, dl = reads_of(root, f, text, ex, None)
        return f, st, ended, fl, dl, tail

    by = collections.defaultdict(dict)
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for f, st, ended, fl, dl, tail in pool.map(one, files):
            print("%s %s files=%d dirs=%d" % (st, f, len(fl), len(dl)), flush=True)
            if st != "passed" or not ended:
                print("  not recorded (the test must pass to be traced; the whole checkout view stays its view): " + tail[-300:].replace("\n", " | "), flush=True)
                continue
            by[os.path.dirname(f)][os.path.basename(f)] = sorted(fl) + [d + "/" for d in dl]
    for d, recs in by.items():
        path = os.path.join(d, "reads.json")
        old = {}
        try:
            old = json.load(open(path))["tests"]
        except (OSError, ValueError, KeyError):
            pass
        old.update(recs)
        with open(path, "w") as fh:
            json.dump({"format": "fnvnx-selftest-reads/1", "tests": old}, fh, indent=0, sort_keys=True)
            fh.write("\n")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
