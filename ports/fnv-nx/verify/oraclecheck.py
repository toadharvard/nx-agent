#!/usr/bin/env python3
"""oraclecheck: verification of the assumption of gate "oracle" - the declaration of a test is true. (ASM-042)
    python3 -I oraclecheck.py [--jobs N] [--timeout-s N] --out REPORT.json [TEST...]      cwd = checkout root

Runs each test file of the oracle universe (or the TESTs named, repo-relative) as `python3 -I FILE` under
`strace -f` (the call set of opentrace.py) and compares the tracked files of the tree the run opened with its entry in
DIR/tests/oracle.json. A compiled module DIR/__pycache__/NAME.<tag>[.opt-N].pyc counts as DIR/NAME.py (PEP 3147, PEP 488).
For a test, with CLOSURE = the import closure of its subject (gate_oracle.closure) and
ALLOWED = CLOSURE + the reference paths + the test file + the tracked files under its own DIR/tests:
  undeclared   tracked files opened outside ALLOWED: the subject list is not complete, or the test reads something that
               is neither under test nor a reference;
  unopened     subject files the run never opened: the test does not exercise what it declares;
  refs_in_run  reference paths the run opened (information: a reference read by the test is compared by the machine,
               one that is not is a text for the reader).
Limit of the method: Linux allows one tracer per process, so a test that itself runs strace (through opentrace.py or the
`kernel ptrace` item of envcheck.py) exits non-zero under this checker and is reported as not passing; its declaration
is not checked by this tool (at 0b9ad57: test_envcheck.py, test_gates.py, test_opentrace.py, 3 of 47).
A test passes iff it exits 0, undeclared and unopened are empty. The report states these facts per test; it ranks
nothing and excuses nothing. Exit 0 iff every test passes, 2 otherwise, 1 on a usage or tool error.
This is a verifier (after-merge, farm/runner.sh job `oraclecheck`), not a gate: it needs strace and runs every test."""
import concurrent.futures
import json
import os
import signal
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_oracle  # noqa: E402
import gate_tests  # noqa: E402
import opentrace  # noqa: E402

FORMAT = "fnvnx-oraclecheck/1"


def source_of(path):
    """DIR/__pycache__/NAME.<tag>.pyc -> DIR/NAME.py; any other path unchanged"""
    d, n = os.path.split(path)
    if os.path.basename(d) == "__pycache__" and n.endswith(".pyc") and n.count(".") >= 2:
        return os.path.join(os.path.dirname(d), n.split(".", 1)[0] + ".py")
    return path


def opened(trace_lines, root, tracked):
    """tracked files (repo-relative) the traced run opened or executed"""
    got = set()
    for _pid, kind, path, _flags in opentrace.parse(trace_lines, root):
        if kind == "fail":
            continue
        p = source_of(path)
        if p == root or not p.startswith(root + "/"):
            continue
        rel = p[len(root) + 1:]
        if rel in tracked:
            got.add(rel)
    return got


def judge(test, entry, got, tracked, read):
    """-> {"undeclared": [...], "unopened": [...], "refs_in_run": [...]}; pure function of its arguments"""
    subject = [s for s in entry.get("subject", []) if isinstance(s, str)]
    clo, why = gate_oracle.closure(subject, tracked, read)
    if why:
        clo = set(subject)
    refs = set(r["path"] for r in entry.get("references", []) if isinstance(r, dict) and isinstance(r.get("path"), str))
    tdir = os.path.dirname(test) + "/"
    allowed = clo | refs | {test} | set(f for f in tracked if f.startswith(tdir))
    return {"undeclared": sorted(got - allowed), "unopened": sorted(set(subject) - got), "refs_in_run": sorted(refs & got)}


def run_one(test, root, timeout):
    """-> (exit status or None on timeout, trace lines)"""
    tmp = tempfile.mkdtemp(prefix="oraclecheck-")
    trace = os.path.join(tmp, "trace")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_")}
    p = subprocess.Popen(["strace", "-f", "-y", "-qq", "-e", "trace=" + opentrace.TRACE_SET, "-o", trace, sys.executable, "-I", test],
                         cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env=env)
    try:
        rc = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        rc = None
    try:
        with open(trace, errors="surrogateescape") as fh:
            lines = fh.readlines()
    except OSError:
        lines = []
    try:
        os.unlink(trace)
        os.rmdir(tmp)
    except OSError:
        pass
    return rc, lines


def main(argv):
    jobs, timeout, out, tests, i = 1, 900.0, None, [], 0
    while i < len(argv):
        if argv[i] in ("--jobs", "--timeout-s", "--out") and i + 1 < len(argv):
            if argv[i] == "--jobs":
                jobs = max(1, int(argv[i + 1]))
            elif argv[i] == "--timeout-s":
                timeout = float(argv[i + 1])
            else:
                out = argv[i + 1]
            i += 2
        else:
            tests.append(argv[i])
            i += 1
    if out is None:
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 1
    root = os.getcwd()
    ls = subprocess.run(["git", "ls-files", "-z", "--", gate_oracle.TREE], capture_output=True, check=True).stdout
    tracked = set(p for p in ls.decode("utf-8", "surrogateescape").split("\0") if p)
    gates = gate_oracle.gatesdir.load_dir_or_empty()
    argv_t = next((g["argv"] for g in gates.get("gates", []) if g.get("name") == "selftests"), None)
    if argv_t is None:
        print("oraclecheck: verify/gates.d has no selftests gate", file=sys.stderr)
        return 1
    universe = gate_tests.universe(gate_tests.parse(argv_t[argv_t.index(gate_oracle.TREE + "/verify/gate_tests.py") + 1:])[1])
    unknown = [t for t in tests if t not in universe]
    if unknown:
        print("oraclecheck: not a test of the universe: " + ", ".join(unknown), file=sys.stderr)
        return 1
    tests = tests or universe

    def read(p):
        with open(p, encoding="utf-8", errors="surrogateescape") as fh:
            return fh.read()

    def one(t):
        doc = gate_oracle.load(os.path.dirname(t) + "/oracle.json")
        entry = doc["tests"].get(os.path.basename(t)) if isinstance(doc, dict) and isinstance(doc.get("tests"), dict) else None
        rc, lines = run_one(t, root, timeout)
        row = {"test": t, "exit": rc, "declared": isinstance(entry, dict)}
        row.update(judge(t, entry if isinstance(entry, dict) else {}, opened(lines, root, tracked), tracked, read))
        row["pass"] = rc == 0 and row["declared"] and not row["undeclared"] and not row["unopened"]
        return row
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
        rows = list(ex.map(one, tests))
    bad = [r["test"] for r in rows if not r["pass"]]
    with open(out, "w") as fh:
        json.dump({"format": FORMAT, "tests": rows, "universe": len(rows), "passed": len(rows) - len(bad), "failed": bad}, fh,
                  sort_keys=True, indent=1)
    print("oraclecheck: %d of %d tests match their declaration" % (len(rows) - len(bad), len(rows)))
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
