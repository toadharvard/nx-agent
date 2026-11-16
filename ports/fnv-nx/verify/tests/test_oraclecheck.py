#!/usr/bin/env python3
"""Tests for verify/oraclecheck.py. Plain script: exit 0 on pass, one PASS/FAIL line per case."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import oraclecheck as O  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


T = "ports/fnv-nx/"
SRC = {T + "v/tool.py": "import helper\n", T + "v/helper.py": "", T + "v/other.py": "", T + "v/SPEC.md": "## tool\n",
       T + "v/tests/test_t.py": "", T + "v/tests/fixture.txt": "", T + "v/tests/oracle.json": "{}"}
TRACKED = set(SRC)
TEST = T + "v/tests/test_t.py"
ENTRY = {"subject": [T + "v/tool.py"], "references": [{"kind": "spec", "path": T + "v/SPEC.md", "anchor": "## tool"}]}


def judge(got):
    return O.judge(TEST, ENTRY, set(got), TRACKED, lambda p: SRC[p])


r = judge([TEST, T + "v/tool.py", T + "v/helper.py", T + "v/tests/fixture.txt"])
check("subject, its imports, the test and its fixtures: nothing undeclared", r == {"undeclared": [], "unopened": [], "refs_in_run": []}, r)
r = judge([TEST, T + "v/tool.py", T + "v/other.py"])
check("a tracked file outside the closure is undeclared", r["undeclared"] == [T + "v/other.py"] and r["unopened"] == [], r)
r = judge([TEST, T + "v/helper.py"])
check("a subject file never opened is reported", r["unopened"] == [T + "v/tool.py"] and r["undeclared"] == [], r)
r = judge([TEST, T + "v/tool.py", T + "v/SPEC.md"])
check("a reference the run opened is listed, not undeclared", r["refs_in_run"] == [T + "v/SPEC.md"] and r["undeclared"] == [], r)
r = O.judge(TEST, {}, {TEST, T + "v/tool.py"}, TRACKED, lambda p: SRC[p])
check("no entry: every opened file outside the test dir is undeclared", r["undeclared"] == [T + "v/tool.py"], r)

check("pyc maps to its source", O.source_of("/r/v/__pycache__/tool.cpython-313.pyc") == "/r/v/tool.py")
check("optimised pyc maps to its source", O.source_of("/r/v/__pycache__/tool.cpython-313.opt-1.pyc") == "/r/v/tool.py")
check("other paths unchanged", O.source_of("/r/v/tool.py") == "/r/v/tool.py" and O.source_of("/r/x/a.pyc") == "/r/x/a.pyc")

lines = ['100 openat(AT_FDCWD</r>, "v/tool.py", O_RDONLY|O_CLOEXEC) = 3</r/v/tool.py>\n',
         '100 openat(AT_FDCWD</r>, "v/__pycache__/helper.cpython-313.pyc", O_RDONLY|O_CLOEXEC) = 3</r/v/__pycache__/helper.cpython-313.pyc>\n',
         '100 openat(AT_FDCWD</r>, "v/other.py", O_RDONLY) = -1 ENOENT (No such file or directory)\n',
         '100 openat(AT_FDCWD</r>, "/etc/passwd", O_RDONLY) = 3</etc/passwd>\n',
         '100 execve("/r/v/tests/test_t.py", ["x"], 0x7ffd /* 3 vars */) = 0\n']
got = O.opened(lines, "/r", {"v/tool.py", "v/helper.py", "v/other.py", "v/tests/test_t.py"})
check("trace: opened and executed tracked files, pyc as source, failures and outside paths dropped",
      got == {"v/tool.py", "v/helper.py", "v/tests/test_t.py"}, got)
sys.exit(1 if fails else 0)
