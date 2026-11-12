#!/usr/bin/env python3
"""Tests for verify/factcache.py: facts keyed by the gate's import closure and the inputs' content. Plain script: PASS/FAIL lines, exit 0 on pass."""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import factcache  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
ENTRY = "ports/fnv-nx/verify/gate_exepin.py"
FAILED = []


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else ": %s" % (detail,)))
    if not ok:
        FAILED.append(name)


tmp = tempfile.mkdtemp(prefix="test-factcache-")
try:
    calls = []
    def comp(v):
        def f():
            calls.append(v)
            return v
        return f
    f = factcache.Facts(REPO, tmp, "g1", ENTRY)
    v1 = f.get(["a", 1], comp({"t": (1, 2), "s": "x"}))
    v2 = f.get(["a", 1], comp({"t": "never"}))
    check("a fact is computed once and then read", calls == [{"t": (1, 2), "s": "x"}] and v1 == v2, (calls, v1, v2))
    check("hit and miss return the same JSON value (tuples are lists)", v1 == {"t": [1, 2], "s": "x"}, v1)
    f.get(["a", 2], comp("other"))
    check("another input makes another fact", len(calls) == 2, calls)
    g = factcache.Facts(REPO, tmp, "g2", ENTRY)
    g.get(["a", 1], comp("g2"))
    check("another gate does not read this gate's fact", calls[-1] == "g2", calls)
    n = len(calls)
    f.get(["b"], comp({"ok": False}), keep=lambda v: v["ok"])
    f.get(["b"], comp({"ok": False}), keep=lambda v: v["ok"])
    check("a value that keep() refuses is not a fact", len(calls) == n + 2, calls)
    check("known() and put()", not f.known(["c"]) and (f.put(["c"], {"x": 1}) or True) and f.known(["c"]), "")
    p = f.path(["a", 1])
    open(p, "w").write("{torn")
    n = len(calls)
    f.get(["a", 1], comp({"again": True}))
    check("a torn fact file is a miss and is replaced", len(calls) == n + 1 and f.get(["a", 1], comp(0)) == {"again": True}, calls)
    h = factcache.Facts(REPO, None, "g3", ENTRY)
    h.get(["a"], comp(1))
    h.get(["a"], comp(1))
    check("without a cache directory every fact is computed and nothing is stored", calls.count(1) == 2 and not os.path.isdir(os.path.join(tmp, "facts", "g3")), calls)
    check("the key covers the gate name, the closure and the parts", f.key(["a"]) != g.key(["a"]) and f.key(["a"]) != f.key(["b"]) and len(f.files) >= 1, "")
    for e in ("ports/fnv-nx/verify/gate_abi.py", "ports/fnv-nx/verify/gate_determinism.py", "ports/fnv-nx/lift/proof/gate_fpexact.py"):
        try:
            factcache.Facts(REPO, None, "x", e)
            ok, why = True, ""
        except factcache.ClosureError as x:
            ok, why = False, str(x)
        check("the import closure of %s resolves with no finding" % os.path.basename(e), ok, why)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("%d failed: %s" % (len(FAILED), FAILED) if FAILED else "ALL PASS")
sys.exit(1 if FAILED else 0)
