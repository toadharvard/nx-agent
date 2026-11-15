#!/usr/bin/env python3
"""selftests of crossing.py: the union, a missing source is red, a dispatch `-` slot is excluded.  Run: python3 -I <this file>."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import crossing  # noqa: E402

fails = []
with tempfile.TemporaryDirectory() as td:
    def w(n, t):
        p = os.path.join(td, n)
        open(p, "w").write(t)
        return p
    H = "dll\tname\tordinal\tconv\tret\targs\tnative_symbol\tbind\n"
    br = w("bridge.tsv", H + "k.dll\tA\t1\tstdcall\ti\t\tsym_a\tsym\nk.dll\tB\t2\tstdcall\ti\t\t-\tlookup\n")
    dp = w("rows.tsv", "# fnvnx-disp-rows/1\n# hash t slots=2 empty=0\n# table index addr symbol slot\n0\t0x1000\tsym_d\t1\n1\t0x2000\t-\t2\n")
    ex = w("exports.txt", "# called\nsym_e\nsym_a\n")
    t = crossing.universe(br, [dp], ex)
    syms = [ln for ln in t.splitlines() if not ln.startswith("#")]
    if syms != ["sym_a", "sym_d", "sym_e"]:
        fails.append("union (sorted, unique, `-` excluded in bridge and dispatch): %r" % syms)
    if sum(1 for ln in t.splitlines() if ln.startswith("# sources ")) != 3:
        fails.append("one `# sources` line per source: %r" % t)
    for name, args in (("bridge", (None, [dp], ex)), ("disp", (br, [], ex)), ("exports", (br, [dp], None)), ("exports", (br, [dp], os.path.join(td, "nope")))):
        try:
            crossing.universe(*args)
            fails.append("missing source %s is not red" % name)
        except SystemExit as e:
            if str(e) != "crossing:source-missing:" + name:
                fails.append("missing %s: wrong message %r" % (name, str(e)))
print("FAILED: %s" % fails if fails else "ok")
sys.exit(1 if fails else 0)
