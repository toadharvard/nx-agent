#!/usr/bin/env python3
"""selftests of crossing.py: the union, a missing source is red, a bridged import with no row is red, a dispatch `-` slot is excluded.  Run: python3 -I <this file>."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import crossing  # noqa: E402
sys.path.insert(0, os.path.join(HERE, "..", "contract"))
import store  # noqa: E402

fails = []
with tempfile.TemporaryDirectory() as td:
    def w(n, t):
        p = os.path.join(td, n)
        open(p, "w").write(t)
        return p
    H = "dll\tname\tordinal\tconv\tret\targs\tnative_symbol\tbind\n"
    br = w("bridge.tsv", H + "k.dll\tA\t1\tstdcall\ti\t\tsym_a\tsym\nk.dll\tB\t2\tstdcall\ti\t\t-\tlookup\n")
    dp = w("rows.tsv", "# fnvnx-disp-rows/1\n# hash t slots=2 empty=0\n# table index addr symbol slot\n0\t0x1000\tsym_d\t1\n1\t0x2000\t-\t2\n")
    def facts(n, imports):
        p = os.path.join(td, n)
        store.save(p, "facts", {"fn": {}, "secs": {}, "imports": imports, "vft": {}, "ptrs": []})
        return p
    ex = facts("exe.facts", {"4096": ["K.DLL", "A"], "4100": ["k", "#2"], "4104": ["lifted.dll", "Z"]})   # K.DLL = k.dll (norm_dll); #2 = ordinal of B; lifted.dll has no bridge rows
    t = crossing.universe(br, [dp], [ex])
    syms = [ln for ln in t.splitlines() if not ln.startswith("#")]
    if syms != ["sym_a", "sym_d"]:
        fails.append("union (sorted, unique, `-` excluded in bridge and dispatch): %r" % syms)
    if sum(1 for ln in t.splitlines() if ln.startswith("# sources ")) != 3:   # bridge, disp, facts
        fails.append("one `# sources` line per source: %r" % t)
    for name, args in (("bridge", (None, [dp], [ex])), ("disp", (br, [], [ex])), ("facts", (br, [dp], [])), ("facts:nope", (br, [dp], [os.path.join(td, "nope")]))):
        try:
            crossing.universe(*args)
            fails.append("missing source %s is not red" % name)
        except SystemExit as e:
            if str(e) != "crossing:source-missing:" + name:
                fails.append("missing %s: wrong message %r" % (name, str(e)))
    bad = facts("bad.facts", {"4096": ["k.dll", "C"], "4100": ["k.dll", "#9"]})
    try:
        crossing.universe(br, [dp], [ex, bad])
        fails.append("an import of a bridged dll with no bridge row is not red")
    except SystemExit as e:
        if str(e) != "crossing:no-bridge-row:k.dll!#9\ncrossing:no-bridge-row:k.dll!C":
            fails.append("no-bridge-row: wrong message %r" % str(e))
print("FAILED: %s" % fails if fails else "ok")
sys.exit(1 if fails else 0)
